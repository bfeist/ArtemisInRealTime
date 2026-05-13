"""Step 4c — Build the canonical per-NASA-ID photo ledger.

Reads every intake source plus the per-copy EXIF (4a) and bracket sets (4b),
then emits one merged record per NASA ID at `mission.photos_ledger_path`.

The ledger is the single source of truth that step 4d (tier generation) and
4e (web JSON) consume. Replaces the merge logic in 3k_web_photos.py and adds
per-copy EXIF + bracket awareness + an explicit `exported` flag.

See `docs/PHOTOS_EXPLAINED.md` §B for the record schema.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig, CREW_RAW_SOURCE_DIR
from shared.eol_naming import to_canonical_nasa_id
from shared.io_api import load_jsonl
from shared.photos_ledger import (
    Bracket,
    Copy,
    LedgerRecord,
    SOURCES,
    save_ledger,
)

console = Console()

# ── Filename → NASA ID helpers ───────────────────────────────────────────────

# Same regex used by 3f_download_photos.py for matching NASA IDs in free text.
_NASA_ID_IN_TEXT_RE = re.compile(
    r"(art\d+[me]\d+|jsc\d+[me]\d+|nhq\d+|iss\d+[a-z]\d+)",
    re.IGNORECASE,
)


def _flickr_to_nasa_id(photo: dict) -> str | None:
    """Same heuristic 3f used to name the downloaded file — extract a NASA ID
    from the title or description."""
    title = (photo.get("title") or "").strip()
    desc_field = photo.get("description") or {}
    if isinstance(desc_field, dict):
        desc = desc_field.get("_content", "")
    else:
        desc = str(desc_field)
    m = _NASA_ID_IN_TEXT_RE.search((title + " " + desc).lower())
    return m.group(1).lower() if m else None


# ── Date / TZ helpers ────────────────────────────────────────────────────────


def _parse_tz_offset(s: str) -> timedelta | None:
    """Parse '-05:00:00' or '-05:00' → timedelta. Returns None on bad input."""
    m = re.match(r"^([+-])(\d{2}):(\d{2})(?::\d{2})?$", s)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    return timedelta(hours=int(m.group(2)), minutes=int(m.group(3))) * sign


def _format_utc(dt: datetime) -> str:
    """Format a UTC datetime as ISO-8601 with millisecond precision when the
    fractional part is non-zero, otherwise second precision."""
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _correct_local_as_utc(date_str: str, offset_str: str) -> str | None:
    """IO stores camera local time as UTC for ground photographers — given the
    photographer's offset, recover true UTC. Preserves millisecond precision
    when the input carries it."""
    offset = _parse_tz_offset(offset_str)
    if offset is None:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return _format_utc((dt - offset).replace(tzinfo=None))


# Strip a trailing "[+-]HH:MM" suffix from a time string and return both parts.
# Examples: "13:42:57.19-04:00" → ("13:42:57.19", "-04:00")
#           "13:42:57"           → ("13:42:57", None)
_TZ_SUFFIX_RE = re.compile(r"([+-]\d{2}:\d{2})$")


def _split_tz_suffix(s: str) -> tuple[str, str | None]:
    if not s:
        return s, None
    m = _TZ_SUFFIX_RE.search(s)
    if m:
        return s[: m.start()], m.group(1)
    return s, None


def _io_local_to_utc(
    date_part: str,
    time_part: str,
    offset_str: str,
) -> str | None:
    """Combine a 'YYYY:MM:DD' date, a 'HH:MM:SS[.fff]' time, and a '±HH:MM'
    offset into a UTC ISO timestamp."""
    offset = _parse_tz_offset(offset_str)
    if offset is None:
        return None
    fmt = "%Y:%m:%d %H:%M:%S.%f" if "." in time_part else "%Y:%m:%d %H:%M:%S"
    try:
        local = datetime.strptime(f"{date_part} {time_part}", fmt)
    except (ValueError, TypeError):
        return None
    return _format_utc(local - offset)


def _exif_copy_to_utc(exif: dict) -> str | None:
    """Derive true UTC from a per-copy EXIF payload (4a output).

    Rules:
      - Prefer `Composite:SubSecDateTimeOriginal` (NEF only — exiftool builds
        this with fractional seconds) over plain `DateTimeOriginal`, so
        bracket-burst frames that share the same wall-clock second order
        correctly.
      - Offset: try `OffsetTimeOriginal` (spec), then `OffsetTime`, then
        `OffsetTimeDigitized`. Lightroom export pipelines (which most NASA
        JPEGs go through) routinely strip the original-offset tag while
        keeping `OffsetTime` — for stills the photographer doesn't change
        zones between shutter and save, so they're interchangeable.
      - Subsec: PIL emits `SubsecTimeOriginal` (lowercase 'sec', per EXIF
        spec); ExifTool emits `SubSecTimeOriginal`. Accept both.
    """
    if not exif:
        return None
    composite_dto = (exif.get("Composite", {}) or {}).get("SubSecDateTimeOriginal")
    plain_dto = exif.get("DateTimeOriginal") or exif.get("DateTimeDigitized")
    subsec = (
        exif.get("SubsecTimeOriginal")
        or exif.get("SubSecTimeOriginal")
        or (exif.get("EXIF", {}) or {}).get("SubSecTimeOriginal")
    )
    offset = (
        exif.get("OffsetTimeOriginal")
        or exif.get("OffsetTime")
        or exif.get("OffsetTimeDigitized")
    )
    if not offset:
        return None

    def _parse_offset(s: str) -> timezone | None:
        try:
            sign = 1 if s.startswith("+") else -1
            h, m = int(s[1:3]), int(s[4:6])
            return timezone(timedelta(hours=h * sign, minutes=m * sign))
        except (ValueError, IndexError):
            return None

    tz = _parse_offset(offset)
    if tz is None:
        return None

    # Try the sub-second composite form first.
    for dto, has_subsec_already in ((composite_dto, True), (plain_dto, False)):
        if not dto:
            continue
        try:
            if "." in dto:
                local = datetime.strptime(dto, "%Y:%m:%d %H:%M:%S.%f")
            else:
                local = datetime.strptime(dto, "%Y:%m:%d %H:%M:%S")
                if not has_subsec_already and subsec is not None:
                    s = str(subsec).strip()
                    if s.isdigit():
                        local = local.replace(microsecond=int(s.ljust(6, "0")[:6]))
        except ValueError:
            continue
        utc_dt = local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        return _format_utc(utc_dt)

    return None


def _io_exif_to_utc(io_exif: dict, fallback_offset: str | None) -> str | None:
    """Derive true UTC from the IO scraped per-photo EXIF (3a3 output).

    Tries (highest precision first):
      1. `DateCreated`           e.g. '2026:03:30 13:42:57.19-04:00'
      2. `DigitalCreationDate` + `DigitalCreationTime`
                                  e.g. '2026:03:30' + '13:42:57-04:00'
      3. `DateTimeOriginal` + a known offset (from same record's tz_offset
                                              or the prefix-default override)

    Deliberately ignores **IO's** `GMT` field and a bare `DateTimeOriginal`
    with no offset. IO's `GMT` column is often filled with the camera's
    local-clock time mislabelled as GMT — the whole reason
    `photo-time-overrides.json` exists. Without an explicit offset on the
    timestamp itself we can't tell true UTC apart from mislabelled local.

    Note: this caveat is specific to the IO-scraped EXIF (this function's
    sole input). A `GMT` tag found inside a real on-disk EXIF payload (e.g.
    a GoPro makernote read by 4a) is unrelated and should be trusted on its
    own merits there if/when 4a starts reading it.
    """
    if not io_exif:
        return None

    dc = io_exif.get("DateCreated")
    if dc and " " in dc:
        date_part, rest = dc.split(" ", 1)
        time_part, off = _split_tz_suffix(rest)
        if off:
            utc = _io_local_to_utc(date_part, time_part, off)
            if utc:
                return utc

    dcd = io_exif.get("DigitalCreationDate")
    dct = io_exif.get("DigitalCreationTime")
    if dcd and dct:
        time_part, off = _split_tz_suffix(dct)
        if off:
            utc = _io_local_to_utc(dcd, time_part, off)
            if utc:
                return utc

    dto = io_exif.get("DateTimeOriginal")
    if dto and " " in dto and fallback_offset:
        date_part, time_part = dto.split(" ", 1)
        utc = _io_local_to_utc(date_part, time_part, fallback_offset)
        if utc:
            return utc

    return None


def _flickr_datetaken_to_utc(datetaken: str, offset_str: str | None) -> str | None:
    """Flickr `datetaken` is camera local time. Apply the same TZ correction
    when we have an offset for the photographer."""
    if not datetaken:
        return None
    if not offset_str:
        # No offset → return Flickr's value as-is, day-precision tolerated.
        # Best-effort ISO normalization.
        try:
            dt = datetime.strptime(datetaken, "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return datetaken
    offset = _parse_tz_offset(offset_str)
    if offset is None:
        return None
    try:
        dt = datetime.strptime(datetaken, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (dt - offset).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Loaders ─────────────────────────────────────────────────────────────────


def _load_exif_for_source(exif_dir: Path, source: str) -> dict[str, dict]:
    """Load every {nasa_id: payload} pair for one source's EXIF cache."""
    src_dir = exif_dir / source
    if not src_dir.exists():
        return {}
    out: dict[str, dict] = {}
    for jf in src_dir.iterdir():
        if jf.suffix != ".json":
            continue
        try:
            with open(jf, "r", encoding="utf-8") as f:
                out[jf.stem.lower()] = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _scan_dir_to_nasa_id(directory: Path, accept_exts: set[str]) -> dict[str, Path]:
    """List a flat directory and return {nasa_id: path} for every recognised file.
    NASA ID = file stem, lowercased, with any '~orig' suffix stripped."""
    out: dict[str, Path] = {}
    if not directory.exists():
        return out
    for f in directory.iterdir():
        if not f.is_file() or f.suffix.lower() not in accept_exts:
            continue
        stem = f.stem.lower()
        if "~" in stem:
            stem = stem.split("~", 1)[0]
        out[stem] = f
    return out


