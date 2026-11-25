"""Talk to Music.app over AppleScript.

Every call goes through one helper that surfaces the AppleScript error number.

`add` with a relative path returns normally and adds nothing, so a naive loop
reports every file as sent while the library is unchanged. The track count is the
only confirmation that holds.

Absolute paths are mandatory for the same reason: ``POSIX file`` resolves
against Music's working directory, not the caller's, and a relative path fails
with -1700.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Iterable

APP = "Music"


class AppleScriptError(RuntimeError):
    """Music returned an error; the message carries the AppleScript code."""


def run(script: str, timeout: float = 300) -> str:
    """Run AppleScript and return stdout, raising on error."""
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise AppleScriptError(r.stderr.strip() or f"osascript exited {r.returncode}")
    return r.stdout.strip()


def _quote(s: str) -> str:
    """Escape for an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def track_count() -> int:
    """Number of tracks in the library.

    This is the only reliable success signal: `add` can return without adding.
    """
    out = run(f'tell application "{APP}" to get count of tracks of library playlist 1')
    return int(out)


def track_names() -> set[str]:
    """Names of every track currently in the library."""
    out = run(
        f'tell application "{APP}" to get name of every track of library playlist 1')
    return {s.strip() for s in out.split(", ") if s.strip()}


def add_files(paths: Iterable[str], batch: int = 20, strict: bool = True) -> dict:
    """Add local files to the library, checking that the count actually moved.

    ``strict`` raises when the library did not grow by exactly the number of
    files handed over. Leave it on: without the check, a run that added nothing
    looks identical to one that succeeded.
    """
    files = [os.path.abspath(p) for p in paths]
    if not files:
        return {"before": track_count(), "after": track_count(),
                "sent": 0, "added": 0}

    before = track_count()
    sent = 0
    for i in range(0, len(files), batch):
        group = files[i:i + batch]
        quoted = ", ".join(f'POSIX file "{_quote(p)}"' for p in group)
        run(f'tell application "{APP}" to add {{ {quoted} }}')
        sent += len(group)

    after = track_count()
    added = after - before
    result = {"before": before, "after": after, "sent": sent, "added": added}
    if strict and added != sent:
        raise AppleScriptError(
            f"asked Music to add {sent} files but the library grew by {added}; "
            "the add call does not report failures, so nothing here can be trusted")
    return result


def read_library(with_lyrics: bool = True) -> list[dict]:
    """Read the library back — the post-import source of truth.

    Returns name/artist/album/genre/year/track/art per track, matching the
    columns of library.tsv. ``art`` is 1 when the track has embedded artwork;
    Music reports this only through `count of artworks`, and it is the one
    field that cannot be reconstructed from the source files alone.
    """
    lyr = "1" if with_lyrics else "0"
    script = f'''
tell application "{APP}"
  set out to ""
  repeat with t in (every track of library playlist 1)
    set nm to ""
    set ar to ""
    set al to ""
    set ge to ""
    set yr to "0"
    set tn to "0"
    set ac to "0"
    set ly to "0"
    try
      set nm to (name of t) as string
    end try
    try
      set ar to (artist of t) as string
    end try
    try
      set al to (album of t) as string
    end try
    try
      set ge to (genre of t) as string
    end try
    try
      set yr to (year of t) as string
    end try
    try
      set tn to (track number of t) as string
    end try
    try
      set ac to (count of artworks of t) as string
    end try
    try
      if (lyrics of t) is not "" then set ly to "1"
    end try
    set out to out & nm & tab & ar & tab & al & tab & ge & tab & yr & tab & tn & tab & ac & tab & ly & linefeed
  end repeat
  return out
end tell'''
    rows = []
    for line in run(script).split("\n"):
        c = line.split("\t")
        if len(c) < 7:
            continue
        rows.append({
            "name": c[0], "artist": c[1], "album": c[2], "genre": c[3],
            "year": c[4], "track": c[5], "art": c[6] == "1" if c[6].isdigit() else False,
            "has_lyrics": (len(c) > 7 and c[7] == "1") if lyr == "1" else None,
        })
    return rows


def set_lyrics(persistent_id: str, text: str) -> None:
    """Write plain-text lyrics onto a track.

    Goes through a temp file because lyric bodies contain quotes and newlines
    that would terminate an inline AppleScript string literal.
    """
    fd, path = tempfile.mkstemp(prefix="musiclib_lyr_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        run(f'''
tell application "{APP}"
  set t to (first track of library playlist 1 whose persistent ID is "{_quote(persistent_id)}")
  set lyrics of t to (read (POSIX file "{_quote(path)}") as «class utf8»)
end tell''')
    finally:
        os.unlink(path)


def as_json(rows) -> str:
    """Convenience for docs and CLIs."""
    return json.dumps(rows, ensure_ascii=False, indent=1)
