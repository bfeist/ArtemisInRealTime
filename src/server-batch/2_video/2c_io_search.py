"""Step 2c — Search IO API for NASA IDs from IA video catalog.

Looks up each IA video's NASA ID in IO to get broadcast timestamps
and other metadata.

Reads:    {data_dir}/{mission}/processed/ia_video_catalog.json
Produces: {data_dir}/{mission}/processed/io_cache/io_found.jsonl
          {data_dir}/{mission}/processed/io_cache/io_notfound.jsonl
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import append_jsonl, load_jsonl, search_io


def extract_nasa_id(identifier: str, patterns: list[str]) -> str | None:
    """Try to extract a NASA ID from an IA identifier using mission patterns."""
    for pattern in patterns:
        m = re.search(pattern, identifier, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def search_videos_in_io(mission: MissionConfig) -> None:
    catalog_path = mission.data_dir / "processed" / "ia_video_catalog.json"
    if not catalog_path.exists():
        print(f"  No catalog found at {catalog_path}. Run step 2a first.")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    found_path = mission.io_cache / "io_found.jsonl"
    notfound_path = mission.io_cache / "io_notfound.jsonl"

    # Load already-processed IDs to skip
    already_found = {r.get("ia_identifier") for r in load_jsonl(found_path)}
    already_notfound = {r.get("ia_identifier") for r in load_jsonl(notfound_path)}
    already_done = already_found | already_notfound

    to_process = [i for i in items if i["identifier"] not in already_done]
    print(f"  {len(items)} items in catalog, {len(already_done)} already processed, {len(to_process)} remaining")

    for i, item in enumerate(to_process, 1):
        ident = item["identifier"]
        nasa_id = extract_nasa_id(ident, mission.nasa_id_patterns)

        if not nasa_id:
            # Try from title
            nasa_id = extract_nasa_id(item.get("title", ""), mission.nasa_id_patterns)

        keyword = nasa_id or ident
        print(f"  [{i}/{len(to_process)}] {ident} -> searching IO for '{keyword}'")

        result = search_io(keyword)

        if result and result["results"]["response"]["numfound"] > 0:
            docs = result["results"]["response"]["docs"]
            record = {
                "ia_identifier": ident,
                "search_keyword": keyword,
                "io_docs": docs,
                "io_count": len(docs),
            }
            append_jsonl(found_path, record)
            print(f"    Found {len(docs)} IO docs")
        else:
            record = {
                "ia_identifier": ident,
                "search_keyword": keyword,
            }
            append_jsonl(notfound_path, record)
            print(f"    Not found in IO")

        time.sleep(0.5)

    print(f"\n  Done. Found: {len(load_jsonl(found_path))}, Not found: {len(load_jsonl(notfound_path))}")


def main():
    parser = argparse.ArgumentParser(description="Search IO for IA video NASA IDs")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2c: IO Search — {mission.name} ===\n")
    search_videos_in_io(mission)


if __name__ == "__main__":
    main()
