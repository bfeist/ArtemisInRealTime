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
import json
import sys
import time
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS

# Named step groups — expand before individual step resolution.
# These are the most common multi-step workflows.
STEP_GROUPS: dict[str, list[str]] = {
    "pds-plan": ["7a", "7b", "7c", "7g"],
    "pds-fetch": ["7d", "7e", "7g"],
    "pds-integrate": ["7f", "1b3", "1b4", "1c", "4a", "4b", "4c", "4d", "4e", "7g"],
    "pds-refresh": ["7a", "7b", "7c", "7d", "7e", "7f", "1b3", "1b4", "1c", "4a", "4b", "4c", "4d", "4e", "7g"],
    "pcd-comm": ["1b3", "1c"],
    "loops-comm": ["1b4", "1c"],
    "comm-history": ["1e", "1f", "1g", "1h", "1i"],
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
    ("1b3", "1_comm.1b3_pcd_audio", "Import PDS crew PCD audio and transcripts", {"artemis-ii"}),
    ("1b4", "1_comm.1b4_loop_audio", "Import corrected PDS voice-loop transcripts", {"artemis-ii"}),
    ("1c", "1_comm.1c_web_comm", "Publish comm transcripts and reconcile web audio", {"artemis-ii"}),
    ("1d", "1_comm.1d_enrich_transcripts", "Enrich comm + PAO transcripts and discover milestones", {"artemis-ii"}),
    ("1e", "1_comm.1e_recover_enrichment", "Recover rejected enrichment; retain every utterance", {"artemis-ii"}),
    ("1f", "1_comm.1f_investigate_history", "Investigate history claims and missing events against sources", {"artemis-ii"}),
    ("1g", "1_comm.1g_mission_context", "Build hierarchy from investigated mission history", {"artemis-ii"}),
    ("1h", "1_comm.1h_web_timeline", "Publish mission timeline and optional crew tracks", {"artemis-ii"}),
    ("1i", "1_comm.1i_web_transcripts", "Publish enriched comm and PAO transcripts", {"artemis-ii"}),
    ("2a", "2_video.2a_ia_video_discover", "Discover IA video items", None),
    ("2b", "2_video.2b_ia_video_download", "Download IA video MP4s", None),
    ("2d", "2_video.2d_yt_metadata", "Fetch YouTube metadata", None),
    ("2e", "2_video.2e_yt_download", "Download YouTube videos", None),
    ("2f", "2_video.2f_ia_video_metadata", "Build legacy IA metadata", None),
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
    ("7a", "7_pds.7a_discover", "Discover complete public PDS bundles", {"artemis-ii"}),
    ("7b", "7_pds.7b_inventory_local", "Inventory local PDS candidates and ZIPs", {"artemis-ii"}),
    ("7c", "7_pds.7c_reconcile", "Verify reuse and save PDS transfer plan", {"artemis-ii"}),
    ("7d", "7_pds.7d_download", "Resume and verify PDS retrieval on D:", {"artemis-ii"}),
    ("7e", "7_pds.7e_verify", "Verify PDS labels, inventories and coverage", {"artemis-ii"}),
    ("7f", "7_pds.7f_integrate", "Build verified PDS media/science catalogs", {"artemis-ii"}),
    ("7g", "7_pds.7g_report", "Report PDS completeness and pending releases", {"artemis-ii"}),
]


