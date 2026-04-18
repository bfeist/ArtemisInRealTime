"""Step 1a — Download comm audio ZIPs from IA.

Downloads ZIP archives from the Artemis II ACR item on archive.org.
Note: ia_comm_collection is an IA *item identifier*, not a searchable collection.

Reads:    mission.ia_comm_collection (from config)
Downloads to: {data_dir}/{mission}/raw/comm/
"""

import argparse
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.ia_helpers import (
    download_file,
    get_item_files,
)

IA_DOWNLOAD_URL = "https://archive.org/download"


def find_zip_files(files: list[dict]) -> list[dict]:
    """Filter to ZIP files only."""
    return [f for f in files if f.get("name", "").lower().endswith(".zip")]


def download_comm(mission: MissionConfig) -> None:
    if not mission.ia_comm_collection:
        print(f"  No comm collection configured for {mission.name}. Skipping.")
        return

    ident = mission.ia_comm_collection
    print(f"  Fetching file list for IA item: {ident}")
    files = get_item_files(ident)
    zips = find_zip_files(files)
    print(f"  Found {len(zips)} ZIP files in {len(files)} total files")

    if not zips:
        print(f"  No ZIP files found.")
        return

    downloaded = 0
    skipped = 0
    extracted = 0

    for i, zf in enumerate(zips, 1):
        name = zf["name"]
        url = f"{IA_DOWNLOAD_URL}/{ident}/{name}"
        dest = mission.raw_comm / name

        if dest.exists():
            skipped += 1
            print(f"  [{i}/{len(zips)}] Already downloaded: {name}")
            continue

        print(f"  [{i}/{len(zips)}] Downloading: {name}")
        ok = download_file(url, dest, desc=name)
        if ok:
            downloaded += 1

            # Auto-extract
            extract_dir = mission.raw_comm / dest.stem
            if not extract_dir.exists():
                try:
                    with zipfile.ZipFile(dest, "r") as z:
                        z.extractall(extract_dir)
                    extracted += 1
                    print(f"    Extracted to {extract_dir.name}/")
                except zipfile.BadZipFile:
                    print(f"    Warning: bad ZIP file: {dest.name}")

        time.sleep(0.5)

    print(f"\n  Done: {downloaded} downloaded, {skipped} skipped, {extracted} extracted")


def main():
    parser = argparse.ArgumentParser(description="Download comm audio ZIPs from IA")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 1a: Comm Download — {mission.name} ===\n")
    download_comm(mission)


if __name__ == "__main__":
    main()