def _scan_eol_dir(directory: Path) -> dict[str, Path]:
    """Map every EOL JPEG to its canonical NASA ID. Handles both the
    canonical filenames step 3h writes (`art002e000168.jpg`) and any legacy
    on-the-wire filenames left over from before the rename migration
    (`ART002-E-168.JPG`)."""
    out: dict[str, Path] = {}
    if not directory.exists():
        return out
    for f in directory.iterdir():
        if not f.is_file():
            continue
        nid = to_canonical_nasa_id(f.name)
        if nid:
            out[nid] = f
    return out


# ── Builder ─────────────────────────────────────────────────────────────────


def _seed_from_io(
    records: dict[str, LedgerRecord],
    io_path: Path,
) -> int:
    """1. Seed ledger rows from the IO catalog. exported defaults to False."""
    if not io_path.exists():
        console.print(f"  [yellow]No IO catalog at {io_path}[/yellow]")
        return 0
    n = 0
    for doc in load_jsonl(io_path):
        nid = (doc.get("nasa_id") or "").lower()
        if not nid:
            continue
        rec = records.setdefault(nid, LedgerRecord(nasa_id=nid))
        rec.title = doc.get("md_title", "") or rec.title
        rec.description = (doc.get("description") or "")[:500] or rec.description
        rec.io = doc
        n += 1
    return n


