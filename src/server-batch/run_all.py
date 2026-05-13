"""ArtemisInRealTime — Pipeline runner.

Usage (recommended — via uv):
    uv run run_all.py --mission artemis-ii              # Run all steps
    uv run run_all.py --mission artemis-ii --step 2a    # Run single step
    uv run run_all.py --mission artemis-i --step 2a 2b  # Run multiple steps

    # Run an individual module directly
    uv run python -m 2_video.2a_ia_video_discover --mission artemis-ii

Usage (after manual uv sync):
    python run_all.py --mission artemis-ii
"""

import argparse
import importlib
import sys
import time
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS

# Step registry: (step_id, module_path, description, missions)
# missions: None = all, or set of slugs
STEPS = [
    ("1a", "1_comm.1a_download_ia_zips", "Download comm audio ZIPs", {"artemis-ii"}),
    ("1b", "1_comm.1b_transcribe", "Transcribe comm audio (WhisperX)", {"artemis-ii"}),
    ("1c", "1_comm.1c_web_comm", "Produce web-ready comm JSON", {"artemis-ii"}),
    ("2a", "2_video.2a_ia_video_discover", "Discover IA video items", None),
    ("2b", "2_video.2b_ia_video_download", "Download IA video MP4s", None),
    ("2d", "2_video.2d_yt_metadata", "Fetch YouTube metadata", None),
    ("2e", "2_video.2e_yt_download", "Download YouTube videos", None),
    ("2g", "2_video.2g_web_video", "Produce web-ready video JSON", None),
    ("3a", "3_photos.3a_ia_stills_download", "Download IA stills", None),
    ("3a2", "3_photos.3a2_io_photo_catalog", "Scrape IO photo collections", None),
    ("3a3", "3_photos.3a3_io_exif_scrape", "Scrape IO EXIF for timezone corrections", None),
    ("3b", "3_photos.3b_flickr_albums", "Fetch Flickr album metadata", None),
    ("3e", "3_photos.3e_images_nasa_gov", "Search images.nasa.gov", None),
    ("3e2", "3_photos.3e2_io_nhq_lookup", "Reverse-lookup NHQ photos in IO", None),
    ("3f", "3_photos.3f_download_photos", "Download full-res photo originals", None),
    ("3g_eol", "3_photos.3g_eol_json", "Fetch EOL crew photo metadata", {"artemis-ii"}),
    ("3h", "3_photos.3h_download_eol_photos", "Download EOL large images to disk", {"artemis-ii"}),
    ("3i", "3_photos.3i_eol_rename_canonical", "Rename EOL JPEGs to canonical NASA IDs (one-shot migration)", {"artemis-ii"}),
    # ── 4* — refactored photo pipeline (see docs/PHOTOS_EXPLAINED.md) ──
    ("4a", "3_photos.4a_extract_all_exif", "Per-copy EXIF (raw_crew via ExifTool, JPEGs via PIL)", None),
    ("4b", "3_photos.4b_detect_brackets",  "Detect AEB bracket sets from EXIF", None),
    ("4c", "3_photos.4c_build_ledger",     "Build canonical per-NASA-ID photo ledger", None),
    ("4d", "3_photos.4d_generate_tiers",   "Generate web tier JPEGs (thumb/lowres/hires)", None),
    ("4e", "3_photos.4e_web_photos_json",  "Emit web/photos.json from the ledger", None),
]


def run_step(step_id: str, module_path: str, description: str, mission_slug: str) -> bool:
    """Import and run a single step's main() function."""
    print(f"\n{'='*60}")
    print(f"  Step {step_id}: {description}")
    print(f"{'='*60}")

    try:
        # Simulate --mission argument for the step
        sys.argv = ["run_all.py", "--mission", mission_slug]
        module = importlib.import_module(module_path)
        # Reload in case it was already imported
        importlib.reload(module)
        module.main()
        return True
    except Exception as e:
        print(f"\n  ERROR in step {step_id}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="ArtemisInRealTime data ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
        help="Mission slug",
    )
    parser.add_argument(
        "--step",
        nargs="*",
        help="Specific step(s) to run (e.g. 2a 2b). Omit to run all.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_steps",
        help="List available steps and exit",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]

    if args.list_steps:
        print(f"\nAvailable steps for {mission.name}:\n")
        for step_id, _, desc, allowed_missions in STEPS:
            if allowed_missions and args.mission not in allowed_missions:
                status = "(skipped — not applicable)"
            else:
                status = ""
            print(f"  {step_id:6s} {desc} {status}")
        return

    mission.ensure_dirs()

    # Filter steps
    if args.step:
        requested = set(args.step)
        steps_to_run = [(s, m, d, a) for s, m, d, a in STEPS if s in requested]
        unknown = requested - {s for s, _, _, _ in STEPS}
        if unknown:
            print(f"Unknown steps: {unknown}")
            print(f"Available: {[s for s, _, _, _ in STEPS]}")
            sys.exit(1)
    else:
        steps_to_run = STEPS

    # Filter by mission applicability
    steps_to_run = [
        (s, m, d, a) for s, m, d, a in steps_to_run
        if a is None or args.mission in a
    ]

    print(f"\n{'#'*60}")
    print(f"  ArtemisInRealTime Data Ingestion — {mission.name}")
    print(f"  Steps: {', '.join(s for s, _, _, _ in steps_to_run)}")
    print(f"{'#'*60}")

    results: list[tuple[str, bool]] = []
    start = time.time()

    for step_id, module_path, description, _ in steps_to_run:
        ok = run_step(step_id, module_path, description, args.mission)
        results.append((step_id, ok))

    elapsed = time.time() - start

    # Summary
    print(f"\n{'='*60}")
    print(f"  Pipeline complete — {elapsed:.1f}s")
    print(f"{'='*60}")
    for step_id, ok in results:
        status = "OK" if ok else "FAILED"
        print(f"  {step_id:6s} {status}")

    if any(not ok for _, ok in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
