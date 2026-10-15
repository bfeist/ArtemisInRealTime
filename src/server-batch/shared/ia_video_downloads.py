"""Plan and resume mission coverage transfers from Internet Archive."""

import hashlib
import json
import os
import re
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from shared.ia_helpers import discover_by_subject, get_item_metadata
from shared.io_api import search_io_collection

NASA_ID = re.compile(r"(?:^|_) (art\d{3}m\d{10}[a-z]?)(?=[_.-]|$)", re.I | re.X)
RECAP = re.compile(r"highlight|recap", re.I)
RANGE_BYTES = 64 << 20


@contextmanager
def plan_lock(path):
    """Prevent two intake processes from writing the same plan/partials."""
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("A downloader already holds this mission plan") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def coverage_exclusion(doc, mission):
    """Keep continuous feeds even when they contain a highlights segment."""
    if not doc:
        return "no_exact_io_mission_match"
    title = doc.get("md_title") or ""
    if RECAP.search(title) and "continuous coverage" not in title.lower():
        return "standalone_highlights_or_recap"
    start = doc.get("vmd_start_gmt") or ""
    if not start or not mission.mission_start <= start[:10] <= mission.mission_end:
        return "outside_mission_dates"
    return None


def select_mp4(files):
    """One complete MP4 per recording, preferring duration then resolution.

    Never fall back to MXF/MOV. Zero-sized and private files are not usable.
    """
    candidates = [f for f in files if f.get("name", "").lower().endswith(".mp4")
                  and int(f.get("size") or 0) > 0 and not f.get("private")]
    if not candidates:
        return None
    def rank(f):
        return (float(f.get("length") or f.get("duration") or 0),
                int(f.get("height") or 0), int(f.get("size") or 0))
    return max(candidates, key=rank)


def build_plan(mission, workers=4):
    # User coverage decisions survive rediscovery, even if IA adds an MP4.
    saved = {}
    completed = {}
    for name in ("ia_video_downloads.json", "ia_video_originals.json"):
        path = mission.data_dir / "processed" / name
        if path.exists():
            prior = json.loads(path.read_text(encoding="utf-8"))
            if prior.get("mission") != mission.slug:
                raise ValueError("Saved video plan belongs to another mission")
            for record in prior.get("records", []):
                if record.get("status") == "unneeded":
                    saved[record["identifier"]] = record
                elif name == "ia_video_downloads.json" and record.get("status") == "complete":
                    completed[record["identifier"]] = record
    if not mission.io_nasatv_cid:
        raise ValueError("Mission coverage requires an IO NASA TV collection")
    docs = search_io_collection(mission.io_nasatv_cid, asset_type=2)
    if not docs:
        raise RuntimeError("IO catalog lookup failed; no downloads planned")
    from shared.io_api import save_jsonl
    save_jsonl(mission.io_cache / "io_nasatv_catalog.jsonl", docs)
    by_id = {}
    for doc in docs:
        by_id.setdefault(doc["nasa_id"].lower(), []).append(doc)
    catalog = discover_by_subject(mission.ia_subject_tag, media_type="movies")
    atomic_json(mission.data_dir / "processed" / "ia_video_catalog.json", catalog)
    eligible, excluded = [], []
    for item in catalog:
        identifier = item["identifier"]
        match = NASA_ID.search(identifier)
        matches = by_id.get(match.group(1).lower(), []) if match else []
        doc = matches[0] if len(matches) == 1 else None
        reason = coverage_exclusion(doc, mission)
        if reason:
            excluded.append({"identifier": identifier, "reason": reason,
                             "title": doc.get("md_title") if doc else item.get("title")})
        else:
            eligible.append((identifier, doc))

    def inspect(pair):
        identifier, doc = pair
        record = {"identifier": identifier, "nasa_id": doc["nasa_id"],
                  "title": doc.get("md_title"), "recorded_at": doc.get("vmd_start_gmt"),
                  "end_utc": doc.get("vmd_end_gmt"), "io_duration_seconds": doc.get("duration_seconds"),
                  "source_url": f"https://archive.org/details/{quote(identifier)}",
                  "io_url": f"https://io.jsc.nasa.gov/app/info.cfm?pid={doc['id']}"}
        if identifier in saved:
            return dict(saved[identifier], **record, status="unneeded")
        meta = get_item_metadata(identifier)
        if not meta:
            return dict(record, status="metadata_failed")
        selected = select_mp4(meta.get("files", []))
        if not selected:
            return dict(record, status="no_mp4_available")
        filename = Path(selected["name"]).name
        # Flat NASA filenames must not collide between IA items.
        if filename != selected["name"]:
            filename = identifier.replace(".", "_") + "__" + filename
        duration = float(selected.get("length") or selected.get("duration") or 0)
        expected = float(doc.get("duration_seconds") or 0)
        result = dict(record, status="pending", filename=filename,
                    download_url=f"https://archive.org/download/{quote(identifier)}/{quote(selected['name'], safe='/')}",
                    bytes=int(selected["size"]), md5=selected.get("md5"),
                    duration_seconds=duration, duration_mismatch=abs(duration - expected) > 1,
                    local_path=str(mission.raw_video_ia / filename))
        previous = completed.get(identifier)
        if previous and all(previous.get(k) == result.get(k) for k in ("bytes", "md5", "local_path")):
            path = Path(previous["local_path"])
            if path.is_file() and path.stat().st_size == result["bytes"]:
                result["status"] = "complete"
        return result

    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, future in enumerate(as_completed([pool.submit(inspect, p) for p in eligible]), 1):
            records.append(future.result())
            if i % 25 == 0:
                print(f"  Inspected {i}/{len(eligible)} mission items", flush=True)
    records.sort(key=lambda r: (r["recorded_at"], r["identifier"]))
    # Keep decisions for recordings no longer returned by discovery as well.
    discovered = {r["identifier"] for r in records}
    records.extend(r for key, r in saved.items() if key not in discovered)
    paths = [r["local_path"] for r in records if "local_path" in r]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate MP4 destination filenames in plan")
    return {"mission": mission.slug, "created_at": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(mission.raw_video_ia), "records": records, "excluded": excluded}


