"""Step 2g — Produce web-ready video JSON.

Merges IA video metadata and YouTube metadata into web-ready JSON files.
IA timestamps come from 2f (parsed directly from filenames / IA metadata).

Input:  processed/ia_video_metadata.json, processed/yt_metadata.json
Output: {data_dir}/{mission}/web/videoIA.json, videoYt.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig


def build_web_video_ia(mission: MissionConfig) -> None:
    """Build web-ready IA video JSON from ia_video_metadata.json (step 2f)."""
    meta_path = mission.data_dir / "processed" / "ia_video_metadata.json"
    if not meta_path.exists():
        print("  No IA video metadata found. Run step 2f first.")
        return

    with open(meta_path, "r", encoding="utf-8") as f:
        ia_items = json.load(f)

    entries = []
    for item in ia_items:
        identifier = item["identifier"]
        entry = {
            "id": identifier,
            "title": item.get("title", identifier),
            "source": "ia",
            "sourceUrl": item.get("source_url") or f"https://archive.org/details/{identifier}",
            "thumbnailUrl": f"https://archive.org/services/img/{identifier}",
            "startTime": item.get("recorded_at") or "",
            "duration": item.get("duration") or 0,
            "description": item.get("description", ""),
        }
        entries.append(entry)

    # Sort by startTime (entries without timestamps go to the end)
    entries.sort(key=lambda x: x.get("startTime") or "9999")

    out_path = mission.web_dir / "videoIA.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    print(f"  Saved {len(entries)} IA video entries to {out_path}")


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
