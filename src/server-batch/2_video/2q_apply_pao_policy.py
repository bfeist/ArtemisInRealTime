"""Stage reviewed PAO playback policy in a new canonical dataset. Never activate."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.pao_policy import apply_dataset_policy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("dataset-manifest", "coverage-manifest", "sync-manifest", "part-manifest", "output"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--audio-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_dataset_policy(args.dataset_manifest, args.coverage_manifest, args.sync_manifest, args.part_manifest, args.output, args.audio_root), indent=2))
