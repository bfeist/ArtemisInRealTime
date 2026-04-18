"""Step 3f — Produce web-ready photos JSON.

Merges IO photo catalog, IA stills, Flickr album metadata, and
images.nasa.gov catalog into a deduplicated web-ready JSON.

Input:  processed/io_cache/io_photo_catalog.jsonl,
        raw/photos/ia_stills/*.jpg,
        raw/photos/flickr/album_metadata.json,
        raw/photos/images_nasa_gov/catalog.json
Output: {data_dir}/{mission}/web/photos.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import load_jsonl


def _build_io_date_lookup(mission: MissionConfig) -> dict[str, str]:
    """Build nasa_id → best available date from IO photo catalog.

    Used to enrich dates for photos found on Flickr, images.nasa.gov,
    and IA stills that have a NASA ID but no precise timestamp.
    """
    lookup: dict[str, str] = {}
    io_photo_path = mission.io_cache / "io_photo_catalog.jsonl"
    if io_photo_path.exists():
        for doc in load_jsonl(io_photo_path):
            nid = doc.get("nasa_id", "").lower()
            date = doc.get("vmd_start_gmt") or doc.get("md_creation_date", "")
            if nid and date:
                lookup[nid] = date
    return lookup


def build_web_photos(mission: MissionConfig) -> None:
    photos: dict[str, dict] = {}  # keyed by nasa_id or unique id

    # Build IO date lookup for enriching other sources
    io_date_lookup = _build_io_date_lookup(mission)

    # ── 1. IO Photo Catalog (authoritative source) ───────────────────────
    io_photo_path = mission.io_cache / "io_photo_catalog.jsonl"
    io_count = 0
    if io_photo_path.exists():
        for doc in load_jsonl(io_photo_path):
            nasa_id = doc.get("nasa_id", "").lower()
            if not nasa_id:
                continue
            photos[nasa_id] = {
                "id": nasa_id,
                "title": doc.get("md_title", ""),
                "description": doc.get("description", "")[:500] if doc.get("description") else "",
                "date": doc.get("vmd_start_gmt") or doc.get("md_creation_date", ""),
                "source": "io",
                "collections": [
                    c.split("/")[-1] if "/" in c else c
                    for c in (doc.get("collections_string") or [])
                ],
                "public": bool(doc.get("on_public_site", 0)),
            }
            io_count += 1
        print(f"  IO photo catalog: {io_count} photos")
    else:
        print("  No IO photo catalog found.")

    # ── 2. IA Stills ─────────────────────────────────────────────────────
    ia_stills_dir = mission.raw_photos_ia
    ia_count = 0
    if ia_stills_dir.exists():
        for img_file in ia_stills_dir.iterdir():
            if not img_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
                continue
            # Extract NASA ID from filename (e.g., art001e000273.jpg)
            nasa_id = img_file.stem.lower()
            if nasa_id not in photos:
                photos[nasa_id] = {
                    "id": nasa_id,
                    "title": nasa_id,
                    "description": "",
                    "date": io_date_lookup.get(nasa_id, ""),
                    "source": "ia_stills",
                    "collections": [],
                    "public": True,
                }
                ia_count += 1
            # Add IA download URL to existing entry
            photos[nasa_id]["iaUrl"] = (
                f"https://archive.org/download/"
                f"{mission.ia_stills_collection}/{img_file.name}"
            ) if mission.ia_stills_collection else ""
        print(f"  IA stills: {ia_count} new (not in IO)")

    # ── 3. images.nasa.gov ───────────────────────────────────────────────
    nasa_path = mission.raw_photos_nasa / "catalog.json"
    nasa_count = 0
    if nasa_path.exists():
        with open(nasa_path, "r", encoding="utf-8") as f:
            nasa_items = json.load(f)
        for item in nasa_items:
            nasa_id = (item.get("nasa_id") or "").lower()
            if not nasa_id:
                continue

            # Add public URL to existing or create new entry
            links = item.get("links", [])
            thumb_url = links[0].get("href", "") if links else ""

            if nasa_id in photos:
                photos[nasa_id]["nasaImagesUrl"] = thumb_url
                if not photos[nasa_id].get("public"):
                    photos[nasa_id]["public"] = True
            else:
                # Use IO date if available (second-precision), fall back to
                # images.nasa.gov date_created (often day-precision only)
                date = io_date_lookup.get(nasa_id) or item.get("date_created", "")
                photos[nasa_id] = {
                    "id": nasa_id,
                    "title": item.get("title", nasa_id),
                    "description": (item.get("description", "") or "")[:500],
                    "date": date,
                    "source": "nasa_images",
                    "collections": [],
                    "public": True,
                    "nasaImagesUrl": thumb_url,
                }
                nasa_count += 1
        print(f"  images.nasa.gov: {nasa_count} new (not in IO)")

    # ── 3b. IO NHQ date enrichment ───────────────────────────────────────
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

    # ── 4. Flickr ────────────────────────────────────────────────────────
    flickr_path = mission.raw_photos_flickr / "album_metadata.json"
    flickr_count = 0
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
                r"(art\d+[me]\d+|jsc\d+[me]\d+|nhq\d+)", search_text
            )
            if nasa_id_match:
                nasa_id = nasa_id_match.group(1)
                if nasa_id in photos:
                    # Add Flickr URL to existing entry
                    photos[nasa_id]["flickrId"] = flickr_id
                    continue
                # NASA ID found but not in IO — create entry with IO date if available
                io_date = io_date_lookup.get(nasa_id, "")
                photos[nasa_id] = {
                    "id": nasa_id,
                    "flickrId": flickr_id,
                    "title": title,
                    "description": "",
                    "date": io_date or photo.get("datetaken", ""),
                    "source": "flickr",
                    "collections": [],
                    "public": True,
                }
                flickr_count += 1
                continue

            # Add as Flickr-only entry (no NASA ID found)
            entry_id = f"flickr_{flickr_id}"
            if entry_id not in photos:
                date_taken = photo.get("datetaken", "")
                photos[entry_id] = {
                    "id": entry_id,
                    "flickrId": flickr_id,
                    "title": title,
                    "description": "",
                    "date": date_taken,
                    "source": "flickr",
                    "collections": [],
                    "public": True,
                }
                flickr_count += 1
        print(f"  Flickr: {flickr_count} entries")

    # ── 5. Timezone corrections from IO EXIF scrape ──────────────────────
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

    # ── Sort and write ───────────────────────────────────────────────────
    all_photos = sorted(photos.values(), key=lambda x: x.get("date") or "9999")

    out_path = mission.web_dir / "photos.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_photos, f, ensure_ascii=False)

    print(f"\n  Total: {len(all_photos)} unique photos saved to {out_path}")

    # Stats by source
    sources = {}
    for p in all_photos:
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

    print(f"\n=== Step 3f: Web Photos JSON — {mission.name} ===\n")
    build_web_photos(mission)


if __name__ == "__main__":
    main()
