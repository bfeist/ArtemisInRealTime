"""Prepare compact YouTube PAO audio and recover exact cached utterance offsets.

The historical cumulative clock is used only to recover occurrence identity.
Playback uses offsets in the exact MP4 that produced each cached transcript.
No transcription, duplicate filtering, UTC correction, or assessment is performed.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import subprocess

from shared.comm_encoding import AAC_PROFILE, encode_comm_aac
from shared.pds import atomic_json

CONTRACT = "youtube-pao-audio-v1"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def identity(row):
    return row["t"], row["text"]


def recover_occurrences(rows, transcript_dir, stream_start):
    """Require a unique exact time/text match, retaining repeated broadcasts."""
    start = datetime.fromisoformat(stream_start.replace("Z", "+00:00"))
    parts = []
    for path in Path(transcript_dir).glob("*_part*.json"):
        if path.name.endswith(".progress.json"):
            continue
        content = path.read_bytes()
        data = json.loads(content)
        parts.append((int(data["part"]), path, data, hashlib.sha256(content).hexdigest()))
    parts.sort(key=lambda item: item[0])
    if not parts or [p[0] for p in parts] != list(range(1, len(parts) + 1)):
        raise ValueError("cached transcript parts must be contiguous from part 1")
    candidates, recordings, cumulative = {}, {}, 0.0
    for number, path, data, transcript_hash in parts:
        duration = float(data["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid cached part duration")
        source = data["source"]
        if Path(source).name != source or Path(source).suffix.lower() != ".mp4":
            raise ValueError("cached source must be an MP4 basename")
        recording_id = f"youtube-part-{number:02d}"
        recordings[recording_id] = {"source": source, "duration": duration,
            "transcriptSha256": transcript_hash}
        for segment in data["segments"]:
            seconds, end = float(segment["start"]), float(segment["end"])
            instant = start + timedelta(seconds=cumulative + seconds)
            utc = instant.strftime("%Y-%m-%dT%H:%M:%S.") + f"{instant.microsecond // 1000:03d}Z"
            key = (utc, segment["text"].strip())
            candidates.setdefault(key, []).append((recording_id, seconds, end))
        cumulative += duration
    entries, failures = [], []
    for row in rows:
        matches = candidates.get(identity(row), [])
        if len(matches) != 1:
            failures.append({"t": row["t"], "text": row["text"], "matches": len(matches)})
            continue
        recording_id, seconds, end = matches[0]
        duration = recordings[recording_id]["duration"]
        if not all(math.isfinite(v) for v in (seconds, end)) or not 0 <= seconds < end <= duration + 0.1:
            failures.append({"t": row["t"], "text": row["text"], "reason": "invalid segment bounds"})
            continue
        entries.append({"t": row["t"], "text": row["text"], "recordingId": recording_id,
                        "s": seconds, "d": end - seconds})
    return entries, recordings, failures


def probe_audio(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def prepare_recording(recording_id, recording, input_dir, cache):
    source = (Path(input_dir) / recording["source"]).resolve()
    stat = source.stat()
    recipe = {"source": str(source), "bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns,
              "profile": AAC_PROFILE}
    token = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    relative = f"pao/{recording_id}-{token}.m4a"
    target = Path(cache) / relative
    receipt_path = target.with_suffix(".json")
    reused = False
    if target.exists() and receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        reused = receipt.get("recipe") == recipe and receipt.get("bytes") == target.stat().st_size and receipt.get("sha256") == digest(target)
    if not reused:
        if target.exists():
            raise ValueError(f"Cached PAO derivative changed; inspect before replacing: {target}")
        source_probe = probe_audio(source)
        if abs(float(source_probe["format"]["duration"]) - recording["duration"]) > 0.25:
            raise ValueError(f"MP4 duration differs from cached transcription: {source}")
        encode_comm_aac(source, target)
        if source.stat().st_size != stat.st_size or source.stat().st_mtime_ns != stat.st_mtime_ns:
            raise ValueError(f"Source changed during encoding: {source}")
        probe = probe_audio(target)
        stream = probe["streams"][0]
        duration = float(probe["format"]["duration"])
        if stream["codec_name"] != "aac" or int(stream["sample_rate"]) != AAC_PROFILE["sample_rate"] or stream["channels"] != 1 or abs(duration - recording["duration"]) > 0.25:
            raise ValueError(f"PAO encoding profile/duration mismatch: {target}")
        receipt = {"recipe": recipe, "bytes": target.stat().st_size, "sha256": digest(target),
                   "duration": duration, "probe": probe}
        atomic_json(receipt_path, receipt)
    print(f"{'Reused' if reused else 'Encoded'} {recording_id}: {receipt['bytes'] / 1e6:.1f} MB", flush=True)
    return recording_id, {**recording, **receipt, "audioPath": "comm/" + relative}


def prepare_pao_audio(mission, input_dir, rows, *, stream_start, workers=3, plan_only=False):
    if workers < 1:
        raise ValueError("workers must be positive")
    root = mission.processed_transcripts / "pao_audio"
    root.mkdir(parents=True, exist_ok=True)
    entries, recordings, failures = recover_occurrences(rows, mission.processed_transcripts / "yt", stream_start)
    report = {"contract": CONTRACT, "paoRows": len(rows), "mappedRows": len(entries),
              "failures": failures, "profile": AAC_PROFILE, "streamStartUtc": stream_start,
              "modelRequests": 0, "textChanged": False}
    atomic_json(root / "coverage-report.json", report)
    if failures or not rows:
        raise ValueError(f"PAO recovery incomplete: {len(failures)} failures; see {root / 'coverage-report.json'}")
    print(f"Exactly mapped all {len(entries):,} PAO occurrences", flush=True)
    selected = {row["recordingId"] for row in entries}
    # Preflight every input before starting lengthy encodes.
    for rid in selected:
        if not (Path(input_dir) / recordings[rid]["source"]).is_file():
            raise FileNotFoundError(recordings[rid]["source"])
    if plan_only:
        return report
    prepared = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = [pool.submit(prepare_recording, rid, recordings[rid], input_dir, mission.comm_audio_cache)
                 for rid in sorted(selected)]
        for task in as_completed(tasks):
            rid, recording = task.result()
            prepared[rid] = recording
    prepared = dict(sorted(prepared.items()))
    for entry in entries:
        rid = entry.pop("recordingId")
        seconds, duration = entry.pop("s"), entry.pop("d")
        entry["playback"] = {"source": "youtube", "recordingId": rid, "feed": "pao-primary",
            "audio": {"src": prepared[rid]["audioPath"], "s": seconds, "d": duration}}
    manifest = {**report, "schemaVersion": 1, "complete": True, "entries": entries,
                "recordings": prepared}
    atomic_json(root / "manifest.json", manifest)
    return {**report, "recordings": len(prepared), "audioBytes": sum(r["bytes"] for r in prepared.values()),
            "manifest": str(root / "manifest.json")}


def apply_pao_audio(transcripts, manifest_path):
    """Attach prepared offsets only to identical accepted PAO occurrences."""
    manifest_path = Path(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("contract") != CONTRACT or manifest.get("schemaVersion") != 1 or not manifest.get("complete"):
        raise ValueError("Incomplete PAO audio manifest")
    entries = {identity(row): row["playback"] for row in manifest["entries"]}
    rows = transcripts.get("pao", [])
    if len(entries) != len(manifest["entries"]) or set(entries) != {identity(row) for row in rows}:
        raise ValueError("Prepared PAO audio does not match current PAO occurrences; rerun 2r")
    output = {**transcripts, "pao": [{**row, "playback": entries[identity(row)]} for row in rows]}
    receipts = {r["audioPath"]: {"sha256": r["sha256"], "bytes": r["bytes"]}
                for r in manifest["recordings"].values()}
    for row in output["pao"]:
        playback = row["playback"]
        recording = manifest["recordings"][playback["recordingId"]]
        audio = playback["audio"]
        if audio["src"] != recording["audioPath"] or not 0 <= audio["s"] < audio["s"] + audio["d"] <= recording["duration"] + 0.1:
            raise ValueError("PAO playback outside prepared recording")
    return output, receipts, hashlib.sha256(manifest_bytes).hexdigest()
