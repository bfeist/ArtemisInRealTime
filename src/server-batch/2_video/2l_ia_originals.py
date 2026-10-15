"""Retrieve only the explicitly approved originals lacking an IA MP4.

Reads the saved MP4 coverage plan; uses a separate manifest and bulk directory.
This does not select any of the excluded highlights or editorial recordings.
"""

import argparse
import json
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS
from shared.ia_helpers import get_item_metadata
from shared.ia_video_downloads import add_retry_arguments, atomic_json, fetch_media, plan_lock, run_transfers

RESERVE_BYTES = 50 << 30


def build_original_plan(source_path, mission, output_dir, workers=4):
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source["mission"] != mission.slug:
        raise ValueError("Source MP4 plan belongs to another mission")
    missing = [r for r in source["records"] if r["status"] == "no_mp4_available"]

    def inspect(record):
        meta = get_item_metadata(record["identifier"])
        if not meta:
            return dict(record, status="metadata_failed")
        candidates = [f for f in meta.get("files", [])
                      if f.get("source") == "original" and not f.get("private")
                      and f.get("name", "").lower().endswith(".mxf")
                      and int(f.get("size") or 0) > 0]
        # All 34 audited recordings have one MXF. Avoid choosing arbitrarily
        # if an item later contains multiple originals.
        exact = [f for f in candidates if f["name"] == record["identifier"]]
        if len(exact) == 1:
            selected = exact[0]
        elif len(candidates) == 1:
            selected = candidates[0]
        else:
            return dict(record, status="original_selection_unresolved")
        if not selected.get("md5"):
            return dict(record, status="original_checksum_missing")
        filename = Path(selected["name"]).name
        return dict(record, status="pending", filename=filename,
                    bytes=int(selected["size"]), md5=selected["md5"],
                    media_kind="original", needs_conversion=True,
                    download_url=f"https://archive.org/download/{quote(record['identifier'])}/{quote(selected['name'], safe='/')}",
                    local_path=str(output_dir / filename))

    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, future in enumerate(as_completed([pool.submit(inspect, r) for r in missing]), 1):
            records.append(future.result())
            print(f"  Inspected original {i}/{len(missing)}", flush=True)
    records.sort(key=lambda r: (r.get("recorded_at") or "", r["identifier"]))
    paths = [r["local_path"] for r in records if r.get("local_path")]
    if len(paths) != len(set(paths)):
        raise ValueError("Original destination paths collide")
    return {"mission": mission.slug, "media_kind": "original", "source_plan": str(source_path),
            "created_at": datetime.now(timezone.utc).isoformat(), "output_dir": str(output_dir),
            "records": records, "status": "prepared"}


def run_original_plan(path, workers=2, max_files=None, retry_rounds=0, retry_delay=300):
    plan = json.loads(path.read_text(encoding="utf-8"))
    records = plan["records"]
    indices = [i for i, r in enumerate(records) if r.get("local_path")
               and r["status"] not in {"complete", "unneeded"}]
    if max_files is not None:
        indices = indices[:max_files]

    def download(record):
        dest = Path(record["local_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        remaining = record["bytes"] - (part.stat().st_size if part.exists() else 0)
        # Reserve enough capacity for this recording; limit parallel originals
        # to two so conversion staging stays manageable.
        if shutil.disk_usage(dest.parent).free < max(0, remaining) + RESERVE_BYTES:
            return dict(record, status="waiting_for_space")
        return fetch_media(record, allowed_extensions={".mxf"})

    return run_transfers(path, download, indices, workers, retry_rounds, retry_delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, choices=list(MISSIONS))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2))
    parser.add_argument("--max-files", type=int, help="Start only this many originals, leaving the rest queued")
    add_retry_arguments(parser)
    args = parser.parse_args()
    if args.max_files is not None and args.max_files < 1:
        parser.error("--max-files must be positive")
    if args.retry_rounds < 0 or args.retry_delay < 1:
        parser.error("--retry-rounds must be nonnegative and --retry-delay positive")
    mission = MISSIONS[args.mission]
    source_path = mission.data_dir / "processed" / "ia_video_downloads.json"
    plan_path = mission.data_dir / "processed" / "ia_video_originals.json"
    with plan_lock(plan_path):
        if args.resume or plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if plan["mission"] != mission.slug or plan.get("media_kind") != "original":
                parser.error("Invalid original plan")
            if args.output_dir:
                # Keep existing partials/completed files in place. Redirect only
                # recordings that have not started; no large-file moving.
                for r in plan["records"]:
                    if not r.get("local_path"):
                        continue
                    dest = Path(r["local_path"])
                    if not dest.exists() and not dest.with_suffix(dest.suffix + ".part").exists():
                        r["local_path"] = str(args.output_dir / r["filename"])
                plan["output_dir"] = str(args.output_dir)
        else:
            output_dir = args.output_dir or mission.bulk_dir / "raw" / "video" / "ia_originals"
            plan = build_original_plan(source_path, mission, output_dir)
        atomic_json(plan_path, plan)
        print(f"  Original plan: {dict(Counter(r['status'] for r in plan['records']))}", flush=True)
        print(f"  Total bytes: {sum(r.get('bytes', 0) for r in plan['records']):,}", flush=True)
        print(f"  Saved: {plan_path}", flush=True)
        if not args.prepare_only:
            if not run_original_plan(plan_path, workers=args.workers, max_files=args.max_files,
                                     retry_rounds=args.retry_rounds, retry_delay=args.retry_delay):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
