"""Step 4a — Per-copy EXIF extraction.

Walks every local copy of every NASA ID we hold across all sources and writes
one EXIF JSON per (source, nasa_id) pair under
`mission.exif_cache_dir/{source}/{nasa_id}.json`.

The ledger build (step 4c) embeds these blocks directly under
`copies[source].exif`, so the keys here become the EXIF payload the frontend
ultimately reads. Bracket detection (step 4b) keys off of `ExposureBiasValue`
and `BracketShotNumber` from this output. UTC derivation (DateTimeOriginal +
offset → true UTC) is **not** done here — that lives in 4c, so the rule can
change without forcing a full per-copy re-extract.

Tooling:
- raw_crew (NEF + DNG) → ExifTool (via pyexiftool). PIL doesn't reliably read
  Nikon makernote bracket tags or DNG raws.
- JPEG copies (eol/flickr/nasa_images/ia_stills) → PIL._getexif. Fast, no
  subprocess. The standard EXIF tags are sufficient — JPEG re-encodes
  typically don't carry makernotes anyway.

Idempotent: skip files whose EXIF JSON already exists AND whose source mtime
is older than the JSON (re-encoded source files trigger re-extraction).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.eol_naming import to_canonical_nasa_id

console = Console()

# Image extensions per source. raw_crew is handled separately via ExifTool.
_RAW_EXTS = {".nef", ".dng"}
_JPEG_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}


# ── EXIF normalization ───────────────────────────────────────────────────────


def _coerce(value):
    """Normalize EXIF values for JSON. PIL returns IFDRational, bytes, etc."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("ascii", errors="ignore").strip("\x00").strip()
        except Exception:
            return None
    # PIL IFDRational has __float__
    try:
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            if value.denominator == 0:
                return None
            return float(value)
    except Exception:
        pass
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    return value


# ── Source-specific extractors ──────────────────────────────────────────────


def _extract_jpeg_exif(path: Path) -> dict | None:
    """PIL-based extraction for JPEG-family files. Captures *every* EXIF tag
    PIL exposes (not a fixed whitelist) so the ledger has full provenance.
    Returns None on failure."""
    try:
        img = Image.open(path)
        raw = img._getexif()
    except (UnidentifiedImageError, Exception):
        return None
    if not raw:
        return {}
    payload: dict = {}
    for tag_id, value in raw.items():
        name = TAGS.get(tag_id, f"Tag{tag_id:#06x}")
        # GPSInfo is a sub-IFD dict — translate inner keys too.
        if name == "GPSInfo" and isinstance(value, dict):
            payload[name] = {
                GPSTAGS.get(k, f"GPSTag{k}"): _coerce(v) for k, v in value.items()
            }
            continue
        v = _coerce(value)
        if v is None or v == "":
            continue
        payload[name] = v
    return payload


def _normalize_exiftool_payload(raw: dict) -> dict:
    """Group exiftool's flat 'Group:Tag' keys into nested dicts per group, so
    a NEF payload looks like {"EXIF": {...}, "MakerNotes": {...}, "XMP": {...}}.

    Adds a top-level alias for a few load-bearing fields so the rest of the
    pipeline can read a uniform schema regardless of source:
      - DateTimeOriginal  ← EXIF:DateTimeOriginal
      - OffsetTimeOriginal ← EXIF:OffsetTimeOriginal OR MakerNotes:TimeZone
      - ExposureBiasValue / ExposureCompensation
    UTC derivation happens in 4c, not here.
    """
    nested: dict[str, dict] = {}
    flat_pairs: list[tuple[str, object]] = []   # for ungrouped keys (e.g. SourceFile)

    for full_key, value in raw.items():
        v = _coerce(value)
        if v is None or v == "":
            continue
        if ":" in full_key:
            group, tag = full_key.split(":", 1)
            nested.setdefault(group, {})[tag] = v
        else:
            flat_pairs.append((full_key, v))

    out: dict = dict(nested)
    for k, v in flat_pairs:
        out[k] = v

    # ── Cross-source field aliases at the top level ─────────────────────
    exif = nested.get("EXIF", {})
    maker = nested.get("MakerNotes", {})

    def _first(*candidates):
        for c in candidates:
            if c not in (None, ""):
                return c
        return None

    dto = _first(exif.get("DateTimeOriginal"), exif.get("CreateDate"))
    if dto is not None:
        out["DateTimeOriginal"] = dto

    # OffsetTimeOriginal — EXIF first, MakerNotes:TimeZone fallback (Nikon
    # writes the camera's UTC offset there instead of into the EXIF tag).
    oto = _first(exif.get("OffsetTimeOriginal"), maker.get("TimeZone"))
    if oto is not None:
        out["OffsetTimeOriginal"] = oto

    ebv = _first(
        exif.get("ExposureBiasValue"),
        exif.get("ExposureCompensation"),
        maker.get("ExposureBracketValue"),
    )
    if ebv is not None:
        out["ExposureBiasValue"] = ebv

    return out


