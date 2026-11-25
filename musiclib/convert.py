"""Transcode source audio into tagged AAC files ready for import.

The settings and the tag-clearing below are not arbitrary: each one is here
because omitting it produced a specific, observed defect.

  disc cleared       The source AIFFs carry TPOS=1 in the appended ID3. ffmpeg
                     inherits it and writes a disc tag; Music then renames the
                     file to "1-08 Name.m4a". Clearing it is the only fix that
                     works (`-metadata track=` does not override it).
  album-level art    Cover art lives on whichever track of the album has it;
                     the rest inherit it. Per-track lookup leaves albums with
                     art on one track and none on the others.
  absolute temp      Art is extracted to a temp file and passed as a second
                     input; passing the source AIFF itself does not work
                     because the art is in a trailing ID3, not a stream.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import itunesdb, scrub

DEFAULT_BITRATE = "256k"
DEFAULT_CODEC = "aac_at"
"""AudioToolbox's AAC encoder. Chosen over the built-in one for quality at the
same bitrate; falls back to "aac" when the machine has no AudioToolbox."""

DEFAULT_COVER_SIZE = 500
"""Pixels. Matches what the source rips embed, so an album does not end up with
a mix of 500px and 1200px covers."""

_TAG_FIELDS = (
    ("title", "title"),
    ("artist", "artist"),
    ("album", "album"),
    ("genre", "genre"),
)


def clean_title(title: str, artist: str, album: str) -> tuple[str, str, str | None]:
    """Undo the "Artist - " prefix that iPod-era rips carry on some titles.

    Only strips when the prefix really is the performer, or when what remains is
    exactly the album name — the shape a duet gets when it was named after both
    performers. Returns (title, artist, original_title_or_None); the original is
    kept so it can be written into a comment field and recovered later.
    """
    if " - " not in title:
        return title, artist, None
    prefix, rest = title.split(" - ", 1)
    if not rest:
        return title, artist, None
    if prefix == artist:
        return rest, artist, title
    if artist and prefix.startswith(artist) and album and rest == album:
        return rest, prefix, title
    if album and rest == album:
        return rest, prefix if prefix.startswith(artist) else artist, title
    return title, artist, None


def outname(track: dict, index: int) -> str:
    """Filename for the converted track.

    The index suffix is not cosmetic: some albums have two tracks with the same
    title (a live version and a studio one), and they would otherwise collide.
    """
    base = track.get("title") or f"track{index:03d}"
    return f"{base} [{index:03d}].m4a"


class CoverFetcher:
    """Download album covers with sacad, for albums that shipped without one.

    sacad is GPL-3.0 and this package is MIT, so it is called as a subprocess
    rather than imported: an exec boundary keeps the licenses separate.

    Known limitation, measured: sacad resolves Latin-script album names but not
    Chinese ones: a Chinese album name returns nothing while a Latin one
    succeeds. A miss is recorded and the
    album is converted without a cover — which is the state it was already in.
    """

    def __init__(self, binary: str | None = None, size: int = DEFAULT_COVER_SIZE,
                 timeout: float = 30):
        self.binary = binary or shutil.which("sacad") or ""
        self.size = size
        self.timeout = timeout
        self.misses: list[dict] = []

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def fetch(self, artist: str, album: str, work_dir: str) -> str | None:
        if not self.available or not artist or not album:
            return None
        safe = re.sub(r"[^\w.-]+", "_", f"{artist}_{album}")[:80]
        dest = os.path.join(work_dir, f"dl_{safe}.jpg")
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        try:
            r = subprocess.run(
                [self.binary, "-v", "quiet", artist, album,
                 str(self.size), dest],
                capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.misses.append({"artist": artist, "album": album,
                                "reason": repr(exc)[:120]})
            return None
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        self.misses.append({"artist": artist, "album": album,
                            "reason": (r.stderr or "no result").strip()[:120]})
        return None


def clean_metadata(track: dict) -> tuple[dict, list[dict]]:
    """Strip download-site watermarks from the tags that are about to be written.

    Returns (cleaned_track, findings). The original title is preserved in
    ``original_title`` so the pre-scrub value survives in the comment field.
    """
    tags = {k: track[k] for k in ("title", "artist", "album", "comment")
            if track.get(k)}
    result = scrub.scrub(tags)
    out = dict(track)
    if not result.changed:
        return out, []

    for name in ("title", "artist", "album"):
        if name in result.tags and result.tags[name] != track.get(name):
            if name == "title" and not out.get("original_title"):
                out["original_title"] = track.get("title")
            out[name] = result.tags[name]
    if "comment" in result.tags:
        out["comment"] = result.tags["comment"]
    return out, [f.as_dict() for f in result.findings]


class Converter:
    """Converts a track list, sharing one album-art cache across the run."""

    def __init__(self, src_root: str, out_dir: str, bitrate: str = DEFAULT_BITRATE,
                 codec: str = DEFAULT_CODEC, work_dir: str | None = None,
                 fetcher: "CoverFetcher | None" = None,
                 scrub_tags: bool = True):
        self.src_root = src_root
        self.out_dir = out_dir
        self.bitrate = bitrate
        self.codec = codec
        self.work_dir = work_dir or os.path.join(out_dir, ".art")
        self.fetcher = fetcher
        self.scrub_tags = scrub_tags
        self._art: dict[str, str | None] = {}
        self.findings: list[dict] = []
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def art_for(self, album: str, tracks: list[dict], artist: str = "") -> str | None:
        """Find a cover for the whole album: embedded first, download second.

        Embedded art comes from the source file itself, so it is preferred — it
        is the cover the album was actually released with. The fetcher is the
        fallback for the albums that shipped with none, which on the measured
        corpus was a quarter of them.
        """
        key = album or ""
        if key in self._art:
            return self._art[key]

        found = None
        for t in tracks:
            if t.get("album") != album or not t.get("art"):
                continue
            dest = os.path.join(self.work_dir, f"art_{t['dbid']}.jpg")
            if itunesdb.read_art(os.path.join(self.src_root, t["relpath"]), dest):
                found = dest
                break

        if not found and self.fetcher and album:
            found = self.fetcher.fetch(artist, album, self.work_dir)

        self._art[key] = found
        return found

    def build_command(self, track: dict, dest: str, art: str | None) -> list[str]:
        src = os.path.join(self.src_root, track["relpath"])
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src]
        if art:
            cmd += ["-i", art, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                    "-disposition:v", "attached_pic"]
        else:
            cmd += ["-map", "0:a"]
        cmd += ["-c:a", self.codec, "-b:a", self.bitrate, "-ac", "2", "-ar", "44100"]

        for key, tag in _TAG_FIELDS:
            if track.get(key):
                cmd += ["-metadata", f"{tag}={track[key]}"]
        if track.get("artist"):
            cmd += ["-metadata", f"album_artist={track['artist']}"]
        if track.get("year"):
            cmd += ["-metadata", f"date={track['year']}"]
        if track.get("track_number"):
            total = track.get("album_total")
            num = f"{track['track_number']}/{total}" if total else str(track["track_number"])
            cmd += ["-metadata", f"track={num}"]
        if track.get("original_title"):
            cmd += ["-metadata", f"comment=iPod title: {track['original_title']}"]
        cmd += ["-metadata", "disc="]
        cmd.append(dest)
        return cmd

    def convert(self, track: dict, tracks: list[dict]) -> tuple[dict, str, str]:
        """Convert one track. Returns (track, status, detail) with status ok/skip/fail."""
        dest = os.path.join(self.out_dir, track["outname"])
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return track, "skip", "exists"
        art = self.art_for(track.get("album") or "", tracks,
                           track.get("artist") or "")
        r = subprocess.run(self.build_command(track, dest, art),
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dest):
            return track, "fail", (r.stderr or "").strip()[:300]
        return track, "ok", str(os.path.getsize(dest))

    def prepare(self, tracks: list[dict]) -> list[dict]:
        """Decide the final tags for every track before any conversion runs.

        Cleaning here rather than inside convert() means the findings list is
        complete before files are written, so a caller can inspect and refuse
        the whole batch. It also keeps the filename and the embedded title
        derived from the same cleaned value, so they cannot disagree.
        """
        prepared = []
        for track in tracks:
            cleaned, findings = (clean_metadata(track) if self.scrub_tags
                                 else (track, []))
            self.findings.extend(findings)
            prepared.append(cleaned)
        return prepared

    def run(self, tracks: list[dict], jobs: int | None = None,
            limit: int = 0) -> list[tuple[dict, str, str]]:
        """Convert many tracks. ``limit`` caps how many are attempted."""
        jobs = jobs or min(8, (os.cpu_count() or 4))
        prepared = self.prepare(tracks)
        pending = prepared[:limit] if limit else prepared
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(self.convert, t, prepared) for t in pending]
            return [f.result() for f in futures]


def summarize(results: list[tuple[dict, str, str]],
              converter: "Converter | None" = None) -> dict:
    """Counts, failures, and what happened to covers and tags."""
    counts = {"ok": 0, "skip": 0, "fail": 0}
    failures = []
    for track, status, detail in results:
        counts[status] = counts.get(status, 0) + 1
        if status == "fail":
            failures.append({"title": track.get("title"), "error": detail})
    out = {**counts, "total": len(results), "failures": failures}
    if converter is not None:
        out["covers"] = {
            "albums": len(converter._art),
            "without": sum(1 for v in converter._art.values() if not v),
        }
        if converter.fetcher is not None:
            out["covers"]["downloaded"] = sum(
                1 for v in converter._art.values()
                if v and os.path.basename(v).startswith("dl_"))
            out["covers"]["fetch_misses"] = len(converter.fetcher.misses)
        out["scrubbed"] = len(converter.findings)
    return out
