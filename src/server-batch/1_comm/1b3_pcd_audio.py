"""Step 1b3: import PDS crew PCD audio and supplied transcripts into comm."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS
from shared.pds_comm import import_pcd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, choices=list(MISSIONS))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--test", type=int, metavar="N")
    args = parser.parse_args()
    if args.test is not None and args.test < 0:
        parser.error("--test must be nonnegative")
    if args.mission == "artemis-ii":
        import_pcd(MISSIONS[args.mission], force=args.force, limit=args.test)


if __name__ == "__main__":
    main()
