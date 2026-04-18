"""Step 3e — Search images.nasa.gov for Artemis photos.

Uses the NASA Image and Video Library API (no key required).

Produces: {data_dir}/{mission}/raw/photos/images_nasa_gov/catalog.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

NASA_IMAGES_API = "https://images-api.nasa.gov/search"


def search_nasa_images(mission: MissionConfig) -> list[dict]:
    """Search images.nasa.gov for mission-related photos."""
    all_items: list[dict] = []
    seen_ids: set[str] = set()

    search_terms = [mission.name]  # e.g. "Artemis I", "Artemis II"

    for term in search_terms:
        print(f"  Searching images.nasa.gov for: '{term}'")
        page = 1

        while True:
            params = {
                "q": term,
                "media_type": "image",
                "page": page,
                "page_size": 100,
            }

            try:
                resp = requests.get(NASA_IMAGES_API, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                print(f"    Error on page {page}: {e}")
                break

            items = data.get("collection", {}).get("items", [])
            if not items:
                break

            for item in items:
                item_data = item.get("data", [{}])[0]
                nasa_id = item_data.get("nasa_id", "")
                if nasa_id and nasa_id not in seen_ids:
                    seen_ids.add(nasa_id)
                    all_items.append({
                        "nasa_id": nasa_id,
                        "title": item_data.get("title", ""),
                        "date_created": item_data.get("date_created"),
                        "description": item_data.get("description", ""),
                        "center": item_data.get("center", ""),
                        "keywords": item_data.get("keywords", []),
                        "media_type": item_data.get("media_type"),
                        "links": item.get("links", []),
                    })

            # Check for next page
            links = data.get("collection", {}).get("links", [])
            has_next = any(l.get("rel") == "next" for l in links)
            if not has_next:
                break

            page += 1
            time.sleep(0.3)

        print(f"    Found {len(seen_ids)} unique images so far")

    return all_items


def main():
    parser = argparse.ArgumentParser(description="Search images.nasa.gov")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3e: images.nasa.gov — {mission.name} ===\n")

    items = search_nasa_images(mission)

    out_path = mission.raw_photos_nasa / "catalog.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    print(f"\n  Saved {len(items)} images to {out_path}")


if __name__ == "__main__":
    main()
