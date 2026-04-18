"""Step 2a — Discover IA video items for a mission.

Uses four strategies:
  1. Subject tag search (e.g. "Artemis II Resource Reel")
  2. Known collection identifiers
  3. Uploader search (NASA Johnson + mission name)
  4. De-duplication

Produces: {data_dir}/{mission}/processed/ia_video_catalog.json
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running from src/ or repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.ia_helpers import (
    deduplicate_items,
    discover_by_collection,
    discover_by_subject,
    discover_by_uploader,
)


def discover_videos(mission: MissionConfig) -> list[dict]:
    """Run all discovery strategies and return deduplicated IA items."""
    all_items: list[dict] = []

    # Strategy 1: subject tag
    print(f"  [1] Searching by subject tag: '{mission.ia_subject_tag}'")
    items = discover_by_subject(mission.ia_subject_tag, media_type="movies")
    print(f"      Found {len(items)} items")
    all_items.extend(items)

    # Strategy 2: known collections
    for coll in mission.ia_collections:
        print(f"  [2] Searching collection: {coll}")
        items = discover_by_collection(coll, media_type="movies")
        print(f"      Found {len(items)} items")
        all_items.extend(items)

    # Strategy 3: uploader search
    for term in [mission.name]:
        print(f"  [3] Searching uploader 'NASA Johnson' for '{term}'")
        items = discover_by_uploader("NASA Johnson", title_filter=term)
        # Filter to movies only
        items = [i for i in items if i.get("mediatype") == "movies"]
        print(f"      Found {len(items)} items")
        all_items.extend(items)

    # Deduplicate
    unique = deduplicate_items(all_items)
    print(f"  Total unique video items: {len(unique)} (from {len(all_items)} raw)")
    return unique


def main():
    parser = argparse.ArgumentParser(description="Discover IA video items for a mission")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
        help="Mission slug (e.g. artemis-i, artemis-ii)",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2a: IA Video Discovery — {mission.name} ===\n")

    items = discover_videos(mission)

    # Add browsable URL to each item
    for item in items:
        item["url"] = f"https://archive.org/details/{item['identifier']}"

    # Save catalog
    out_path = mission.data_dir / "processed" / "ia_video_catalog.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    print(f"\n  Saved {len(items)} items to {out_path}")

    # Print summary with URLs for quick inspection
    for item in items:
        print(f"    {item['title']}")
        print(f"      {item['url']}")


if __name__ == "__main__":
    main()