def checksum(path):
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_file(path, record):
    return (path.is_file() and path.stat().st_size == record["bytes"]
            and (not record.get("md5") or checksum(path) == record["md5"]))


def fetch_mp4(record, attempts=5):
    """MP4-only entry point: raw formats require the separate original intake."""
    return fetch_media(record, attempts=attempts, allowed_extensions={".mp4"})


def fetch_media(record, attempts=5, allowed_extensions=None):
    """Resume to .part; limit consecutive failures, not lifetime failures."""
    record = {k: v for k, v in record.items() if k not in {"error", "retryable"}}
    dest = Path(record["local_path"])
    if dest.suffix.lower() not in (allowed_extensions or {".mp4"}):
        raise ValueError("Destination format is not allowed by this intake")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if valid_file(dest, record):
        return dict(record, status="complete", reused=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"  Downloading: {dest.name}", flush=True)
    failures = 0
    retryable = False
    error_name = None
    reported = time.monotonic()
    while failures < attempts:
        starting_size = part.stat().st_size if part.exists() else 0
        try:
            offset = part.stat().st_size if part.exists() else 0
            if offset >= record["bytes"]:
                if valid_file(part, record):
                    part.replace(dest)
                    return dict(record, status="complete")
                part.unlink()
                offset = 0
            while offset < record["bytes"]:
                # Bounded ranges avoid IA's slow full-file responses. Requests
                # remain sequential within each file, with a capped worker pool.
                end = min(offset + RANGE_BYTES - 1, record["bytes"] - 1)
                headers = {"Accept-Encoding": "identity", "Range": f"bytes={offset}-{end}"}
                with requests.get(record["download_url"], headers=headers, stream=True,
                                  timeout=(30, 120)) as response:
                    response.raise_for_status()
                    if response.status_code == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if (not match or int(match[1]) != offset or int(match[2]) != end
                                or int(match[3]) != record["bytes"]):
                            raise ValueError("Invalid range response")
                        mode = "ab" if offset else "wb"
                        expected_end = end + 1
                    elif response.status_code == 200:
                        mode = "wb"  # Server ignored Range: restart, never append.
                        expected_end = record["bytes"]
                    else:
                        raise ValueError(f"Unexpected HTTP {response.status_code}")
                    with part.open(mode) as handle:
                        for block in response.iter_content(chunk_size=1 << 20):
                            if block:
                                handle.write(block)
                            if time.monotonic() - reported >= 60:
                                handle.flush()
                                print(f"  Progress: {dest.name}: {handle.tell():,}/{record['bytes']:,} bytes", flush=True)
                                reported = time.monotonic()
                        offset = handle.tell()
                if offset != expected_end:
                    raise requests.exceptions.ChunkedEncodingError("Incomplete range transfer")
                failures = 0
            if not valid_file(part, record):
                if part.stat().st_size >= record["bytes"]:
                    part.unlink()
                return dict(record, status="failed", retryable=False,
                            error="Downloaded media failed size/checksum validation")
            part.replace(dest)
            return dict(record, status="complete", reused=False)
        except (requests.RequestException, OSError, ValueError) as error:
            # Never print URLs from exception text; record only a safe error class.
            error_name = type(error).__name__
            retryable = isinstance(error, (requests.Timeout, requests.ConnectionError,
                                          requests.exceptions.ChunkedEncodingError))
            if isinstance(error, requests.HTTPError) and error.response is not None:
                retryable = error.response.status_code in {408, 429, 500, 502, 503, 504}
            current_size = part.stat().st_size if part.exists() else 0
            if current_size > starting_size:
                failures = 0
            failures += 1
            print(f"  Retry {failures}/{attempts}: {dest.name} ({error_name}); "
                  f"resume at {current_size:,}/{record['bytes']:,} bytes", flush=True)
            if isinstance(error, requests.HTTPError) and not retryable:
                break
            if failures < attempts:
                time.sleep(min(60, 2 ** failures))
    return dict(record, status="failed", retryable=retryable,
                error=f"{error_name}: download failed after consecutive retries; partial retained")


