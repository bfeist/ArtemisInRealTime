"""Step 1c — Prepare private comm inputs and playback cache.

Reads per-file transcript JSONs and prepares internal comm.json and comm.csv,
sorted chronologically. Step 1i publishes enriched transcripts after classification.
Audio imports prepare a cache in processed/comm_audio/. After filtering and
loop precedence, this step validates referenced cached audio without changing web/.

Output columns: t|s|d|src|text
  t   — ISO-8601 UTC timestamp of the segment
  s   — segment start offset (seconds) within the audio file
  d   — segment duration (seconds)
  src — audio source relative to web/comm/ (legacy basenames use a date folder)
  text — transcript text

Input:  {data_dir}/{mission}/processed/transcripts/comm/{date}/*.json
Output: {data_dir}/{mission}/processed/transcripts/inputs/comm.csv
        {data_dir}/{mission}/processed/transcripts/inputs/comm.json (optional speaker)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.pds import atomic_json
from shared.pds_loops import apply_loop_precedence
from shared.comm_audio import prepare_comm_audio
from shared.ia_video_downloads import plan_lock
from shared.pds_comm import ensure_pcd_audio

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


def collect_comm(mission: MissionConfig, audit_dir: Path):
    """Read normalized originals and reconcile rows; write audits only to audit_dir."""
    transcript_dir = mission.processed_transcripts / "comm"
    # Collect all transcript JSONs, building comm entries and CSV rows
    transcripts = []  # list of (t, s, d, src, text) tuples for comm.csv
    web_entries = {}
    sources = {}
    pcd_transcripts = {}

    # Hours to add to convert filename-embedded local time → UTC.
    # Applied only to JSONs produced by the old 1b code that did not apply the
    # correction itself (those lack the "tz_corrected": true marker).
    tz_delta = timedelta(hours=mission.comm_tz_offset_hours)

    for json_path in sorted(transcript_dir.rglob("*.json")):
        # Skip Mission Audio transcripts — analysis showed ~89% of their content
        # is already captured by the YT/PAO stream (PAO.csv via step 2k) or by
        # the OE Comp recordings already in comm.csv.  Only ~11% is unique, and
        # that content is PAO-style narration rather than crew-ground comm.
        # Mission Audio is processed separately by step 1b2 and intentionally
        # excluded here to avoid flooding comm.csv with duplicate PAO content.
        if "MISSION_AUDIO" in json_path.name:
            continue
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
        aac_name = data.get("web_src") or json_path.stem + ".aac"
        supplied = data.get("transcript_source") == "pds_supplied"
        if data.get("source_kind") == "crew_onboard":
            pcd_transcripts[aac_name] = (json_path, data)
        if data.get("source_kind") in {"crew_onboard", "mission_voice_loop"}:
            sources[aac_name] = {key: data.get(key) for key in (
                "source_kind", "channel", "nasa_id", "utcTime", "time_source",
                "transcript_source", "pds_products", "pds_transcripts", "authority_intervals")}

        for seg in data.get("segments", []):
            text = seg.get("text", "").strip()
            if not text or (not supplied and text in HALLUCINATIONS):
                continue
            text = " ".join(text.splitlines())
            speaker = " ".join((seg.get("speaker") or "").split()).strip()

            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            seg_utc = seg.get("utcTime") or (_offset_utc(base_utc, seg_start) if base_utc else "")

            row = (
                seg_utc,
                str(round(seg_start, 3)),
                str(round(seg_end - seg_start, 3)),
                aac_name,
                f"[{speaker}] {text}" if speaker else text,
            )
            transcripts.append(row)
            entry = {"t": seg_utc, "s": round(seg_start, 3),
                     "d": round(seg_end - seg_start, 3), "src": aac_name, "text": text}
            if speaker:
                entry["speaker"] = speaker
            web_entries[row] = entry

    transcripts = apply_loop_precedence(transcripts, sources, audit_dir)

    # Sort by UTC (column 0)
    transcripts.sort(key=lambda r: datetime.fromisoformat(r[0].replace("Z", "+00:00"))
                     if r[0] else datetime.min.replace(tzinfo=timezone.utc))

    return transcripts, web_entries, sources, pcd_transcripts


def prepare_comm(mission: MissionConfig) -> None:
    if not mission.ia_comm_collection:
        print(f"  No comm audio configured for {mission.name}. Skipping.")
        return

    transcript_dir = mission.processed_transcripts / "comm"
    if not transcript_dir.exists():
        print(f"  Transcript directory not found: {transcript_dir}")
        print("  Run step 1b (transcription) first.")
        return

    transcripts, web_entries, sources, pcd_transcripts = collect_comm(
        mission, mission.data_dir / "processed/pds")

    # Routine builds also upgrade cached PCD audio without WhisperX or PDS retrieval.
    # Compact loop AAC is already suitable and is kept byte-for-byte unchanged.
    for src in sorted({row[3] for row in transcripts} & pcd_transcripts.keys()):
        path, data = pcd_transcripts[src]
        if ensure_pcd_audio(mission, data):
            atomic_json(path, data)
            print(f"  Updated PCD AAC encoding: {src}", flush=True)
        sources[src]["audio_encoding"] = data["audio_encoding"]

    # Validate all playback files before publishing indexes or retiring old audio.
    # On existing installations this also adopts legacy web files into the cache.
    audio_manifest = prepare_comm_audio(mission, transcripts)

    # Write pipe-delimited comm.csv
    out_path = mission.transcript_inputs / "comm.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in transcripts:
            f.write("|".join(row) + "\n")
    os.replace(tmp, out_path)
    atomic_json(mission.transcript_inputs / "comm.json", [web_entries[row] for row in transcripts])

    print(f"  Saved {len(transcripts)} comm entries to {out_path}")
    referenced = {row[3] for row in transcripts}
    atomic_json(mission.transcript_inputs / "comm_sources.json",
                {src: metadata for src, metadata in sources.items() if src in referenced})
    atomic_json(mission.transcript_inputs / "comm_audio_manifest.json", audio_manifest)

    # Stats
    if transcripts:
        print(f"  Time range: {transcripts[0][0]} to {transcripts[-1][0]}")
        total_dur = sum(float(r[2]) for r in transcripts)
        print(f"  Total audio duration: {total_dur / 3600:.1f} hours")


def main():
    parser = argparse.ArgumentParser(description="Prepare private comm classification inputs and audio cache")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 1c: Prepare comm — {mission.name} ===\n")
    with plan_lock(mission.data_dir / "processed" / "comm_audio_manifest.json"):
        prepare_comm(mission)


if __name__ == "__main__":
    main()
