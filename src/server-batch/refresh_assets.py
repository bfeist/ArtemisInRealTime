"""Small staged refresh of existing pipelines; bulk transfers stay explicit.

Examples: python refresh_assets.py --mission artemis-ii --stage metadata
          python refresh_assets.py --mission artemis-ii --stage build
          python refresh_assets.py --mission artemis-ii --stage status
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import MISSIONS

# Independent source catalogs first; expensive downloads/transcription excluded.
STAGES = {
    "metadata": [
        ("comm", "1_comm.1a_download_ia_zips", ["--catalog-only"]),
        ("io-photos", "3_photos.3a2_io_photo_catalog", []),
        ("flickr", "3_photos.3b_flickr_albums", []),
        ("nasa-images", "3_photos.3e_images_nasa_gov", []),
        ("eol", "3_photos.3g_eol_json", []),
        ("youtube", "2_video.2d_yt_metadata", []),
        ("ia-io-video", "2_video.2b_ia_video_download", ["--mission-coverage", "--plan-only"]),
        ("pds-discovery", "7_pds.7a_discover", []),
    ],
    "photo-details": [
        ("io-exif", "3_photos.3a3_io_exif_scrape", ["--public-only"]),
        ("io-nhq", "3_photos.3e2_io_nhq_lookup", []),
    ],
    "photo-downloads": [
        ("photo-originals", "3_photos.3f_download_photos", []),
        ("eol-originals", "3_photos.3h_download_eol_photos", []),
    ],
    "build": [
        ("pds-integrate", "7_pds.7f_integrate", []),
        ("exif", "3_photos.4a_extract_all_exif", []),
        ("brackets", "3_photos.4b_detect_brackets", []),
        ("ledger", "3_photos.4c_build_ledger", []),
        ("photo-tiers", "3_photos.4d_generate_tiers", []),
        ("photo-index", "3_photos.4e_web_photos_json", []),
        ("photo-cleanup", "3_photos.4f_cleanup_web", []),
        ("web-media", "prepare_web_media", []),
        ("video-index", "2_video.2g_web_video", []),
        ("comm-index", "1_comm.1c_web_comm", []),
        ("trajectory", "5_trajectory.5a_orion_track", []),
        ("moon", "5_trajectory.5b_moon_ephemeris", []),
        ("trajectory-index", "5_trajectory.5c_web_trajectory", []),
        ("itinerary", "6_itinerary.6a_web_itinerary", []),
    ],
    "status": [("status", "asset_status", [])],
}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def refresh(mission, stage, only=None):
    jobs = STAGES[stage]
    if only:
        unknown = set(only) - {j[0] for j in jobs}
        if unknown:
            raise ValueError(f"Unknown jobs: {sorted(unknown)}")
        jobs = [j for j in jobs if j[0] in only]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = mission.bulk_dir / "processed" / "refresh" / stamp
    root.mkdir(parents=True)
    run = {"mission": mission.slug, "stage": stage, "started": now(), "jobs": [], "path": str(root)}
    latest = mission.data_dir / "processed" / "refresh-latest.json"

    # Save small authoritative inputs before catalog replacement or rebuild.
    # Never back up media, EXIF trees, or the multi-terabyte PDS archive here.
    candidates = [mission.photos_ledger_path, mission.eol_json_path,
                  mission.raw_photos_flickr / "album_metadata.json",
                  mission.raw_photos_nasa / "catalog.json"]
    for base in (mission.io_cache, mission.data_dir / "processed", mission.web_dir):
        if base.exists():
            candidates.extend(p for p in base.iterdir() if p.is_file() and p.suffix in {".json", ".jsonl", ".csv"})
    for path in dict.fromkeys(candidates):
        if path.is_file():
            target = root / "before" / path.relative_to(mission.data_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    for name, module, extra in jobs:
        job = {"name": name, "started": now(), "status": "running", "log": str(root / (name + ".log"))}
        run["jobs"].append(job)
        save(root / "run.json", run)
        save(latest, run)
        print(f"START {name}; log: {job['log']}", flush=True)
        with open(job["log"], "w", encoding="utf-8") as log:
            result = subprocess.run([sys.executable, "-u", "-m", module, "--mission", mission.slug, *extra],
                                    cwd=Path(__file__).parent, stdout=log, stderr=subprocess.STDOUT,
                                    env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        job.update(status="ok" if result.returncode == 0 else "failed", finished=now(), returncode=result.returncode)
        print(f"{job['status'].upper()} {name}", flush=True)
        save(root / "run.json", run)
        save(latest, run)
        # Metadata sources can fail independently. Builds stop at failed inputs.
        if result.returncode and stage != "metadata":
            break
    run["finished"] = now()
    run["status"] = "failed" if any(j["status"] != "ok" for j in run["jobs"]) else "ok"
    save(root / "run.json", run)
    save(latest, run)
    return run["status"] == "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, choices=list(MISSIONS))
    parser.add_argument("--stage", required=True, choices=list(STAGES))
    parser.add_argument("--only", nargs="+", help="Run named jobs within the stage")
    args = parser.parse_args()
    from shared.ia_video_downloads import plan_lock
    mission = MISSIONS[args.mission]
    with plan_lock(mission.bulk_dir / "processed" / "refresh" / (args.stage + ".json")):
        raise SystemExit(0 if refresh(mission, args.stage, args.only) else 1)
