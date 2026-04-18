"""Step 3e2 — Reverse-lookup NHQ photos in IO for precise date_taken.

images.nasa.gov only stores day-precision dates for NHQ (NASA HQ) photos.
IO (Imagery Online at io.jsc.nasa.gov) often has the same photos with
second-precision creation dates.

This script reads the images.nasa.gov catalog, extracts NHQ NASA IDs,
and looks each one up in IO via keyword search.

Reads:    {data_dir}/{mission}/raw/photos/images_nasa_gov/catalog.json
Produces: {data_dir}/{mission}/processed/io_cache/io_nhq_photos_found.jsonl
          {data_dir}/{mission}/processed/io_cache/io_nhq_photos_notfound.jsonl
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import append_jsonl, load_jsonl, search_io


def lookup_nhq_in_io(mission: MissionConfig) -> None:
    catalog_path = mission.raw_photos_nasa / "catalog.json"
    if not catalog_path.exists():
        print(f"  No images.nasa.gov catalog at {catalog_path}. Run step 3e first.")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    # Filter to NHQ IDs only
    nhq_items = [i for i in items if i.get("nasa_id", "").upper().startswith("NHQ")]
    print(f"  {len(items)} images.nasa.gov items, {len(nhq_items)} with NHQ IDs")

    if not nhq_items:
        print("  Nothing to look up.")
        return

    found_path = mission.io_cache / "io_nhq_photos_found.jsonl"
    notfound_path = mission.io_cache / "io_nhq_photos_notfound.jsonl"

    # Load already-processed IDs to skip
    already_found = {r.get("nasa_id") for r in load_jsonl(found_path)}
    already_notfound = {r.get("nasa_id") for r in load_jsonl(notfound_path)}
    already_done = already_found | already_notfound

    to_process = [i for i in nhq_items if i["nasa_id"] not in already_done]
    print(f"  {len(already_done)} already processed, {len(to_process)} remaining")

    if not to_process:
        print("  Nothing to do.")
        return

    found_count = 0
    notfound_count = 0

    for i, item in enumerate(to_process, 1):
        nasa_id = item["nasa_id"]

        result = search_io(nasa_id)

        if result and result["results"]["response"]["numfound"] > 0:
            docs = result["results"]["response"]["docs"]
            # Pick the best match (exact nasa_id match preferred)
            best = None
            for doc in docs:
                if doc.get("nasa_id", "").upper() == nasa_id.upper():
                    best = doc
                    break
            if best is None:
                best = docs[0]

            record = {
                "nasa_id": nasa_id,
                "io_nasa_id": best.get("nasa_id", ""),
                "io_date": best.get("md_creation_date", ""),
                "io_title": best.get("md_title", ""),
                "io_doc_count": len(docs),
            }
            append_jsonl(found_path, record)
            found_count += 1

            if i <= 3 or i % 100 == 0:
                print(f"  [{i}/{len(to_process)}] {nasa_id} -> IO date: {record['io_date']}")
        else:
            record = {"nasa_id": nasa_id}
            append_jsonl(notfound_path, record)
            notfound_count += 1

            if i <= 3 or i % 100 == 0:
                print(f"  [{i}/{len(to_process)}] {nasa_id} -> not in IO")

        if i % 200 == 0:
            print(f"  Progress: {found_count} found, {notfound_count} not found")

        time.sleep(0.3)

    total_found = len(load_jsonl(found_path))
    total_notfound = len(load_jsonl(notfound_path))
    print(f"\n  Done. IO found: {total_found}, not found: {total_notfound}")


def main():
    parser = argparse.ArgumentParser(
        description="Reverse-lookup NHQ photos in IO for date_taken"
    )
    parser.add_argument(
        "--mission", required=True, choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3e2: IO NHQ Lookup — {mission.name} ===\n")
    lookup_nhq_in_io(mission)


if __name__ == "__main__":
    main()
