"""Authoritative PDS loop transcripts and interval-based comm replacement."""
from __future__ import annotations

import bisect
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from shared.pds import atomic_json, utc
from shared.pds_comm import BUNDLE, _clock, _duration, _latest, utc_time

SCHEMA = 3
CLOCK_TOLERANCE = 1.0  # CSV audio offsets are only whole seconds.


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def loop_channel(source):
    match = re.search(r"(?:_OE_Comp_|_oe)([12])(?:_|\.)", source, re.I)
    return "OE-" + match[1] if match else None


def _key(record):
    return re.sub(r"_(?:raw|src|trn-csv|trn-srt)_v\d+$", "", Path(record["path"]).stem)


def merge_intervals(intervals, tolerance=0):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1] + tolerance:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _silences(record, mission, duration):
    """Detect trailing quiet for playback only; this never defines UTC coverage."""
    cache = mission.pds_processed / "loop_silence" / (record["sha256"] + ".json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    result = subprocess.run([exe, "-hide_banner", "-nostdin", "-i", record["path"],
        "-af", "silencedetect=noise=-40dB:d=0.35", "-f", "null", "-"],
        check=True, capture_output=True, text=True)
    intervals = []
    start = None
    for match in re.finditer(r"silence_(start|end):\s*([\d.]+)", result.stderr):
        if match[1] == "start":
            start = float(match[2])
        elif start is not None:
            intervals.append([start, float(match[2])])
            start = None
    if start is not None:
        intervals.append([start, duration])
    atomic_json(cache, intervals)
    return intervals


def _playback_end(start, end, silences):
    # Trim trailing quiet and isolated subsecond clicks before the next cue.
    # Keep short responses near this cue's start. Radio squelch clicks at the
    # far end of a long silent interval must not turn a response into an hour.
    cursor = end
    for quiet_start, quiet_end in reversed(silences):
        if quiet_start >= cursor:
            continue
        gap = max(0, cursor - quiet_end)
        if gap > 0.75 or (gap > 0 and quiet_end <= start + 2):
            break
        cursor = min(cursor, quiet_start)
        if cursor <= start:
            return start
    return min(end, cursor + 0.1) if cursor < end else end


def _remux(source, target):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + ".tmp.m4a")
    try:
        subprocess.run([exe, "-v", "error", "-nostdin", "-y", "-i", str(source),
                        "-map", "0:a:0", "-c:a", "copy", "-movflags", "+faststart", str(tmp)],
                       check=True, capture_output=True)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def normalize_loop(csv_path, duration, silences):
    """Keep official UTC and CSV audio positions as independent coordinates.

    Clock jumps reveal edits in the source audio. No authority is extended across
    those omitted intervals, or across rows without a usable absolute timestamp.
    SRT timings are deliberately not used: delivered cues can be missing, span
    long silence, or contain minute/second errors.
    """
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"audio_time", "utc", "speaker", "text"}.issubset(reader.fieldnames or []):
            raise ValueError(f"Unsupported loop CSV columns: {csv_path}")
        rows = [dict(row, source_row=i + 1) for i, row in enumerate(reader)]
    rows.sort(key=lambda row: _clock(row["audio_time"]))
    order_repairs = [{"source_row": row["source_row"], "playback_order": i + 1}
                     for i, row in enumerate(rows) if row["source_row"] != i + 1]
    offsets = [_clock(r["audio_time"]) for r in rows]
    if any(not 0 <= s <= duration for s in offsets):
        raise ValueError(f"Invalid loop audio positions: {csv_path}")
    stamps = [utc_time(r["utc"], csv_utc=True) for r in rows]
    repairs = []
    for i, row in enumerate(rows):
        # The delivery contains HH:MM:SSffff with a missing decimal point.
        # Repair punctuation only when BOTH adjacent UTC/audio clocks corroborate it.
        if stamps[i] is None and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d{6}", row["utc"]):
            candidate = utc_time(row["utc"][:19] + "." + row["utc"][19:], csv_utc=True)
            if candidate and 0 < i < len(rows) - 1 and stamps[i - 1] and stamps[i + 1]:
                basis = timestamp(candidate) - offsets[i]
                if all(abs(timestamp(stamps[j]) - offsets[j] - basis) <= CLOCK_TOLERANCE for j in (i - 1, i + 1)):
                    stamps[i] = candidate
                    repairs.append({"row": row["source_row"], "supplied_utc": row["utc"], "utcTime": candidate,
                                    "reason": "missing_decimal_corroborated_by_adjacent_clocks"})
    known = [timestamp(stamp) for stamp in stamps if stamp]
    if known != sorted(known):
        raise ValueError(f"Loop UTC runs backwards after ordering by audio position: {csv_path}")
    segments, untimed, intervals = [], [], []
    for i, row in enumerate(rows):
        start = offsets[i]
        end = offsets[i + 1] if i + 1 < len(rows) else duration
        segment = {"start": start, "end": _playback_end(start, end, silences),
                   "text": row["text"].strip(), "speaker": row["speaker"].strip(),
                   "row": row["source_row"], "supplied_utc": row["utc"], "audio_span_end": end,
                   "end_basis": "next_csv_audio_position_with_trailing_quiet_removed"}
        if not stamps[i]:
            untimed.append(segment)
            continue
        segment["utcTime"] = stamps[i]
        segments.append(segment)
        if segment["text"]:
            absolute = timestamp(stamps[i])
            intervals.append([absolute, absolute + end - start])
    intervals = merge_intervals(intervals, CLOCK_TOLERANCE)
    return {"segments": segments, "untimed_segments": untimed, "utc_repairs": repairs,
            "row_order_repairs": order_repairs,
            "authority_intervals": [{"start": iso(a), "end": iso(b)} for a, b in intervals]}


