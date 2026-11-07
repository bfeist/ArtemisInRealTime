"""Import existing local split/upload evidence without uploading or decoding media."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.pao_sync import import_upload_state, part_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--stream-start-utc", required=True)
    parser.add_argument("--chunk-seconds", type=float, default=28800)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if "processed" not in args.output_dir.resolve().parts:
        parser.error("output must be under processed")
    if any((args.output_dir / name).exists() for name in ("video-parts-manifest.json", "video-parts.json")):
        parser.error("refusing to replace existing part evidence")
    parts = []
    for path in sorted(args.input_dir.glob("*_part*.mp4")):
        number = int(re.search(r"_part(\d+)\.mp4$", path.name).group(1))
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,start_time:stream=codec_type,start_time,duration", "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=30)
        metadata = json.loads(probe.stdout)
        parts.append({"part": number, "filename": path.name,
                      "originalStartSeconds": (number - 1) * args.chunk_seconds,
                      "offsetVerification": "candidate-requested-split-offset-not-measured-cut",
                      "durationSeconds": float(metadata["format"]["duration"]),
                      "bytes": path.stat().st_size, "probe": metadata})
    manifest = import_upload_state({"schemaVersion": 1, "originalVideoId": "m3kR2KK8TEs", "parts": parts}, args.input_dir / ".yt_upload_state.json")
    catalog = part_catalog(manifest, args.stream_start_utc)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Refuse replacement of prior evidence.
    for name, data in [("video-parts-manifest.json", manifest), ("video-parts.json", catalog)]:
        with (args.output_dir / name).open("x", encoding="utf-8") as target:
            json.dump(data, target, indent=2, sort_keys=True)
            target.write("\n")


if __name__ == "__main__":
    main()