def run_step(
    step_id: str, module_path: str, description: str, mission_slug: str,
    backend: str = "hgx", model: str | None = None,
    reassess_models: list[str] | None = None,
    hide_reasoning: bool = False,
    force: bool = False,
    extraction_reasoning_effort: str | None = None,
    reasoning_effort: str | None = None,
    source_run: str | None = None,
    history_limit: int | None = None,
    retry_unresolved: bool = False,
    max_tokens: int | None = None,
    follow: bool = False,
    poll_seconds: float = 30,
    retry_reasoning_effort: str | None = None,
    retry_max_tokens: int | None = None,
    retry_types: list[str] | None = None,
    retry_batch_size: int | None = None,
) -> bool:
    """Import and run a single step's main() function."""
    print(f"\n{'='*60}")
    print(f"  Step {step_id}: {description}")
    print(f"{'='*60}")

    try:
        # Simulate --mission argument for the step
        sys.argv = ["run_all.py", "--mission", mission_slug]
        if step_id in {"1d", "1e", "1f"}:
            sys.argv.extend(["--backend", backend])
            if model:
                sys.argv.extend(["--model", model])
            if step_id == "1d" and reassess_models:
                sys.argv.extend(["--reassess-model", *reassess_models])
            if hide_reasoning:
                sys.argv.append("--hide-reasoning")
            if step_id == "1d" and force:
                sys.argv.append("--force")
            if step_id == "1d" and extraction_reasoning_effort:
                sys.argv.extend(["--extraction-reasoning-effort", extraction_reasoning_effort])
            if reasoning_effort:
                sys.argv.extend(["--reasoning-effort", reasoning_effort])
            if max_tokens:
                sys.argv.extend(["--max-tokens", str(max_tokens)])
        if step_id in {"1e", "1f"}:
            if history_limit:
                sys.argv.extend(["--limit", str(history_limit)])
            if retry_unresolved:
                sys.argv.append("--retry-unresolved")
        if step_id == "1f":
            if retry_reasoning_effort is not None:
                sys.argv.extend(["--retry-reasoning-effort", retry_reasoning_effort])
            if retry_max_tokens is not None:
                sys.argv.extend(["--retry-max-tokens", str(retry_max_tokens)])
            if retry_types:
                sys.argv.extend(["--retry-types", *retry_types])
            if retry_batch_size is not None:
                sys.argv.extend(["--retry-batch-size", str(retry_batch_size)])
        if step_id == "1e" and source_run:
            sys.argv.extend(["--source-run", source_run])
        if step_id == "1e" and follow:
            sys.argv.extend(["--follow", "--poll-seconds", str(poll_seconds)])
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
    parser.add_argument("--backend", choices=["hgx", "strata"], default="hgx",
                        help="LLM backend for steps 1d/1e/1f (default: hgx)")
    parser.add_argument("--model", default=None, help="Override the LLM model for steps 1d/1e/1f")
    parser.add_argument("--reassess-model", nargs="+", default=[], metavar="MODEL",
                        help="Also reassess step 1d results previously made by these model IDs")
    parser.add_argument("--hide-reasoning", action="store_true",
                        help="Hide reasoning in steps 1d/1e/1f while keeping output")
    parser.add_argument("--force", action="store_true",
                        help="Reassess cached step 1d transcripts; applies only to step 1d")
    parser.add_argument("--extraction-reasoning-effort", choices=["auto", "low", "medium", "high"],
                        help="Step 1d conversation extraction reasoning (default: auto)")
    parser.add_argument("--reasoning-effort", choices=["auto", "low", "medium", "high"],
                        help="Labeling/recovery/investigation reasoning (default: low)")
    parser.add_argument("--source-run", help="1d diagnostics run to recover in 1e (default: latest)")
    parser.add_argument("--history-limit", type=int, help="Limit pending work items per 1e/1f invocation")
    parser.add_argument("--retry-unresolved", action="store_true", help="Retry exhausted 1e/1f work items")
    parser.add_argument("--retry-reasoning-effort", choices=["auto", "low", "medium", "high"],
                        help="Override reasoning on 1f retries (requires --retry-unresolved)")
    parser.add_argument("--retry-max-tokens", type=int,
                        help="Override the output token ceiling on 1f retries (requires --retry-unresolved)")
    parser.add_argument("--retry-types", nargs="+", choices=["coverage", "event", "labels"],
                        help="Only retry these 1f task types (requires --retry-unresolved)")
    parser.add_argument("--retry-batch-size", type=int,
                        help="Split 1f coverage/label retries into small checkpointed groups")
    parser.add_argument("--max-tokens", type=int, help="Override token ceiling for steps 1d/1e/1f")
    parser.add_argument("--follow", action="store_true", help="Allow 1e to recover completed failures while 1d runs")
    parser.add_argument("--poll-seconds", type=float, default=30, help="1e --follow completed-batch polling interval")
    args = parser.parse_args()
    if (args.retry_reasoning_effort is not None or args.retry_max_tokens is not None
            or args.retry_types is not None or args.retry_batch_size is not None):
        if not args.retry_unresolved:
            parser.error("Retry overrides require --retry-unresolved")
        if args.retry_max_tokens is not None and args.retry_max_tokens < 1:
            parser.error("--retry-max-tokens must be positive")
        if args.retry_batch_size is not None and args.retry_batch_size < 1:
            parser.error("--retry-batch-size must be positive")

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
            print(f"  {group:20s} -> {' '.join(members)}")
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
        # Multi-terabyte retrieval requires an explicit PDS step/group selection.
        steps_to_run = [s for s in STEPS if not s[0].startswith("7")]

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
        diagnostic_root = mission.transcript_enrichment / "diagnostics"
        prior_runs = {p.parent.name for p in diagnostic_root.glob("*/run.json")} if step_id == "1d" else set()
        ok = run_step(step_id, module_path, description, args.mission,
                      args.backend, args.model, args.reassess_model, args.hide_reasoning,
                      force=args.force, extraction_reasoning_effort=args.extraction_reasoning_effort,
                      reasoning_effort=args.reasoning_effort, source_run=args.source_run,
                      history_limit=args.history_limit, retry_unresolved=args.retry_unresolved,
                      max_tokens=args.max_tokens, follow=args.follow, poll_seconds=args.poll_seconds,
                      retry_reasoning_effort=args.retry_reasoning_effort, retry_max_tokens=args.retry_max_tokens,
                      retry_types=args.retry_types, retry_batch_size=args.retry_batch_size)
        results.append((step_id, ok))
        if step_id == "1e" and ok and ("1d", False) in results:
            results[results.index(("1d", False))] = ("1d (recovered by 1e)", True)
        if not ok:
            # A completed 1d with rejected batches is precisely what 1e repairs.
            # Never continue after a crash/configuration error or into an older run.
            next_position = len(results)
            if step_id == "1d" and next_position < len(steps_to_run) and steps_to_run[next_position][0] == "1e":
                recent = sorted((p for p in diagnostic_root.glob("*/run.json") if p.parent.name not in prior_runs),
                                key=lambda p: p.parent.name, reverse=True)
                if recent:
                    meta = json.loads(recent[0].read_text(encoding="utf-8"))
                    summaries_path = recent[0].parent / "batches.jsonl"
                    rows = [json.loads(l) for l in summaries_path.read_text(encoding="utf-8").splitlines()] if summaries_path.exists() else []
                    if {r["batch"] for r in rows} == set(range(1, meta["batches"] + 1)):
                        args.source_run = recent[0].parent.name
                        print("  1d finished with rejected rows; continuing to their recovery in 1e.")
                        continue
            if step_id.startswith("7"):
                run_step("7g", "7_pds.7g_report", "Report incomplete PDS run", args.mission)
            break

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
