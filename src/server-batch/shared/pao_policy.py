"""Apply reviewed PAO playback authority without revising speech or assessments."""
import importlib
import json
from pathlib import Path
import shutil
import hashlib

from shared.pao_sync import compact_sync_projection, epoch, finite


def reviewed_playback(rows, coverage, sync, parts):
    if coverage.get("schemaVersion") != 1:
        raise ValueError("unsupported reviewed coverage schema")
    review = coverage.get("review", {})
    if coverage.get("status") != "reviewed" or not all(
        review.get(key) for key in ("reviewer", "reference")
    ):
        raise ValueError("coverage requires explicit reviewed authority")
    intervals = sorted(coverage.get("intervals", []), key=lambda r: (r["feed"], epoch(r["startUtc"])))
    if not intervals:
        raise ValueError("reviewed coverage is empty")
    for i, interval in enumerate(intervals):
        if epoch(interval["endUtc"]) <= epoch(interval["startUtc"]) or not interval.get("reviewReference"):
            raise ValueError("invalid or unreviewed boundary")
        if (
            i
            and intervals[i - 1]["feed"] == interval["feed"]
            and epoch(intervals[i - 1]["endUtc"]) > epoch(interval["startUtc"])
        ):
            raise ValueError("overlapping reviewed feed intervals")
        if interval["source"] not in {"ia", "youtube", "unknown"}:
            raise ValueError("invalid selected source")
        if interval["source"] != "unknown" and interval.get("verification") != "verified":
            raise ValueError("candidate coverage cannot change playback")
    projection = compact_sync_projection(sync, parts)
    recordings = coverage.get("recordings", {})
    selections, unchanged = {}, []
    for row in rows:
        if row.get("channel") != "pao":
            continue
        instant = epoch(row["t"])
        requested_duration = finite(row.get("d") or 0)
        if requested_duration < 0:
            raise ValueError("negative utterance duration")
        feed = row.get("feed", "pao-primary")
        applicable = [
            interval for interval in intervals
            if interval["feed"] == feed
            and epoch(interval["startUtc"]) <= instant < epoch(interval["endUtc"])
        ]
        if not applicable or applicable[0]["source"] == "unknown":
            unchanged.append(row["uid"])
            continue
        interval = applicable[0]
        end = instant + requested_duration
        playable_duration = requested_duration or epoch(interval["endUtc"]) - instant
        if end > epoch(interval["endUtc"]):
            raise ValueError("utterance crosses reviewed source boundary")
        rid = interval["recordingId"]
        playback = {"source": interval["source"], "recordingId": rid, "feed": feed}
        mapped = [
            piece for piece in projection["mappings"]
            if piece["feed"] == feed and piece["recordingId"] == rid
            and epoch(piece["startUtc"]) <= instant < epoch(piece["endUtc"])
            and end <= epoch(piece["endUtc"])
        ]
        if mapped:
            piece = mapped[0]
            playable_duration = min(playable_duration, epoch(piece["endUtc"]) - instant)
            playback["video"] = {
                "id": piece["videoId"],
                "s": piece["videoStartSeconds"]
                    + (instant - epoch(piece["startUtc"])) * piece["rate"],
                "mappingId": piece["id"],
            }
        elif interval.get("videoStatus") != "unavailable":
            raise ValueError("selected utterance lacks verified video map or explicit unavailable status")
        if interval["source"] == "youtube" and not mapped:
            raise ValueError("YouTube selection requires verified uploaded playback")
        if interval["source"] == "ia":
            recording = recordings.get(rid, {})
            if recording.get("verification") != "verified" or not recording.get("reviewReference"):
                raise ValueError("IA playback requires reviewed recording clock and derivative")
            offset = instant - epoch(recording["startUtc"])
            if offset < 0 or offset + requested_duration > finite(recording["durationSeconds"]):
                raise ValueError("utterance outside reviewed IA recording")
            relative = Path(recording["audioPath"])
            if (
                relative.is_absolute() or ".." in relative.parts
                or not relative.parts or relative.parts[0] != "comm"
                or relative.suffix.lower() not in {".aac", ".m4a"}
            ):
                raise ValueError("IA audio path must be a relative comm derivative")
            playback["audio"] = {"src": relative.as_posix(), "s": offset}
            playable_duration = min(playable_duration, finite(recording["durationSeconds"]) - offset)
        if playable_duration <= 0:
            raise ValueError("empty reviewed playback interval")
        if "audio" in playback:
            playback["audio"]["d"] = playable_duration
        if "video" in playback:
            playback["video"]["d"] = playable_duration * piece["rate"]
        selections[row["uid"]] = playback
    return selections, {"selectedRows": len(selections), "unchangedPaoUids": unchanged, "modelRequests": 0, "textChanged": False}