def _add_eol(
    records: dict[str, LedgerRecord],
    eol_json_path: Path,
    eol_dir: Path,
) -> tuple[int, int]:
    """2a. Mark exported via EOL.

    Two signals, either is sufficient:
      - eol_photos.json (from step 3g) lists the NASA ID → exported
      - the JPEG is already on disk (from step 3h) → exported
    The on-disk signal is what makes the ledger correct even when 3g hasn't
    been re-run since the JPEGs landed.
    """
    eol_files = _scan_eol_dir(eol_dir)

    seen: set[str] = set()
    marked = with_file = 0

    # Pass 1: items listed in eol_photos.json (when present)
    if eol_json_path.exists():
        with open(eol_json_path, "r", encoding="utf-8") as f:
            eol_items = json.load(f)
        for item in eol_items:
            nasa_id = (item.get("ID") or "").lower()
            if not nasa_id:
                continue
            rec = records.setdefault(nasa_id, LedgerRecord(nasa_id=nasa_id))
            rec.mark_exported_in("eol")
            seen.add(nasa_id)
            marked += 1
            path = eol_files.get(nasa_id)
            if path:
                rec.copies["eol"] = Copy(path=str(path))
                with_file += 1
            if item.get("dateTaken"):
                rec.io.setdefault("_eol_dateTaken", item["dateTaken"])

    # Pass 2: JPEGs on disk that the JSON didn't already cover. File presence
    # alone proves the photo was released to EOL.
    for nasa_id, path in eol_files.items():
        if nasa_id in seen:
            continue
        rec = records.setdefault(nasa_id, LedgerRecord(nasa_id=nasa_id))
        rec.mark_exported_in("eol")
        rec.copies["eol"] = Copy(path=str(path))
        marked += 1
        with_file += 1

    return marked, with_file


