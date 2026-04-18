"""Step 3b — Discover & fetch Flickr album metadata.

Uses known album IDs from config, or searches by keyword.

Produces: {data_dir}/{mission}/raw/photos/flickr/album_metadata.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.flickr_api import get_photoset_photos


def fetch_album(mission: MissionConfig) -> None:
    if not mission.flickr_album_id:
        print(f"  No Flickr album ID configured for {mission.name}. Skipping.")
        return

    out_path = mission.raw_photos_flickr / "album_metadata.json"

    # Check if already fetched
    if out_path.exists():
        with open(out_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  Album already fetched: {len(existing)} photos. Delete {out_path} to re-fetch.")
        return

    print(f"  Fetching Flickr album {mission.flickr_album_id}...")
    photos = get_photoset_photos(mission.flickr_album_id)
    print(f"  Fetched {len(photos)} photos")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(photos, f, indent=2, ensure_ascii=False)

    print(f"  Saved to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Fetch Flickr album metadata")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3b: Flickr Album — {mission.name} ===\n")
    fetch_album(mission)


if __name__ == "__main__":
    main()
