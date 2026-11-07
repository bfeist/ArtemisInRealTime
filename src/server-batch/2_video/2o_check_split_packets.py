"""Bounded read-only AAC packet comparison of existing stream-copy splits.

Exact packet hashes establish media offsets, not a mission UTC/audio handoff.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.pao_sync import packet_offset_evidence


def packets(path, start, seconds):
    command = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-read_intervals", f"{start}%+{seconds}", "-show_packets", "-show_entries", "packet=pts_time,data_hash", "-show_data_hash", "sha256", "-of", "json", str(path)]
    return json.loads(subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout).get("packets", [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--parts-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if "processed" not in args.output.resolve().parts:
        parser.error("output must be under processed")
    if args.output.exists():
        parser.error("refusing to replace existing packet evidence")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    findings = []
    for part in manifest["parts"]:
        nominal = part["originalStartSeconds"]
        original = packets(args.original, max(0, nominal - 6), 14)
        chunk = packets(args.parts_dir / part["filename"], 0, 2)
        findings.append({"part": part["part"], "filename": part["filename"], **packet_offset_evidence(original, chunk), "requestedSplitOffsetSeconds": nominal})
    with args.output.open("x", encoding="utf-8") as target:
        json.dump({"schemaVersion": 1, "method": "unique-sha256-aac-packets-first-two-seconds-vs-original-14-second-window", "utcVerification": "candidate-unreviewed", "parts": findings}, target, indent=2, sort_keys=True)
        target.write("\n")


if __name__ == "__main__":
    main()