def _add_nasa_images(
    records: dict[str, LedgerRecord],
    catalog_path: Path,
    file_dir: Path,
) -> tuple[int, int]:
    """2b. images.nasa.gov."""
    if not catalog_path.exists():
        return 0, 0
    with open(catalog_path, "r", encoding="utf-8") as f:
        items = json.load(f)
    files = _scan_dir_to_nasa_id(file_dir, {".jpg", ".jpeg", ".png", ".tif", ".tiff"})
    marked = with_file = 0
    for item in items:
        nasa_id = (item.get("nasa_id") or "").lower()
        if not nasa_id:
            continue
        rec = records.setdefault(nasa_id, LedgerRecord(nasa_id=nasa_id))
        rec.mark_exported_in("nasa_images")
        marked += 1
        if not rec.title:
            rec.title = item.get("title", "")
        if not rec.description:
            rec.description = (item.get("description") or "")[:500]
        # Stash a candidate date — the date chain will pick it up.
        rec.io.setdefault("_nasa_images_date", item.get("date_taken") or item.get("date_created"))
        path = files.get(nasa_id)
        if path:
            rec.copies["nasa_images"] = Copy(path=str(path))
            with_file += 1
    return marked, with_file


def _add_flickr(
    records: dict[str, LedgerRecord],
    album_meta_path: Path,
    file_dir: Path,
) -> tuple[int, int, int]:
    """2c. Flickr — match by NASA ID extracted from title/description."""
    if not album_meta_path.exists():
        return 0, 0, 0
    with open(album_meta_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    photos = data if isinstance(data, list) else data.get("photos", [])
    files = _scan_dir_to_nasa_id(
        file_dir, {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    )
    marked = with_file = unmatched = 0
    for photo in photos:
        nasa_id = _flickr_to_nasa_id(photo)
        if not nasa_id:
            unmatched += 1
            continue
        rec = records.setdefault(nasa_id, LedgerRecord(nasa_id=nasa_id))
        rec.mark_exported_in("flickr")
        marked += 1
        # Stash datetaken for the date chain.
        if photo.get("datetaken"):
            rec.io.setdefault("_flickr_datetaken", photo["datetaken"])
        path = files.get(nasa_id)
        if path:
            rec.copies["flickr"] = Copy(path=str(path))
            with_file += 1
    return marked, with_file, unmatched


def _add_ia_stills(
    records: dict[str, LedgerRecord],
    file_dir: Path,
) -> tuple[int, int]:
    """2d. IA still imagery — filename IS the NASA ID."""
    files = _scan_dir_to_nasa_id(
        file_dir, {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    )
    marked = with_file = 0
    for nasa_id, path in files.items():
        rec = records.setdefault(nasa_id, LedgerRecord(nasa_id=nasa_id))
        rec.mark_exported_in("ia_stills")
        marked += 1
        rec.copies["ia_stills"] = Copy(path=str(path))
        with_file += 1
    return marked, with_file


def _add_raw_crew(
    records: dict[str, LedgerRecord],
    primary_dir: Path,
    fallback_dir: Path,
) -> int:
    """3. Crew raws — file presence does NOT imply exported. We just attach
    the path; the export flag stays whatever the public sources set it to."""
    src_dir = primary_dir
    if not src_dir.exists() or not any(src_dir.iterdir()):
        if fallback_dir.exists():
            src_dir = fallback_dir
    if not src_dir.exists():
        return 0
    n = 0
    # Walk recursively — the legacy D: dump has subfolders.
    for f in src_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".nef", ".dng"):
            nid = f.stem.lower()
            rec = records.setdefault(nid, LedgerRecord(nasa_id=nid))
            rec.copies["raw_crew"] = Copy(path=str(f))
            n += 1
    return n


def _attach_exif(
    records: dict[str, LedgerRecord],
    exif_dir: Path,
) -> int:
    """4. Embed each per-source EXIF JSON under copies[source].exif."""
    n = 0
    for source in SOURCES:
        for nid, payload in _load_exif_for_source(exif_dir, source).items():
            rec = records.get(nid)
            if rec is None:
                continue
            copy = rec.copies.get(source)
            if copy is None:
                # We have EXIF but no recorded copy path — rare race; skip.
                continue
            copy.exif = payload
            n += 1
    return n


def _resolve_date(
    rec: LedgerRecord,
    nhq_dates: dict[str, str],
    tz_overrides: dict[str, str],
    onboard_overrides: dict[str, str],
    io_exif_records: dict[str, dict],
) -> None:
    """5. Apply the date-priority chain. Sets `utc` and `utc_source` in place.

    Priority (highest first):
      1. exif_offset    — UTC derived from each copy's DateTimeOriginal +
                          OffsetTime* (computed on the fly — see
                          _exif_copy_to_utc)
      2. io_nhq         — second-precision date from io_nhq_photos_found.jsonl
      3. io_exif        — IO-scraped per-photo EXIF (DateCreated /
                          DigitalCreationTime / DateTimeOriginal+offset).
                          Often sub-second. Skips bare GMT — see _io_exif_to_utc.
      4. io_corrected   — IO md_creation_date with TZ correction applied
      5. io_onboard     — onboard-camera UTC from photo-datetime-overrides.json,
                          or IO md_creation_date for art002e/a prefixes
      6. flickr         — Flickr datetaken (TZ-corrected if we have an offset)
      7. nasa_images    — date_created / date_taken from images.nasa.gov
      8. eol            — dateTaken from EOL JSON
    """
    # 1. EXIF offset — derive UTC on the fly from each copy's EXIF.
    # raw_crew first because the NEF carries Composite:SubSecDateTimeOriginal
    # (sub-second) and Nikon's MakerNotes:TimeZone alias.
    for source in ("raw_crew", "eol", "flickr", "nasa_images", "ia_stills"):
        copy = rec.copies.get(source)
        if not copy or not copy.exif:
            continue
        utc = _exif_copy_to_utc(copy.exif)
        if utc:
            rec.utc = utc
            rec.utc_source = "exif_offset"
            return

    # 2. IO NHQ second-precision lookup
    nhq = nhq_dates.get(rec.nasa_id)
    if nhq:
        rec.utc = nhq
        rec.utc_source = "io_nhq"
        return

    # 3. IO scraped EXIF — DateCreated / DigitalCreationTime carry their own
    # offset suffix; DateTimeOriginal needs an explicit offset (from the same
    # record's tz_offset suffix, or the prefix-default tz_overrides map).
    io_entry = io_exif_records.get(rec.nasa_id)
    if io_entry:
        fallback_offset = io_entry.get("tz_offset") or tz_overrides.get(rec.nasa_id)
        utc = _io_exif_to_utc(io_entry.get("exif") or {}, fallback_offset)
        if utc:
            rec.utc = utc
            rec.utc_source = "io_exif"
            return

    md_date = (rec.io or {}).get("md_creation_date", "")
    nasa_id = rec.nasa_id

    # 4. IO ground-photographer with TZ override
    offset = tz_overrides.get(nasa_id)
    if md_date and offset:
        corrected = _correct_local_as_utc(md_date, offset)
        if corrected:
            rec.utc = corrected
            rec.utc_source = "io_corrected"
            return

    # 4. Onboard camera datetime
    onboard = onboard_overrides.get(nasa_id)
    if onboard:
        rec.utc = onboard
        rec.utc_source = "io_onboard"
        return
    # IO md_creation_date for onboard art002e/a is already UTC
    if md_date and (nasa_id.startswith("art") and (
            "e" in nasa_id[3:7] or "a" in nasa_id[3:7])):
        rec.utc = md_date
        rec.utc_source = "io_onboard"
        return

    # 5. Flickr datetaken
    flickr_dt = (rec.io or {}).get("_flickr_datetaken")
    if flickr_dt:
        utc = _flickr_datetaken_to_utc(flickr_dt, offset)
        if utc:
            rec.utc = utc
            rec.utc_source = "flickr"
            return

    # 6. images.nasa.gov
    nasa_date = (rec.io or {}).get("_nasa_images_date")
    if nasa_date:
        rec.utc = nasa_date
        rec.utc_source = "nasa_images"
        return

    # 7. EOL fallback
    eol_dt = (rec.io or {}).get("_eol_dateTaken")
    if eol_dt:
        rec.utc = eol_dt
        rec.utc_source = "eol"
        return


def _attach_brackets(
    records: dict[str, LedgerRecord],
    bracket_path: Path,
) -> int:
    """6. Join bracket sets onto every member record."""
    if not bracket_path.exists():
        return 0
    n = 0
    for set_rec in load_jsonl(bracket_path):
        members: list[str] = set_rec.get("members", [])
        evs: list[float] = set_rec.get("evs", [])
        hero: str = set_rec.get("hero", "")
        detection_source: str = set_rec.get("detection_source", "ev_heuristic")
        for nid, ev in zip(members, evs):
            rec = records.get(nid)
            if rec is None:
                continue
            position = (
                "metered" if ev == 0.0
                else "darker" if ev < 0.0
                else "lighter"
            )
            rec.bracket = Bracket(
                set_id=set_rec["set_id"],
                members=members,
                position=position,
                ev=float(ev),
                is_hero=(nid == hero),
                detection_source=detection_source,
            )
            n += 1
    return n


def _strip_internal_io_keys(records: Iterable[LedgerRecord]) -> None:
    """Drop the `_*_date(taken)` candidates we stashed in `io` during merging."""
    for rec in records:
        for k in list(rec.io.keys()):
            if k.startswith("_"):
                del rec.io[k]


# ── Orchestration ───────────────────────────────────────────────────────────


def build_ledger(mission: MissionConfig) -> None:
    records: dict[str, LedgerRecord] = {}

    # 1. IO catalog seed
    seeded = _seed_from_io(records, mission.io_cache / "io_photo_catalog.jsonl")
    console.print(f"  IO seed:           [cyan]{seeded:>6}[/cyan] records")

    # 2. Public-source intake
    eol_marked, eol_files = _add_eol(
        records, mission.eol_json_path, mission.photos_eol
    )
    console.print(
        f"  EOL:               [cyan]{eol_marked:>6}[/cyan] exported "
        f"([dim]{eol_files} with file on disk[/dim])"
    )

    nasa_marked, nasa_files = _add_nasa_images(
        records, mission.raw_photos_nasa / "catalog.json", mission.photos_nasa_orig
    )
    console.print(
        f"  images.nasa.gov:   [cyan]{nasa_marked:>6}[/cyan] exported "
        f"([dim]{nasa_files} with file on disk[/dim])"
    )

    fl_marked, fl_files, fl_unmatched = _add_flickr(
        records, mission.raw_photos_flickr / "album_metadata.json", mission.photos_flickr_orig
    )
    console.print(
        f"  Flickr:            [cyan]{fl_marked:>6}[/cyan] exported (matched), "
        f"[dim]{fl_files} with file, {fl_unmatched} unmatched[/dim]"
    )

    ia_marked, ia_files = _add_ia_stills(records, mission.photos_ia_stills)
    console.print(
        f"  IA stills:         [cyan]{ia_marked:>6}[/cyan] exported "
        f"([dim]{ia_files} files[/dim])"
    )

    # 3. Crew raws — files only, no export effect
    raw_count = _add_raw_crew(records, mission.photos_raw_crew, CREW_RAW_SOURCE_DIR)
    console.print(f"  raw_crew files:    [cyan]{raw_count:>6}[/cyan]")

    # 4. Per-copy EXIF
    exif_attached = _attach_exif(records, mission.exif_cache_dir)
    console.print(f"  EXIF attached:     [cyan]{exif_attached:>6}[/cyan] payloads")

    # 5. Date resolution
    nhq_dates: dict[str, str] = {}
    nhq_path = mission.io_cache / "io_nhq_photos_found.jsonl"
    if nhq_path.exists():
        for rec in load_jsonl(nhq_path):
            nid = (rec.get("nasa_id") or "").lower()
            d = rec.get("io_date", "")
            if nid and d:
                nhq_dates[nid] = d

    tz_overrides: dict[str, str] = {}
    tz_path = mission.io_cache / "photo-time-overrides.json"
    if tz_path.exists():
        with open(tz_path, "r", encoding="utf-8") as f:
            tz_overrides = {k.lower(): v for k, v in json.load(f).items()}

    onboard_overrides: dict[str, str] = {}
    onboard_path = mission.io_cache / "photo-datetime-overrides.json"
    if onboard_path.exists():
        with open(onboard_path, "r", encoding="utf-8") as f:
            onboard_overrides = {k.lower(): v for k, v in json.load(f).items()}

    io_exif_records: dict[str, dict] = {}
    io_exif_path = mission.io_cache / "io_photo_exif.jsonl"
    if io_exif_path.exists():
        for entry in load_jsonl(io_exif_path):
            nid = (entry.get("nasa_id") or "").lower()
            if nid:
                io_exif_records[nid] = entry

    for rec in records.values():
        _resolve_date(
            rec, nhq_dates, tz_overrides, onboard_overrides, io_exif_records
        )

    # 6. Bracket sets
    bracket_attached = _attach_brackets(records, mission.bracket_sets_path)
    console.print(f"  Bracket members:   [cyan]{bracket_attached:>6}[/cyan] frames")

    # 7. Cleanup + write
    _strip_internal_io_keys(records.values())
    n = save_ledger(mission.photos_ledger_path, records.values())
    console.print(f"\n  [green]Wrote {n:,} records -> {mission.photos_ledger_path}[/green]")

    # ── Summary breakdown ────────────────────────────────────────────────
    by_utc_source: dict[str, int] = {}
    exported = 0
    with_any_copy = 0
    for rec in records.values():
        by_utc_source[rec.utc_source or "(none)"] = (
            by_utc_source.get(rec.utc_source or "(none)", 0) + 1
        )
        if rec.exported:
            exported += 1
        if rec.has_any_copy():
            with_any_copy += 1

    table = Table(title="Ledger summary", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Total records",        f"{n:,}")
    table.add_row("Exported (any source)",f"{exported:,}")
    table.add_row("Has at least one copy",f"{with_any_copy:,}")
    console.print(table)

    src_table = Table(title="UTC source distribution", show_lines=False)
    src_table.add_column("utc_source", style="bold")
    src_table.add_column("Count", justify="right")
    for src, cnt in sorted(by_utc_source.items(), key=lambda kv: -kv[1]):
        src_table.add_row(src, f"{cnt:,}")
    console.print(src_table)


def main():
    parser = argparse.ArgumentParser(description="Build the canonical photo ledger")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    console.print(f"\n[bold]=== Step 4c: Build photo ledger — {mission.name} ===[/bold]\n")
    build_ledger(mission)


if __name__ == "__main__":
    main()
