"""Prepare already compatible public video/audio on F; keep originals in place.

Uses existing IA/PDS evidence. Scientific images and unneeded MXFs are excluded.
Copies across drives with SHA-256 verification; same-drive IA MP4s use hard links.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from config import MISSIONS
from shared.ia_video_downloads import atomic_json


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"Cannot probe {path.name}")
    return json.loads(result.stdout)


def copy_verified(source, target, sha256):
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    with source.open("rb") as src, part.open("wb") as dst:
        for block in iter(lambda: src.read(8 << 20), b""):
            dst.write(block)
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise RuntimeError(f"Copy checksum mismatch: {source.name}")
    shutil.copystat(source, part)
    part.replace(target)


def prepare(mission):
    state_path = mission.data_dir / "processed" / "web-media.json"
    old = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    jobs = []
    ia = mission.data_dir / "processed" / "ia_video_downloads.json"
    if ia.exists():
        for r in json.loads(ia.read_text(encoding="utf-8"))["records"]:
            if r["status"] == "complete" and r.get("local_path"):
                jobs.append(("ia:" + r["identifier"], Path(r["local_path"]),
                             mission.web_dir / "video" / "ia" / r["filename"], r))
    for family in ("video", "audio", "transcripts"):
        catalog = mission.data_dir / "processed" / "pds" / (family + ".jsonl")
        if catalog.exists():
            for line in catalog.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                if Path(r["path"]).suffix.lower() in {".mp4", ".m4a", ".mp3", ".csv", ".srt"}:
                    jobs.append(("pds:" + r["uri"], Path(r["path"]),
                                 mission.web_dir / family / "pds" / r["bundle"] / r["relative"], r))
    state = {}
    failures = []
    for key, source, target, evidence in jobs:
        record = {"source": str(source), "path": str(target),
                  "url": "/" + target.relative_to(mission.web_dir).as_posix(), "status": "pending"}
        try:
            source_stat = source.stat()
            if source_stat.st_size != evidence.get("size", evidence.get("bytes")):
                raise RuntimeError("Original size differs from verified catalog")
            previous = old.get(key, {})
            if (target.is_file() and previous.get("status") == "ready"
                    and previous.get("source_size") == source_stat.st_size
                    and previous.get("source_mtime_ns") == source_stat.st_mtime_ns
                    and previous.get("size") == target.stat().st_size
                    and previous.get("mtime_ns") == target.stat().st_mtime_ns):
                state[key] = previous
                continue
            info = probe(source) if source.suffix.lower() not in {".csv", ".srt"} else {"streams": []}
            codecs = [s["codec_name"] for s in info["streams"] if s["codec_type"] in {"audio", "video"}]
            if source.suffix.lower() not in {".csv", ".srt"} and (not codecs or any(c not in {"h264", "aac", "mp3"} for c in codecs)):
                record.update(status="needs_conversion", codecs=codecs)
                state[key] = record
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Do not replace existing prepared media without explicit evidence.
            if target.exists():
                raise RuntimeError("Untracked existing web destination; review before replacement")
            if source.anchor.casefold() == target.anchor.casefold():
                os.link(source, target)
                record["method"] = "hardlink"
            else:
                if shutil.disk_usage(target.parent).free < source_stat.st_size + (100 << 30):
                    raise RuntimeError("Web drive would have less than 100 GiB reserve")
                if not evidence.get("sha256"):
                    raise RuntimeError("Cross-drive copy needs verified SHA-256")
                copy_verified(source, target, evidence["sha256"])
                record["method"] = "verified_copy"
            stat = target.stat()
            record.update(status="ready", size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                          source_size=source_stat.st_size, source_mtime_ns=source_stat.st_mtime_ns,
                          codecs=codecs, duration=float(info.get("format", {}).get("duration", 0)))
            print(f"READY {key}: {target}", flush=True)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            record.update(status="failed", error=str(e))
            failures.append(key)
            print(f"FAILED {key}: {e}", flush=True)
        state[key] = record
        atomic_json(state_path, state)
    atomic_json(state_path, state)
    print(f"Web media: {len(state)} records; {len(failures)} failures", flush=True)
    if failures:
        raise RuntimeError("Some web media could not be prepared; see web-media.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, choices=list(MISSIONS))
    args = parser.parse_args()
    prepare(MISSIONS[args.mission])
