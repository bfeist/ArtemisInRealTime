"""Import crew PCD recordings into comm without guessing UTC from NASA IDs."""
from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.pds import atomic_json, nasa_id, utc
from shared.comm_encoding import AAC_PROFILE, encode_comm_aac

BUNDLE = "artemis2_mission_audio"


def utc_time(value, *, csv_utc=False):
    """Require an explicit zone, except for the delivery's documented utc column."""
    if not value or "T" not in value and " " not in value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            if not csv_utc:
                return None
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError):
        return None


def enrich_pcd(record, mission, lookup=None):
    """Use label UTC first; cache exact-ID IO results only when UTC is missing."""
    if record["bundle"] != BUNDLE or record["collection"] != "data_audio_pcd":
        return
    record.update(source_kind="crew_onboard", channel="PCD", time_source="pds_label")
    stamp = utc_time(record.get("start_utc"))
    if stamp:
        record["start_utc"] = stamp
        return
    record["start_utc"] = None
    record["time_source"] = None
    nid = record.get("nasa_id")
    if not nid:
        return
    cache = mission.pds_processed / "io_pcd" / (nid + ".json")
    docs = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
    if docs is None:
        from config import IO_KEY
        from shared.io_api import search_io
        if lookup is None and not IO_KEY:
            return
        # The legacy IO client prints request URLs on failure (including its key).
        with contextlib.redirect_stdout(io.StringIO()):
            response = (lookup or search_io)(nid)
        if not response:
            return  # Do not cache transient failures or misses; retry on the next run.
        docs = [d for d in response["results"]["response"]["docs"]
                if str(d.get("nasa_id", "")).lower() == nid]
        if docs:
            atomic_json(cache, docs)
    matches = {utc_time(d.get("vmd_start_gmt")) for d in docs}
    matches.discard(None)
    record["io_metadata"] = docs
    if len(matches) == 1:
        record.update(start_utc=matches.pop(), time_source="imagery_online")


def _clock(value):
    hours, minutes, seconds = value.strip().replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def read_srt(path):
    segments = []
    for block in re.split(r"\n\s*\n", Path(path).read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if index is None:
            raise ValueError(f"Malformed SRT cue in {path}")
        start, end = lines[index].split(" --> ")
        text = " ".join(lines[index + 1:]).strip()
        match = re.match(r"^\[([^\[\]]+)\]\s+(.*)$", text)
        segments.append({"start": _clock(start), "end": _clock(end),
                         "speaker": match[1] if match else "",
                         "text": match[2] if match else text})
    return segments


def supplied_segments(csv_path, srt_path, duration):
    """Preserve CSV UTC independently of relative SRT playback positions."""
    cues = read_srt(srt_path) if srt_path else []
    if not csv_path:
        return cues
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"audio_time", "utc", "speaker", "text"}.issubset(reader.fieldnames or []):
            raise ValueError(f"Unsupported PCD CSV columns: {csv_path}")
        rows = list(reader)
    if cues and len(cues) != len(rows):
        raise ValueError("PCD CSV/SRT cue counts differ")
    segments = []
    for i, row in enumerate(rows):
        start = _clock(row["audio_time"])
        stamp = utc_time(row["utc"], csv_utc=True)
        if row["utc"] and not stamp:
            raise ValueError(f"Invalid supplied UTC in {csv_path}")
        if cues:
            cue = cues[i]
            # Two delivered SRTs contain replacement characters in place of dashes.
            def comparable(text):
                return " ".join(re.sub(r"[-\u2013\u2014\ufffd]", " ", text).split())
            if (comparable(cue["text"]) != comparable(row["text"])
                    or cue["speaker"] and cue["speaker"] != row["speaker"].strip()):
                raise ValueError(f"PCD CSV/SRT mismatch at cue {i + 1}")
            start = cue["start"]
            end = cue["end"]
        else:
            end = _clock(rows[i + 1]["audio_time"]) if i + 1 < len(rows) else duration
        segment = {"start": start, "end": end, "text": row["text"].strip(),
                   "speaker": row["speaker"].strip(),
                   "csv_audio_start": _clock(row["audio_time"]),
                   "end_basis": "pds_srt" if cues else "next_start_or_audio_end"}
        if stamp:
            segment["utcTime"] = stamp
        segments.append(segment)
    return segments


