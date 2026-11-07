"""Reproducible cached-text study and optional bounded local acoustic checks."""
import argparse
import json
from pathlib import Path
import subprocess
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.pao_alignment import text_anchor_candidates, normalized_audio_match
from shared.pao_sync import epoch, utc


def decode(path, start, duration, sample_rate=4000):
    import numpy as np
    command = ["ffmpeg", "-v", "error", "-ss", str(max(0, start)), "-i", str(path), "-t", str(duration), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1"]
    return np.frombuffer(subprocess.run(command, check=True, capture_output=True, timeout=30).stdout, dtype="<f4")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ia-dir", required=True, type=Path)
    parser.add_argument("--yt-dir", required=True, type=Path)
    parser.add_argument("--part-manifest", required=True, type=Path)
    parser.add_argument("--stream-start-utc", required=True)
    parser.add_argument("--raw-ia-dir", type=Path)
    parser.add_argument("--parts-dir", type=Path)
    parser.add_argument("--acoustic-limit", type=int, default=0)
    parser.add_argument("--lowpass-hz", type=float, default=0, help="Optional lowpass before NCC for independent resampling grids")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 0 <= args.acoustic_limit <= 50:
        parser.error("acoustic limit must be 0..50")
    if not 0 <= args.lowpass_hz < 2000:
        parser.error("lowpass must be below Nyquist2000Hz")
    if "processed" not in args.output.resolve().parts or args.output.exists():
        parser.error("output must be a new artifact under processed")
    started = time.monotonic()
    ia_rows, yt_rows, recordings = [], [], {}
    for path in sorted(args.ia_dir.rglob("*MISSION_AUDIO*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        rid = "ia:" + path.stem
        recordings[rid] = {"source": row.get("source"), "utcTime": row.get("utcTime"), "duration": row.get("duration")}
        for i, seg in enumerate(row.get("segments", [])):
            if seg.get("end", 0) > seg.get("start", 0):
                ia_rows.append({"id": f"{rid}:{i}", "recordingId": rid, "start": seg["start"], "end": seg["end"], "text": seg.get("text", "")})
    parts = {p["part"]: p for p in json.loads(args.part_manifest.read_text(encoding="utf-8"))["parts"]}
    for path in sorted(args.yt_dir.glob("*part*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(row.get("part"), int):
            continue
        for i, seg in enumerate(row.get("segments", [])):
            yt_rows.append({"id": f"yt:{path.stem}:{i}", "part": row["part"], "start": seg["start"], "end": seg["end"], "text": seg.get("text", "")})
    result = text_anchor_candidates(ia_rows, yt_rows)
    for anchor in result["anchors"]:
        recording = recordings[anchor["recordingId"]]
        if recording.get("utcTime") and anchor["youtube"]["part"] in parts:
            ia_utc = epoch(recording["utcTime"]) + anchor["iaStart"]
            yt_utc = epoch(args.stream_start_utc) + parts[anchor["youtube"]["part"]]["originalStartSeconds"] + anchor["youtube"]["start"]
            anchor["candidateUtcDeltaSeconds"] = yt_utc - ia_utc
            anchor["iaStartUtc"] = utc(ia_utc)
    result["candidateClockSummary"] = []
    for part in sorted(parts):
        deltas = [a["candidateUtcDeltaSeconds"] for a in result["anchors"] if a["youtube"]["part"] == part and "candidateUtcDeltaSeconds" in a]
        if deltas:
            result["candidateClockSummary"].append({"part": part, "anchorCount": len(deltas),
                                                    "minimumDeltaSeconds": min(deltas), "medianDeltaSeconds": statistics.median(deltas),
                                                    "maximumDeltaSeconds": max(deltas), "verification": "text-candidate-only"})
    result.update({"schemaVersion": 1, "verification": "text-candidates-not-playback-authority", "recordings": recordings,
                   "method": "unique-central-10-token-exact-phrase-vs-contiguous-unfiltered-yt-per-part", "acousticChecks": []})
    if args.acoustic_limit:
        if not args.raw_ia_dir or not args.parts_dir:
            parser.error("acoustic checks require --raw-ia-dir and --parts-dir")
        raw_paths = {p.name: p for p in args.raw_ia_dir.rglob("*MISSION_AUDIO*.wav")}
        candidates = sorted((a for a in result["anchors"] if a["orderedWithinRecording"]), key=lambda a: (a["youtube"]["part"], a["youtube"]["start"]))
        # Include both endpoints, every represented part's nearest cut, then
        # evenly spread remaining samples. One sample per recording initially.
        selected, seen = [], set()
        def choose(anchor):
            if anchor["recordingId"] not in seen and len(selected) < args.acoustic_limit:
                selected.append(anchor)
                seen.add(anchor["recordingId"])
        if candidates:
            choose(candidates[0]); choose(candidates[-1])
            for part in sorted({a["youtube"]["part"] for a in candidates}):
                group = [a for a in candidates if a["youtube"]["part"] == part]
                choose(group[0]); choose(group[-1])
            if len(selected) < args.acoustic_limit:
                for i in range(args.acoustic_limit):
                    choose(candidates[round(i*(len(candidates)-1)/max(1, args.acoustic_limit-1))])
        for anchor in selected:
            recording = recordings[anchor["recordingId"]]
            source = raw_paths.get(recording.get("source"))
            if source is None:
                source = next((p for name, p in raw_paths.items() if name.split("_by_ui_")[0] == (recording.get("source") or "").split("_by_ui_")[0]), None)
            check = {"iaId": anchor["iaId"], "recordingId": anchor["recordingId"], "part": anchor["youtube"]["part"], "evidenceKind": "acoustic"}
            if not source:
                result["acousticChecks"].append(check | {"verification": "unknown", "reason": "missing-original"})
                continue
            duration = min(8, anchor["iaEnd"] - anchor["iaStart"])
            ia_start = anchor["iaStart"]
            # Phrase can sit inside differently segmented YT/IA sentences.
            # A broad ±20s window is candidate evidence, never assumed timing.
            yt_start = max(0, anchor["youtube"]["start"] - 20)
            try:
                ref = decode(source, ia_start, duration)
                search = decode(args.parts_dir / parts[anchor["youtube"]["part"]]["filename"], yt_start, 48)
                matched = normalized_audio_match(ref, search, 4000, lowpass_hz=args.lowpass_hz or None)
                check.update(matched)
                check["method"] = {"sampleRate": 4000, "lowpassHz": args.lowpass_hz, "nccThreshold": 0.75, "minimumPeakMargin": 0.1}
                check.update({"iaStartSeconds": ia_start, "youtubeSearchStartSeconds": yt_start})
                if matched["verification"] == "verified-bounded-acoustic":
                    actual_start = yt_start + matched["offsetSeconds"]
                    check["youtubeStartSeconds"] = actual_start
                    check["youtubeEndSeconds"] = actual_start + matched["durationSeconds"]
                    if recording.get("utcTime"):
                        check["candidateStartUtc"] = utc(epoch(recording["utcTime"]) + ia_start)
                        check["utcVerification"] = "inherited-ia-clock-not-independently-verified"
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                check.update({"verification": "unknown", "reason": type(error).__name__})
            result["acousticChecks"].append(check)
    result["runtimeSeconds"] = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, sort_keys=True)
        target.write("\n")


if __name__ == "__main__":
    main()
