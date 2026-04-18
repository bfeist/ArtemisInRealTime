"""Step 1c — Produce web-ready comm JSON from transcripts.

Reads per-file transcript JSONs and produces a single comm.json for
the web frontend, sorted chronologically.

Input:  {data_dir}/{mission}/processed/transcripts/comm/{date}/*.json
Output: {data_dir}/{mission}/web/comm.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

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

    # Collect all transcript JSONs
    transcripts = []
    for json_path in sorted(transcript_dir.rglob("*.json")):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Join segment text
        segments = data.get("segments", [])
        text = " ".join(s.get("text", "").strip() for s in segments)

        if not text.strip() or text.strip() in HALLUCINATIONS:
            continue

        entry = {
            "t": data.get("utcTime", ""),
            "d": data.get("duration", 0),
            "text": text,
            "lang": data.get("language", "en"),
        }

        # Include segment-level timing for the frontend
        if segments:
            entry["segments"] = [
                {
                    "s": s.get("start", 0),
                    "e": s.get("end", 0),
                    "text": s.get("text", "").strip(),
                }
                for s in segments
                if s.get("text", "").strip()
            ]

        transcripts.append(entry)

    # Sort by UTC time
    transcripts.sort(key=lambda x: x["t"])

    # Write output
    out_path = mission.web_dir / "comm.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(transcripts, f, ensure_ascii=False)

    print(f"  Saved {len(transcripts)} comm entries to {out_path}")

    # Stats
    if transcripts:
        print(f"  Time range: {transcripts[0]['t']} to {transcripts[-1]['t']}")
        total_dur = sum(t.get("d", 0) for t in transcripts)
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