def _latest(records):
    def version(record):
        label_version = (record.get("metadata", {}).get("version_id") or ["0"])[0]
        name = Path(record["path"]).stem
        file_version = re.search(r"_v(\d+)", name)
        return (tuple(int(n) for n in re.findall(r"\d+", label_version)),
                int(file_version[1]) if file_version else 0, "_raw_" in name, record["path"])
    return max(records, key=version)


def _duration(path):
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe not found on PATH")
    result = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                            check=True, capture_output=True, text=True)
    value = float(result.stdout.strip())
    if not 0 < value < float("inf"):
        raise ValueError("Invalid audio duration")
    return value


def _web_audio(source, target):
    """Use dated comm's mono/22,050 Hz/64 kbps AAC in a seekable M4A container."""
    encode_comm_aac(source, target)


def ensure_pcd_audio(mission, transcript):
    """Upgrade playback encoding without re-transcribing existing PCD utterances."""
    from shared.comm_audio import cached_audio_path
    target = cached_audio_path(mission, transcript["web_src"])
    if (transcript.get("audio_encoding") == AAC_PROFILE
            and target.is_file() and target.stat().st_size):
        return False
    # Prefer the verified original over another lossy playback encode.
    products = [r for r in transcript.get("pds_products", [])
                if r.get("sha256") == transcript.get("audio_sha256") and Path(r["path"]).is_file()]
    source = Path(_latest(products)["path"]) if products else target
    if not source.is_file():
        raise RuntimeError(f"Missing PCD recording for playback conversion: {transcript['web_src']}")
    _web_audio(source, target)
    transcript["audio_encoding"] = dict(AAC_PROFILE)
    return True


