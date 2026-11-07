"""Model-free PAO coverage and video synchronization contracts.

Cached transcripts establish candidate coverage, never acoustic equivalence.
All intervals are half open. No function mutates a transcript or its IDs.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import statistics
from pathlib import Path


def epoch(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp requires timezone")
    return parsed.timestamp()


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite timing")
    return value


def plan_coverage(recordings, scope):
    """Partition each explicit feed scope; IA preferred only within real intervals.

    An unverified IA interval remains a candidate selection. Consumers must not
    mistake this planning result for a reviewed source handoff.
    """
    result = []
    for window in sorted(scope, key=lambda x: (x["feed"], epoch(x["startUtc"]))):
        start, end = epoch(window["startUtc"]), epoch(window["endUtc"])
        if end <= start:
            raise ValueError("empty scope")
        candidates = []
        cuts = {start, end}
        for row in recordings:
            if row["feed"] != window["feed"]:
                continue
            a, b = epoch(row["startUtc"]), epoch(row["endUtc"])
            if b <= a:
                raise ValueError("empty recording interval")
            if a < end and b > start:
                candidates.append((a, b, row))
                cuts.update((max(start, a), min(end, b)))
        cuts = sorted(cuts)
        for a, b in zip(cuts, cuts[1:]):
            active = sorted((r for x, y, r in candidates if x <= a and y >= b),
                            key=lambda r: (r["source"] != "ia", r.get("verification") != "verified", r["id"]))
            selected = active[0] if active else None
            result.append({"feed": window["feed"], "startUtc": utc(a), "endUtc": utc(b),
                           "recordingId": selected["id"] if selected else None,
                           "source": selected["source"] if selected else "unknown",
                           "verification": selected.get("verification", "candidate") if selected else "unknown",
                           "reason": "ia-preferred" if selected and selected["source"] == "ia" else "ia-uncovered" if selected else "no-timed-cache",
                           "alternatives": [r["id"] for r in active[1:]]})
    for left, right in zip(result, result[1:]):
        if left["feed"] == right["feed"] and epoch(left["endUtc"]) > epoch(right["startUtc"]):
            raise ValueError("overlapping feed scopes")
    return result


def validate_part_manifest(manifest):
    """Require measured original offsets, not (part number - 1) * chunk size."""
    parts = manifest.get("parts", [])
    previous_start = None
    numbers = []
    names, uploads = set(), set()
    for part in parts:
        numbers.append(part["part"])
        start = finite(part["originalStartSeconds"])
        duration = finite(part["durationSeconds"])
        if start < 0 or duration <= 0:
            raise ValueError("invalid part timing")
        if previous_start is not None and start <= previous_start:
            raise ValueError("reordered parts")
        previous_start = start
        name = part["filename"]
        if name in names:
            raise ValueError("duplicate part filename")
        names.add(name)
        if part.get("uploadedVideoId"):
            if part["uploadedVideoId"] in uploads or not part.get("uploadProvenance"):
                raise ValueError("duplicate upload or missing upload provenance")
            uploads.add(part["uploadedVideoId"])
    if numbers != list(range(1, len(parts) + 1)):
        raise ValueError("missing/reordered parts")
    return parts


def import_upload_state(manifest, state_path):
    """Import filename-to-ID state without network access or changing the original."""
    result = json.loads(json.dumps(manifest))
    raw = Path(state_path).read_bytes()
    state = json.loads(raw)
    provenance = {"kind": "yt-upload-state", "sha256": hashlib.sha256(raw).hexdigest()}
    for part in result["parts"]:
        if part["filename"] in state:
            part["uploadedVideoId"] = state[part["filename"]]
            part["uploadProvenance"] = provenance
    validate_part_manifest(result)
    return result


def compact_sync_projection(manifest, part_manifest):
    """Publish only reviewed piecewise mappings to exact uploaded identities.

    Each piece has its own UTC/video rate, allowing drift and cuts. Loose text
    anchors and inferred uniform chunk durations cannot produce live mappings.
    """
    parts = {p["filename"]: p for p in validate_part_manifest(part_manifest)}
    output = []
    for piece in manifest.get("mappings", []):
        if piece.get("verification") != "verified":
            continue
        evidence = piece.get("evidence", [])
        if not any(e.get("kind") in {"acoustic", "manual-audio-review"} and e.get("reference") for e in evidence):
            raise ValueError("verified mapping requires audio evidence")
        part = parts[piece["partFilename"]]
        if not part.get("uploadedVideoId"):
            raise ValueError("verified mapping lacks uploaded identity")
        start, end = epoch(piece["startUtc"]), epoch(piece["endUtc"])
        offset, rate = finite(piece["videoStartSeconds"]), finite(piece.get("rate", 1))
        if finite(piece["recordingStartSeconds"]) < 0:
            raise ValueError("negative recording offset")
        if end <= start or rate <= 0 or offset < 0 or offset + (end-start)*rate > part["durationSeconds"] + 0.05:
            raise ValueError("mapping outside actual part duration")
        output.append({k: piece[k] for k in ("id", "feed", "startUtc", "endUtc", "recordingId", "recordingStartSeconds")} | {"videoId": part["uploadedVideoId"], "videoStartSeconds": offset, "rate": rate})
    output.sort(key=lambda p: (p["feed"], epoch(p["startUtc"]), p["id"]))
    for a, b in zip(output, output[1:]):
        if a["feed"] == b["feed"] and epoch(a["endUtc"]) > epoch(b["startUtc"]):
            raise ValueError("overlapping verified sync pieces")
    return {"schemaVersion": 1, "mappings": output}


def cached_recordings(ia_dir, yt_dir, part_manifest=None, stream_start=None):
    """Read unfiltered completed caches. Missing exact part offsets stay untimed."""
    recordings, untimed = [], []
    for path in sorted(Path(ia_dir).rglob("*MISSION_AUDIO*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("utcTime") and row.get("duration", 0) > 0:
            start = epoch(row["utcTime"])
            recordings.append({"id": "ia:" + path.stem, "feed": "pao-primary", "source": "ia", "startUtc": utc(start), "endUtc": utc(start + finite(row["duration"])), "verification": "candidate", "evidence": "cached-transcript-duration"})
    parts = {p["filename"]: p for p in validate_part_manifest(part_manifest)} if part_manifest else {}
    for path in sorted(Path(yt_dir).glob("*part*.json")):
        if path.name.endswith(".progress.json"):
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        part = parts.get(row.get("source"))
        if not part or not stream_start:
            untimed.append({"id": "yt:" + path.stem, "reason": "missing-measured-part-offset-or-utc-anchor", "durationSeconds": row.get("duration"), "part": row.get("part")})
            continue
        start = epoch(stream_start) + finite(part["originalStartSeconds"])
        recordings.append({"id": "yt:" + path.stem, "feed": "pao-primary", "source": "youtube", "startUtc": utc(start), "endUtc": utc(start + min(finite(row["duration"]), finite(part["durationSeconds"]))), "verification": "candidate", "evidence": "cached-unfiltered-transcript-and-part-manifest"})
    return recordings, untimed


def part_catalog(manifest, stream_start):
    """Compatibility player clock stays explicit until acoustic cuts are reviewed."""
    parts = validate_part_manifest(manifest)
    elapsed = 0.0
    output = []
    for part in parts:
        if not part.get("uploadedVideoId"):
            raise ValueError("catalog requires uploaded identity")
        output.append({"partNum": part["part"], "videoId": part["uploadedVideoId"],
                       "durationS": part["durationSeconds"], "startOffsetS": elapsed,
                       "originalStartSeconds": part["originalStartSeconds"],
                       "timingStatus": "candidate"})
        elapsed += part["durationSeconds"]
    return {"schemaVersion": 1, "streamStartUtc": stream_start, "timingStatus": "candidate",
            "timingBasis": "legacy-cumulative-playback-not-verified-original-sync", "parts": output}


def packet_offset_evidence(original, chunk):
    """Exact unique AAC packets verify media identity, never mission UTC."""
    by_hash = {}
    for packet in original:
        if packet.get("data_hash"):
            by_hash.setdefault(packet["data_hash"], []).append(finite(packet["pts_time"]))
    offsets = [by_hash[p["data_hash"]][0] - finite(p["pts_time"]) for p in chunk
               if len(by_hash.get(p.get("data_hash"), [])) == 1]
    spread = max(offsets) - min(offsets) if offsets else None
    verified = len(offsets) >= 20 and spread < 0.002
    return {"verification": "verified-packet-identity" if verified else "unknown",
            "matchedUniquePackets": len(offsets), "offsetSpreadSeconds": spread,
            "originalStartSeconds": statistics.median(offsets) if verified else None}


def apply_packet_evidence(manifest, evidence, reference):
    """Produce a new manifest retaining requested cuts and measured media offsets."""
    result = json.loads(json.dumps(manifest))
    checks = {p["filename"]: p for p in evidence["parts"]}
    for part in result["parts"]:
        check = checks.get(part["filename"], {})
        if check.get("verification") == "verified-packet-identity":
            part["requestedSplitOffsetSeconds"] = part["originalStartSeconds"]
            part["originalStartSeconds"] = check["originalStartSeconds"]
            part["offsetVerification"] = "verified-packet-identity"
            part["offsetEvidence"] = reference
    validate_part_manifest(result)
    return result
