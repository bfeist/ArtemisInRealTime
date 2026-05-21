"""ArtemisInRealTime — Pipeline runner.

Usage (recommended — via uv):
    uv run run_all.py --mission artemis-ii              # Run all steps
    uv run run_all.py --mission artemis-ii --step 2a    # Run single step
    uv run run_all.py --mission artemis-i --step 2a 2b  # Run multiple steps

    # Named step groups (shortcuts for common workflows):
    uv run run_all.py --mission artemis-ii --step photos-refresh
        Re-fetch Flickr/NASA/EOL metadata, download any new originals,
        regenerate EXIF, ledger, tiers, and web JSON.  Safe to run at any
        time — all download steps are idempotent (skip existing files).

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

# Named step groups — expand before individual step resolution.
# These are the most common multi-step workflows.
STEP_GROUPS: dict[str, list[str]] = {
    # Re-check all external photo sources for new arrivals, download them,
    # and rebuild the ledger + web tiers + photos.json.
    # All download steps are idempotent — safe to re-run at any time.
    "photos-refresh": ["3b", "3e", "3g_eol", "3f", "3h", "4a", "4b", "4c", "4d", "4e"],
    # Re-scrape the IO photo catalog and EXIF overrides, then rebuild the ledger
    # and web output.  Run this when IO adds new records or corrects timezone data.
    "io-refresh": ["3a2", "3a3", "4c", "4d", "4e"],
}

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
    # ── 2h/2i — standalone scripts (no --mission arg); run directly via uv ───
    # uv run 2_video/2h_split_mkv.py --input "D:/..." --output-dir "D:/chunks"
    # uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks"
    ("2j", "2_video.2j_transcribe_yt",           "Transcribe YT chunks (WhisperX + diarization)", {"artemis-ii"}),
    ("2k", "2_video.2k_filter_yt_transcript",     "Filter YT transcript; produce combined web transcript", {"artemis-ii"}),
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
    ("4f", "3_photos.4f_cleanup_web",      "Delete stale web/ files (run after 4e)", None),
    # ── 5* — trajectory ──
    ("5a", "5_trajectory.5a_orion_track",     "Build canonical Orion track from OEM/Horizons", None),
    ("5b", "5_trajectory.5b_moon_ephemeris",  "Compute geocentric Moon at every Orion sample", None),
    ("5c", "5_trajectory.5c_web_trajectory",  "Emit web/ephemeris/trajectory.json", None),
    ("6a", "6_itinerary.6a_web_itinerary",   "Emit web/itinerary.json",  None),
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
    except SystemExit as e:
        if e.code == 0:
            return True
        print(f"\n  ERROR in step {step_id}: sys.exit({e.code})")
        return False
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
        print(f"\nNamed step groups:")
        for group, members in STEP_GROUPS.items():
            print(f"  {group:20s} → {' '.join(members)}")
        return

    mission.ensure_dirs()

    # Filter steps — expand any named group tokens first.
    if args.step:
        expanded: list[str] = []
        for token in args.step:
            if token in STEP_GROUPS:
                expanded.extend(STEP_GROUPS[token])
            else:
                expanded.append(token)
        requested = list(dict.fromkeys(expanded))  # deduplicate, preserve order
        requested_set = set(requested)
        known_ids = {s for s, _, _, _ in STEPS}
        unknown = requested_set - known_ids
        if unknown:
            print(f"Unknown steps: {unknown}")
            print(f"Available: {sorted(known_ids)}")
            print(f"Available groups: {list(STEP_GROUPS)}")
            sys.exit(1)
        steps_to_run = [(s, m, d, a) for sid in requested for s, m, d, a in STEPS if s == sid]
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
