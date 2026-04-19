"""Step 3e — Search images.nasa.gov for Artemis photos.

Uses the NASA Image and Video Library API (no key required).

Produces: {data_dir}/{mission}/raw/photos/images_nasa_gov/catalog.json
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

NASA_IMAGES_API = "https://images-api.nasa.gov/search"


def _in_mission_window(date_str: str, start: datetime, end: datetime) -> bool:
    """Return True if date_str (ISO) falls within [start, end]. Unknown dates pass through."""
    if not date_str:
        return True
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
        return start <= dt <= end
    except ValueError:
        return True  # unparseable — keep it


def search_nasa_images(mission: MissionConfig) -> list[dict]:
    """Search images.nasa.gov for mission-related photos.

    Applies two filters to avoid cross-mission contamination:
    1. API-level: year_start/year_end scoped to mission dates.
    2. Client-level: date_created must fall within mission_start..mission_end.
    3. Client-level: keywords/title must not exclusively name a different Artemis mission.
    """
    all_items: list[dict] = []
    seen_ids: set[str] = set()

    # Mission date window
    win_start = datetime.strptime(mission.mission_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    win_end = datetime.strptime(mission.mission_end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    year_start = win_start.year
    year_end = win_end.year

    # Word-boundary patterns for this mission's search terms
    our_patterns = [
        re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)
        for t in ([mission.name] + getattr(mission, "yt_search_terms", []))
    ]

    # Patterns for OTHER missions — to exclude results that only mention them
    other_patterns = [
        re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)
        for key, m in MISSIONS.items() if key != mission.slug
        for t in ([m.name] + getattr(m, "yt_search_terms", []))
    ]

    search_terms = [mission.name]  # e.g. "Artemis I", "Artemis II"

    for term in search_terms:
        print(f"  Searching images.nasa.gov for: '{term}' ({year_start}–{year_end})")
        page = 1

        while True:
            params = {
                "q": term,
                "media_type": "image",
                "page": page,
                "page_size": 100,
                "year_start": year_start,
                "year_end": year_end,
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
                if not nasa_id or nasa_id in seen_ids:
                    continue

                # Date filter: drop anything outside the mission window
                date_created = item_data.get("date_created", "")
                if not _in_mission_window(date_created, win_start, win_end):
                    continue

                # Term filter: drop results that only name a different mission
                search_text = (
                    item_data.get("title", "") + " " +
                    " ".join(item_data.get("keywords", []))
                )
                has_ours = any(p.search(search_text) for p in our_patterns)
                has_other_only = (
                    any(p.search(search_text) for p in other_patterns)
                    and not has_ours
                )
                if has_other_only:
                    continue

                seen_ids.add(nasa_id)
                all_items.append({
                    "nasa_id": nasa_id,
                    "title": item_data.get("title", ""),
                    "date_created": date_created,
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
