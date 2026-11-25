"""What this library can and cannot do, enforced at runtime.

Three things look like features but are platform limits, not missing code:

  artist image   Music.app renders artist photos only from Apple's cloud, keyed
                 by catalog ID. A local file has no field to carry one.
  timed lyrics   Music.app renders syllable-timed lyrics only from its own
                 cached TTML, same catalog-ID requirement.
  disc / gapless that metadata exists, but writing it makes Music rename files.

The first two raise on call. Accepting them and producing no effect would ship
the bug silently.
"""

from __future__ import annotations


class Unsupported(Exception):
    """The platform cannot express this, not that the argument was wrong."""


#: Capabilities with a working implementation.
SUPPORTED = {
    "import": "add files to the Music library, verified by track-count delta",
    "metadata": "title / artist / album_artist / album / genre / year / track",
    "cover": "album cover embedded as a covr atom in the AAC file",
    "lyrics_plain": "plain-text lyrics written to track.lyrics",
    "readback": "read the library back as the source of truth after import",
}

#: Refused on purpose, with the reason a caller needs.
UNSUPPORTED = {
    "artist_image": (
        "Music.app renders artist photos only from Apple Music's cloud, keyed by "
        "catalog ID. Locally imported files have no field that carries one, so "
        "nothing written here would ever display."
    ),
    "lyrics_timed": (
        "Timed and syllable lyrics render only from Music.app's cached TTML, "
        "which Apple returns only for catalog matches. A CD rip has no catalog "
        "ID; LRC, TTML and .lrc sidecars are all ignored (see docs/LYRICS.md)."
    ),
    "lyrics_sidecar": (
        "A sibling .lrc file is never read by Music.app, before or after a "
        "restart. Only the track.lyrics property displays."
    ),
}


def require(capability: str) -> None:
    """Raise before doing work rather than after.

    Called at the top of any public entry point that would otherwise reach the
    platform and quietly succeed at nothing.
    """
    if capability in UNSUPPORTED:
        raise Unsupported(f"{capability}: {UNSUPPORTED[capability]}")


def matrix() -> dict:
    """Machine-readable capability report, for docs and for callers."""
    return {
        "supported": dict(SUPPORTED),
        "unsupported": dict(UNSUPPORTED),
    }