def import_loops(mission, *, force=False):
    from shared.io_api import load_jsonl
    directory = mission.data_dir / "processed/pds"
    if not (directory / "transcripts.jsonl").exists():
        print("  No PDS transcript catalog. Run step 7f first.")
        return
    transcripts = [r for r in load_jsonl(directory / "transcripts.jsonl")
                   if r["bundle"] == BUNDLE and r["collection"] == "data_transcripts_loops"
                   and Path(r["path"]).suffix.lower() == ".csv"
                   and loop_channel(Path(r["path"]).name)]
    audio = [r for r in load_jsonl(directory / "audio.jsonl")
             if r["bundle"] == BUNDLE and r["collection"] == "data_audio_loops"]
    groups = {}
    for rec in transcripts:
        groups.setdefault(_key(rec), []).append(rec)
    report = {"date": utc(), "recordings": [], "errors": 0}
    for key, versions in sorted(groups.items()):
        state = {"recording": key}
        report["recordings"].append(state)
        try:
            csv_rec = _latest(versions)
            matching = [r for r in audio if _key(r) == key]
            if not matching:
                raise ValueError("Corrected loop transcript has no verified audio product")
            audio_rec = _latest(matching)
            channel = loop_channel(Path(csv_rec["path"]).name)
            if not channel:
                raise ValueError("Cannot determine loop channel")
            fingerprint = hashlib.sha256(json.dumps([SCHEMA, csv_rec, audio_rec], sort_keys=True).encode()).hexdigest()
            target = mission.processed_transcripts / "comm/loops" / (key + ".json")
            web_src = "loops/" + key + ".m4a"
            from shared.comm_audio import cached_audio_path
            web_audio = cached_audio_path(mission, web_src)
            previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            if not force and previous.get("pds_fingerprint") == fingerprint and web_audio.exists():
                payload = previous
                state["status"] = "cached"
            else:
                duration = _duration(Path(audio_rec["path"]))
                payload = normalize_loop(csv_rec["path"], duration, _silences(audio_rec, mission, duration))
                payload.update(version=SCHEMA, source=Path(audio_rec["path"]).name,
                    source_kind="mission_voice_loop", channel=channel, transcript_source="pds_supplied",
                    utcTime=utc_time(audio_rec.get("start_utc")), time_source="pds_csv_utterance_utc",
                    tz_corrected=True, duration=duration, language="en", web_src=web_src,
                    pds_products=matching, pds_transcripts=versions, pds_fingerprint=fingerprint,
                    audio_sha256=audio_rec["sha256"])
                if force or previous.get("audio_sha256") != audio_rec["sha256"] or not web_audio.exists():
                    _remux(audio_rec["path"], web_audio)
                atomic_json(target, payload)
                state["status"] = "imported"
            state.update(segments=len(payload["segments"]), untimed=len(payload["untimed_segments"]),
                         utc_repairs=payload["utc_repairs"], row_order_repairs=payload["row_order_repairs"],
                         untimed_rows=[{"row": s["row"], "supplied_utc": s["supplied_utc"], "text": s["text"]}
                                       for s in payload["untimed_segments"]],
                         authority_intervals=payload["authority_intervals"])
            print(f"  {key}: {state['status']}, {state['segments']} timed / {state['untimed']} untimed utterances", flush=True)
        except Exception as exc:
            state.update(status="error", error=str(exc))
            report["errors"] += 1
    atomic_json(directory / "loop_comm_import_report.json", report)
    if report["errors"]:
        raise RuntimeError("Loop transcript imports failed; see loop_comm_import_report.json")


