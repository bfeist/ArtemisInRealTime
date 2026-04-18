"""Step 2b — Download MP4s from discovered IA video items.

Reads:    {data_dir}/{mission}/processed/ia_video_catalog.json
Downloads to: {data_dir}/{mission}/raw/video/ia/
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.ia_helpers import download_file, find_best_mp4, get_item_files


def download_videos(mission: MissionConfig) -> None:
    catalog_path = mission.data_dir / "processed" / "ia_video_catalog.json"
    if not catalog_path.exists():
        print(f"  No catalog found at {catalog_path}. Run step 2a first.")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"  {len(items)} items in catalog")

    downloaded = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(items, 1):
        ident = item["identifier"]
        print(f"\n  [{i}/{len(items)}] {ident}")

        # Check if already downloaded
        existing = list(mission.raw_video_ia.glob(f"{ident}*"))
        if existing:
            print(f"    Already downloaded: {existing[0].name}")
            skipped += 1
            continue

        # Get file list and find best MP4
        files = get_item_files(ident)
        if not files:
            print(f"    No files found")
            failed += 1
            continue

        result = find_best_mp4(files, ident)
        if result is None:
            print(f"    No MP4 found in {len(files)} files")
            failed += 1
            continue

        url, filename = result
        dest = mission.raw_video_ia / filename
        print(f"    Downloading: {filename}")

        ok = download_file(url, dest, desc=ident)
        if ok:
            downloaded += 1
        else:
            failed += 1

        time.sleep(0.5)

    print(f"\n  Done: {downloaded} downloaded, {skipped} skipped, {failed} failed")


def main():
    parser = argparse.ArgumentParser(description="Download IA video MP4s")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2b: IA Video Download — {mission.name} ===\n")
    download_videos(mission)


if __name__ == "__main__":
    main()
