"""Step 2b — Download MP4s from discovered IA video items.

Reads:    {data_dir}/{mission}/processed/ia_video_catalog.json
Downloads to: {data_dir}/{mission}/raw/video/ia/
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.ia_helpers import download_file, find_best_mp4, get_item_files


def download_videos(mission: MissionConfig) -> None:
    catalog_path = mission.data_dir / "processed" / "ia_video_catalog.json"
    if not catalog_path.exists():
        print(f"  No catalog found at {catalog_path}. Run step 2a first.")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"  {len(items)} items in catalog")

    downloaded = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(items, 1):
        ident = item["identifier"]
        print(f"\n  [{i}/{len(items)}] {ident}")

        # Check if already downloaded
        existing = list(mission.raw_video_ia.glob(f"{ident}*"))
        if existing:
            print(f"    Already downloaded: {existing[0].name}")
            skipped += 1
            continue

        # Get file list and find best MP4
        files = get_item_files(ident)
        if not files:
            print(f"    No files found")
            failed += 1
            continue

        result = find_best_mp4(files, ident)
        if result is None:
            print(f"    No MP4 found in {len(files)} files")
            failed += 1
            continue

        url, filename = result
        dest = mission.raw_video_ia / filename
        print(f"    Downloading: {filename}")

        ok = download_file(url, dest, desc=ident)
        if ok:
            downloaded += 1
        else:
            failed += 1

        time.sleep(0.5)

    print(f"\n  Done: {downloaded} downloaded, {skipped} skipped, {failed} failed")


def main():
    from shared.ia_video_downloads import add_retry_arguments
    parser = argparse.ArgumentParser(description="Download IA video MP4s")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    parser.add_argument("--mission-coverage", action="store_true",
                        help="Refresh IA/IO catalogs and download mission coverage MP4s, excluding standalone recaps")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9),
                        help="Concurrent mission MP4 transfers (default: 4)")
    parser.add_argument("--plan-only", action="store_true", help="Save mission coverage plan without downloading")
    parser.add_argument("--download-plan", type=Path,
                        help="Resume an existing mission coverage plan without refreshing catalogs")
    add_retry_arguments(parser)
    args = parser.parse_args()
    if args.retry_rounds < 0 or args.retry_delay < 1:
        parser.error("--retry-rounds must be nonnegative and --retry-delay positive")

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2b: IA Video Download — {mission.name} ===\n")
    if args.download_plan or args.mission_coverage:
        from shared.ia_video_downloads import atomic_json, build_plan, plan_lock, run_plan
        from collections import Counter
        plan_path = args.download_plan or mission.data_dir / "processed" / "ia_video_downloads.json"
        with plan_lock(plan_path):
            if args.download_plan:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if plan["mission"] != mission.slug:
                    parser.error("Download plan belongs to a different mission")
            else:
                plan = build_plan(mission, workers=args.workers)
                atomic_json(plan_path, plan)
            print(f"  Plan: {dict(Counter(r['status'] for r in plan['records']))}", flush=True)
            print(f"  Excluded: {dict(Counter(r['reason'] for r in plan['excluded']))}", flush=True)
            print(f"  Selected MP4 bytes: {sum(r.get('bytes', 0) for r in plan['records'] if r['status'] != 'unneeded'):,}", flush=True)
            print(f"  Plan saved: {plan_path}", flush=True)
            if not args.plan_only and not run_plan(plan_path, workers=args.workers,
                                                 retry_rounds=args.retry_rounds,
                                                 retry_delay=args.retry_delay):
                raise SystemExit(1)
        return
    if args.plan_only:
        parser.error("--plan-only requires --mission-coverage or --download-plan")
    download_videos(mission)


if __name__ == "__main__":
    main()
