"""Step 3a2 — Scrape IO flight photo collections.

Fetches all photo docs from IO flight collections for a mission
using the cols= and as=1 (photo) API parameters.

Produces: {data_dir}/{mission}/processed/io_cache/io_photo_catalog.jsonl
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import save_jsonl, search_io_collection


def scrape_photo_collections(mission: MissionConfig) -> None:
    if not mission.io_parent_cid:
        print(f"  No IO parent CID configured for {mission.name}. Skipping.")
        return

    print(f"  Scraping IO photos under parent CID {mission.io_parent_cid}")
    docs = search_io_collection(mission.io_parent_cid, asset_type=1)

    # Deduplicate by nasa_id — the Solr query returns one doc per collection
    # membership, so a photo in 33 sub-collections appears 33 times.
    seen: set[str] = set()
    unique_docs = []
    for doc in docs:
        nid = doc.get("nasa_id", "")
        if nid not in seen:
            seen.add(nid)
            unique_docs.append(doc)
    if len(docs) != len(unique_docs):
        print(f"  Deduplicated: {len(docs)} → {len(unique_docs)} unique photos")

    out_path = mission.io_cache / "io_photo_catalog.jsonl"
    save_jsonl(out_path, unique_docs)
    print(f"\n  Saved {len(unique_docs)} photo docs to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Scrape IO flight photo collections")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3a2: IO Photo Catalog — {mission.name} ===\n")
    scrape_photo_collections(mission)


if __name__ == "__main__":
    main()
