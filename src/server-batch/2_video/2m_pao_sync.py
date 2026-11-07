"""Read-only PAO coverage planner. No ASR, model, download or upload imports.

The only writes are explicitly requested new processed report artifacts.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.pao_sync import cached_recordings, plan_coverage, compact_sync_projection, part_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ia-dir", type=Path, required=True)
    parser.add_argument("--yt-dir", type=Path, required=True)
    parser.add_argument("--part-manifest", type=Path)
    parser.add_argument("--sync-manifest", type=Path)
    parser.add_argument("--stream-start-utc")
    parser.add_argument("--scope-start", required=True)
    parser.add_argument("--scope-end", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8")) if path else None
    parts = load(args.part_manifest)
    recordings, untimed = cached_recordings(args.ia_dir, args.yt_dir, parts, args.stream_start_utc)
    report = {"schemaVersion": 1, "status": "candidate-review-required", "recordings": recordings,
              "untimedRecordings": untimed,
              "coverage": plan_coverage(recordings, [{"feed": "pao-primary", "startUtc": args.scope_start, "endUtc": args.scope_end}]),
              "sync": compact_sync_projection(load(args.sync_manifest) or {}, parts or {"parts": []})}
    if parts and args.stream_start_utc:
        report["partCatalog"] = part_catalog(parts, args.stream_start_utc)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        if "processed" not in args.output.resolve().parts:
            parser.error("report output must be under processed")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as target:
            target.write(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
