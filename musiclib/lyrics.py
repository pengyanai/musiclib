"""Fetch plain-text lyrics from LRCLib and write them into the library.

Only plain text. Timed lyric files are a dead end on this platform — see
docs/LYRICS.md and :data:`musiclib.capabilities.UNSUPPORTED`.

Matching, not fetching, is the hard part. A library of Chinese live recordings
has several tracks with the same name, so a title-only search returns the studio
version as often as not. Two safeguards:

  * queries escalate from precise to loose, and the first tier that yields a
    candidate within the duration window wins;
  * a candidate is only accepted when its duration is within ``max_delta`` of
    the local track. Duration is the cheapest signal that the match is the same
    recording, and it is what separates a live take from its studio original.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Iterator

from . import musicapp

LRCLIB = "https://lrclib.net/api/search"
UA = "musiclib/1.0 (local library maintenance)"
DEFAULT_MAX_DELTA = 8
"""Seconds. A live and a studio take of the same song differ by far more."""

_PAREN = re.compile(r"\s*[\(（\[【].*?[\)）\]】]")


def query(params: dict, retries: int = 2, timeout: float = 15) -> list[dict] | None:
    """One LRCLib search. Returns None on network failure, distinguishing
    "the service is down" from "there is no such song" — they need different
    handling upstream."""
    url = f"{LRCLIB}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == retries:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def variants(title: str, artist: str) -> Iterator[dict]:
    """Escalating queries, most precise first.

    The parenthetical strip handles titles like "Name (Live)": the suffix
    rarely appears on LRCLib, but dropping it can also match the studio take, so
    it is tried second, not first, and still subject to the duration check.
    """
    yield {"track_name": title, "artist_name": artist}
    stripped = _PAREN.sub("", title).strip()
    if stripped and stripped != title:
        yield {"track_name": stripped, "artist_name": artist}
    yield {"q": title}


def pick(candidates: list[dict], duration: int,
         max_delta: int) -> tuple[dict, int] | None:
    """Closest candidate inside the duration window, or None."""
    if not candidates or not duration:
        return None
    scored = [(abs(c["duration"] - duration), c) for c in candidates
              if c.get("duration") and c.get("plainLyrics")]
    if not scored:
        return None
    delta, best = min(scored, key=lambda x: x[0])
    return (best, delta) if delta <= max_delta else None


def find(title: str, artist: str, duration: int,
         max_delta: int = DEFAULT_MAX_DELTA) -> tuple[dict | None, str, int | None]:
    """Locate lyrics. Returns (candidate, status, delta) with status ok/miss/network."""
    seen: set = set()
    for params in variants(title, artist):
        cands = query(params)
        if cands is None:
            return None, "network", None
        cands = [c for c in cands if c.get("id") not in seen]
        seen.update(c.get("id") for c in cands)
        hit = pick(cands, duration, max_delta)
        if hit:
            return hit[0], "ok", hit[1]
        time.sleep(0.25)
    return None, "miss", None


def enrich(library: list[dict] | None = None, max_delta: int = DEFAULT_MAX_DELTA,
           only_missing: bool = True) -> dict:
    """Fill in plain-text lyrics for the library.

    Idempotent with ``only_missing``: tracks that already have lyrics are
    skipped without a network call, so a re-run after a partial failure costs
    only the tracks that failed.
    """
    tracks = library if library is not None else musicapp.read_library()
    report = {"tracks": len(tracks), "considered": 0, "ok": 0, "miss": 0,
              "network": 0, "written": []}

    for t in tracks:
        if only_missing and t.get("has_lyrics"):
            continue
        report["considered"] += 1
        pid = t.get("pid")
        if not pid:
            continue
        cand, status, delta = find(t.get("name", ""), t.get("artist", ""),
                                   t.get("duration", 0), max_delta)
        report[status] = report.get(status, 0) + 1
        if status != "ok":
            continue
        try:
            musicapp.set_lyrics(pid, cand["plainLyrics"])
        except musicapp.AppleScriptError as exc:
            report.setdefault("write_errors", []).append(
                {"name": t.get("name"), "error": str(exc)})
            continue
        report["written"].append({"name": t.get("name"), "delta_s": delta,
                                  "source": "lrclib"})
    return report
