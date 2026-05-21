"""Step 2k — Filter YouTube transcript and produce combined web transcript.

Reads per-part JSON files produced by step 2j, determines the UTC start time
of the YouTube stream (auto-detected by matching speech against the comm
transcript, or supplied via --stream-start-utc), removes utterances from the
YouTube transcript that also appear on the space-to-ground comm channel, and
writes a combined web transcript merging the unique YouTube commentary with
all comm entries.

─── Why this is needed ───────────────────────────────────────────────────────
The YouTube NASA live stream carries the public affairs commentary AND also
picks up the space-to-ground audio (everything on the comm channels, which is
what steps 1b/1c capture separately).  The integrated timeline should show:

  • All comm entries (step 1c, space-to-ground channel — authoritative source
    with precise UTC).
  • Only the YouTube-unique utterances (public affairs commentary, host
    narration, etc.).  Comm duplicates are removed.

The result is written to web/PAO.csv (public affairs commentary only).
The comm channel is unchanged in web/comm.csv (produced by step 1c).

─── Stream start auto-detection ─────────────────────────────────────────────
The YouTube stream has no inherent UTC clock — WhisperX produces only
file-relative timestamps (seconds from start of the video).  To anchor those
to wall-clock UTC we need to know when the stream began.

Auto-detection algorithm:
  1. Sample up to AUTO_DETECT_CANDIDATES YouTube segments with text ≥ 20 chars.
  2. For each candidate, compute text similarity against ALL comm entries (no
     time constraint — we don't know the offset yet).  Uses
     rapidfuzz.token_set_ratio (or difflib fallback).
  3. Every match with score ≥ AUTO_DETECT_MIN_SCORE yields one estimate:
         stream_start_ts = comm_utc_unix − yt_abs_seconds
     where yt_abs_seconds = (part_num − 1) × chunk_s + segment_start_s.
  4. Collect all estimates; take the median as the stream start UTC.
  5. Warn if the inter-quartile range of estimates exceeds AUTO_DETECT_IQR_WARN
     seconds (indicating noisy text matches).

Supply --stream-start-utc to override auto-detection entirely.  The chosen
stream start is saved to {processed_transcripts}/yt/_stream_start.json for
audit and for re-use when tuning thresholds without re-running auto-detection.

─── Two-pass de-duplication ─────────────────────────────────────────────────
Pass 1 — text + time matching:
  Candidate pool: comm entries within ±MAX_TIME_DELTA seconds of the YouTube
  segment's UTC timestamp.  Score: rapidfuzz.token_set_ratio (falls back to
  difflib).  Accept if score ≥ SIMILARITY_THRESHOLD (default 75).
  Fallback: wider ±MAX_TIME_DELTA_WIDE window, threshold SIMILARITY_THRESHOLD_WIDE.

Pass 2 — speaker cluster promotion (only when diarization was run):
  For each speaker ID, compute comm_rate = matched / total.  If comm_rate ≥
  COMM_SPEAKER_THRESHOLD AND total ≥ COMM_SPEAKER_MIN_UTTS, mark the speaker
  as a "comm speaker" and remove all their remaining unmatched utterances.
  Assessment: for each promotion removal, the best available text match in the
  ±MAX_TIME_DELTA_WIDE comm window is also logged with its similarity score.
  Removals where the best score is below PROMOTION_AUDIT_WARN are flagged
  "low_confidence": true in _dedup_matches.json so the operator can review them.

─── Outputs ──────────────────────────────────────────────────────────────────
{web_dir}/PAO.csv                                — YT-unique PAO commentary (web)
{processed_transcripts}/yt/_stream_start.json    — stream start UTC + anchors
{processed_transcripts}/yt/_dedup_matches.json   — match evidence for audit
{processed_transcripts}/yt/_comm_speakers.json   — identified comm speaker IDs
{processed_transcripts}/yt/_transcript.csv       — all YT utterances with
                                                   dedup status (debug)

PAO.csv columns (pipe-delimited):
  t        ISO-8601 UTC of the utterance
  text     transcript text

─── Thresholds (override via env vars) ──────────────────────────────────────
  YT_DEDUP_DELTA_S            narrow time window           (default  90 s)
  YT_DEDUP_DELTA_WIDE_S       wide time window             (default 300 s)
  YT_DEDUP_SIM                narrow sim threshold         (default  75)
  YT_DEDUP_SIM_WIDE           wide sim threshold           (default  90)
  YT_COMM_SPK_RATE            speaker promotion rate       (default 0.25)
  YT_COMM_SPK_MIN_UTTS        min utterances for promotion (default   5)
  YT_AUTO_DETECT_CANDIDATES   max YT segments to sample    (default 200)
  YT_AUTO_DETECT_MIN_SCORE    min text score for anchor    (default  85)
  YT_AUTO_DETECT_IQR_WARN     IQR warn threshold (seconds) (default 120)
  YT_PROMOTION_AUDIT_WARN     min score before low_confidence flag (default 40)

Usage (run from src/server-batch/):
    uv run 2_video/2k_filter_yt_transcript.py \\
        --mission artemis-ii

    # Override stream start (skip auto-detection):
    uv run 2_video/2k_filter_yt_transcript.py \\
        --mission artemis-ii \\
        --stream-start-utc "2026-03-30T12:00:00Z"
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Optional rapidfuzz ────────────────────────────────────────────────────────
try:
    from rapidfuzz.fuzz import token_set_ratio as _rfuzz_tsr
    from rapidfuzz.fuzz import ratio as _rfuzz_ratio

    def _text_similarity(a: str, b: str) -> float:
        """Dedup similarity — token_set_ratio tolerates word reordering/partial coverage."""
        return _rfuzz_tsr(a, b)

    def _detect_similarity(a: str, b: str) -> float:
        """Detection similarity — fuzz.ratio requires sequential character match.

        Used only for stream start detection where false-positive token-bag
        matches (e.g. 'And you have 15 minutes...' ≈ 'Houston, go for the
        vehicle system update...') would contaminate the stream-start estimate.
        """
        return _rfuzz_ratio(a, b)

    _FUZZY_BACKEND = "rapidfuzz"
except ImportError:
    from difflib import SequenceMatcher

    def _text_similarity(a: str, b: str) -> float:  # type: ignore[misc]
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100

    def _detect_similarity(a: str, b: str) -> float:  # type: ignore[misc]
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100

    _FUZZY_BACKEND = "difflib (install rapidfuzz for better results)"

# ── Config ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS  # noqa: E402

# Dedup thresholds — override via env vars
MAX_TIME_DELTA            = float(os.environ.get("YT_DEDUP_DELTA_S",       "90"))
MAX_TIME_DELTA_WIDE       = float(os.environ.get("YT_DEDUP_DELTA_WIDE_S",  "300"))
SIMILARITY_THRESHOLD      = float(os.environ.get("YT_DEDUP_SIM",           "75"))
SIMILARITY_THRESHOLD_WIDE = float(os.environ.get("YT_DEDUP_SIM_WIDE",      "90"))

# Speaker cluster promotion thresholds
COMM_SPEAKER_THRESHOLD    = float(os.environ.get("YT_COMM_SPK_RATE",       "0.25"))
COMM_SPEAKER_MIN_UTTS     = int(  os.environ.get("YT_COMM_SPK_MIN_UTTS",   "5"))

# Stream start auto-detection thresholds
AUTO_DETECT_CANDIDATES    = int(  os.environ.get("YT_AUTO_DETECT_CANDIDATES", "200"))
AUTO_DETECT_MIN_SCORE     = float(os.environ.get("YT_AUTO_DETECT_MIN_SCORE",  "85"))
AUTO_DETECT_MIN_LEN       = int(  os.environ.get("YT_AUTO_DETECT_MIN_LEN",   "30"))
AUTO_DETECT_CONSENSUS_TOL = float(os.environ.get("YT_AUTO_DETECT_CONSENSUS_S", "60"))
AUTO_DETECT_IQR_WARN      = float(os.environ.get("YT_AUTO_DETECT_IQR_WARN",  "120"))

# Speaker promotion audit threshold — removals below this text score are flagged
PROMOTION_AUDIT_WARN      = float(os.environ.get("YT_PROMOTION_AUDIT_WARN",  "40"))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_utc(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def _parse_part_num(filename: str) -> int:
    m = re.search(r"_part(\d+)", filename, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ── Comm index ────────────────────────────────────────────────────────────────


def load_comm_index(comm_csv_path: Path) -> tuple[list[dict], list[float]]:
    """Load comm.csv → (entries sorted by UTC, parallel float timestamp list for bisect).

    comm.csv is pipe-delimited with columns: t|s|d|src|text
    Preserves s, d, src fields needed for playback sync.
    """
    if not comm_csv_path.exists():
        return [], []
    entries: list[dict] = []
    with open(comm_csv_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            # Split into at most 5 parts so text (last) may contain "|"
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue
            t_str, s_str, d_str, src, text = parts
            text = text.strip()
            if t_str and text:
                entries.append({
                    "utc":     _parse_utc(t_str),
                    "utc_str": t_str,
                    "text":    text,
                    "s":       float(s_str) if s_str else 0.0,
                    "d":       float(d_str) if d_str else 0.0,
                    "src":     src,
                    "lang":    "en",
                })
    entries.sort(key=lambda e: e["utc"])
    timestamps = [e["utc"].timestamp() for e in entries]
    return entries, timestamps


# ── YT transcript loading ─────────────────────────────────────────────────────


def load_yt_transcripts(transcript_dir: Path, chunk_seconds: float) -> list[dict]:
    """Load per-part 2j JSONs and return a flat list of utterance dicts.

    Each utterance dict contains:
      yt_abs_s  — seconds from stream start = part_start_offset + seg_start,
                  where part_start_offset is the cumulative duration of all
                  preceding parts (read from the JSON ``duration`` field so the
                  calculation is independent of any assumed chunk size)
      part_num  — integer part number (used for display)
      part_stem — filename stem without extension (used as "src" in combined output)
      start     — file-relative start seconds (for output)
      end       — file-relative end seconds
      text, lang, speaker

    UTC fields (utc, utc_str) are NOT assigned here; call assign_utc() after the
    stream start is known.

    ``chunk_seconds`` is only used as a fallback when a part's JSON does not
    carry a ``duration`` field (pre-v1 files).
    """
    part_files = sorted(
        p for p in transcript_dir.glob("*_part*.json")
        if not p.name.startswith("_")
    )

    # First pass: collect part metadata so we can compute cumulative offsets.
    part_meta: list[tuple[int, float, Path]] = []  # (pnum, duration_s, path)
    for part_path in part_files:
        with open(part_path, encoding="utf-8") as fh:
            data = json.load(fh)
        pnum = data.get("part") or _parse_part_num(part_path.name)
        # Prefer the duration stored in the JSON; fall back to chunk_seconds.
        duration = float(data.get("duration") or chunk_seconds)
        part_meta.append((pnum, duration, part_path))

    # Sort by part number and compute cumulative start offsets.
    part_meta.sort(key=lambda t: t[0])
    part_offsets: dict[int, float] = {}
    cumulative = 0.0
    for pnum, duration, _ in part_meta:
        part_offsets[pnum] = cumulative
        cumulative += duration

    # Second pass: build utterances using the actual cumulative offsets.
    utterances: list[dict] = []
    for pnum, duration, part_path in part_meta:
        with open(part_path, encoding="utf-8") as fh:
            data = json.load(fh)
        part_offset_s = part_offsets[pnum]
        segs          = data.get("segments", [])
        print(f"  {part_path.name}: {len(segs)} segments  (part {pnum}, offset {part_offset_s/3600:.2f}h)")
        for seg in segs:
            if not _has_speech(seg.get("text", "")):
                continue  # skip silence markers (e.g. ".")
            utterances.append({
                "yt_abs_s":  part_offset_s + seg["start"],
                "part_num":  pnum,
                "part_stem": part_path.stem,
                "start":     seg["start"],
                "end":       seg["end"],
                "text":      seg["text"],
                "lang":      seg.get("lang", "en"),
                "speaker":   seg.get("speaker", ""),
            })
    return utterances


# ── Stream start auto-detection ───────────────────────────────────────────────


def detect_stream_start(
    utterances: list[dict],
    comm_entries: list[dict],
) -> tuple[datetime | None, list[dict], list[float], list[float]]:
    """Auto-detect YouTube stream start UTC by text-matching against comm.

    Does NOT use time constraints — at this point the UTC offset is unknown.
    Samples up to AUTO_DETECT_CANDIDATES utterances with text ≥ AUTO_DETECT_MIN_LEN
    chars, finds the best comm text match for each using fuzz.ratio (sequential
    character match), and computes:

        stream_start_estimate = comm_utc_unix − yt_abs_seconds

    Uses consensus clustering (±AUTO_DETECT_CONSENSUS_TOL seconds) to select the
    tightest dense cluster of estimates rather than the simple median, which is
    vulnerable to false-positive token-bag matches polluting the result.

    Returns (stream_start_utc, anchor_log, all_estimate_timestamps, consensus_inliers).
    Returns (None, [], [], []) if no qualifying matches are found.
    """
    # Prefer longer utterances — they are more distinctive and yield fewer
    # false-positive matches that would scatter the estimates.
    candidates = [u for u in utterances if len(u["text"]) >= AUTO_DETECT_MIN_LEN]
    if not candidates:
        candidates = utterances[:]

    # Sample evenly across the stream so we get coverage from all parts
    if len(candidates) > AUTO_DETECT_CANDIDATES:
        step = max(1, len(candidates) // AUTO_DETECT_CANDIDATES)
        candidates = candidates[::step][:AUTO_DETECT_CANDIDATES]

    estimates: list[float] = []
    anchors:   list[dict]  = []

    for u in candidates:
        best_score: float       = 0.0
        best_comm:  dict | None = None
        for c in comm_entries:
            # Use fuzz.ratio (sequential character match) rather than
            # token_set_ratio so that token-bag coincidences — e.g.
            # "And you have 15 minutes in this handover time..." matching
            # "Houston, go for the vehicle system update..." — are rejected.
            score = _detect_similarity(u["text"], c["text"])
            if score > best_score:
                best_score, best_comm = score, c

        if best_score >= AUTO_DETECT_MIN_SCORE and best_comm is not None:
            est_ts = best_comm["utc"].timestamp() - u["yt_abs_s"]
            estimates.append(est_ts)
            anchors.append({
                "yt_text":               u["text"],
                "yt_abs_s":              round(u["yt_abs_s"], 3),
                "yt_part":               u["part_num"],
                "comm_text":             best_comm["text"],
                "comm_utc":              best_comm["utc_str"],
                "score":                 round(best_score, 1),
                "stream_start_estimate": _fmt_utc(
                    datetime.fromtimestamp(est_ts, tz=timezone.utc)
                ),
            })

    if not estimates:
        return None, [], [], []

    # ── Consensus clustering ──────────────────────────────────────────────────
    # Use a sliding-window consensus rather than the simple median.  The median
    # is vulnerable to a large number of loosely-matched false positives that
    # happen to cluster around a wrong value.  The consensus approach picks the
    # stream-start value that maximises the number of inlier estimates within a
    # ±AUTO_DETECT_CONSENSUS_TOL second window, which isolates the tightest
    # cluster of high-quality matches.
    tol       = AUTO_DETECT_CONSENSUS_TOL
    estimates_sorted = sorted(estimates)
    best_center, best_inliers = estimates_sorted[0], [estimates_sorted[0]]
    for pivot in estimates_sorted:
        inliers = [e for e in estimates_sorted if abs(e - pivot) <= tol]
        if len(inliers) > len(best_inliers):
            best_center  = statistics.mean(inliers)
            best_inliers = inliers
        elif len(inliers) == len(best_inliers):
            # Prefer the tighter cluster on tie
            if (max(inliers) - min(inliers)) < (max(best_inliers) - min(best_inliers)):
                best_center  = statistics.mean(inliers)
                best_inliers = inliers

    stream_start = datetime.fromtimestamp(best_center, tz=timezone.utc)
    return stream_start, anchors, estimates, best_inliers


# ── Comm matching helpers ─────────────────────────────────────────────────────


def find_comm_match(
    yt_utc: datetime,
    yt_text: str,
    comm_entries: list[dict],
    comm_timestamps: list[float],
    *,
    time_delta: float,
    sim_threshold: float,
) -> dict | None:
    """Return the best-scoring comm entry within the time window, or None."""
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


def _best_comm_text_match(
    yt_text: str,
    yt_utc: datetime,
    comm_entries: list[dict],
    comm_timestamps: list[float],
    time_delta: float,
) -> tuple[float, dict | None]:
    """Return (best_score, best_entry) in the time window regardless of threshold.

    Used for promotion audit: lets us see how well each promoted utterance
    actually matches comm text, without applying a hard cutoff.
    """
    ts   = yt_utc.timestamp()
    lo_i = bisect.bisect_left(comm_timestamps,  ts - time_delta)
    hi_i = bisect.bisect_right(comm_timestamps, ts + time_delta)
    candidates = comm_entries[lo_i:hi_i]
    if not candidates:
        return 0.0, None
    best_score, best_entry = 0.0, None
    for c in candidates:
        score = _text_similarity(yt_text, c["text"])
        if score > best_score:
            best_score, best_entry = score, c
    return best_score, best_entry


# ── Two-pass de-duplication ───────────────────────────────────────────────────


def run_dedup(
    utterances: list[dict],
    comm_entries: list[dict],
    comm_timestamps: list[float],
) -> tuple[list[dict], list[dict], set[str]]:
    """Two-pass de-duplication.  Returns (annotated_utterances, match_log, comm_speaker_ids).

    Utterances matched as comm duplicates are marked with dup_comm_utc != "".
    The caller decides which ones to exclude from the combined output; this
    function only annotates.

    Pass 1: text + time seed matching.
    Pass 2: speaker cluster promotion with audit assessment.
    """
    match_log: list[dict] = []

    # ── Pass 1 — text + time seed matching ───────────────────────────────────
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
                "yt_utc":          u["utc_str"],
                "yt_text":         u["text"],
                "comm_utc":        match["utc_str"],
                "comm_text":       match["text"],
                "score":           match["score"],
                "best_text_score": match["score"],
                "delta_s":         round(abs((u["utc"] - match["utc"]).total_seconds()), 1),
                "speaker":         u.get("speaker", ""),
                "source":          "text_match",
                "low_confidence":  False,
            })
        else:
            u["dup_comm_utc"] = ""
            u["dup_score"]    = None
            u["dup_source"]   = ""

    # ── Pass 2 — speaker cluster promotion ───────────────────────────────────
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
        if (stats["total"] >= COMM_SPEAKER_MIN_UTTS
                and stats["matched"] / stats["total"] >= COMM_SPEAKER_THRESHOLD):
            comm_speakers.add(sid)

    if comm_speakers:
        promoted  = 0
        low_conf  = 0
        for u in utterances:
            if u.get("speaker") not in comm_speakers or u["dup_comm_utc"]:
                continue

            # Audit: find best text match in the wide time window (no threshold)
            # so we can assess how well this promotion is backed by actual text.
            best_score, best_comm = _best_comm_text_match(
                u["text"], u["utc"], comm_entries, comm_timestamps, MAX_TIME_DELTA_WIDE
            )
            is_low_conf = best_score < PROMOTION_AUDIT_WARN

            # Find temporally nearest comm entry for the log anchor
            ts   = u["utc"].timestamp()
            lo_i = bisect.bisect_left(comm_timestamps,  ts - MAX_TIME_DELTA_WIDE)
            hi_i = bisect.bisect_right(comm_timestamps, ts + MAX_TIME_DELTA_WIDE)
            window = comm_entries[lo_i:hi_i]

            if window:
                nearest = min(window, key=lambda c: abs(c["utc"].timestamp() - ts))
                u["dup_comm_utc"] = nearest["utc_str"]
                delta_s: float | None = round(
                    abs((u["utc"] - nearest["utc"]).total_seconds()), 1
                )
                anchor_utc  = nearest["utc_str"]
                anchor_text = nearest["text"]
            else:
                u["dup_comm_utc"] = "promoted_no_anchor"
                delta_s     = None
                anchor_utc  = ""
                anchor_text = ""
                is_low_conf = True

            u["dup_score"]  = None
            u["dup_source"] = "speaker_promotion"
            match_log.append({
                "yt_utc":          u["utc_str"],
                "yt_text":         u["text"],
                "comm_utc":        anchor_utc,
                "comm_text":       anchor_text,
                "score":           None,
                "best_text_score": round(best_score, 1),
                "best_comm_text":  best_comm["text"] if best_comm else "",
                "delta_s":         delta_s,
                "speaker":         u.get("speaker", ""),
                "source":          "speaker_promotion",
                "low_confidence":  is_low_conf,
            })
            promoted += 1
            if is_low_conf:
                low_conf += 1

        if promoted:
            print(
                f"  Speaker promotion: {promoted} additional utterance(s) removed "
                f"via {len(comm_speakers)} comm speaker(s): {sorted(comm_speakers)}"
            )
            if low_conf:
                print(
                    f"  WARNING: {low_conf} speaker-promoted removal(s) have low text "
                    f"similarity (< {PROMOTION_AUDIT_WARN:.0f}). "
                    f"Review _dedup_matches.json (low_confidence: true entries)."
                )

    return utterances, match_log, comm_speakers


# ── Combined web output ───────────────────────────────────────────────────────


def build_pao_transcript(yt_utterances: list[dict]) -> list[dict]:
    """YT-unique public affairs commentary, sorted by UTC.

    Excludes comm duplicates and utterances with no alphabetic content
    (WhisperX silence markers like ".").
    """
    return [
        {"t": u["utc_str"], "text": u["text"]}
        for u in yt_utterances
        if not u.get("dup_comm_utc")
    ]


_ALPHA_RE = re.compile(r'[a-zA-Z]')


def _has_speech(text: str) -> bool:
    """Return True if the text contains at least one alphabetic character."""
    return bool(_ALPHA_RE.search(text))


# ── Debug CSV ─────────────────────────────────────────────────────────────────


def write_debug_csv(utterances: list[dict], out_path: Path) -> None:
    """Pipe-delimited CSV with all YT utterances including dedup status.

    Columns: utc | part | dur | lang | speaker | text | dup_comm_utc | dup_source
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for u in utterances:
            dur = round(u["end"] - u["start"], 3)
            row = [
                u.get("utc_str", ""),
                str(u["part_num"]),
                str(dur),
                u.get("lang", "en"),
                u.get("speaker", ""),
                u["text"],
                u.get("dup_comm_utc", ""),
                u.get("dup_source", ""),
            ]
            fh.write("|".join(row) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter YouTube transcript and produce combined web transcript",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--stream-start-utc", default=None,
        help=(
            "UTC time when the YouTube live stream began, e.g. 2026-03-30T12:00:00Z. "
            "If omitted, the script auto-detects the stream start by matching speech "
            "against comm.csv.  See the module docstring for algorithm details."
        ),
    )
    parser.add_argument(
        "--chunk-hours", type=float, default=12.0,
        help="Chunk length used in step 2h (default: 12). Must match actual part durations.",
    )
    parser.add_argument(
        "--transcript-dir", type=Path, default=None,
        help=(
            "Directory containing per-part JSON files from step 2j. "
            "Defaults to {mission.processed_transcripts}/yt/"
        ),
    )
    args = parser.parse_args()

    mission        = MISSIONS[args.mission]
    transcript_dir = (args.transcript_dir or mission.processed_transcripts / "yt").resolve()

    if not transcript_dir.exists():
        print(f"ERROR: transcript dir not found: {transcript_dir}")
        print("       Run step 2j first.")
        sys.exit(1)

    chunk_seconds = int(args.chunk_hours * 3600)
    comm_csv      = mission.web_dir / "comm.csv"

    print(f"\n=== Step 2k: Filter YouTube Transcript — {mission.name} ===\n")
    print(f"  Mission        : {mission.name}")
    print(f"  Transcript dir : {transcript_dir}")
    print(f"  Comm CSV       : {comm_csv}")
    print(f"  Fuzzy backend  : {_FUZZY_BACKEND}")
    print(
        f"  Dedup window   : ±{MAX_TIME_DELTA:.0f}s sim≥{SIMILARITY_THRESHOLD:.0f}"
        f" / fallback ±{MAX_TIME_DELTA_WIDE:.0f}s sim≥{SIMILARITY_THRESHOLD_WIDE:.0f}"
    )
    print(
        f"  Comm spk thresh: rate≥{COMM_SPEAKER_THRESHOLD:.0%}"
        f"  min_utterances={COMM_SPEAKER_MIN_UTTS}"
    )
    print()

    # ── Load comm ─────────────────────────────────────────────────────────────
    comm_entries, comm_timestamps = load_comm_index(comm_csv)
    if comm_entries:
        print(f"  Loaded {len(comm_entries)} comm entries from comm.csv")
        if comm_entries:
            print(
                f"  Comm range: {comm_entries[0]['utc_str']} → {comm_entries[-1]['utc_str']}"
            )
    else:
        print(f"  WARNING: comm.csv not found or empty at {comm_csv}")
        print("           Run steps 1b + 1c first for deduplication and stream start detection.")

    # ── Load YT transcripts ───────────────────────────────────────────────────
    print()
    utterances = load_yt_transcripts(transcript_dir, chunk_seconds)
    if not utterances:
        print(f"ERROR: No *_part*.json files found in {transcript_dir}")
        print("       Run step 2j first.")
        sys.exit(1)

    part_nums = sorted({u["part_num"] for u in utterances})
    print(
        f"\n  Loaded {len(utterances)} YouTube utterances  "
        f"(parts: {part_nums}  chunk_hours: {args.chunk_hours})"
    )
    print()

    # ── Determine stream start UTC ────────────────────────────────────────────
    stream_start_anchors:   list[dict]  = []
    stream_start_estimates: list[float] = []
    stream_start_inliers:   list[float] = []
    iqr: float | None = None

    if args.stream_start_utc:
        try:
            stream_start = _parse_utc(args.stream_start_utc)
        except ValueError as exc:
            print(f"ERROR: invalid --stream-start-utc: {exc}")
            sys.exit(1)
        print(f"  Stream start (provided): {_fmt_utc(stream_start)}")

    elif mission.yt_stream_start_utc:
        try:
            stream_start = _parse_utc(mission.yt_stream_start_utc)
        except ValueError as exc:
            print(f"ERROR: invalid yt_stream_start_utc in mission config: {exc}")
            sys.exit(1)
        print(f"  Stream start (from mission config): {_fmt_utc(stream_start)}")

    elif comm_entries:
        print(
            f"  Auto-detecting stream start  "
            f"(sampling ≤{AUTO_DETECT_CANDIDATES} utterances, "
            f"min_score={AUTO_DETECT_MIN_SCORE:.0f}) …"
        )
        t0 = time.monotonic()
        stream_start, stream_start_anchors, stream_start_estimates, stream_start_inliers = detect_stream_start(
            utterances, comm_entries
        )
        elapsed = time.monotonic() - t0

        if stream_start is None:
            print(
                f"ERROR: Could not auto-detect stream start UTC — no comm matches "
                f"≥ {AUTO_DETECT_MIN_SCORE} found after {elapsed:.1f}s."
            )
            print(
                "       Either run step 1b/1c first, or supply --stream-start-utc manually."
            )
            sys.exit(1)

        cluster_spread: float | None = None
        if len(stream_start_inliers) >= 2:
            cluster_spread = round(max(stream_start_inliers) - min(stream_start_inliers), 1)
            iqr = cluster_spread  # keep for JSON output (semantics changed to cluster spread)

        print(
            f"  Auto-detection: {elapsed:.1f}s — {len(stream_start_anchors)} anchor(s) "
            f"({len(stream_start_inliers)} consensus)  → {_fmt_utc(stream_start)}"
        )
        if cluster_spread is not None:
            print(f"  Consensus cluster spread: {cluster_spread:.1f}s", end="")
            if cluster_spread > AUTO_DETECT_IQR_WARN:
                print(
                    f"  ← WARNING: spread exceeds {AUTO_DETECT_IQR_WARN:.0f}s. "
                    "Consider supplying --stream-start-utc manually."
                )
            else:
                print()

    else:
        print("ERROR: comm.csv not found and --stream-start-utc not provided.")
        print("       Cannot determine YouTube stream start UTC.")
        sys.exit(1)

    # Save stream start info
    _save_json(transcript_dir / "_stream_start.json", {
        "stream_start_utc":     _fmt_utc(stream_start),
        "provided_by_user":     args.stream_start_utc is not None or mission.yt_stream_start_utc is not None,
        "anchor_count":         len(stream_start_anchors),
        "consensus_count":      len(stream_start_inliers) if not (args.stream_start_utc or mission.yt_stream_start_utc) else 0,
        "cluster_spread_s":     iqr,
        "anchors":              stream_start_anchors,
    })
    print(f"  Stream start saved → {transcript_dir / '_stream_start.json'}")
    print()

    # ── Assign UTC to all utterances ──────────────────────────────────────────
    for u in utterances:
        utc = stream_start + timedelta(seconds=u["yt_abs_s"])
        u["utc"]     = utc
        u["utc_str"] = _fmt_utc(utc)
    utterances.sort(key=lambda u: u["utc"])

    total_segs = len(utterances)
    if utterances:
        print(
            f"  YT UTC range: {utterances[0]['utc_str']} → {utterances[-1]['utc_str']}"
        )
    print()

    # ── De-duplicate ──────────────────────────────────────────────────────────
    match_log:     list[dict] = []
    comm_speakers: set[str]   = set()

    if comm_entries:
        print(
            f"  Running de-duplication against {len(comm_entries)} comm entries …"
        )
        t0 = time.monotonic()
        utterances, match_log, comm_speakers = run_dedup(
            utterances, comm_entries, comm_timestamps
        )
        elapsed   = time.monotonic() - t0
        dup_count = sum(1 for u in utterances if u["dup_comm_utc"])
        unique    = total_segs - dup_count

        by_source: dict[str, int] = defaultdict(int)
        for u in utterances:
            if u.get("dup_source"):
                by_source[u["dup_source"]] += 1

        print(
            f"  Done in {elapsed:.1f}s — "
            f"{dup_count} removed ({100 * dup_count / max(1, total_segs):.1f}%)"
            + (", ".join(f"  {v} via {k}" for k, v in by_source.items()) if by_source else "")
            + f"  |  {unique} unique YT utterances kept"
        )

        _save_json(transcript_dir / "_dedup_matches.json", match_log)
        print(f"  Match evidence  → {transcript_dir / '_dedup_matches.json'}")

        if comm_speakers:
            _save_json(transcript_dir / "_comm_speakers.json", sorted(comm_speakers))
            print(
                f"  Comm speakers   → {transcript_dir / '_comm_speakers.json'}: "
                f"{sorted(comm_speakers)}"
            )
    else:
        print("  Skipping de-duplication (no comm entries loaded).")
        for u in utterances:
            u["dup_comm_utc"] = ""
            u["dup_score"]    = None
            u["dup_source"]   = ""

    # ── Debug CSV ─────────────────────────────────────────────────────────────
    csv_path  = transcript_dir / "_transcript.csv"
    dup_count_csv = sum(1 for u in utterances if u.get("dup_comm_utc"))
    write_debug_csv(utterances, csv_path)
    print()
    print(
        f"  Debug CSV       → {csv_path}"
        f"  ({total_segs} rows, {dup_count_csv} marked as removed)"
    )

    # ── PAO web output ────────────────────────────────────────────────────────
    pao       = build_pao_transcript(utterances)
    out_path  = mission.web_dir / "PAO.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for entry in pao:
            # text may contain "|"; since it's always the last column that's fine
            fh.write(f"{entry['t']}|{entry['text']}\n")

    print(f"  PAO output      → {out_path}")
    print(f"    {len(pao)} entries")
    if pao:
        print(f"    UTC range: {pao[0]['t']} → {pao[-1]['t']}")

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
