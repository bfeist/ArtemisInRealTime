"""Step 1c — Produce web-ready comm CSV from transcripts.

Reads per-file transcript JSONs and produces a single pipe-delimited
comm.csv for the web frontend, sorted chronologically.
AAC files are written directly to web/comm/ by step 1b.

Output columns: t|s|d|src|text
  t   — ISO-8601 UTC timestamp of the segment
  s   — segment start offset (seconds) within the AAC file
  d   — segment duration (seconds)
  src — AAC filename (relative to web/comm/)
  text — transcript text

Input:  {data_dir}/{mission}/processed/transcripts/comm/{date}/*.json
Output: {data_dir}/{mission}/web/comm.csv
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

def _offset_utc(utc_str: str, seconds: float) -> str:
    """Return a new ISO-8601 UTC string shifted by `seconds`."""
    dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    dt += timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"  # trim µs to ms


# Known hallucination strings
HALLUCINATIONS = {
    "Thank you.", "Bye.", "...", "Thanks for watching!",
    "Thank you for watching.", "Thank you for watching!",
    "Mmm.", "Hmm.", "Mmmmmmmm.", "MMMMMMMM",
    "Beep.", "BEEP", "Beeping.", "BEEEEEP",
    "BOOOOOM", "BOOOOOM!", "BELL RINGS",
}


def build_web_comm(mission: MissionConfig) -> None:
    if not mission.ia_comm_collection:
        print(f"  No comm audio configured for {mission.name}. Skipping.")
        return

    transcript_dir = mission.processed_transcripts / "comm"
    if not transcript_dir.exists():
        print(f"  Transcript directory not found: {transcript_dir}")
        print("  Run step 1b (transcription) first.")
        return

    # Collect all transcript JSONs, building comm entries and CSV rows
    transcripts = []  # list of (t, s, d, src, text) tuples for comm.csv

    # Hours to add to convert filename-embedded local time → UTC.
    # Applied only to JSONs produced by the old 1b code that did not apply the
    # correction itself (those lack the "tz_corrected": true marker).
    tz_delta = timedelta(hours=mission.comm_tz_offset_hours)

    for json_path in sorted(transcript_dir.rglob("*.json")):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_utc = data.get("utcTime", "")
        # Apply timezone correction for pre-fix JSONs that have local time
        # stored as if it were UTC (missing the tz_corrected flag).
        if raw_utc and tz_delta and not data.get("tz_corrected"):
            corrected = datetime.fromisoformat(raw_utc.replace("Z", "+00:00")) + tz_delta
            base_utc = corrected.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            base_utc = raw_utc
        source = data.get("source", json_path.stem)
        lang = data.get("language", "en")
        aac_name = json_path.stem + ".aac"

        for seg in data.get("segments", []):
            text = seg.get("text", "").strip()
            if not text or text in HALLUCINATIONS:
                continue

            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            seg_utc = _offset_utc(base_utc, seg_start) if base_utc else ""

            transcripts.append((
                seg_utc,
                str(round(seg_start, 3)),
                str(round(seg_end - seg_start, 3)),
                aac_name,
                text,
            ))

    # Sort by UTC (column 0)
    transcripts.sort(key=lambda r: r[0])

    # Write pipe-delimited comm.csv
    out_path = mission.web_dir / "comm.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in transcripts:
            f.write("|".join(row) + "\n")

    print(f"  Saved {len(transcripts)} comm entries to {out_path}")

    # Stats
    if transcripts:
        print(f"  Time range: {transcripts[0][0]} to {transcripts[-1][0]}")
        total_dur = sum(float(r[2]) for r in transcripts)
        print(f"  Total audio duration: {total_dur / 3600:.1f} hours")


def main():
    parser = argparse.ArgumentParser(description="Produce web-ready comm JSON")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 1c: Web Comm JSON — {mission.name} ===\n")
    build_web_comm(mission)


if __name__ == "__main__":
    main()
