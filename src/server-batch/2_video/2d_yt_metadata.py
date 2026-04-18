"""Step 2d — Fetch YouTube livestream metadata via Data API.

Searches for mission-related livestreams on the NASA YouTube channel
and filters to only videos whose title or description actually
contains the mission search terms.

Produces: {data_dir}/{mission}/processed/yt_metadata.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, YOUTUBE_API_KEY, MissionConfig

try:
    from googleapiclient.discovery import build
except ImportError:
    print("google-api-python-client not installed. Run: uv pip install google-api-python-client")
    sys.exit(1)


NASA_CHANNEL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"


def search_livestreams(mission: MissionConfig) -> list[dict]:
    """Search YouTube for completed livestreams matching mission terms."""
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    all_videos: list[dict] = []
    seen_ids: set[str] = set()

    for term in mission.yt_search_terms:
        print(f"  Searching YouTube for: '{term}'")
        next_page = None

        while True:
            request = youtube.search().list(
                q=term,
                part="id,snippet",
                type="video",
                eventType="completed",
                channelId=NASA_CHANNEL_ID,
                maxResults=50,
                pageToken=next_page,
            )
            response = request.execute()

            for item in response.get("items", []):
                vid_id = item["id"]["videoId"]
                if vid_id not in seen_ids:
                    seen_ids.add(vid_id)
                    all_videos.append({
                        "video_id": vid_id,
                        "title": item["snippet"]["title"],
                        "published_at": item["snippet"]["publishedAt"],
                        "description": item["snippet"].get("description", ""),
                        "search_term": term,
                    })

            next_page = response.get("nextPageToken")
            if not next_page:
                break

        print(f"    Found {len(seen_ids)} unique videos so far")

    # Fetch detailed metadata (duration, live timing) for each video
    if all_videos:
        print(f"\n  Fetching detailed metadata for {len(all_videos)} videos...")
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

        # Process in batches of 50 (API limit)
        for batch_start in range(0, len(all_videos), 50):
            batch = all_videos[batch_start : batch_start + 50]
            ids = ",".join(v["video_id"] for v in batch)

            request = youtube.videos().list(
                id=ids,
                part="contentDetails,liveStreamingDetails,snippet",
            )
            response = request.execute()

            details_map = {item["id"]: item for item in response.get("items", [])}

            for video in batch:
                detail = details_map.get(video["video_id"], {})
                live = detail.get("liveStreamingDetails", {})
                snippet = detail.get("snippet", {})
                video["duration"] = detail.get("contentDetails", {}).get("duration")
                video["actual_start_time"] = live.get("actualStartTime")
                video["actual_end_time"] = live.get("actualEndTime")
                # Update description from detailed snippet (search snippet is truncated)
                if snippet.get("description"):
                    video["description"] = snippet["description"]

    # Filter to only videos whose title or description matches a search term.
    # Use word-boundary matching so "Artemis I" doesn't match "Artemis II".
    patterns = [
        re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        for term in mission.yt_search_terms
    ]

    # Build patterns for OTHER missions to exclude cross-contamination.
    # If a video's title explicitly names a different mission, skip it.
    other_patterns = []
    for key, other_mission in MISSIONS.items():
        if key == mission.slug:
            continue
        for term in other_mission.yt_search_terms:
            other_patterns.append(
                re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
            )

    filtered = []
    for video in all_videos:
        title = video["title"]
        text = title + " " + video.get("description", "")

        if not any(p.search(text) for p in patterns):
            continue  # no match at all

        # If title explicitly names a DIFFERENT mission but NOT this one, skip.
        title_has_ours = any(p.search(title) for p in patterns)
        title_has_other = any(p.search(title) for p in other_patterns)
        if title_has_other and not title_has_ours:
            continue

        filtered.append(video)

    print(f"  Filtered to {len(filtered)} mission-relevant videos "
          f"(dropped {len(all_videos) - len(filtered)} unrelated)")

    # Filter to mission date window ± 1 day buffer
    buf = timedelta(days=1)
    win_start = datetime.strptime(mission.mission_start, "%Y-%m-%d").replace(tzinfo=timezone.utc) - buf
    win_end = datetime.strptime(mission.mission_end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + buf + timedelta(days=1)  # end of day

    date_filtered = []
    date_dropped = []
    for video in filtered:
        # Use actual_start_time (livestream) or published_at (fallback)
        ts = video.get("actual_start_time") or video.get("published_at", "")
        if not ts:
            date_filtered.append(video)  # keep if no date (shouldn't happen)
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if win_start <= dt <= win_end:
                date_filtered.append(video)
            else:
                date_dropped.append(video)
        except (ValueError, TypeError):
            date_filtered.append(video)  # keep if unparseable

    if date_dropped:
        print(f"  Date-filtered to {len(date_filtered)} videos within "
              f"{mission.mission_start} ± 1 day "
              f"(dropped {len(date_dropped)} outside mission window):")
        for v in date_dropped:
            ts = v.get("actual_start_time") or v.get("published_at", "?")
            print(f"    - {ts[:10]} {v['title'][:60]}")
    else:
        print(f"  All {len(date_filtered)} videos within mission date window")

    return date_filtered


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube livestream metadata")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2d: YouTube Metadata — {mission.name} ===\n")

    videos = search_livestreams(mission)

    out_path = mission.data_dir / "processed" / "yt_metadata.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)

    print(f"\n  Saved {len(videos)} videos to {out_path}")


if __name__ == "__main__":
    main()
