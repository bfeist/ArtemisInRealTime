"""Step 2e — Download YouTube videos via yt-dlp.

Reads:    {data_dir}/{mission}/processed/yt_metadata.json
Downloads to: D:/ArtemisInRealTime_yt_videos/{mission}/
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig


def download_videos(mission: MissionConfig) -> None:
    meta_path = mission.data_dir / "processed" / "yt_metadata.json"
    if not meta_path.exists():
        print(f"  No metadata found at {meta_path}. Run step 2d first.")
        return

    with open(meta_path, "r", encoding="utf-8") as f:
        all_videos = json.load(f)

    # Only download videos within mission date window ± 1 day
    buf = timedelta(days=1)
    win_start = datetime.strptime(mission.mission_start, "%Y-%m-%d").replace(tzinfo=timezone.utc) - buf
    win_end = datetime.strptime(mission.mission_end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + buf + timedelta(days=1)

    videos = []
    for v in all_videos:
        ts = v.get("actual_start_time") or v.get("published_at", "")
        if not ts:
            videos.append(v)
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if win_start <= dt <= win_end:
                videos.append(v)
            else:
                print(f"  Skipping (outside mission window): {ts[:10]} {v['title'][:60]}")
        except (ValueError, TypeError):
            videos.append(v)

    print(f"  {len(videos)}/{len(all_videos)} videos within mission date window")

    out_dir = mission.yt_video_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0
    failed = 0

    for i, video in enumerate(videos, 1):
        vid_id = video["video_id"]
        title = video["title"]

        # Check if already downloaded
        existing = list(out_dir.glob(f"*{vid_id}*"))
        if existing:
            print(f"  [{i}/{len(videos)}] Already downloaded: {vid_id}")
            skipped += 1
            continue

        print(f"  [{i}/{len(videos)}] Downloading: {title[:60]}...")

        url = f"https://www.youtube.com/watch?v={vid_id}"
        cmd = [
            "yt-dlp",
            "--cookies-from-browser", "firefox",
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--output", str(out_dir / f"%(id)s.%(ext)s"),
            "--no-overwrites",
            url,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode == 0:
                downloaded += 1
            else:
                print(f"    yt-dlp error: {result.stderr[:200]}")
                failed += 1
        except subprocess.TimeoutExpired:
            print(f"    Timeout downloading {vid_id}")
            failed += 1

    print(f"\n  Done: {downloaded} downloaded, {skipped} skipped, {failed} failed")


def main():
    parser = argparse.ArgumentParser(description="Download YouTube videos via yt-dlp")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2e: YouTube Download — {mission.name} ===\n")
    download_videos(mission)


if __name__ == "__main__":
    main()
