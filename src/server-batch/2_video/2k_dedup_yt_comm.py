"""Step 2k — Align YouTube transcripts to UTC and de-duplicate against comm.

Reads the per-part JSON files produced by step 2j, applies a known stream
start UTC to give every segment an absolute timestamp, then runs a two-pass
de-duplication algorithm against the comm transcript (steps 1b/1c) to flag
utterances that appear in both sources so the frontend can suppress them in
the integrated timeline.

─── Why a separate step? ────────────────────────────────────────────────────
The stream start UTC may not be known before transcription.  Transcribing
first (step 2j) and comparing the resulting text against comm.json is often
HOW the stream start is determined.  Keeping dedup here means:

  • You can tune thresholds (see env vars below) and re-run in seconds.
  • You can revise the stream start estimate without re-transcribing.
  • This step can run on a different machine from the GPU transcription box.

─── Two-pass matching algorithm ─────────────────────────────────────────────
Pass 1 — text + time seed matching:
  • Candidate pool: comm entries within ±MAX_TIME_DELTA seconds of the
    YouTube segment's UTC timestamp.
  • Score: rapidfuzz.token_set_ratio (falls back to difflib).
    token_set_ratio handles word reordering and partial coverage well
    (e.g. "copy that Flight" vs "copy, flight" → ~92).
  • Accept if score ≥ SIMILARITY_THRESHOLD (default 75).
  • Fallback: wider ±MAX_TIME_DELTA_WIDE window, higher threshold 90.

Pass 2 — speaker cluster promotion (only when diarization was run):
  • For each speaker ID, compute comm_rate = matched / total.
  • If comm_rate ≥ COMM_SPEAKER_THRESHOLD (default 0.25) AND total ≥ 5,
    mark the speaker as a "comm speaker".
  • All utterances from comm speakers are flagged as duplicates, even if
    text matching alone did not score them above threshold.
  • This handles garbled audio and ASR mismatches.

─── Outputs ──────────────────────────────────────────────────────────────────
{processed_transcripts}/yt/_transcript.csv     — pipe-delimited combined CSV
{processed_transcripts}/yt/_dedup_matches.json — match evidence for audit
{processed_transcripts}/yt/_comm_speakers.json — identified comm speaker IDs

CSV columns (pipe-delimited, no header row):
  utc_iso | part_stem | start | end | lang | speaker | text | dup_comm_utc

  dup_comm_utc: matched comm segment UTC, or "" if none (unique to YouTube).
  speaker:      SPEAKER_NN label, or "" if diarization was skipped.

─── Thresholds (override via env vars) ──────────────────────────────────────
  YT_DEDUP_DELTA_S       narrow time window    (default 90 s)
  YT_DEDUP_DELTA_WIDE_S  wide time window      (default 300 s)
  YT_DEDUP_SIM           narrow sim threshold  (default 75)
  YT_DEDUP_SIM_WIDE      wide sim threshold    (default 90)
  YT_COMM_SPK_RATE       speaker promotion rate threshold (default 0.25)
  YT_COMM_SPK_MIN_UTTS   min utterances to consider for promotion (default 5)

Usage (run from src/server-batch/):
    uv run 2_video/2k_dedup_yt_comm.py \\
        --mission artemis-ii \\
        --stream-start-utc "2026-03-30T12:00:00Z"
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ── Optional rapidfuzz ────────────────────────────────────────────────────────
try:
    from rapidfuzz.fuzz import token_set_ratio as _rfuzz_tsr

    def _text_similarity(a: str, b: str) -> float:
        return _rfuzz_tsr(a, b)

    _FUZZY_BACKEND = "rapidfuzz"
except ImportError:
    from difflib import SequenceMatcher

    def _text_similarity(a: str, b: str) -> float:  # type: ignore[misc]
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100

    _FUZZY_BACKEND = "difflib (install rapidfuzz for better results)"

# ── Config ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS  # noqa: E402

# Dedup thresholds — override via env vars
MAX_TIME_DELTA            = float(os.environ.get("YT_DEDUP_DELTA_S",      "90"))
MAX_TIME_DELTA_WIDE       = float(os.environ.get("YT_DEDUP_DELTA_WIDE_S", "300"))
SIMILARITY_THRESHOLD      = float(os.environ.get("YT_DEDUP_SIM",          "75"))
SIMILARITY_THRESHOLD_WIDE = float(os.environ.get("YT_DEDUP_SIM_WIDE",     "90"))

# Speaker promotion
COMM_SPEAKER_THRESHOLD = float(os.environ.get("YT_COMM_SPK_RATE",     "0.25"))
COMM_SPEAKER_MIN_UTTS  = int(os.environ.get("YT_COMM_SPK_MIN_UTTS",   "5"))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_utc(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def _parse_part_num(filename: str) -> int:
    import re
    m = re.search(r"_part(\d+)", filename, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _save_json(path: Path, data: object) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ── Comm index ────────────────────────────────────────────────────────────────


def load_comm_index(comm_json_path: Path) -> tuple[list[dict], list[float]]:
    """Load comm.json → (entries sorted by UTC, parallel float timestamp list for bisect)."""
    if not comm_json_path.exists():
        return [], []
    with open(comm_json_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    entries: list[dict] = []
    for item in raw:
        t_str = item.get("t", "")
        text  = item.get("text", "").strip()
        if t_str and text:
            entries.append({"utc": _parse_utc(t_str), "utc_str": t_str, "text": text})
    entries.sort(key=lambda e: e["utc"])
    timestamps = [e["utc"].timestamp() for e in entries]
    return entries, timestamps


# ── Matching ──────────────────────────────────────────────────────────────────


def find_comm_match(
    yt_utc: datetime,
    yt_text: str,
    comm_entries: list[dict],
    comm_timestamps: list[float],
    *,
    time_delta: float,
    sim_threshold: float,
) -> dict | None:
    ts   = yt_utc.timestamp()
    lo_i = bisect.bisect_left(comm_timestamps,  ts - time_delta)
    hi_i = bisect.bisect_right(comm_timestamps, ts + time_delta)
    candidates = comm_entries[lo_i:hi_i]
    if not candidates:
        return None
    best_score, best_entry = -1.0, None
    for c in candidates:
        score = _text_similarity(yt_text, c["text"])
        if score > best_score:
            best_score, best_entry = score, c
    if best_score >= sim_threshold:
        return {**best_entry, "score": round(best_score, 1)}
    return None


def run_dedup(
    utterances: list[dict],
    comm_entries: list[dict],
    comm_timestamps: list[float],
) -> tuple[list[dict], list[dict], set[str]]:
    """Two-pass de-duplication.  Returns (annotated_utterances, match_log, comm_speaker_ids).

    Pass 1: text + time seed matching.
    Pass 2: speaker cluster promotion.
    """
    match_log: list[dict] = []

    # Pass 1 — seed matching
    for u in utterances:
        match = find_comm_match(
            u["utc"], u["text"], comm_entries, comm_timestamps,
            time_delta=MAX_TIME_DELTA,
            sim_threshold=SIMILARITY_THRESHOLD,
        )
        if match is None:
            match = find_comm_match(
                u["utc"], u["text"], comm_entries, comm_timestamps,
                time_delta=MAX_TIME_DELTA_WIDE,
                sim_threshold=SIMILARITY_THRESHOLD_WIDE,
            )
        if match:
            u["dup_comm_utc"] = match["utc_str"]
            u["dup_score"]    = match["score"]
            u["dup_source"]   = "text_match"
            match_log.append({
                "yt_utc":    u["utc_str"],
                "yt_text":   u["text"],
                "comm_utc":  match["utc_str"],
                "comm_text": match["text"],
                "score":     match["score"],
                "delta_s":   round(abs((u["utc"] - match["utc"]).total_seconds()), 1),
                "speaker":   u.get("speaker", ""),
                "source":    "text_match",
            })
        else:
            u["dup_comm_utc"] = ""
            u["dup_score"]    = None
            u["dup_source"]   = ""

    # Pass 2 — speaker cluster promotion
    speaker_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "matched": 0})
    for u in utterances:
        sid = u.get("speaker", "")
        if not sid:
            continue
        speaker_stats[sid]["total"] += 1
        if u["dup_comm_utc"]:
            speaker_stats[sid]["matched"] += 1

    comm_speakers: set[str] = set()
    for sid, stats in speaker_stats.items():
        if stats["total"] >= COMM_SPEAKER_MIN_UTTS:
            if stats["matched"] / stats["total"] >= COMM_SPEAKER_THRESHOLD:
                comm_speakers.add(sid)

    if comm_speakers:
        promoted = 0
        for u in utterances:
            if u.get("speaker") in comm_speakers and not u["dup_comm_utc"]:
                ts   = u["utc"].timestamp()
                lo_i = bisect.bisect_left(comm_timestamps,  ts - MAX_TIME_DELTA_WIDE)
                hi_i = bisect.bisect_right(comm_timestamps, ts + MAX_TIME_DELTA_WIDE)
                candidates = comm_entries[lo_i:hi_i]
                if candidates:
                    nearest = min(candidates, key=lambda c: abs(c["utc"].timestamp() - ts))
                    u["dup_comm_utc"] = nearest["utc_str"]
                    u["dup_score"]    = None
                    u["dup_source"]   = "speaker_promotion"
                    match_log.append({
                        "yt_utc":    u["utc_str"],
                        "yt_text":   u["text"],
                        "comm_utc":  nearest["utc_str"],
                        "comm_text": nearest["text"],
                        "score":     None,
                        "delta_s":   round(abs((u["utc"] - nearest["utc"]).total_seconds()), 1),
                        "speaker":   u.get("speaker", ""),
                        "source":    "speaker_promotion",
                    })
                    promoted += 1
        if promoted:
            print(f"  Speaker promotion: {promoted} additional utterance(s) marked as comm "
                  f"via {len(comm_speakers)} comm speaker(s): {sorted(comm_speakers)}")

    return utterances, match_log, comm_speakers


# ── CSV writing ───────────────────────────────────────────────────────────────


def write_csv(utterances: list[dict], out_path: Path) -> None:
    """Pipe-delimited CSV: utc | part_stem | start | end | lang | speaker | text | dup_comm_utc"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for u in utterances:
            row = [
                u["utc_str"],
                u["part_stem"],
                str(u["start"]),
                str(u["end"]),
                u.get("lang", "en"),
                u.get("speaker", ""),
                u["text"],
                u.get("dup_comm_utc", ""),
            ]
            fh.write("|".join(row) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Align YouTube transcripts to UTC and de-duplicate against comm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--stream-start-utc", required=True,
        help="UTC time when the YouTube live stream began, e.g. 2026-03-30T12:00:00Z",
    )
    parser.add_argument(
        "--chunk-hours", type=float, default=12.0,
        help="Chunk length used in step 2h (default: 12). Must match actual part durations.",
    )
    parser.add_argument(
        "--transcript-dir", type=Path, default=None,
        help="Directory containing per-part JSON files from step 2j. "
             "Defaults to {mission.processed_transcripts}/yt/",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]

    try:
        stream_start = _parse_utc(args.stream_start_utc)
    except ValueError as exc:
        print(f"ERROR: invalid --stream-start-utc: {exc}")
        sys.exit(1)

    transcript_dir = (args.transcript_dir or mission.processed_transcripts / "yt").resolve()
    if not transcript_dir.exists():
        print(f"ERROR: transcript dir not found: {transcript_dir}")
        print("       Run step 2j first.")
        sys.exit(1)

    chunk_seconds = int(args.chunk_hours * 3600)
    comm_json     = mission.web_dir / "comm.json"
    comm_entries, comm_timestamps = load_comm_index(comm_json)

    # ── Load per-part JSONs ─────────────────────────────────────────────────
    part_files = sorted(
        p for p in transcript_dir.glob("*_part*.json")
        if not p.name.startswith("_")
    )
    if not part_files:
        print(f"No *_part*.json files found in {transcript_dir}")
        print("Run step 2j first.")
        sys.exit(1)

    print(f"\n=== Step 2k: De-duplicate YouTube Transcripts ===\n")
    print(f"  Mission        : {mission.name}")
    print(f"  Transcript dir : {transcript_dir}")
    print(f"  Part files     : {len(part_files)}")
    print(f"  Stream start   : {stream_start.isoformat()}")
    print(f"  Chunk size     : {args.chunk_hours}h")
    print(f"  Comm index     : {len(comm_entries)} entries" if comm_entries
          else f"  Comm index     : NOT FOUND — dedup skipped ({comm_json})")
    print(f"  Fuzzy backend  : {_FUZZY_BACKEND}")
    print(f"  Dedup window   : ±{MAX_TIME_DELTA:.0f}s sim≥{SIMILARITY_THRESHOLD:.0f}"
          f" / fallback ±{MAX_TIME_DELTA_WIDE:.0f}s sim≥{SIMILARITY_THRESHOLD_WIDE:.0f}")
    print(f"  Comm spk thresh: rate≥{COMM_SPEAKER_THRESHOLD:.0%}"
          f"  min_utterances={COMM_SPEAKER_MIN_UTTS}")
    print()

    all_utterances: list[dict] = []
    for part_path in part_files:
        with open(part_path, encoding="utf-8") as fh:
            data = json.load(fh)
        pnum   = data.get("part") or _parse_part_num(part_path.name)
        pstart = stream_start + timedelta(seconds=(pnum - 1) * chunk_seconds)
        segs   = data.get("segments", [])
        print(f"  {part_path.name}: {len(segs)} segments  "
              f"(part {pnum}, starts {pstart.strftime('%Y-%m-%dT%H:%M:%SZ')})")

        for seg in segs:
            seg_utc = pstart + timedelta(seconds=seg["start"])
            all_utterances.append({
                "utc":       seg_utc,
                "utc_str":   _fmt_utc(seg_utc),
                "part_stem": part_path.stem,
                "start":     seg["start"],
                "end":       seg["end"],
                "lang":      seg.get("lang", "en"),
                "speaker":   seg.get("speaker", ""),
                "text":      seg["text"],
            })

    all_utterances.sort(key=lambda u: u["utc"])
    total_segs = len(all_utterances)
    print(f"\n  {total_segs} total utterances loaded.")

    # ── De-duplicate ────────────────────────────────────────────────────────
    match_log: list[dict]   = []
    comm_speakers: set[str] = set()

    if comm_entries:
        print(f"  Running de-duplication against {len(comm_entries)} comm entries …")
        t0 = time.monotonic()
        all_utterances, match_log, comm_speakers = run_dedup(
            all_utterances, comm_entries, comm_timestamps
        )
        elapsed   = time.monotonic() - t0
        dup_count = sum(1 for u in all_utterances if u["dup_comm_utc"])
        by_source: dict[str, int] = defaultdict(int)
        for u in all_utterances:
            if u.get("dup_source"):
                by_source[u["dup_source"]] += 1
        print(
            f"  Done in {elapsed:.1f}s — "
            f"{dup_count} duplicate(s) ({100 * dup_count / max(1, total_segs):.1f}%) "
            + ", ".join(f"{v} via {k}" for k, v in by_source.items())
        )

        _save_json(transcript_dir / "_dedup_matches.json", match_log)
        print(f"  Match evidence  → {transcript_dir / '_dedup_matches.json'}")

        if comm_speakers:
            _save_json(transcript_dir / "_comm_speakers.json", sorted(comm_speakers))
            print(f"  Comm speakers   → {transcript_dir / '_comm_speakers.json'}: "
                  f"{sorted(comm_speakers)}")
    else:
        print("  Skipping de-duplication (no comm.json found).")
        for u in all_utterances:
            u["dup_comm_utc"] = ""
            u["dup_score"]    = None
            u["dup_source"]   = ""

    # ── Write combined CSV ──────────────────────────────────────────────────
    csv_path = transcript_dir / "_transcript.csv"
    write_csv(all_utterances, csv_path)
    print(f"  Transcript CSV  → {csv_path}  ({total_segs} rows)")

    if all_utterances:
        print(f"  UTC range: {all_utterances[0]['utc_str']} → {all_utterances[-1]['utc_str']}")

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