def _batch_extract_raw_crew(
    work: list[tuple[Path, Path]],
    progress=None,
    task=None,
) -> bool:
    """Stream ExifTool batches, writing per-file JSON as soon as each chunk
    returns. Avoids buffering 10K payloads in memory and gives us partial
    progress on disk if the process is interrupted.

    Returns True on success, False if ExifTool is unavailable.
    """
    if not work:
        return True
    try:
        import exiftool  # pyexiftool
    except ImportError:
        console.print(
            "[red]pyexiftool not installed — install with `uv add pyexiftool` "
            "and ensure exiftool.exe is on PATH.[/red]"
        )
        return False

    CHUNK = 100
    try:
        # Default common_args is ['-G', '-n'] — `-n` strips PrintConv so
        # `ShootingMode` comes back as "17" instead of "Continuous, Exposure
        # Bracketing". Override with just `-G` so we get human-readable strings.
        with exiftool.ExifToolHelper(common_args=["-G"]) as et:
            for i in range(0, len(work), CHUNK):
                chunk = work[i : i + CHUNK]
                src_paths = [str(src) for src, _ in chunk]
                try:
                    metas = et.execute_json(*src_paths)
                except exiftool.exceptions.ExifToolExecuteError as e:
                    console.print(f"[yellow]ExifTool batch error at {i}: {e}[/yellow]")
                    if progress and task is not None:
                        progress.update(task, advance=len(chunk))
                    continue
                for (src_path, dest_path), raw in zip(chunk, metas or []):
                    payload = _normalize_exiftool_payload(raw or {})
                    _write_payload(dest_path, payload)
                    if progress and task is not None:
                        progress.advance(task)
    except FileNotFoundError:
        console.print(
            "[red]exiftool binary not found on PATH. Install ExifTool and try again.[/red]"
        )
        return False
    return True


# ── File walking + I/O ───────────────────────────────────────────────────────


def _nasa_id_from_filename(path: Path) -> str:
    """Map an on-disk filename to its canonical NASA ID.

    Handles three patterns:
      - `art002e000168.NEF` / `art002e000168.jpg`        (already canonical)
      - `art002e000168~orig.jpg`                          (3f's NASA originals)
      - `ART002-E-168.JPG`                                (legacy EOL — rare
        once 3i has run; covered as defense-in-depth)
    """
    # EOL hyphenated → canonical
    canonical = to_canonical_nasa_id(path.name)
    if canonical and "-" in path.name:
        return canonical
    stem = path.stem.lower()
    if "~" in stem:
        stem = stem.split("~", 1)[0]
    return stem


def _needs_extraction(src: Path, dest: Path) -> bool:
    """Skip when EXIF JSON exists and is newer than the source file."""
    if not dest.exists():
        return True
    try:
        return src.stat().st_mtime > dest.stat().st_mtime
    except OSError:
        return True


def _scan_source(
    source_name: str,
    source_dir: Path,
    accept_exts: set[str],
    exif_dir: Path,
    *,
    recursive: bool = False,
) -> tuple[list[tuple[Path, Path]], int]:
    """Return (work_list, skipped_count) for one source.

    `recursive=True` for raw_crew, where the on-disk layout has flight-day
    subfolders (FD_01/, FD_02/, …) under the source directory.

    The per-source EXIF output dir is created lazily — only when at least
    one source file exists — so empty source dirs don't litter
    `processed/exif/` with empty subfolders.
    """
    if not source_dir.exists():
        return [], 0
    work: list[tuple[Path, Path]] = []
    skipped = 0
    out_dir = exif_dir / source_name
    iterator = source_dir.rglob("*") if recursive else source_dir.iterdir()
    out_dir_created = False
    for f in iterator:
        if not f.is_file() or f.suffix.lower() not in accept_exts:
            continue
        if not out_dir_created:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_dir_created = True
        nasa_id = _nasa_id_from_filename(f)
        dest = out_dir / f"{nasa_id}.json"
        if _needs_extraction(f, dest):
            work.append((f, dest))
        else:
            skipped += 1
    return work, skipped