def validate_audio_derivatives(selections, recordings, audio_root):
    """Resolve containment, reject empty media, bind any reviewed derivative hash."""
    receipts = {}
    for playback in selections.values():
        if "audio" not in playback:
            continue
        if audio_root is None:
            raise ValueError("reviewed IA audio root missing")
        root = Path(audio_root).resolve()
        relative = playback["audio"]["src"]
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("reviewed IA derivative escapes audio root")
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("reviewed IA audio derivative missing or empty")
        if relative not in receipts:
            before = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("reviewed IA derivative changed during validation")
            receipts[relative] = {"sha256": digest.hexdigest(), "bytes": after.st_size}
        expected = recordings[playback["recordingId"]].get("audioSha256")
        if expected is not None and expected != receipts[relative]["sha256"]:
            raise ValueError("reviewed IA derivative hash mismatch")
    return receipts


def apply_dataset_policy(dataset_manifest, coverage_path, sync_path, parts_path, output, audio_root=None):
    """Clone a pinned dataset, change playback metadata, write manifest last."""
    adapter = importlib.import_module("1_comm.transcript_dataset")
    dataset_manifest, output = Path(dataset_manifest), Path(output)
    if output.exists() or "processed" not in output.resolve().parts:
        raise ValueError("output must be a new processed dataset directory")
    data = adapter.read_dataset(dataset_manifest)
    coverage, sync, parts = [json.loads(Path(p).read_text(encoding="utf-8")) for p in (coverage_path, sync_path, parts_path)]
    selections, receipt = reviewed_playback(data["utterances"], coverage, sync, parts)
    if not selections:
        raise ValueError("reviewed policy selects no PAO rows")
    audio_receipts = validate_audio_derivatives(selections, coverage.get("recordings", {}), audio_root)
    utterances = [dict(row, playback=selections[row["uid"]]) if row["uid"] in selections else row for row in data["utterances"]]
    published = json.loads(json.dumps(data["published_transcripts"]))
    for mapping in data["entry_mapping"]:
        if mapping["uid"] in selections:
            published[mapping["channel"]][mapping["position"]]["playback"] = selections[mapping["uid"]]
    output.mkdir(parents=True)
    manifest = json.loads(json.dumps(data["manifest"]))
    for name, artifact in manifest["artifacts"].items():
        source = dataset_manifest.parent / artifact["path"]
        target = output / artifact["path"]
        if adapter.checksum(source) != artifact["sha256"]:
            raise ValueError("source dataset artifact changed during policy application")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if adapter.checksum(target) != artifact["sha256"]:
            raise ValueError("copied dataset artifact differs from pinned source")
        if name == "utterances":
            target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in utterances), encoding="utf-8")
        elif name == "published_transcripts":
            adapter.write(target, published)
        artifact["sha256"] = adapter.checksum(target)
        artifact["bytes"] = target.stat().st_size
    receipt.update({"contract": "reviewed-pao-playback-v1", "parentManifestSha256": adapter.checksum(dataset_manifest),
                    "coverageSha256": adapter.checksum(coverage_path), "syncSha256": adapter.checksum(sync_path),
                    "partsSha256": adapter.checksum(parts_path), "semanticRowsPreserved": len(utterances)})
    receipt["audioDerivatives"] = audio_receipts
    adapter.write(output / "pao_policy_receipt.json", receipt)
    manifest["paoPolicy"] = receipt
    adapter.write(output / "manifest.json", manifest)
    return receipt