def import_pcd(mission, *, force=False, limit=None):
    """Prefer supplied transcripts; WhisperX is loaded only for genuinely absent ones."""
    from shared.io_api import load_jsonl
    catalog_dir = mission.data_dir / "processed" / "pds"
    if not (catalog_dir / "audio.jsonl").exists():
        print("  No PDS audio catalog. Run step 7f first.")
        return
    if not (catalog_dir / "transcripts.jsonl").exists():
        raise RuntimeError("No PDS transcript catalog. Run step 7f before deciding transcripts are absent.")
    audio = [r for r in load_jsonl(catalog_dir / "audio.jsonl")
             if r["bundle"] == BUNDLE and r["collection"] == "data_audio_pcd" and r.get("nasa_id")]
    transcripts = [r for r in load_jsonl(catalog_dir / "transcripts.jsonl")
                   if r["bundle"] == BUNDLE and r["collection"] == "data_transcripts_pcd"
                   and Path(r["path"]).suffix.lower() in {".csv", ".srt"}]
    expected = {}
    unavailable_products = []
    inventory = mission.pds_processed / "inventory.sqlite"
    if inventory.exists():
        with contextlib.closing(sqlite3.connect(inventory.as_uri() + "?mode=ro", uri=True)) as conn:
            for relative, status in conn.execute("SELECT relative,status FROM remote WHERE active=1 AND bundle=? AND collection='data_audio_pcd'", (BUNDLE,)):
                if Path(relative).suffix.lower() == ".m4a":
                    expected.setdefault(nasa_id(relative), []).append(status)
                    if status not in {"verified", "verified_size_only"}:
                        unavailable_products.append({"relative": relative, "status": status})
            supplied_ids = {nasa_id(r[0]) for r in conn.execute("SELECT relative FROM remote WHERE active=1 AND bundle=? AND collection='data_transcripts_pcd' AND (relative LIKE '%.csv' OR relative LIKE '%.srt')", (BUNDLE,))}
    else:
        supplied_ids = set()
    grouped = {}
    for rec in audio:
        grouped.setdefault(rec["nasa_id"], []).append(rec)
    report = {"date": utc(), "recordings": [], "errors": 0,
              "unavailable_products": unavailable_products}
    resources = None
    selected = sorted(grouped)[:limit] if limit is not None else sorted(grouped)
    for nid in selected:
        rec = dict(_latest(grouped[nid]))
        state = {"nasa_id": nid}
        report["recordings"].append(state)
        try:
            enrich_pcd(rec, mission)
            if not rec.get("start_utc"):
                state["status"] = "missing_utc"
                continue
            source = Path(rec["path"])
            matching = [t for t in transcripts if t.get("nasa_id") == nid]
            csvs = [t for t in matching if Path(t["path"]).suffix.lower() == ".csv"]
            srts = [t for t in matching if Path(t["path"]).suffix.lower() == ".srt"]
            chosen = [_latest(group) for group in (csvs, srts) if group]
            if not chosen and nid in supplied_ids:
                state["status"] = "awaiting_supplied_transcript"
                continue
            fingerprint = hashlib.sha256(json.dumps([1, rec, chosen], sort_keys=True).encode()).hexdigest()
            out = mission.processed_transcripts / "comm" / "pcd" / (nid + ".json")
            web_src = "pcd/" + nid + ".m4a"
            from shared.comm_audio import cached_audio_path
            web_audio = cached_audio_path(mission, web_src)
            previous = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
            if not force and previous.get("pds_fingerprint") == fingerprint and web_audio.exists():
                changed = ensure_pcd_audio(mission, previous)
                if changed:
                    atomic_json(out, previous)
                state.update(status="audio_updated" if changed else "cached", segments=len(previous["segments"]),
                             timing_adjustments=len(previous.get("timing_adjustments", [])))
                continue
            duration = _duration(source)
            if chosen:
                segments = supplied_segments(_latest(csvs)["path"] if csvs else None,
                                             _latest(srts)["path"] if srts else None, duration)
                transcript_source = "pds_supplied"
            else:
                transcriber = importlib.import_module("1_comm.1b_transcribe")
                if resources is None:
                    resources = transcriber.WhisperResources(
                        os.environ.get("WHISPER_DEVICE", "cuda"),
                        os.environ.get("WHISPER_COMPUTE_TYPE", "float16"),
                        int(os.environ.get("WHISPER_BATCH_SIZE", "16")))
                # Decode compressed PCD audio to WhisperX's float32 mono input.
                import numpy as np
                decoded = subprocess.run([transcriber._ffmpeg_exe(), "-v", "error", "-nostdin",
                    "-i", str(source), "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
                    check=True, capture_output=True)
                segments = resources.transcribe(np.frombuffer(decoded.stdout, dtype=np.float32), language="en").get("segments", [])
                transcript_source = "whisperx_large-v3"
            base = datetime.fromisoformat(rec["start_utc"].replace("Z", "+00:00"))
            adjustments = []
            for index, segment in enumerate(segments):
                segment.setdefault("utcTime", (base + timedelta(seconds=segment["start"])).isoformat().replace("+00:00", "Z"))
                start, end = segment["start"], segment["end"]
                if not 0 <= start <= end <= duration:
                    # Keep the supplied text/UTC and original offsets for audit,
                    # but bound browser seeking to the actual media duration.
                    segment.update(supplied_start=start, supplied_end=end)
                    segment["start"] = min(duration, max(0, start))
                    segment["end"] = max(segment["start"], min(duration, end))
                    adjustments.append({"segment": index, "utcTime": segment["utcTime"],
                                        "supplied_start": start, "supplied_end": end})
            if (force or previous.get("audio_sha256") != rec["sha256"] or not web_audio.exists()
                    or previous.get("audio_encoding") != AAC_PROFILE):
                _web_audio(source, web_audio)
            payload = {"version": 1, "source": source.name, "source_kind": "crew_onboard",
                       "channel": "PCD", "nasa_id": nid, "utcTime": rec["start_utc"],
                       "time_source": rec["time_source"], "tz_corrected": True,
                       "duration": duration, "language": "en", "web_src": web_src,
                       "transcript_source": transcript_source, "audio_sha256": rec["sha256"],
                       "audio_encoding": dict(AAC_PROFILE),
                       "pds_fingerprint": fingerprint, "pds_products": grouped[nid],
                       "pds_transcripts": chosen, "segments": segments,
                       "timing_adjustments": adjustments}
            atomic_json(out, payload)
            state.update(status="imported", transcript_source=transcript_source, segments=len(segments),
                         timing_adjustments=len(adjustments))
            print(f"  {nid}: {len(segments)} segments ({transcript_source})", flush=True)
        except Exception as exc:
            state.update(status="error", error=str(exc))
            report["errors"] += 1
            print(f"  {nid}: {exc}")
    for nid in sorted(expected.keys() - grouped.keys() - {None}):
        report["recordings"].append({"nasa_id": nid, "status": "audio_unavailable", "transfer_status": expected[nid]})
    report["counts"] = {status: sum(r["status"] == status for r in report["recordings"])
                        for status in sorted({r["status"] for r in report["recordings"]})}
    atomic_json(catalog_dir / "pcd_comm_report.json", report)
    print(f"  PCD comm: {report['counts']}")
    if report["errors"]:
        raise RuntimeError(f"{report['errors']} PCD imports failed; see pcd_comm_report.json")