def _write_payload(dest: Path, payload: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)


# ── main pipeline ───────────────────────────────────────────────────────────


def extract_all(mission: MissionConfig) -> None:
    exif_dir = mission.exif_cache_dir
    exif_dir.mkdir(parents=True, exist_ok=True)

    # JPEG-family sources walk through PIL.
    jpeg_sources = [
        ("eol",         mission.photos_eol),
        ("ia_stills",   mission.photos_ia_stills),
        ("flickr",      mission.photos_flickr_orig),
        ("nasa_images", mission.photos_nasa_orig),
        ("manual",      mission.photos_manual),
    ]

    # raw_crew goes through ExifTool. Falls back to legacy CREW_RAW_SOURCE_DIR
    # if the per-mission dir is empty (matches 3l_flight_nef.py's behaviour).
    from config import CREW_RAW_SOURCE_DIR
    raw_crew_dir = mission.photos_raw_crew
    if not raw_crew_dir.exists() or not any(raw_crew_dir.iterdir()):
        if CREW_RAW_SOURCE_DIR.exists():
            console.print(
                f"  [dim]raw_crew falling back to legacy location: "
                f"{CREW_RAW_SOURCE_DIR}[/dim]"
            )
            raw_crew_dir = CREW_RAW_SOURCE_DIR

    # ── Plan work ───────────────────────────────────────────────────────────
    plan: dict[str, list[tuple[Path, Path]]] = {}
    skipped_total = 0
    for name, dir_ in jpeg_sources:
        work, skipped = _scan_source(name, dir_, _JPEG_EXTS, exif_dir)
        plan[name] = work
        skipped_total += skipped
        console.print(
            f"  {name:12s}  {len(work):>5} to extract, {skipped:>5} up-to-date"
            + (f"  [dim]({dir_})[/dim]" if not dir_.exists() else "")
        )

    raw_work, raw_skipped = _scan_source(
        "raw_crew", raw_crew_dir, _RAW_EXTS, exif_dir, recursive=True
    )
    plan["raw_crew"] = raw_work
    skipped_total += raw_skipped
    console.print(
        f"  {'raw_crew':12s}  {len(raw_work):>5} to extract, {raw_skipped:>5} up-to-date"
    )

    total_work = sum(len(w) for w in plan.values())
    if total_work == 0:
        console.print(f"\n  [green]All {skipped_total} EXIF files up-to-date.[/green]")
        return

    # ── JPEG sources (PIL, in-process) ─────────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        for source_name, _ in jpeg_sources:
            work = plan[source_name]
            if not work:
                continue
            task = progress.add_task(
                f"[cyan]EXIF {source_name}[/cyan]", total=len(work)
            )
            for src_path, dest_path in work:
                payload = _extract_jpeg_exif(src_path) or {}
                _write_payload(dest_path, payload)
                progress.advance(task)

        # ── raw_crew via ExifTool batch (streams writes per chunk) ─────────
        raw_work = plan["raw_crew"]
        if raw_work:
            task = progress.add_task(
                f"[magenta]EXIF raw_crew (exiftool)[/magenta]", total=len(raw_work)
            )
            ok = _batch_extract_raw_crew(raw_work, progress=progress, task=task)
            if not ok:
                console.print(
                    "[yellow]ExifTool unavailable — raw_crew EXIFs not written. "
                    "Install ExifTool and re-run.[/yellow]"
                )
                progress.update(task, completed=len(raw_work))

    console.print(f"\n  [green]Extracted {total_work} EXIF payloads "
                  f"({skipped_total} skipped as up-to-date).[/green]")


def main():
    parser = argparse.ArgumentParser(
        description="Per-copy EXIF extraction across all photo sources"
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    console.print(f"\n[bold]=== Step 4a: Per-copy EXIF — {mission.name} ===[/bold]\n")
    extract_all(mission)


if __name__ == "__main__":
    main()