def add_retry_arguments(parser):
    parser.add_argument("--retry-rounds", type=int, default=0,
                        help="Automatic retry rounds for network failures (0: keep retrying)")
    parser.add_argument("--retry-delay", type=int, default=300,
                        help="Seconds between network retry rounds (default: 300)")


def partial_size(record):
    # A worker can rename a completed partial while progress is being sampled.
    try:
        return Path(record["local_path"] + ".part").stat().st_size
    except FileNotFoundError:
        return 0


def run_transfers(path, download, indices, workers, retry_rounds=0, retry_delay=300):
    """Persist live progress and retry temporary failures from retained bytes."""
    if retry_rounds < 0 or retry_delay < 1:
        raise ValueError("Retry rounds must be nonnegative and retry delay positive")
    plan = json.loads(path.read_text(encoding="utf-8"))
    records = plan["records"]

    def save(status):
        plan["status"] = status
        plan["updated_at"] = datetime.now(timezone.utc).isoformat()
        plan["summary"] = dict(Counter(r["status"] for r in records))
        atomic_json(path, plan)

    def progress(active):
        for i in active.values():
            record = records[i]
            size = partial_size(record)
            if size > record.get("downloaded_bytes", 0):
                record["last_progress_at"] = datetime.now(timezone.utc).isoformat()
            record["downloaded_bytes"] = size

    round_number = 0
    try:
        while indices:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                active = {}
                queued = deque(indices)
                for i in indices:
                    records[i]["status"] = "pending"

                def start_next():
                    while queued and len(active) < workers:
                        i = queued.popleft()
                        records[i].pop("error", None)
                        records[i].pop("retryable", None)
                        active[pool.submit(download, dict(records[i]))] = i
                        records[i]["status"] = "downloading"

                start_next()
                progress(active)
                save("running")
                while active:
                    done, _ = wait(active, timeout=30, return_when=FIRST_COMPLETED)
                    progress(active)
                    for future in done:
                        i = active.pop(future)
                        last_progress = records[i].get("last_progress_at")
                        try:
                            records[i] = future.result()
                        except Exception as error:
                            records[i] = dict(records[i], status="failed", retryable=False,
                                              error=type(error).__name__)
                        if last_progress:
                            records[i]["last_progress_at"] = last_progress
                        records[i]["downloaded_bytes"] = (
                            records[i]["bytes"] if records[i]["status"] == "complete"
                            else partial_size(records[i]))
                        print(f"  {records[i]['status']}: {records[i]['filename']}", flush=True)
                    start_next()
                    save("running")
            indices = [i for i in indices if records[i]["status"] == "failed"
                       and records[i].get("retryable")]
            if not indices or (retry_rounds and round_number >= retry_rounds):
                break
            round_number += 1
            plan["retry_round"] = round_number
            save("waiting_to_retry")
            print(f"  Retrying {len(indices)} network failures in {retry_delay}s "
                  f"(round {round_number}); partials retained", flush=True)
            time.sleep(retry_delay)
        save("finished" if all(r["status"] in {"complete", "no_mp4_available", "unneeded"}
                               for r in records) else "incomplete")
    except KeyboardInterrupt:
        save("interrupted")
        raise
    print(f"  Download result: {plan['summary']}", flush=True)
    return plan["status"] == "finished"


def run_plan(path, workers=4, retry_rounds=0, retry_delay=300):
    plan = json.loads(path.read_text(encoding="utf-8"))
    records = plan["records"]
    tasks = [i for i, r in enumerate(records) if r.get("local_path")
             and r["status"] != "unneeded"]
    return run_transfers(path, fetch_mp4, tasks, workers, retry_rounds, retry_delay)