def apply_loop_precedence(rows, sources, report_dir):
    """Replace whole AI utterances by channel/time, independent of segmentation.

    Text is never sliced according to a proportional time estimate. Whole AI
    utterances intersecting authority are superseded; boundary overhangs are
    recorded in the audit along with the full original text and audio reference.
    """
    authority = {}
    source_intervals = {}
    for name, source in sources.items():
        if source.get("source_kind") == "mission_voice_loop" and source.get("transcript_source") == "pds_supplied":
            intervals = [(timestamp(w["start"]), timestamp(w["end"])) for w in source["authority_intervals"]]
            authority.setdefault(source["channel"], []).extend(intervals)
            source_intervals[name] = (source["channel"], intervals)
    authority = {ch: merge_intervals(intervals) for ch, intervals in authority.items()}
    indexes = {ch: [start for start, end in intervals] for ch, intervals in authority.items()}
    kept, replaced = [], []
    for row in rows:
        channel = loop_channel(row[3])
        # PCD, supplied loop transcripts and unrelated channels have separate authority.
        if row[3] in sources or channel not in authority or not row[0]:
            kept.append(row)
            continue
        start = timestamp(row[0])
        end = start + max(float(row[2]), 0.000001)
        intervals = authority[channel]
        index = max(0, bisect.bisect_right(indexes[channel], start) - 1)
        overlaps = []
        for left, right in intervals[index:]:
            if left >= end:
                break
            if right > start:
                overlaps.append((max(start, left), min(end, right)))
        if not overlaps:
            kept.append(row)
            continue
        overhang = max(0, end - start - sum(right - left for left, right in overlaps))
        replaced.append({"t": row[0], "s": row[1], "d": row[2], "src": row[3], "text": row[4],
                         "channel": channel, "reason": "superseded_by_pds_loop_coverage",
                         "replacement_sources": [name for name, (ch, windows) in source_intervals.items()
                                                 if ch == channel and any(a < end and b > start for a, b in windows)],
                         "outside_coverage_seconds": round(overhang, 3)})
    report_dir.mkdir(parents=True, exist_ok=True)
    audit = report_dir / "superseded_comm.jsonl"
    tmp = audit.with_name(audit.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        for record in replaced:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, audit)
    report = {"date": utc(), "superseded_ai_utterances": len(replaced),
              "boundary_overlaps": sum(r["outside_coverage_seconds"] > 0 for r in replaced),
              "boundary_overlaps_over_one_second": sum(r["outside_coverage_seconds"] > 1 for r in replaced),
              "authority_intervals": {ch: [{"start": iso(a), "end": iso(b)} for a, b in intervals]
                                      for ch, intervals in authority.items()},
              "audit": str(audit), "output_rows": len(kept)}
    atomic_json(report_dir / "loop_comm_merge_report.json", report)
    print(f"  Superseded {len(replaced)} AI utterances with corrected PDS loop coverage")
    return kept
