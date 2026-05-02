"""Step 3f2 — Extract EXIF datetimes from downloaded photo originals.

Reads DateTimeOriginal + OffsetTimeOriginal from files produced by step 3f,
converts to true UTC, and validates against IO catalog md_creation_date to
confirm that IO stores ground-photographer local times verbatim as UTC.

Reads:
  {PHOTO_ASSETS_DIR}/{mission}/nasa_orig/*.{jpg,jpeg,tiff,...}
  {PHOTO_ASSETS_DIR}/{mission}/flickr_orig/*.{jpg,jpeg,...}
  {data_dir}/{mission}/processed/io_cache/io_photo_catalog.jsonl  (validation)

Writes:
  {data_dir}/{mission}/processed/io_cache/photo-exif-datetimes.json
  Mapping { nasa_id: "YYYY-MM-DDTHH:MM:SSZ" } — only entries where
  OffsetTimeOriginal was present in EXIF, enabling true UTC conversion.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import TAGS
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import load_jsonl

_EXIF_DT_FORMAT = "%Y:%m:%d %H:%M:%S"
_IMAGE_EXTS = {".jpg", ".jpeg", ".tiff", ".tif", ".png"}

# Tags we care about by numeric ID (faster than name lookup for every pixel)
_TAG_DateTimeOriginal = 36867   # 0x9003
_TAG_OffsetTimeOriginal = 36881  # 0x9011


def _read_exif_datetime(path: Path) -> tuple[str | None, str | None]:
    """Return (DateTimeOriginal, OffsetTimeOriginal) from an image file.

    Uses Pillow's _getexif() which returns the raw EXIF tag dict.
    Returns (None, None) on any error or if tags are absent.
    """
    try:
        img = Image.open(path)
        raw = img._getexif()
        if not raw:
            return None, None
        dto = raw.get(_TAG_DateTimeOriginal)
        oto = raw.get(_TAG_OffsetTimeOriginal)
        # Sanitize: OffsetTimeOriginal is sometimes bytes in older Pillow builds
        if isinstance(oto, bytes):
            oto = oto.decode("ascii", errors="ignore").strip("\x00")
        return dto, oto
    except (UnidentifiedImageError, Exception):
        return None, None


def _to_utc(dto: str, oto: str) -> str | None:
    """Convert DateTimeOriginal + OffsetTimeOriginal → UTC ISO-8601 string.

    Example: dto='2026:04:01 21:21:15', oto='-05:00' → '2026-04-02T02:21:15Z'
    Returns None if parsing fails.
    """
    try:
        local_dt = datetime.strptime(dto, _EXIF_DT_FORMAT)
        sign = 1 if oto.startswith("+") else -1
        h, m = int(oto[1:3]), int(oto[4:6])
        tz = timezone(timedelta(hours=h * sign, minutes=m * sign))
        local_dt = local_dt.replace(tzinfo=tz)
        return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, IndexError):
        return None


def extract_photo_exif(mission: MissionConfig) -> None:
    # ── Load IO catalog for validation ──────────────────────────────────────
    io_catalog: dict[str, str] = {}  # nasa_id → md_creation_date
    catalog_path = mission.io_cache / "io_photo_catalog.jsonl"
    if catalog_path.exists():
        for doc in load_jsonl(catalog_path):
            nid = doc.get("nasa_id", "").lower()
            date = doc.get("md_creation_date", "")
            if nid and date:
                io_catalog[nid] = date
    print(f"  IO catalog: {len(io_catalog)} records loaded for validation")

    # ── Scan photo directories ───────────────────────────────────────────────
    results: dict[str, str] = {}  # nasa_id → UTC datetime string

    total = with_offset = no_offset = no_exif = 0
    val_compared = val_wrong = val_correct = 0
    wrong_examples: list[dict] = []

    sources = [
        ("nasa_orig", mission.photos_nasa_orig),
        ("flickr_orig", mission.photos_flickr_orig),
    ]

    for source_name, source_dir in sources:
        if not source_dir.exists():
            print(f"  {source_name}: directory not found, skipping")
            continue

        files = [f for f in source_dir.iterdir() if f.suffix.lower() in _IMAGE_EXTS]
        print(f"  {source_name}: {len(files)} image files")

        for f in tqdm(files, desc=f"  EXIF {source_name}", unit="file", leave=False):
            nasa_id = f.stem.lower()
            total += 1

            dto, oto = _read_exif_datetime(f)
            if dto is None:
                no_exif += 1
                continue

            if not oto:
                no_offset += 1
                continue

            utc_str = _to_utc(dto, oto)
            if not utc_str:
                no_offset += 1
                continue

            results[nasa_id] = utc_str
            with_offset += 1

            # ── Validate against IO catalog ──────────────────────────────
            if nasa_id in io_catalog:
                io_date = io_catalog[nasa_id]
                val_compared += 1
                if io_date == utc_str:
                    val_correct += 1
                else:
                    val_wrong += 1
                    if len(wrong_examples) < 5:
                        wrong_examples.append({
                            "nasa_id": nasa_id,
                            "exif_local": dto,
                            "exif_offset": oto,
                            "exif_utc": utc_str,
                            "io_date": io_date,
                        })

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"\n  EXIF extraction summary:")
    print(f"    Total files:                           {total}")
    print(f"    UTC computed (OffsetTimeOriginal present): {with_offset}")
    print(f"    No timezone offset in EXIF:            {no_offset}")
    print(f"    No EXIF data:                          {no_exif}")

    print(f"\n  Validation against IO catalog ({val_compared} overlapping):")
    if val_compared:
        print(f"    IO date matches true UTC:              {val_correct}")
        print(f"    IO date is local time stored as UTC:   {val_wrong}")
        if wrong_examples:
            print(f"\n  Examples of IO timezone discrepancy:")
            for ex in wrong_examples:
                print(f"    {ex['nasa_id']}:")
                print(f"      EXIF local:  {ex['exif_local']}  (offset {ex['exif_offset']})")
                print(f"      EXIF → UTC:  {ex['exif_utc']}")
                print(f"      IO date:     {ex['io_date']}  ← local time stored as UTC")

    # ── Write output ─────────────────────────────────────────────────────────
    out_path = mission.io_cache / "photo-exif-datetimes.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(results.items())), f, indent=2, ensure_ascii=False)
    print(f"\n  Wrote {len(results)} EXIF UTC datetimes → {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract EXIF datetimes from downloaded photo originals"
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3f2: Extract Photo EXIF Datetimes — {mission.name} ===\n")
    extract_photo_exif(mission)


if __name__ == "__main__":
    main()
