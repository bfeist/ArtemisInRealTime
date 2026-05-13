"""EOL filename normalization helpers.

EOL serves files as `ART002-E-168.JPG` (uppercase mission code + roll, hyphen
separators, frame number un-padded). The canonical NASA ID we use throughout
the pipeline is `art002e000168` (lowercase, no separators, 6-digit zero-padded
frame). This module centralizes the conversion so 3h (download), 4a (EXIF
intake), 4c (ledger), and 3l (NEF/EOL diff) all agree.
"""

from __future__ import annotations

import re

# ART002-E-168.JPG  →  ('ART002', 'E', '168')
_EOL_HYPHEN_RE = re.compile(
    r"^(?P<mission>[A-Z0-9]+)-(?P<roll>[A-Z])-(?P<frame>\d+)\.(?P<ext>jpe?g)$",
    re.IGNORECASE,
)


def to_canonical_nasa_id(filename: str) -> str | None:
    """Return the canonical NASA ID stem for an EOL filename.

    Accepts either the EOL on-the-wire form (`ART002-E-168.JPG`) or files
    that are already canonical (`art002e000168.jpg`). Returns None for
    unrecognised filenames.

    >>> to_canonical_nasa_id('ART002-E-168.JPG')
    'art002e000168'
    >>> to_canonical_nasa_id('art002e000168.jpg')
    'art002e000168'
    >>> to_canonical_nasa_id('weird.txt') is None
    True
    """
    m = _EOL_HYPHEN_RE.match(filename)
    if m:
        return (
            f"{m.group('mission').lower()}"
            f"{m.group('roll').lower()}"
            f"{m.group('frame').zfill(6)}"
        )
    # Already canonical? Lowercase with no hyphens, .jpg/.jpeg extension.
    stem, _, ext = filename.rpartition(".")
    if ext.lower() in ("jpg", "jpeg") and "-" not in stem:
        return stem.lower()
    return None


def to_canonical_filename(filename: str) -> str | None:
    """Like `to_canonical_nasa_id` but returns the full canonical filename
    (`art002e000168.jpg`). Always uses `.jpg` extension."""
    nasa_id = to_canonical_nasa_id(filename)
    return f"{nasa_id}.jpg" if nasa_id else None
