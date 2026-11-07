"""Path-independent semantic dependencies and read-only impact previews."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
if __package__:
    from .transcript_stages import transcript_source
else:
    from transcript_stages import transcript_source


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def semantic(row, labels=False):
    # Playback offsets, aliases, derivative filenames and display names are not speech.
    utc = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
    value = {"t": utc.astimezone(timezone.utc).isoformat(), "channel": row.get("channel"),
             "text": row["text"], "speaker_id": row.get("speaker_id"),
             "source": row.get("logical_source") or transcript_source(row)}
    if labels:
        value["labels"] = {k: row.get(k) for k in
            ("category", "importance", "tags", "topic", "statement_kinds", "pao_type", "thread_context")}
    return value


def dependency_key(targets, context, settings, prompt, schema=None, labels=False):
    return digest({"contract": 1, "targets": [semantic(r, labels) for r in targets],
                   "context": [semantic(r, labels) for r in context],
                   "settings": settings, "prompt": prompt, "schema": schema})


def logical_batches(records, size, seconds=600):
    """Bound regrouping after insertion to its ten-minute UTC partition.

    The size remains a transport upper bound. Unchanged later partitions retain
    their complete supplied context signatures instead of shifting mission-wide.
    """
    start = 0
    while start < len(records):
        bucket = int(datetime.fromisoformat(records[start]["t"].replace("Z", "+00:00")).timestamp()) // seconds
        end = start + 1
        while end < len(records) and end - start < size:
            following = int(datetime.fromisoformat(records[end]["t"].replace("Z", "+00:00")).timestamp()) // seconds
            if following != bucket:
                break
            end += 1
        yield start, records[start:end]
        start = end


def validate_plan(args, records, stage):
    """Bind authorized bounded work to its source meaning before any transport."""
    from pathlib import Path
    path = getattr(args, "work_plan", None)
    if not path or not getattr(args, "limit", None) or args.limit < 1:
        raise ValueError("Model work requires --work-plan and a positive --limit")
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    revision = digest([semantic(r) for r in records])
    if plan.get("contract") != "transcript-impact-v1" or plan.get("semantic_revision") != revision:
        raise ValueError("Work plan does not match current transcript semantics")
    if stage not in plan.get("stages", []) or args.limit > plan.get("max_requests", 0):
        raise ValueError("Requested stage/work limit exceeds the work plan")
    args.model_request_limit = plan["max_requests"]
    return plan


def inherited_assessments(manifest_path, current, context_lines=5):
    """Preserve accepted legacy fields under their actual historical contract."""
    if __package__:
        from .transcript_dataset import read_dataset
    else:
        from transcript_dataset import read_dataset
    previous = manifest_path if isinstance(manifest_path, dict) else read_dataset(manifest_path)
    reusable = set(impact(previous["utterances"], current, context_lines)["reused"])
    assessments = {r["uid"]: r for r in previous["assessments"]}
    old_by_semantic = {}
    for row in previous["utterances"]:
        old_by_semantic.setdefault(digest(semantic(row)), []).append(row["uid"])
    aliases = {row["uid"]: old_by_semantic[digest(semantic(row))][0] for row in current
        if len(old_by_semantic.get(digest(semantic(row)), [])) == 1}
    projection = previous["published_transcripts"]
    types = {r["uid"]: projection[r["channel"]][r["position"]].get("pao_type")
             for r in previous["entry_mapping"]}
    result = {}
    for uid in reusable:
        old_uid = uid if uid in assessments else aliases.get(uid)
        if old_uid not in assessments:
            continue
        label = {k: v for k, v in assessments[old_uid].items() if k != "uid"}
        if types.get(old_uid) is not None:
            label["pao_type"] = types[old_uid]
        label["assessment_provenance"] = {"contract": previous["manifest"]["contract"],
            "status": "accepted-legacy", "pao_type": "legacy-published-projection"}
        result[uid] = label
    return result


def impact(previous, current, context_lines=5, investigations=(), changed_evidence=()):
    """Preview work without constructing a model client or retrying unresolved work."""
    new = {r["uid"]: r for r in current}
    new_semantics = {}
    for row in current:
        new_semantics.setdefault(digest(semantic(row)), []).append(row["uid"])
    normalized_previous = []
    aliases = {}
    for row in previous:
        matching = new_semantics.get(digest(semantic(row)), [])
        uid = matching[0] if row["uid"] not in new and len(matching) == 1 else row["uid"]
        if uid != row["uid"]:
            aliases[row["uid"]] = uid
        normalized_previous.append({**row, "uid": uid})
    previous = normalized_previous
    old = {r["uid"]: r for r in previous}
    changed = {uid for uid in new.keys() & old.keys() if semantic(new[uid]) != semantic(old[uid])}
    added, removed = new.keys() - old.keys(), old.keys() - new.keys()
    affected = set(changed) | set(added)
    # Compare actual source-local context, so insertion cannot shift every later batch.
    def windows(records):
        groups = {}
        for row in records:
            key = (row.get("channel"), semantic(row)["source"])
            groups.setdefault(key, []).append(row)
        result = {}
        for group in groups.values():
            times = [datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp() for r in group]
            for i, row in enumerate(group):
                neighbors = [semantic(group[j]) for j in range(max(0, i-context_lines), min(len(group), i+context_lines+1))
                             if abs(times[j] - times[i]) <= 600]
                result[row["uid"]] = digest(neighbors)
        return result
    a, b = windows(previous), windows(current)
    affected.update(uid for uid in new.keys() & old.keys() if a[uid] != b[uid])
    evidence = set(changed_evidence)
    stale = []
    for claim in investigations:
        dependencies = set(claim.get("source_uids", [])) | set(claim.get("evidence_dependencies", []))
        reason = dependencies & (changed | set(removed) | evidence)
        # Search-scope revisions include newly available contradictory evidence.
        reason |= set(claim.get("search_dependencies", [])) & evidence
        if reason:
            stale.append({"id": claim["id"], "reason": "changed dependency", "dependencies": sorted(reason)})
    requests = sum(bool({r["uid"] for r in group} & affected) for _, group in logical_batches(current, 50))
    return {"new": sorted(added), "changed": sorted(changed), "removed": sorted(removed),
            "affected_context": sorted(affected), "reused": sorted(new.keys() - affected),
            "stale_investigations": stale, "estimated_classification_requests": requests,
            "estimate_batch_size": 50, "research_dependency_coverage": "supplied" if investigations else "unknown; dependency ledger not supplied",
            "identity_aliases": aliases, "model_requests": 0}


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    if __package__:
        from .transcript_dataset import read_dataset, rows, write
    else:
        from transcript_dataset import read_dataset, rows, write
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--current-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--context-lines", type=int, default=5)
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--stages", nargs="+", choices=["1d", "1e", "1f"], default=["1d"])
    args = parser.parse_args()
    if args.max_requests < 1 or args.context_lines < 0:
        parser.error("max-requests must be positive and context-lines nonnegative")
    prior = read_dataset(args.dataset_manifest)
    current = rows(args.current_jsonl)
    report = impact(prior["utterances"], current, args.context_lines)
    write(Path(args.output), {**report, "contract": "transcript-impact-v1",
        "semantic_revision": digest([semantic(r) for r in current]),
        "stages": args.stages, "max_requests": args.max_requests,
        "baseline_dataset_manifest": str(Path(args.dataset_manifest).resolve())})
