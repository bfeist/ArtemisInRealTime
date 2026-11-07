"""Prepare compact PAO audio from existing YouTube parts; no ASR or model calls."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS
from shared.pao_audio import digest, prepare_pao_audio
from shared.ia_video_downloads import plan_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", choices=list(MISSIONS), default="artemis-ii")
    parser.add_argument("--input-dir", type=Path, required=True, help="Existing MP4 parts used by cached 2j transcripts")
    parser.add_argument("--dataset-manifest", type=Path, help="Accepted dataset; otherwise read current web release")
    parser.add_argument("--stream-start-utc", help="Historical timestamp baseline; defaults to cached _stream_start.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    mission = MISSIONS[args.mission]
    if args.dataset_manifest:
        dataset = json.loads(args.dataset_manifest.read_text(encoding="utf-8"))
        if dataset.get("contract") != "legacy-transcript-import-v1" or not dataset.get("report", {}).get("complete"):
            raise ValueError("Accepted dataset is incomplete or unsupported")
        artifact = dataset["artifacts"]["published_transcripts"]
        source = (args.dataset_manifest.parent / artifact["path"]).resolve()
        if args.dataset_manifest.parent.resolve() not in source.parents or digest(source) != artifact["sha256"]:
            raise ValueError("Accepted transcript artifact failed validation")
    else:
        root = mission.web_dir
        pointer = root / "active-release.json"
        if pointer.exists():
            data = json.loads(pointer.read_text(encoding="utf-8"))
            root = root / data["base_url"]
        source = root / "transcripts.json"
    rows = json.loads(source.read_text(encoding="utf-8"))["pao"]
    stream_start = args.stream_start_utc
    if not stream_start:
        saved = mission.processed_transcripts / "yt" / "_stream_start.json"
        stream_start = json.loads(saved.read_text(encoding="utf-8"))["stream_start_utc"]
    with plan_lock(mission.processed_transcripts / "pao_audio/manifest.json"):
        report = prepare_pao_audio(mission, args.input_dir, rows, stream_start=stream_start,
            workers=args.workers, plan_only=args.plan_only)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
