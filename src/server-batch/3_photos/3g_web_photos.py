"""Step 3g — Produce web-ready photos JSON.

Builds the public photo set from Flickr and images.nasa.gov only.
IO catalog is used as an enrichment lookup (dates, titles, descriptions)
but does NOT contribute new entries on its own.

Input:  raw/photos/flickr/album_metadata.json,
        raw/photos/images_nasa_gov/catalog.json,
        processed/io_cache/io_photo_catalog.jsonl          (enrichment)
        processed/io_cache/photo-datetime-overrides.json   (enrichment)
        processed/io_cache/io_nhq_photos_found.jsonl       (enrichment)
        processed/io_cache/photo-time-overrides.json       (enrichment)
        processed/io_cache/photo-exif-datetimes.json       (enrichment)
Output: {data_dir}/{mission}/web/photos.json
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import load_jsonl


_IORecord = dict  # type alias for clarity


def _build_io_lookup(mission: MissionConfig) -> dict[str, _IORecord]:
    """Build nasa_id → enrichment record from IO catalog.

    Used to fill in dates, titles, and descriptions for photos
    that were discovered via Flickr or images.nasa.gov.  Does NOT drive which
    photos appear in the output — only enriches entries already created by
    those public-facing sources.

    Date priority within the lookup:
      1. photo-datetime-overrides.json  (onboard camera UTC timestamps, step 3a3)
      2. md_creation_date               (local time stored as UTC for ground photos)
    """
    lookup: dict[str, _IORecord] = {}
    io_photo_path = mission.io_cache / "io_photo_catalog.jsonl"
    if io_photo_path.exists():
        for doc in load_jsonl(io_photo_path):
            nid = doc.get("nasa_id", "").lower()
            if not nid:
                continue
            lookup[nid] = {
                "date": doc.get("md_creation_date", ""),
                "title": doc.get("md_title", ""),
                "description": (doc.get("description") or "")[:500],
            }

    # Override dates with second-precision onboard-camera timestamps (step 3a3)
    dt_overrides_path = mission.io_cache / "photo-datetime-overrides.json"
    if dt_overrides_path.exists():
        with open(dt_overrides_path, "r", encoding="utf-8") as f:
            dt_overrides: dict[str, str] = json.load(f)
        for nid, dt_str in dt_overrides.items():
            if dt_str and nid.lower() in lookup:
                lookup[nid.lower()]["date"] = dt_str

    return lookup


def _nasa_image_urls(links: list[dict]) -> dict[str, str]:
    """Extract thumb/img/hiRes URLs from an images.nasa.gov links array.

    ~thumb  ≈ 640px preview (preview rel)
    ~small  ≈ 640px
    ~medium ≈ 1280px
    ~large  ≈ 1920px  (not present on ~3 items)
    """
    by_suffix: dict[str, str] = {}
    for link in links:
        href = link.get("href", "")
        for suffix in ("~thumb", "~small", "~medium", "~large"):
            if suffix in href:
                by_suffix[suffix] = href
    return {
        "thumbUrl": by_suffix.get("~thumb", ""),
        "imgUrl": by_suffix.get("~small", by_suffix.get("~medium", "")),
        "hiResUrl": by_suffix.get("~large", by_suffix.get("~medium", "")),
    }


def _flickr_urls(photo: dict) -> dict[str, str]:
    """Extract thumb/img/hiRes URLs from a Flickr photo record.

    url_t  ≈ 100px wide   (thumbnail)
    url_z  ≈ 640px wide   (medium, present on all photos)
    url_l  ≈ 1024px wide  (large, present on all photos)
    """
    return {
        "thumbUrl": photo.get("url_t", ""),
        "imgUrl": photo.get("url_z", photo.get("url_m", "")),
        "hiResUrl": photo.get("url_l", photo.get("url_h", photo.get("url_k", ""))),
    }


def build_web_photos(mission: MissionConfig) -> None:
    photos: dict[str, dict] = {}  # keyed by nasa_id or unique id

    # Build IO lookup for enriching entries created by public-facing sources
    io_lookup = _build_io_lookup(mission)
    print(f"  IO lookup: {len(io_lookup)} records loaded for enrichment")

    # ── 1. images.nasa.gov ───────────────────────────────────────────────
    nasa_path = mission.raw_photos_nasa / "catalog.json"
    nasa_count = 0
    if nasa_path.exists():
        with open(nasa_path, "r", encoding="utf-8") as f:
            nasa_items = json.load(f)
        for item in nasa_items:
            nasa_id = (item.get("nasa_id") or "").lower()
            if not nasa_id:
                continue
            io = io_lookup.get(nasa_id, {})
            urls = _nasa_image_urls(item.get("links", []))
            photos[nasa_id] = {
                "id": nasa_id,
                "title": io.get("title") or item.get("title", nasa_id),
                "description": io.get("description") or (item.get("description", "") or "")[:500],
                "date": io.get("date") or item.get("date_created", ""),
                "source": "nasa_images",
                **urls,
            }
            nasa_count += 1
        print(f"  images.nasa.gov: {nasa_count} photos")
    else:
        print("  No images.nasa.gov catalog found.")

    # ── 2. Flickr ────────────────────────────────────────────────────────
    flickr_path = mission.raw_photos_flickr / "album_metadata.json"
    flickr_matched = flickr_new = flickr_only = 0
    if flickr_path.exists():
        with open(flickr_path, "r", encoding="utf-8") as f:
            flickr_data = json.load(f)

        flickr_photos = flickr_data if isinstance(flickr_data, list) else flickr_data.get("photos", [])
        for photo in flickr_photos:
            flickr_id = photo.get("id", "")
            title = photo.get("title", "")

            # Try to extract NASA ID from title or description
            search_text = (title + " " + photo.get("description", {}).get("_content", "")).lower()
            nasa_id_match = re.search(
                r"(art\d+[me]\d+|jsc\d+[me]\d+|nhq\d+|iss\d+[a-z]\d+)", search_text
            )
            if nasa_id_match:
                nasa_id = nasa_id_match.group(1)
                if nasa_id in photos:
                    # Enrich existing nasa_images entry with Flickr ID
                    photos[nasa_id]["flickrId"] = flickr_id
                    flickr_matched += 1
                    continue
                # NASA ID not yet in set — create entry, enriched from IO
                io = io_lookup.get(nasa_id, {})
                urls = _flickr_urls(photo)
                photos[nasa_id] = {
                    "id": nasa_id,
                    "flickrId": flickr_id,
                    "title": io.get("title") or title,
                    "description": io.get("description", ""),
                    "date": io.get("date") or photo.get("datetaken", ""),
                    "source": "flickr",
                    **urls,
                }
                flickr_new += 1
                continue

            # No NASA ID found — Flickr-only entry
            entry_id = f"flickr_{flickr_id}"
            if entry_id not in photos:
                urls = _flickr_urls(photo)
                photos[entry_id] = {
                    "id": entry_id,
                    "flickrId": flickr_id,
                    "title": title,
                    "description": "",
                    "date": photo.get("datetaken", ""),
                    "source": "flickr",
                    **urls,
                }
                flickr_only += 1
        print(f"  Flickr: {flickr_matched} matched existing, {flickr_new} new with NASA ID, {flickr_only} Flickr-only")
    else:
        print("  No Flickr album metadata found.")

    # ── 3. IO NHQ date enrichment ─────────────────────────────────────────
    nhq_found_path = mission.io_cache / "io_nhq_photos_found.jsonl"
    nhq_enriched = 0
    if nhq_found_path.exists():
        for rec in load_jsonl(nhq_found_path):
            nasa_id = (rec.get("nasa_id") or "").lower()
            io_date = rec.get("io_date", "")
            if nasa_id in photos and io_date:
                photos[nasa_id]["date"] = io_date
                nhq_enriched += 1
        print(f"  IO NHQ date enrichment: {nhq_enriched} photos updated with precise dates")

    # ── 4. Timezone corrections from IO EXIF scrape ──────────────────────────
    overrides_path = mission.io_cache / "photo-time-overrides.json"
    tz_corrected = 0
    if overrides_path.exists():
        with open(overrides_path, "r", encoding="utf-8") as f:
            tz_overrides = json.load(f)
        from datetime import datetime, timedelta, timezone

        for nasa_id_key, offset_str in tz_overrides.items():
            nid = nasa_id_key.lower()
            if nid not in photos:
                continue
            date_str = photos[nid].get("date", "")
            if not date_str:
                continue
            # Parse the offset (e.g. "-05:00:00" or "-06:00:00")
            # The offset tells us how far behind UTC the local time is.
            # md_creation_date has local time stored as UTC, so we subtract
            # the offset to get true UTC (i.e. add its absolute value).
            m = re.match(r"([+-])(\d{2}):(\d{2}):\d{2}$", offset_str)
            if not m:
                continue
            sign = -1 if m.group(1) == "-" else 1
            offset_hours = int(m.group(2))
            offset_mins = int(m.group(3))
            offset_td = timedelta(hours=offset_hours, minutes=offset_mins) * sign

            # Parse the date, correct it, and write back
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                corrected = dt - offset_td
                photos[nid]["date"] = corrected.strftime("%Y-%m-%dT%H:%M:%SZ")
                photos[nid]["tzOffset"] = offset_str
                tz_corrected += 1
            except (ValueError, TypeError):
                pass
        print(f"  Timezone corrections: {tz_corrected} photos corrected")
    else:
        print("  No timezone overrides found (run 3a3 to generate).")

    # ── 5. EXIF UTC overrides from downloaded photos (step 3f2) ─────────
    # Highest-priority date source: true UTC derived from DateTimeOriginal +
    # OffsetTimeOriginal in the actual downloaded file.  Overrides both the
    # IO md_creation_date and the photo-time-overrides.json TZ correction.
    exif_datetimes_path = mission.io_cache / "photo-exif-datetimes.json"
    exif_applied = 0
    if exif_datetimes_path.exists():
        with open(exif_datetimes_path, "r", encoding="utf-8") as f:
            exif_datetimes: dict[str, str] = json.load(f)
        for nasa_id, dt_str in exif_datetimes.items():
            nid = nasa_id.lower()
            if nid in photos and dt_str:
                photos[nid]["date"] = dt_str
                # Remove tzOffset if previously set — EXIF is already true UTC
                photos[nid].pop("tzOffset", None)
                exif_applied += 1
        print(f"  EXIF datetime overrides (step 3f2): {exif_applied} applied")
    else:
        print("  No EXIF datetime overrides found (run 3f2 to generate).")

    # ── Sort, filter to mission date range, and write ────────────────────
    mission_start = date.fromisoformat(mission.mission_start)
    mission_end = date.fromisoformat(mission.mission_end)

    def _in_range(photo: dict) -> bool:
        d = photo.get("date", "")
        if not d:
            return False
        try:
            photo_date = date.fromisoformat(d[:10])
            return mission_start <= photo_date <= mission_end
        except (ValueError, TypeError):
            return False

    all_photos = sorted(photos.values(), key=lambda x: x.get("date") or "9999")
    filtered = [p for p in all_photos if _in_range(p)]
    out_of_range = len(all_photos) - len(filtered)
    if out_of_range:
        print(f"  Filtered out {out_of_range} photos outside mission range "
              f"({mission.mission_start} – {mission.mission_end})")

    out_path = mission.web_dir / "photos.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"\n  Total: {len(filtered)} unique photos saved to {out_path}")

    # Stats by source
    sources = {}
    for p in filtered:
        s = p.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    for src, cnt in sorted(sources.items()):
        print(f"    {src}: {cnt}")


def main():
    parser = argparse.ArgumentParser(description="Produce web-ready photos JSON")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3g: Web Photos JSON — {mission.name} ===\n")
    build_web_photos(mission)


if __name__ == "__main__":
    main()
