"""Step 1b4: import authoritative PDS voice-loop transcripts and playback audio."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS
from shared.pds_loops import import_loops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, choices=list(MISSIONS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.mission == "artemis-ii":
        import_loops(MISSIONS[args.mission], force=args.force)


if __name__ == "__main__":
    main()
