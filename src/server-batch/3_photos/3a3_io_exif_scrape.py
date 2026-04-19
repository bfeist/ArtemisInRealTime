"""Step 3a3 — Scrape EXIF metadata from IO photo info pages.

IO stores camera local time as UTC in md_creation_date. The timezone
offset is only available in EXIF fields (DigitalCreationTime / TimeCreated)
visible on individual photo info pages (info.cfm?pid=XXXXX).

This script:
  1. Fetches all mission-day ground photos from IO (date-range + collection)
  2. Scrapes each photo's info page to extract EXIF metadata
  3. Writes photo-exif-metadata.json (full EXIF) and photo-time-overrides.json
     (per-photo timezone correction map)

Only jsc* and nhq* prefixed photos are scraped — onboard cameras (art002e/a)
are already in UTC.

Reads:    IO API (date range + collection CID)
Produces: {data_dir}/{mission}/processed/io_cache/photo-exif-metadata.json
          {data_dir}/{mission}/processed/io_cache/photo-time-overrides.json
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, IO_API_BASE, IO_KEY, IO_ORIGIN_HEADER, MissionConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# IO info page URL pattern
IO_HOST = "https://io.jsc.nasa.gov"
IO_INFO_URL = IO_HOST + "/app/info.cfm?pid={pid}"

# Ground-photographer prefixes — need timezone correction for md_creation_date.
SCRAPE_PREFIXES = ("jsc", "nhq")

# Regex to parse capture timestamp from original filenames like:
#   cmaopnav_20221209175700.tiff  →  2022-12-09T17:57:00Z
#   hd_cam_20221118081220.jpg     →  2022-11-18T08:12:20Z
_ORIG_FILENAME_TS_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})[_.]") 

# Concurrency for scraping info pages
CONCURRENCY = 10

# ── Camera serial → timezone mapping ────────────────────────────────────────
# Only cameras with a CONSISTENT non-default timezone across the entire
# mission. Cameras that changed timezone mid-mission are excluded — handled
# by scraped EXIF tz_offset on the days where it's available.
CAMERA_TZ = {
    "3023828": "-06:00",  # David DeHoyos — NIKON Z 9 (CST, missed DST switch)
    "3035041": "-06:00",  # Luna Posadas Nava — NIKON Z 9 (CST, missed DST switch)
}

# Default timezone for each nasa_id prefix. Applied to photos where we have
# no EXIF tz_offset and no camera serial mapping.
PREFIX_DEFAULTS = {
    "jsc2026e": "-05:00",  # JSC Houston photographers, CDT
    "nhq": "-04:00",       # NHQ photographers at KSC, EDT
}

# IO collection CID containing Artemis mission photos
# (parent "Artemis - Missions" collection at io.jsc.nasa.gov)
ARTEMIS_MISSIONS_CID = "2346894"


def get_prefix_default(nasa_id: str) -> str | None:
    """Get the default timezone for a nasa_id prefix."""
    for prefix, tz in PREFIX_DEFAULTS.items():
        if nasa_id.startswith(prefix):
            return tz
    return None


def get_serial(exif: dict | None) -> str | None:
    """Extract camera body serial number from EXIF data.

    Handles two formats:
      - Processed JPGs: fields named "SerialNumber" or "Serial Number"
      - Raw NEFs (Nikon): "MODEL" field contains "NIKON D6 S/N: 3001958"
    """
    if not exif:
        return None
    serial = exif.get("SerialNumber") or exif.get("Serial Number")
    if serial:
        return serial
    model_field = exif.get("MODEL", "")
    m = re.search(r"S/N:\s*(\d+)", model_field)
    if m:
        return m[1]
    return None


# ── IO API: fetch mission-day ground photos ─────────────────────────────────


def fetch_mission_day_photos(mission: MissionConfig) -> list[dict]:
    """Fetch all mission-day photos from IO using date range + collection."""
    # IO date format: MM-DD-YYYY
    start = mission.mission_start  # "2026-04-01"
    end = mission.mission_end      # "2026-04-11"
    # Convert YYYY-MM-DD to MM-DD-YYYY
    sy, sm, sd = start.split("-")
    ey, em, ed = end.split("-")
    # Extend end date by 2 days to catch late-arriving photos
    from datetime import datetime, timedelta
    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=2)
    end_ext = end_dt.strftime("%m-%d-%Y")
    start_fmt = f"{sm}-{sd}-{sy}"

    rpp = 500
    headers = {"Origin": IO_ORIGIN_HEADER}
    base_params = (
        f"rpp={rpp}&s_dt={start_fmt}&e_dt={end_ext}"
        f"&cols={ARTEMIS_MISSIONS_CID}&as=1&so=7"
    )
    url = f"{IO_API_BASE}/{base_params}?key={IO_KEY}&format=json"

    resp = requests.get(url, verify=False, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    total = data["results"]["response"]["numfound"]
    docs = list(data["results"]["response"]["docs"])
    print(f"  IO date-range query: {total} total photos")

    # Paginate
    import math
    pages = math.ceil(total / rpp)
    for page in range(1, pages):
        sr = page * rpp + 1
        page_url = f"{url}&sr={sr}"
        pr = requests.get(page_url, verify=False, headers=headers, timeout=60)
        pr.raise_for_status()
        docs.extend(pr.json()["results"]["response"]["docs"])
        print(f"    Page {page + 1}/{pages} ({len(docs)} fetched)")
        time.sleep(0.3)

    return docs


# ── Scrape EXIF from info pages ─────────────────────────────────────────────

# Regex to extract key-value pairs from the IO camera data table.
# Pattern: <td class="nowrap"...>FieldName</td> ... <td ...>Value</td>
KV_PATTERN = re.compile(
    r'<td\s+class="nowrap"[^>]*>\s*(\w[\w\s]*?)\s*</td>'
    r'\s*(?:</tr>\s*)?<td[^>]*>\s*([\s\S]*?)\s*</td>',
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")


def parse_original_filename_dt(filename: str) -> str | None:
    """Parse a UTC datetime from an IO original filename.

    Handles patterns like  cmaopnav_20221209175700.tiff  →  '2022-12-09T17:57:00Z'.
    Returns None if no 14-digit timestamp is found.
    """
    m = _ORIG_FILENAME_TS_RE.search(filename + "_")  # ensure trailing delimiter
    if not m:
        return None
    yr, mo, dy, hh, mm, ss = m.groups()
    return f"{yr}-{mo}-{dy}T{hh}:{mm}:{ss}Z"


def scrape_info_page(pid: str, nasa_id: str) -> dict:
    """Scrape EXIF metadata from an individual IO photo info page."""
    url = IO_INFO_URL.format(pid=pid)
    try:
        resp = requests.get(
            url, verify=False,
            headers={"Accept": "text/html"},
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text

        fields = {}
        for m in KV_PATTERN.finditer(html):
            key = m.group(1).strip()
            val = HTML_TAG_RE.sub("", m.group(2)).strip()
            if val and val != "&nbsp;":
                fields[key] = val

        # PreservedFileName is the original camera filename (e.g. cmaopnav_20221209175700.tiff)
        # It's already captured by KV_PATTERN in the EXIF table.
        original_filename = fields.get("PreservedFileName")

        # Derive timezone offset from DigitalCreationTime (e.g. "17:06:20-07:00")
        dct = fields.get("DigitalCreationTime") or fields.get("TimeCreated")
        tz_offset = None
        if dct:
            tz_match = re.search(r"([+-]\d{2}:\d{2})$", dct)
            if tz_match:
                tz_offset = tz_match.group(1)

        return {
            "nasa_id": nasa_id,
            "pid": pid,
            "tz_offset": tz_offset,
            "original_filename": original_filename,
            "exif": fields,
        }
    except Exception as e:
        return {
            "nasa_id": nasa_id,
            "pid": pid,
            "tz_offset": None,
            "original_filename": None,
            "exif": None,
            "error": str(e),
        }


def scrape_batch(batch: list[tuple[str, str]]) -> list[dict]:
    """Scrape a batch of (pid, nasa_id) pairs sequentially."""
    results = []
    for pid, nasa_id in batch:
        results.append(scrape_info_page(pid, nasa_id))
    return results


# ── Generate timezone overrides ─────────────────────────────────────────────


def generate_overrides(metadata: list[dict]) -> dict[str, str]:
    """Generate per-photo timezone correction map from scraped EXIF.

    Returns { nasa_id: timeOffset } where timeOffset is "-HH:MM:00".
    """
    overrides = {}
    stats = {"exif": 0, "serial": 0, "default": 0, "skipped": 0}

    for photo in metadata:
        default_tz = get_prefix_default(photo["nasa_id"])
        if not default_tz:
            stats["skipped"] += 1
            continue

        # Priority 1: scraped tz_offset from EXIF
        actual_tz = photo.get("tz_offset")
        source = "exif"

        # Priority 2: camera serial → CAMERA_TZ mapping
        if not actual_tz:
            serial = get_serial(photo.get("exif"))
            if serial and serial in CAMERA_TZ:
                actual_tz = CAMERA_TZ[serial]
                source = "serial"

        # Priority 3: prefix default
        if not actual_tz:
            actual_tz = default_tz
            source = "default"

        # Convert tz offset to timeOffset format: "-05:00" → "-05:00:00"
        overrides[photo["nasa_id"]] = actual_tz + ":00"
        stats[source] += 1

    print(f"  Overrides: {len(overrides)} total")
    print(f"    From EXIF tz_offset: {stats['exif']}")
    print(f"    From camera serial:  {stats['serial']}")
    print(f"    From prefix default: {stats['default']}")
    print(f"    Skipped (no prefix): {stats['skipped']}")

    # Summary by timezone
    from collections import Counter
    by_tz = Counter(overrides.values())
    print("  By timezone:")
    for tz, count in sorted(by_tz.items()):
        print(f"    {tz}: {count}")

    return dict(sorted(overrides.items()))


# ── Main ────────────────────────────────────────────────────────────────────


def scrape_io_exif(mission: MissionConfig) -> None:
    # 1. Fetch all mission-day photos from IO
    print("  Fetching mission-day photos from IO...")
    all_docs = fetch_mission_day_photos(mission)

    # 2. Filter to ground-photographer prefixes
    docs_to_scrape = [
        d for d in all_docs
        if any(d.get("nasa_id", "").startswith(pfx) for pfx in SCRAPE_PREFIXES)
    ]
    print(f"  Filtered to {len(docs_to_scrape)} ground photos "
          f"({', '.join(SCRAPE_PREFIXES)}), "
          f"skipping {len(all_docs) - len(docs_to_scrape)} onboard/other")

    if not docs_to_scrape:
        print("  Nothing to scrape.")
        return

    # 3. Check for existing metadata (resume support)
    metadata_path = mission.io_cache / "photo-exif-metadata.json"
    existing = {}
    if metadata_path.exists():
        with open(metadata_path, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                existing[entry["nasa_id"]] = entry
        print(f"  Existing metadata: {len(existing)} entries")

    to_scrape = [
        d for d in docs_to_scrape
        if d["nasa_id"] not in existing
    ]
    print(f"  Need to scrape: {len(to_scrape)} new photos")

    # 4. Scrape EXIF from info pages in batches
    if to_scrape:
        print(f"\n  Scraping EXIF from {len(to_scrape)} info pages "
              f"({CONCURRENCY} concurrent)...")
        scraped = 0
        for i in range(0, len(to_scrape), CONCURRENCY):
            batch = [
                (str(d["id"]), d["nasa_id"])
                for d in to_scrape[i:i + CONCURRENCY]
            ]
            results = scrape_batch(batch)
            for r in results:
                existing[r["nasa_id"]] = r
            scraped += len(results)
            pct = round(scraped / len(to_scrape) * 100)
            print(f"\r    Progress: {scraped}/{len(to_scrape)} ({pct}%)",
                  end="", flush=True)
        print()

    # 5. Build full metadata list (merge IO doc fields + scraped EXIF)
    metadata = []
    doc_by_nid = {d["nasa_id"]: d for d in docs_to_scrape}
    for nid in sorted(existing):
        entry = existing[nid]
        doc = doc_by_nid.get(nid, {})
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", doc.get("md_creation_date", ""))
        metadata.append({
            "nasa_id": nid,
            "pid": entry.get("pid") or str(doc.get("id", "")),
            "date": date_match.group(1) if date_match else None,
            "md_creation_date": doc.get("md_creation_date", ""),
            "tz_offset": entry.get("tz_offset"),
            "exif": entry.get("exif"),
            "collections_string": doc.get("collections_string", ""),
        })

    # 6. Write metadata
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"\n  Wrote {len(metadata)} entries to {metadata_path}")

    # 7. Generate and write timezone overrides
    overrides = generate_overrides(metadata)
    overrides_path = mission.io_cache / "photo-time-overrides.json"
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)
    print(f"  Wrote {len(overrides)} overrides to {overrides_path}")


def scrape_io_onboard_datetimes(mission: MissionConfig) -> None:
    """Scrape Original Filename from IO info pages for onboard camera photos.

    Reads io_photo_catalog.jsonl (all photos), skips ground-photographer prefixes
    already handled by scrape_io_exif(), scrapes each photo's info page to extract
    the Original Filename, parses the embedded UTC timestamp, and writes
    photo-datetime-overrides.json: { nasa_id: "YYYY-MM-DDTHH:MM:SSZ" }.
    """
    catalog_path = mission.io_cache / "io_photo_catalog.jsonl"
    if not catalog_path.exists():
        print("  No io_photo_catalog.jsonl — run step 3a2 first.")
        return

    from shared.io_api import load_jsonl
    all_docs = list(load_jsonl(catalog_path))
    # Skip ground-photographer prefixes — their dates come from md_creation_date + TZ correction
    onboard_docs = [
        d for d in all_docs
        if not any(d.get("nasa_id", "").startswith(pfx) for pfx in SCRAPE_PREFIXES)
    ]
    print(f"  Onboard photos to scrape: {len(onboard_docs)} "
          f"(of {len(all_docs)} total in catalog)")

    overrides_path = mission.io_cache / "photo-datetime-overrides.json"
    existing: dict[str, str] = {}
    if overrides_path.exists():
        with open(overrides_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  Existing overrides: {len(existing)} entries (will resume)")

    to_scrape = [d for d in onboard_docs if d["nasa_id"] not in existing]
    print(f"  Need to scrape: {len(to_scrape)} new photos")

    if to_scrape:
        print(f"  Scraping {len(to_scrape)} info pages ({CONCURRENCY} concurrent)...")
        scraped = parsed = 0
        for i in range(0, len(to_scrape), CONCURRENCY):
            batch = [
                (str(d["id"]), d["nasa_id"])
                for d in to_scrape[i:i + CONCURRENCY]
            ]
            results = scrape_batch(batch)
            for r in results:
                nid = r["nasa_id"]
                orig = r.get("original_filename")
                dt_str = parse_original_filename_dt(orig) if orig else None
                existing[nid] = dt_str  # None means scraped but no parseable timestamp
                if dt_str:
                    parsed += 1
            scraped += len(results)
            pct = round(scraped / len(to_scrape) * 100)
            print(f"\r    Progress: {scraped}/{len(to_scrape)} ({pct}%) — {parsed} timestamps parsed",
                  end="", flush=True)
            # Save incrementally every 500 photos for resume support
            if scraped % 500 == 0:
                _save_overrides(overrides_path, existing)
        print()

    _save_overrides(overrides_path, existing)
    valid = sum(1 for v in existing.values() if v)
    print(f"  Wrote {len(existing)} entries ({valid} with parsed timestamps) "
          f"to {overrides_path}")


def _save_overrides(path: Path, overrides: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(overrides.items())), f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape IO EXIF metadata and generate timezone overrides"
    )
    parser.add_argument(
        "--mission", required=True, choices=list(MISSIONS.keys()),
    )
    parser.add_argument(
        "--onboard-only", action="store_true",
        help="Only scrape onboard camera datetimes (skip ground TZ correction)",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3a3: IO EXIF Scrape — {mission.name} ===\n")
    if not args.onboard_only:
        scrape_io_exif(mission)
    print()
    scrape_io_onboard_datetimes(mission)


if __name__ == "__main__":
    main()
