"""Read an iPod Classic iTunesDB.

The database is a tree of length-prefixed chunks: mhbd (whole file) >
mhsd (dataset) > mhlt (track list) > mhit (one track) > mhod (one field).
Only the track-list dataset is read; playlists, artwork database and the
podcast datasets are ignored.

Two quirks this handles, both measured on a real Classic:

  * mhlt's own length field is unreliable, so the track list is bounded by the
    enclosing mhsd instead of trusting it.
  * mhit has a fixed header holding the numeric fields, followed by mhod
    children that use an older "header at 40, UTF-16LE body" layout.

Reading is strictly read-only: the volume is opened "rb" and never written.
"""

from __future__ import annotations

import os
import struct
from typing import Iterator

#: mhod string-type id -> field name. Only the ones worth carrying are mapped;
#: anything else is skipped rather than guessed at.
STRING_MHOD = {
    1: "title",
    2: "path",
    3: "album",
    4: "artist",
    5: "genre",
    8: "comment",
    10: "composer",
    12: "album_artist",
    13: "grouping",
}

#: Fixed-header field -> (byte offset inside mhit, struct format).
FIXED = {
    "dbid": (16, "<I"),
    "length_ms": (40, "<I"),
    "track_number": (44, "<H"),
    "cd_number": (46, "<H"),
    "year": (52, "<H"),
    "bitrate": (56, "<I"),
    "sample_rate": (60, "<I"),
    "play_count": (80, "<I"),
    "rating": (84, "<I"),
}

ART_TAIL_SCAN = 200_000
"""How far back from EOF to look for the appended ID3 tag."""


class NotAnITunesDB(ValueError):
    """The file does not start with mhbd, or its length disagrees with its size."""


def chunks(buf: bytes, off: int, end: int) -> Iterator[tuple[bytes, int, int, int]]:
    """Walk sibling chunks: yields (id, offset, header_len, total_len)."""
    while off + 12 <= end:
        cid = buf[off:off + 4]
        hlen, tlen = struct.unpack("<II", buf[off + 4:off + 12])
        if tlen < hlen or off + tlen > end:
            break
        yield cid, off, hlen, tlen
        off += tlen


def has_art(aiff_path: str, scan: int = ART_TAIL_SCAN) -> bool:
    """Whether an iPod AIFF carries embedded artwork.

    iTunes appends a bare ID3v2.3 tag to the end of each AIFF: the literal
    bytes ``ID3 <size>`` followed by a second, normal ``ID3\\x03`` header. The
    artwork is an APIC frame inside that inner tag. Nothing in the AIFF header
    points at it, which is why ffprobe reports no artwork for these files.
    """
    try:
        size = os.path.getsize(aiff_path)
        with open(aiff_path, "rb") as f:
            f.seek(max(0, size - scan))
            tail = f.read()
    except OSError:
        return False

    pos = tail.rfind(b"ID3 ")
    if pos < 0:
        return False
    inner = pos + 8
    if tail[inner:inner + 3] != b"ID3":
        return False

    # ID3v2 synchsafe size: seven bits per byte.
    tag_len = 0
    for b in tail[inner + 6:inner + 10]:
        tag_len = (tag_len << 7) | (b & 0x7F)

    pos, end = inner + 10, min(inner + 10 + tag_len, len(tail))
    while pos + 10 <= end:
        fid = tail[pos:pos + 4]
        flen = int.from_bytes(tail[pos + 4:pos + 8], "big")
        if fid == b"APIC":
            return b"\xff\xd8\xff" in tail[pos + 10:pos + 10 + flen]
        pos += 10 + flen
    return False


