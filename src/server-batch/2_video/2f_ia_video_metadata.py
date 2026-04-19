"""Step 2f — Build IA video metadata JSON, parallel to yt_metadata.json.

Usage:
    python -m 2_video.2f_ia_video_metadata --mission artemis-i
    python -m 2_video.2f_ia_video_metadata --mission artemis-ii

Reads:    {data_dir}/{mission}/processed/ia_video_catalog.json
Writes:   {data_dir}/{mission}/processed/ia_video_metadata.json

Resumable — re-running skips items already present in the output file.
Run 2a (IA Discover) and 2b (IA Download) before this step.

For each item in the catalog this script:
  1. Parses a timestamp from the identifier using known NASA naming conventions.
  2. Fetches the IA item's full metadata to get title, description, and duration.
  3. Finds the matching downloaded file (if any) in raw/video/ia/.

---
Known identifier patterns
--------------------------

ART-DL resource reel (precise UTC timestamp encoded in filename):
    <Subject>_ART-DL-<CamN>_<YYYY>_<DOY>_<HHMM>_<SS><MMM>_<AssetID>[.ext]
    e.g. Orion-Movement_ART-DL-2_2022_341_0755_30000_1736075
         → 2022 DOY 341 = Dec 7, 2022  ·  07:55:30 UTC  ·  asset 1736075

NASA PAO / JSC / KSC identifiers with YYMMDD suffix:
    art001m<catalog>_Title_YYMMDD[_AssetID]
    jsc<YYYY>m<N>_Title_YYMMDD
    KSC-YYYYMMDD-...
    Artemis_I_Title_YYMMDD[_AssetID]

Everything else: date comes from the IA item metadata 'date' field.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.ia_helpers import get_item_metadata


# ── Timestamp parsing ─────────────────────────────────────────────────────────

# ART-DL: Subject_ART-DL-N_YYYY_DOY_HHMM_SSMMM[_AssetID][.ext]
_ART_DL_RE = re.compile(
    r"^(?P<subject>[^_].+?)_ART-DL-(?P<cam>\d+)"
    r"_(?P<year>\d{4})_(?P<doy>\d{3})_(?P<hhmm>\d{4})_(?P<sec>\d{2})(?P<ms>\d{3})"
    r"(?:_(?P<asset_id>\d+))?(?:\.[a-z0-9]+)?$",
    re.IGNORECASE,
)

# Trailing _YYMMDD or _YYMMDD_AssetID  (also handles -YYMMDD)
_YYMMDD_RE = re.compile(r"[_-](\d{6})(?:_\d+)?(?:\.[a-z0-9]+)?$", re.IGNORECASE)

# KSC-YYYYMMDD- prefix
_KSC_DATE_RE = re.compile(r"^KSC-(\d{8})-", re.IGNORECASE)


def _doy_to_datetime(year: int, doy: int, hh: int, mm: int, ss: int) -> datetime:
    base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
    return base.replace(hour=hh, minute=mm, second=ss)


def _parse_yymmdd(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_identifier_timestamp(identifier: str) -> tuple[datetime | None, str, dict]:
    """Return (datetime_utc | None, method_label, extra_fields).

    extra_fields may contain 'subject' and 'camera' for ART-DL items.
    """
    # Strip known file extensions that may appear in the identifier
    stripped = re.sub(r"\.(mp4|mxf|mov|ia\.mp4)$", "", identifier, flags=re.IGNORECASE)

    # 1. ART-DL resource reel
    m = _ART_DL_RE.match(stripped)
    if m:
        g = m.groupdict()
        dt = _doy_to_datetime(
            int(g["year"]), int(g["doy"]),
            int(g["hhmm"][:2]), int(g["hhmm"][2:]),
            int(g["sec"]),
        )
        extra = {"subject": g["subject"].replace("-", " "), "camera": g["cam"]}
        if g["asset_id"]:
            extra["asset_id"] = g["asset_id"]
        return dt, "filename_artdl", extra

    # 2. KSC-YYYYMMDD- prefix
    m = _KSC_DATE_RE.match(identifier)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
            return dt, "filename_ksc", {}
        except ValueError:
            pass

    # 3. Trailing _YYMMDD or _YYMMDD_AssetID
    m = _YYMMDD_RE.search(stripped)
    if m:
        dt = _parse_yymmdd(m.group(1))
        if dt:
            return dt, "filename_yymmdd", {}

    return None, "ia_metadata", {}


# ── Duration helpers ──────────────────────────────────────────────────────────

def _seconds_to_iso_duration(seconds_str: str) -> str | None:
    """Convert a seconds float/int string to ISO 8601 duration (PT...)."""
    try:
        total = float(seconds_str)
    except (ValueError, TypeError):
        return None
    total = int(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = "PT"
    if h:
        parts += f"{h}H"
    if m:
        parts += f"{m}M"
    if s or (not h and not m):
        parts += f"{s}S"
    return parts


def _best_duration(files: list[dict]) -> str | None:
    """Find the longest duration among all files in an IA item (usually the original)."""
    best = None
    best_val = 0.0
    for f in files:
        raw = f.get("length") or f.get("duration")
        if not raw:
            continue
        try:
            val = float(raw)
        except (ValueError, TypeError):
            continue
        if val > best_val:
            best_val = val
            best = raw
    return _seconds_to_iso_duration(best) if best else None


# ── Matching downloaded file ──────────────────────────────────────────────────

def find_downloaded_file(mission: MissionConfig, identifier: str) -> str | None:
    """Return the filename (stem only is fine, we store relative name) if downloaded."""
    # The downloader saves files named after the IA file, prefixed by identifier
    matches = list(mission.raw_video_ia.glob(f"{identifier}*"))
    if matches:
        return matches[0].name
    # Fallback: identifier contains a dot (it is itself a filename)
    matches = list(mission.raw_video_ia.glob(f"*{Path(identifier).stem}*"))
    if matches:
        return matches[0].name
    return None


# ── Core builder ──────────────────────────────────────────────────────────────

def build_ia_metadata(mission: MissionConfig) -> list[dict]:
    catalog_path = mission.data_dir / "processed" / "ia_video_catalog.json"
    if not catalog_path.exists():
        print(f"  No catalog found at {catalog_path}. Run step 2a first.")
        return []

    with open(catalog_path, encoding="utf-8") as f:
        items = json.load(f)

    print(f"  {len(items)} items in catalog")

    # Load existing output to allow incremental / resumable runs
    out_path = mission.data_dir / "processed" / "ia_video_metadata.json"
    existing: dict[str, dict] = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for entry in json.load(f):
                existing[entry["identifier"]] = entry
        print(f"  {len(existing)} already processed (resuming)")

    results: list[dict] = []

    for i, item in enumerate(items, 1):
        ident = item["identifier"]
        print(f"  [{i}/{len(items)}] {ident}")

        if ident in existing:
            print(f"    Already done, skipping")
            results.append(existing[ident])
            continue

        # --- parse timestamp from identifier ---
        recorded_at, date_source, extra = parse_identifier_timestamp(ident)

        # --- fetch IA item metadata ---
        ia_meta = get_item_metadata(ident)
        time.sleep(0.3)  # be polite

        ia_fields: dict = {}
        ia_files: list[dict] = []
        if ia_meta:
            ia_fields = ia_meta.get("metadata", {})
            ia_files = ia_meta.get("files", [])

        # Use IA metadata date as fallback
        if recorded_at is None and ia_fields.get("date"):
            raw_date = ia_fields["date"]
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y"):
                try:
                    recorded_at = datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

        title = ia_fields.get("title") or ident
        description = ia_fields.get("description", "")
        if isinstance(description, list):
            description = " ".join(description)

        duration = _best_duration(ia_files)

        downloaded_file = find_downloaded_file(mission, ident)

        entry: dict = {
            "identifier": ident,
            "title": title,
            "description": description,
            "recorded_at": recorded_at.isoformat() if recorded_at else None,
            "date_source": date_source,
            "duration": duration,
            "source_url": item.get("url") or f"https://archive.org/details/{ident}",
            "filename": downloaded_file,
        }
        if extra.get("subject"):
            entry["subject"] = extra["subject"]
        if extra.get("camera"):
            entry["camera"] = extra["camera"]
        if extra.get("asset_id"):
            entry["asset_id"] = extra["asset_id"]

        results.append(entry)

        if recorded_at:
            print(f"    recorded_at={recorded_at.isoformat()}  source={date_source}")
        else:
            print(f"    recorded_at=None  (could not determine date)")
        if duration:
            print(f"    duration={duration}")
        if downloaded_file:
            print(f"    file={downloaded_file}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Build IA video metadata JSON (equivalent to yt_metadata.json)"
    )
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2f: IA Video Metadata — {mission.name} ===\n")
    results = build_ia_metadata(mission)

    out_path = mission.data_dir / "processed" / "ia_video_metadata.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n  Wrote {len(results)} entries to {out_path}")

    # Summary
    with_date = sum(1 for r in results if r["recorded_at"])
    with_file = sum(1 for r in results if r["filename"])
    with_dur = sum(1 for r in results if r["duration"])
    by_source: dict[str, int] = {}
    for r in results:
        by_source[r["date_source"]] = by_source.get(r["date_source"], 0) + 1

    print(f"\n  Summary:")
    print(f"    With timestamp : {with_date}/{len(results)}")
    print(f"    With duration  : {with_dur}/{len(results)}")
    print(f"    With local file: {with_file}/{len(results)}")
    print(f"    Date sources   : {by_source}")


if __name__ == "__main__":
    main()
