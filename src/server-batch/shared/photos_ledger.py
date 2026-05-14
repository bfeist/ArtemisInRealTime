"""Per-NASA-ID photo ledger — the canonical merged record.

The ledger is one JSONL file per mission at `mission.photos_ledger_path`.
Every row is a `LedgerRecord` keyed by lowercase `nasa_id`. The shape is
documented in `docs/PHOTOS_EXPLAINED.md` §B.

Design notes:
- Sources (`raw_crew`, `eol`, `flickr`, `nasa_images`, `ia_stills`) are a
  closed set named in `SOURCES`. Each appears as a key under `copies` with
  either a `Copy` payload or `None` when we don't hold that source.
- `exported_in` is a list (not a set) so JSONL serialization stays simple;
  builders should treat it as set-shaped.
- Fields are dataclasses so the builder can construct, mutate, then dump in
  one pass without round-tripping through JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# Closed set of source identifiers — one per intake pipeline.
SOURCES: tuple[str, ...] = (
    "raw_crew",     # NEF + DNG from the crew imagery hand-off
    "eol",          # Large JPEGs from eol.jsc.nasa.gov
    "ia_stills",    # JPEGs from Internet Archive bulk uploads
    "flickr",       # Originals from Flickr (url_o)
    "nasa_images",  # Originals from images.nasa.gov (~orig)
    "manual",       # Manually added photos from outside the pipeline
)


@dataclass
class Copy:
    """One physical file we hold for a NASA ID, plus its EXIF."""
    path: str                    # absolute filesystem path
    exif: dict = field(default_factory=dict)


@dataclass
class Bracket:
    """Bracket-set membership for a single NASA ID frame.

    `set_id` is the NASA ID of the metered (0 EV) hero frame. Every member
    of a set carries the same `set_id` and `members` list; only the hero
    has `is_hero == True`.
    """
    set_id: str
    members: list[str]
    position: str                # "darker" | "metered" | "lighter"
    ev: float
    is_hero: bool
    detection_source: str = "ev_heuristic"   # "makernote" | "ev_heuristic"


@dataclass
class LedgerRecord:
    nasa_id: str
    title: str = ""
    description: str = ""

    # Best-known UTC timestamp + which source it came from.
    # utc_source ∈ {"exif_offset", "io_nhq", "io_corrected", "io_onboard",
    #               "flickr", "nasa_images", "eol", ""}
    utc: str = ""
    utc_source: str = ""

    # Export status — exported iff seen in at least one public archive.
    exported: bool = False
    exported_in: list[str] = field(default_factory=list)

    # Per-source file holdings. Keys are SOURCES; values are Copy or None.
    copies: dict[str, Optional[Copy]] = field(
        default_factory=lambda: {s: None for s in SOURCES}
    )

    # Bracket-set membership; None when this is a singleton shot.
    bracket: Optional[Bracket] = None

    # Verbatim IO record when IO has it. Useful for debugging the merge;
    # the web build doesn't read it.
    io: dict = field(default_factory=dict)

    # ── helpers ──────────────────────────────────────────────────────────

    def mark_exported_in(self, source: str) -> None:
        """Idempotently record that this NASA ID was found in `source`."""
        if source not in self.exported_in:
            self.exported_in.append(source)
        self.exported = True

    def has_any_copy(self) -> bool:
        return any(c is not None for c in self.copies.values())

    def to_jsonable(self) -> dict:
        """Convert to a plain dict for json.dump — None copies stay None."""
        d = asdict(self)
        # asdict turns Copy into a dict; nothing else to do.
        return d


# ── I/O ──────────────────────────────────────────────────────────────────


def _record_from_jsonable(d: dict) -> LedgerRecord:
    """Inverse of to_jsonable — rebuild dataclasses from a plain dict."""
    copies_raw = d.get("copies") or {}
    copies: dict[str, Optional[Copy]] = {}
    for src in SOURCES:
        c = copies_raw.get(src)
        copies[src] = Copy(**c) if isinstance(c, dict) else None

    bracket_raw = d.get("bracket")
    bracket = Bracket(**bracket_raw) if isinstance(bracket_raw, dict) else None

    return LedgerRecord(
        nasa_id=d["nasa_id"],
        title=d.get("title", ""),
        description=d.get("description", ""),
        utc=d.get("utc", ""),
        utc_source=d.get("utc_source", ""),
        exported=bool(d.get("exported", False)),
        exported_in=list(d.get("exported_in", [])),
        copies=copies,
        bracket=bracket,
        io=d.get("io", {}) or {},
    )


def load_ledger(path: Path) -> dict[str, LedgerRecord]:
    """Load the ledger JSONL into a {nasa_id: LedgerRecord} dict.

    Returns an empty dict if the file doesn't exist yet — callers can build
    from scratch on first run.
    """
    out: dict[str, LedgerRecord] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = _record_from_jsonable(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            out[rec.nasa_id] = rec
    return out


def save_ledger(path: Path, records: Iterable[LedgerRecord]) -> int:
    """Write the ledger JSONL, sorted by nasa_id. Returns count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_records = sorted(records, key=lambda r: r.nasa_id)
    with open(path, "w", encoding="utf-8") as f:
        for rec in sorted_records:
            f.write(json.dumps(rec.to_jsonable(), ensure_ascii=False) + "\n")
    return len(sorted_records)
