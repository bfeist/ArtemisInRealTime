"""Step 2g — Produce web-ready video JSON.

Merges IA video catalog, IO video catalog, IO cross-reference data,
and YouTube metadata into web-ready JSON files.

Input:  processed/ia_video_catalog.json, processed/io_cache/*.jsonl,
        processed/yt_metadata.json
Output: {data_dir}/{mission}/web/videoIA.json, videoYt.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import load_jsonl


def build_io_lookup(mission: MissionConfig) -> dict[str, dict]:
    """Build a nasa_id → IO doc lookup from io_found.jsonl and io_video_catalog.jsonl.

    Merges both sources: io_found.jsonl (per-item IA lookups from 2c) and
    io_video_catalog.jsonl (bulk collection scrape from 2c2). The catalog
    is the more comprehensive source; io_found adds any items found via
    direct keyword search that may not be in the collection.
    """
    lookup: dict[str, dict] = {}

    # Load bulk IO video catalog (from 2c2) — comprehensive collection scrape
    catalog_path = mission.io_cache / "io_video_catalog.jsonl"
    if catalog_path.exists():
        for doc in load_jsonl(catalog_path):
            nid = doc.get("nasa_id", "").lower()
            if nid:
                lookup[nid] = doc

    # Layer on io_found.jsonl (from 2c) — per-item lookups may have extras
    found_path = mission.io_cache / "io_found.jsonl"
    if found_path.exists():
        for record in load_jsonl(found_path):
            for doc in record.get("io_docs", []):
                nid = doc.get("nasa_id", "").lower()
                if nid and nid not in lookup:
                    lookup[nid] = doc

    return lookup


def _extract_nasa_id(text: str, patterns: list[str]) -> str | None:
    """Extract a NASA ID from text using mission-specific regex patterns."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(0).lower()
    return None


def build_web_video_ia(mission: MissionConfig) -> None:
    """Build web-ready IA video JSON."""
    catalog_path = mission.data_dir / "processed" / "ia_video_catalog.json"
    if not catalog_path.exists():
        print("  No IA video catalog found. Run step 2a first.")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        ia_items = json.load(f)

    io_lookup = build_io_lookup(mission)
    io_catalog_count = len(io_lookup)

    entries = []
    matched = 0
    for item in ia_items:
        identifier = item.get("identifier", "")
        title = item.get("title", identifier)

        # Match IA item to IO doc by extracting NASA ID from identifier/title
        io_doc = None
        nasa_id = _extract_nasa_id(identifier, mission.nasa_id_patterns)
        if not nasa_id:
            nasa_id = _extract_nasa_id(title, mission.nasa_id_patterns)
        if nasa_id and nasa_id in io_lookup:
            io_doc = io_lookup[nasa_id]
            matched += 1

        entry = {
            "id": identifier,
            "title": title,
            "source": "ia",
            "sourceUrl": f"https://archive.org/details/{identifier}",
            "thumbnailUrl": f"https://archive.org/services/img/{identifier}",
        }

        if io_doc:
            entry["startTime"] = io_doc.get("vmd_start_gmt", "")
            entry["endTime"] = io_doc.get("vmd_end_gmt", "")
            entry["duration"] = io_doc.get("duration_seconds", 0)
            entry["description"] = io_doc.get("description", "")
        else:
            entry["startTime"] = ""
            entry["duration"] = 0

        entries.append(entry)

    # Sort by startTime (entries without timestamps go to the end)
    entries.sort(key=lambda x: x.get("startTime") or "9999")

    out_path = mission.web_dir / "videoIA.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    print(f"  Saved {len(entries)} IA video entries to {out_path}")
    print(f"    IO lookup: {io_catalog_count} docs, {matched}/{len(entries)} IA items matched")


def build_web_video_yt(mission: MissionConfig) -> None:
    """Build web-ready YouTube video JSON."""
    yt_path = mission.data_dir / "processed" / "yt_metadata.json"
    if not yt_path.exists():
        print("  No YouTube metadata found. Run step 2d first.")
        return

    with open(yt_path, "r", encoding="utf-8") as f:
        yt_videos = json.load(f)

    entries = []
    for video in yt_videos:
        vid_id = video.get("video_id", "")

        entry = {
            "id": vid_id,
            "title": video.get("title", ""),
            "source": "youtube",
            "sourceUrl": f"https://www.youtube.com/watch?v={vid_id}",
            "startTime": video.get("actual_start_time") or video.get("published_at", ""),
            "endTime": video.get("actual_end_time", ""),
            "duration": video.get("duration", ""),
            "description": video.get("description", "")[:500] if video.get("description") else "",
        }
        entries.append(entry)

    # Sort by startTime
    entries.sort(key=lambda x: x.get("startTime") or "9999")

    out_path = mission.web_dir / "videoYt.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    print(f"  Saved {len(entries)} YouTube video entries to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Produce web-ready video JSON")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2g: Web Video JSON — {mission.name} ===\n")
    build_web_video_ia(mission)
    build_web_video_yt(mission)


if __name__ == "__main__":
    main()