def read_art(aiff_path: str, dest: str, scan: int = ART_TAIL_SCAN) -> bool:
    """Extract the APIC JPEG from an iPod AIFF into ``dest``.

    Returns False when there is none, matching :func:`has_art`. The JPEG is
    located by its own SOI marker inside the frame, because the frame body
    starts with a MIME type string of varying length.
    """
    try:
        size = os.path.getsize(aiff_path)
        with open(aiff_path, "rb") as f:
            f.seek(max(0, size - scan))
            tail = f.read()
    except OSError:
        return False

    pos = tail.rfind(b"ID3 ")
    if pos < 0:
        return False
    inner = pos + 8
    if tail[inner:inner + 3] != b"ID3":
        return False

    tag_len = 0
    for b in tail[inner + 6:inner + 10]:
        tag_len = (tag_len << 7) | (b & 0x7F)

    pos, end = inner + 10, min(inner + 10 + tag_len, len(tail))
    while pos + 10 <= end:
        fid = tail[pos:pos + 4]
        flen = int.from_bytes(tail[pos + 4:pos + 8], "big")
        if fid == b"APIC":
            body = tail[pos + 10:pos + 10 + flen]
            jpg = body.find(b"\xff\xd8\xff")
            if jpg < 0:
                return False
            with open(dest, "wb") as f:
                f.write(body[jpg:])
            return True
        pos += 10 + flen
    return False


def parse(path: str) -> Iterator[dict]:
    """Yield one dict per track, in database order."""
    with open(path, "rb") as f:
        buf = f.read()

    if buf[:4] != b"mhbd":
        raise NotAnITunesDB(f"{path}: not an iTunesDB (no mhbd header)")
    mhbd_hlen, mhbd_tlen = struct.unpack("<II", buf[4:12])
    if mhbd_tlen != len(buf):
        raise NotAnITunesDB(
            f"{path}: mhbd length {mhbd_tlen} != file size {len(buf)}")

    for cid, off, hlen, tlen in chunks(buf, mhbd_hlen, mhbd_tlen):
        if cid != b"mhsd":
            continue
        # Dataset type 1 is the track list; 2 is playlists, 3 podcasts, 4/5 art.
        if struct.unpack("<I", buf[off + 12:off + 16])[0] != 1:
            continue
        for c2, o2, h2, _ in chunks(buf, off + hlen, off + tlen):
            if c2 != b"mhlt":
                continue
            pos, end = o2 + h2, off + tlen
            while pos + 12 <= end and buf[pos:pos + 4] == b"mhit":
                h3, l3 = struct.unpack("<II", buf[pos + 4:pos + 12])
                if l3 < h3 or pos + l3 > end:
                    break
                yield _read_mhit(buf, pos, h3, l3)
                pos += l3


def _read_mhit(buf: bytes, pos: int, hlen: int, tlen: int) -> dict:
    rec: dict = {}
    for name, (off, fmt) in FIXED.items():
        n = struct.calcsize(fmt)
        if off + n <= hlen:
            rec[name] = struct.unpack(fmt, buf[pos + off:pos + off + n])[0]

    for cid, off, _h, len4 in chunks(buf, pos + hlen, pos + tlen):
        if cid != b"mhod":
            continue
        name = STRING_MHOD.get(struct.unpack("<I", buf[off + 12:off + 16])[0])
        if not name:
            continue
        slen = struct.unpack("<I", buf[off + 28:off + 32])[0]
        if 40 + slen <= len4:
            rec[name] = buf[off + 40:off + 40 + slen].decode("utf-16-le", "replace")
    return rec


def normalize(rec: dict, music_root: str | None = None) -> dict:
    """Turn a raw record into the shape the rest of the library expects.

    The path field is an HFS-style colon path ending at the file. Only the part
    below ``Music:`` is kept, as a relative path, so the record is portable
    across mount points.
    """
    out = dict(rec)
    raw_path = out.pop("path", "").lstrip(":")
    out["relpath"] = raw_path.split("Music:", 1)[-1].lstrip(":").replace(":", os.sep)
    out["source"] = raw_path
    if music_root is not None:
        out["art"] = has_art(os.path.join(music_root, out["relpath"]))
    return out


def read_all(db_path: str, music_root: str | None = None) -> list[dict]:
    """Parse and normalise every track in one call."""
    return [normalize(r, music_root) for r in parse(db_path)]
