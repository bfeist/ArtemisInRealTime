"""Step 3a — Download still imagery from IA collections.

Downloads JPEG files from IA still imagery collections.

Reads:    mission.ia_stills_collection (from config)
Downloads to: {data_dir}/{mission}/raw/photos/ia_stills/
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.ia_helpers import (
    discover_by_collection,
    download_file,
    get_item_files,
)

IA_DOWNLOAD_URL = "https://archive.org/download"


def find_jpeg_files(files: list[dict]) -> list[dict]:
    """Filter to JPEG files only."""
    return [
        f for f in files
        if f.get("name", "").lower().endswith((".jpg", ".jpeg"))
        and f.get("source") == "original"
    ]


def download_stills(mission: MissionConfig) -> None:
    if not mission.ia_stills_collection:
        print(f"  No IA stills collection configured for {mission.name}. Skipping.")
        return

    ident = mission.ia_stills_collection

    # First try as a direct item (single IA item with files)
    files = get_item_files(ident)

    if files:
        # It's a direct IA item — download JPEGs from it
        print(f"  IA item '{ident}' has {len(files)} files")
        jpegs = find_jpeg_files(files)
        if not jpegs:
            jpegs = [f for f in files if f.get("name", "").lower().endswith((".jpg", ".jpeg"))]
        print(f"  Found {len(jpegs)} JPEG files")

        downloaded = 0
        skipped = 0

        for i, jpeg in enumerate(jpegs, 1):
            url = f"{IA_DOWNLOAD_URL}/{ident}/{jpeg['name']}"
            dest = mission.raw_photos_ia / jpeg["name"]

            if dest.exists():
                skipped += 1
                continue

            ok = download_file(url, dest, desc=jpeg["name"])
            if ok:
                downloaded += 1
            time.sleep(0.2)

        print(f"\n  Done: {downloaded} downloaded, {skipped} skipped")
        return

    # Otherwise treat as a collection of items
    print(f"  Discovering items in collection: {ident}")
    items = discover_by_collection(ident)
    print(f"  Found {len(items)} items")

    downloaded = 0
    skipped = 0

    for i, item in enumerate(items, 1):
        item_ident = item["identifier"]
        print(f"\n  [{i}/{len(items)}] {item_ident}")

        item_files = get_item_files(item_ident)
        jpegs = find_jpeg_files(item_files)

        if not jpegs:
            jpegs = [f for f in item_files if f.get("name", "").lower().endswith((".jpg", ".jpeg"))]

        if not jpegs:
            print(f"    No JPEGs found in {len(item_files)} files")
            continue

        for jpeg in jpegs:
            url = f"{IA_DOWNLOAD_URL}/{item_ident}/{jpeg['name']}"
            dest = mission.raw_photos_ia / jpeg["name"]

            if dest.exists():
                skipped += 1
                continue

            ok = download_file(url, dest, desc=jpeg["name"])
            if ok:
                downloaded += 1
            time.sleep(0.2)

    print(f"\n  Done: {downloaded} downloaded, {skipped} skipped")


def main():
    parser = argparse.ArgumentParser(description="Download IA still imagery")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3a: IA Stills Download — {mission.name} ===\n")
    download_stills(mission)


if __name__ == "__main__":
    main()
