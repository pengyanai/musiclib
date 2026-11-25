"""musiclib — move a music collection into Apple Music on macOS.

Everything here is either something that worked on that corpus or something
that failed in a way worth encoding, so the next person does not repeat it.

Entry points:

    musiclib.itunesdb    read the iPod database  (read-only, no libgpod needed)
    musiclib.convert     transcode to tagged AAC with album cover
    musiclib.musicapp    import into Music.app and read the library back
    musiclib.lyrics      plain-text lyrics from LRCLib
    musiclib.scrub       remove download-site watermarks, keep license text

Artist images and timed lyrics have no implementation: both render only from
Apple's cloud, keyed by catalog ID, which a local file does not have.
:mod:`musiclib.capabilities` raises on them.

Imports are verified against the library's track count, because Music's `add`
returns without adding when given a relative path.
"""

from .capabilities import SUPPORTED, UNSUPPORTED, Unsupported, matrix, require
from . import convert, itunesdb, lyrics, musicapp, scrub

__version__ = "0.1.0"

__all__ = [
    "SUPPORTED", "UNSUPPORTED", "Unsupported", "matrix", "require",
    "itunesdb", "convert", "musicapp", "lyrics", "scrub",
]
