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


def prepared_media(mission):
    path = mission.data_dir / "processed" / "web-media.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_web_video_pds(mission: MissionConfig) -> None:
    """Expose each verified PDS channel/version without changing IA/YT catalogs."""
    from urllib.parse import quote
    from shared.pds import atomic_json
    catalog = mission.data_dir / "processed" / "pds" / "video.jsonl"
    if not catalog.exists():
        return
    entries = []
    prepared = prepared_media(mission)
    for line in catalog.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        media = prepared.get("pds:" + record["uri"], {})
        ready = media.get("status") == "ready" and Path(media["path"]).is_file()
        entries.append({"id": record.get("lidvid") or record["uri"], "source": "pds",
            "title": Path(record["path"]).stem, "nasaId": record.get("nasa_id"), "channel": record.get("channel"),
            "startTime": record.get("start_utc") or "", "endTime": record.get("stop_utc") or "",
            "duration": record.get("duration_seconds"), "codec": record.get("codec"),
            "sourceUrl": "https://pds-imaging.jpl.nasa.gov/api/data/" + quote(record["uri"], safe=":/"),
            "localUrl": ("/artemis-assets/" + mission.slug + "/web" + media["url"]) if ready else "",
            "webUrl": media["url"] if ready else "", "webStatus": "ready" if ready else "needs_preparation",
            "assurance": record["assurance"]})
    entries.sort(key=lambda e: (e["startTime"], e["channel"] or "", e["id"]))
    atomic_json(mission.web_dir / "videoPDS.json", entries)
    print(f"  Saved {len(entries)} PDS video entries to {mission.web_dir / 'videoPDS.json'}")


def build_web_video_ia(mission: MissionConfig) -> None:
    """Build web-ready IA video JSON from ia_video_metadata.json (step 2f)."""
    plan_path = mission.data_dir / "processed" / "ia_video_downloads.json"
    meta_path = plan_path if plan_path.exists() else mission.data_dir / "processed" / "ia_video_metadata.json"
    if not meta_path.exists():
        print("  No IA video metadata found. Run step 2f first.")
        return

    with open(meta_path, "r", encoding="utf-8") as f:
        ia_items = json.load(f)
    if isinstance(ia_items, dict):
        ia_items = [r for r in ia_items["records"] if r.get("status") == "complete"]

    entries = []
    prepared = prepared_media(mission)
    for item in ia_items:
        identifier = item["identifier"]
        entry = {
            "id": identifier,
            "title": item.get("title", identifier),
            "source": "ia",
            "sourceUrl": item.get("source_url") or f"https://archive.org/details/{identifier}",
            "thumbnailUrl": f"https://archive.org/services/img/{identifier}",
            "startTime": item.get("recorded_at") or "",
            "duration": item.get("duration_seconds") or item.get("duration") or 0,
            "description": item.get("description", ""),
        }
        media = prepared.get("ia:" + identifier, {})
        if media.get("status") == "ready" and Path(media["path"]).is_file():
            entry["webUrl"] = media["url"]
            entry["localUrl"] = "/artemis-assets/" + mission.slug + "/web" + media["url"]
        entries.append(entry)

    # Sort by startTime (entries without timestamps go to the end)
    entries.sort(key=lambda x: x.get("startTime") or "9999")

    out_path = mission.web_dir / "videoIA.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    print(f"  Saved {len(entries)} IA video entries to {out_path}")


def build_web_pds_audio_transcripts(mission):
    """Publish separate PDS indexes; don't overwrite comm/PAO alignment."""
    from shared.io_api import load_jsonl
    from shared.pds import atomic_json
    prepared = prepared_media(mission)
    for family, filename in (("audio", "audioPDS.json"), ("transcripts", "transcriptsPDS.json")):
        entries = []
        for r in load_jsonl(mission.data_dir / "processed" / "pds" / (family + ".jsonl")):
            media = prepared.get("pds:" + r["uri"], {})
            if media.get("status") == "ready" and Path(media["path"]).is_file():
                entries.append({"id": r.get("lidvid") or r["uri"], "title": Path(r["path"]).name,
                                "startTime": r.get("start_utc"), "endTime": r.get("stop_utc"),
                                "channel": r.get("channel"), "source": "pds", "webUrl": media["url"],
                                "assurance": r["assurance"]})
        atomic_json(mission.web_dir / filename, entries)
        print(f"  Saved {len(entries)} entries to {filename}")


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
    build_web_video_pds(mission)
    build_web_pds_audio_transcripts(mission)
    # IO supplies timing, but only confirmed public recordings reach web output.
    import importlib
    from shared.io_api import load_jsonl
    importlib.import_module("2_video.2c_io_nasatv_download").build_web_videos(
        mission, load_jsonl(mission.io_cache / "io_nasatv_catalog.jsonl"))


if __name__ == "__main__":
    main()
