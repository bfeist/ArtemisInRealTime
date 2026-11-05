"""Step 1e: retain every source utterance and recover failed 1d assessments.

Run after 1d, or use --follow to recover completed failed batches while 1d runs.
Outputs are separate from 1d so old cache rows cannot hide a failed reassessment.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import MISSIONS
from history_common import (ModelClient, add_model_arguments, atomic_json, digest, enrich,
                            events_from_records, file_digest, now, publish, read_jsonl, stage_lock)

RECOVERY_VERSION = 2


def completed_lines(path: Path) -> list[bytes]:
    """Read a bounded prefix; never consume a producer's partially written last line."""
    if not path.exists():
        return []
    with path.open("rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        data = fh.read(size)
    if data and not data.endswith(b"\n"):
        data = data[:data.rfind(b"\n") + 1]
    return [line for line in data.splitlines() if line.strip()]


def ledger_prefix(path: Path) -> dict:
    latest = {}
    for line in completed_lines(path):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # Preserve 1d's historical malformed-line handling.
        if isinstance(row, dict) and row.get("uid"):
            latest[row["uid"]] = row
    return latest


def select_run(root: Path, source_run: str | None = None) -> Path:
    candidates = [p for p in root.iterdir() if (p / "run.json").exists()] if root.exists() else []
    if source_run:
        candidates = [p for p in candidates if p.name == source_run]
    if not candidates:
        raise RuntimeError("No matching 1d diagnostics run; specify --source-run with its directory name")
    return max(candidates, key=lambda p: p.name)


def recovery_plan(mission, source_run: str | None = None, follow: bool = False):
    run_dir = select_run(mission.transcript_enrichment / "diagnostics", source_run)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    summaries = ([json.loads(line) for line in completed_lines(run_dir / "batches.jsonl")]
                 if follow else read_jsonl(run_dir / "batches.jsonl"))
    numbers = [r["batch"] for r in summaries]
    if numbers != list(range(1, len(numbers) + 1)) or len(numbers) > meta["batches"]:
        raise RuntimeError("1d diagnostics must be a unique contiguous sequence of completed batches")
    finished = len(numbers) == meta["batches"]
    if not finished and not follow:
        raise RuntimeError("The selected 1d run is still running or was interrupted; finish it before 1e")
    ledger_path = mission.transcript_enrichment / "enriched_utterances.jsonl"
    stable_paths = [mission.web_dir / name for name in
                    ("comm.csv", "PAO.csv", "itinerary.json", "photos.json", "videoYt.json", "videoPDS.json")]
    stable_paths += [run_dir / "run.json", Path(__file__).with_name("mission_context.py")]
    paths = stable_paths.copy()
    # During a live run the ledger and diagnostics are append-only queues, not immutable inputs.
    # Their complete batch files ARE immutable. Once the producer finishes, hash everything.
    if finished:
        paths += [ledger_path, run_dir / "batches.jsonl"]
    for summary in summaries:
        if summary["missing"]:
            paths.extend(run_dir / summary[key] for key in ("stages_file", "detail_file") if summary.get(key))
    checksums = {str(p): file_digest(p) for p in paths}
    utterances = enrich.load_all_utterances(mission)
    latest = ledger_prefix(ledger_path) if follow else enrich.load_existing_ledger(ledger_path)
    missing = {m["uid"]: (r, m) for r in summaries for m in r["missing"]}
    pending = {u["uid"] for u in utterances if u["uid"] in missing or (finished
               and latest.get(u["uid"], {}).get("assessment_version") != enrich.ASSESSMENT_VERSION)}
    if checksums != {str(p): file_digest(p) for p in paths}:
        raise RuntimeError("Stable source inputs or completed batch files changed while taking the recovery snapshot")
    identity = digest({"version": RECOVERY_VERSION, "inputs": checksums})
    progress = {"completed_batches": len(numbers), "total_batches": meta["batches"], "producer_finished": finished,
                "cache_seed": digest({str(p): checksums[str(p)] for p in stable_paths})}
    return run_dir, utterances, latest, missing, pending, checksums, identity, progress


def recovered_record(u: dict, assessment: dict, source_run: str, method: str, settings: dict) -> dict:
    return {**u, **assessment, "assessment_version": enrich.ASSESSMENT_VERSION,
            "recovery": {"status": "recovered", "method": method, "source_run": source_run,
                         "at": now(), "settings": settings}}


def unresolved_record(u: dict, source_run: str, error: str, previous: dict | None) -> dict:
    # A failed model request must never make the source disappear or masquerade as filler.
    return {**u, "importance": None, "category": "unassessed", "tags": [], "topic": "",
            "crew": [], "event_id": "", "milestones": [],
            "assessment_version": enrich.ASSESSMENT_VERSION,
            "recovery": {"status": "unresolved", "source_run": source_run, "error": error,
                         "at": now(), "previous_assessment": previous}}


def recover(mission, args, client=None) -> dict:
    root = mission.transcript_recovery
    with stage_lock(root):
        run_dir, utterances, latest, missing, pending, checksums, identity, progress = recovery_plan(
            mission, args.source_run, getattr(args, "follow", False))
        client = client or ModelClient(args, root / "requests")
        work_id = digest([progress["cache_seed"], client.settings, args.batch_size, args.context_lines,
                           file_digest(Path(__file__)), file_digest(Path(enrich.__file__)),
                           file_digest(Path(__file__).with_name("transcript_stages.py"))])
        work = root / "work" / work_id
        work.mkdir(parents=True, exist_ok=True)
        atomic_json(work / "snapshot.json", {"inputs": checksums, "source_run": run_dir.name,
                                            "settings": client.settings, "batch_size": args.batch_size,
                                            "context_lines": args.context_lines})
        current = {u["uid"]: u for u in utterances}
        positions = {u["uid"]: i for i, u in enumerate(utterances)}
        fingerprints = {}
        for uid in pending:
            if uid in missing:
                summary, item = missing[uid]
                fingerprints[uid] = digest([summary, item, {key: checksums.get(str(run_dir / summary[key]))
                    for key in ("stages_file", "detail_file") if summary.get(key)}])
            else:
                fingerprints[uid] = digest([uid, "missing_or_outdated_assessment"])
        saved = {}
        for path in work.glob("row_*.json"):
            row = json.loads(path.read_text(encoding="utf-8"))
            if row["uid"] not in pending or row.get("recovery", {}).get("input_fingerprint") != fingerprints[row["uid"]]:
                continue
            if args.retry_unresolved and row.get("recovery", {}).get("status") == "unresolved":
                continue
            saved[row["uid"]] = row
        context = enrich.MissionContextIndex(mission)
        count = 0

        def save(u, assessment, method):
            row = recovered_record(u, assessment, run_dir.name, method, client.settings)
            row["recovery"]["input_fingerprint"] = fingerprints[u["uid"]]
            saved[u["uid"]] = row
            atomic_json(work / f"row_{digest(u['uid'])}.json", row)

        def assess(targets):
            """Salvage valid labels; recursively split only the missing targets."""
            if not targets:
                return
            first, last = positions[targets[0]["uid"]], positions[targets[-1]["uid"]]
            before = utterances[max(0, first - args.context_lines):first]
            after = utterances[last + 1:last + 1 + args.context_lines]
            prompt = enrich.build_user_prompt(targets, before,
                context.query_window(targets[0]["ts"], targets[-1]["ts"]), {}, after, {})
            event_ids = context.query_event_ids(targets[0]["ts"], targets[-1]["ts"])
            error = "No valid assessment returned"
            try:
                content = client.request(enrich.EXTRACTION_SYSTEM_PROMPT, prompt, "extract:" + digest(targets))
                extraction = enrich.parse_extraction(content, targets, event_ids)
                label_prompt = enrich.build_label_prompt(targets, extraction)
                content = client.request(enrich.SYSTEM_PROMPT, label_prompt, "label:" + digest(targets))
                parsed = enrich.parse_staged_assessments(content, targets, event_ids, extraction, [])
                for i, assessment in parsed.items():
                    save(targets[i], assessment, "reextracted")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            left = [u for u in targets if u["uid"] not in saved]
            if len(targets) == 1 and left:
                u = left[0]
                # A missing extraction must not prevent a useful utterance label.
                # Milestone discovery is explicitly deferred to 1f's coverage review.
                try:
                    minimal = {"threads": [], "developments": []}
                    prompt = enrich.build_label_prompt([u], minimal) + "\n" + json.dumps({
                        "extraction_unavailable": True,
                        "preceding_context": before, "following_context": after,
                        "instruction": "Label the target from its own words and context. No developments or crew have been established."
                    }, ensure_ascii=False)
                    content = client.request(enrich.SYSTEM_PROMPT, prompt, "label_only:" + u["uid"])
                    parsed = enrich.parse_staged_assessments(content, [u], event_ids, minimal, [])
                    if 0 in parsed:
                        save(u, parsed[0], "labels_only")
                        saved[u["uid"]]["recovery"]["extraction_status"] = "unresolved"
                        atomic_json(work / f"row_{digest(u['uid'])}.json", saved[u["uid"]])
                        return
                except Exception as exc:
                    error += f"; label-only recovery: {type(exc).__name__}: {exc}"
                row = unresolved_record(u, run_dir.name, error, latest.get(u["uid"]))
                row["recovery"]["input_fingerprint"] = fingerprints[u["uid"]]
                saved[u["uid"]] = row
                atomic_json(work / f"row_{digest(u['uid'])}.json", row)
            elif left:
                size = max(1, min(len(left), len(targets) // 2))
                for start in range(0, len(left), size):
                    assess(left[start:start + size])

        # Recover labels from already validated extractions before paying for extraction again.
        failed_batches = {r["batch"]: r for uid, (r, _) in missing.items()
                          if uid in pending and uid not in saved}
        for number, summary in sorted(failed_batches.items()):
            if args.limit is not None and count >= args.limit:
                break
            stage_path = run_dir / summary.get("stages_file", f"batch_{number:05d}_stages.json")
            detail_path = run_dir / summary.get("detail_file", f"batch_{number:05d}.json")
            if not stage_path.exists() or not detail_path.exists():
                continue
            stages = json.loads(stage_path.read_text(encoding="utf-8"))["stages"]
            stage = stages.get("extraction_repair", stages.get("extraction", {}))
            if "extraction" not in stage:
                continue
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            targets = detail["target_batch"]
            wanted = {i for i, u in enumerate(targets) if u["uid"] in pending and u["uid"] not in saved}
            if not wanted:
                continue
            count += 1
            try:
                event_ids = context.query_event_ids(targets[0]["ts"], targets[-1]["ts"])
                # Revalidate saved extraction against the original targets before reuse.
                extraction = enrich.parse_extraction(json.dumps(stage["extraction"]), targets, event_ids)
                prompt = enrich.build_label_prompt(targets, extraction) + "\n" + json.dumps({
                    "repair_only_indices": sorted(wanted),
                    "validation_errors": detail.get("parse_issues", []),
                    "instruction": "Return labels only for repair_only_indices. Non-filler categories require 1..4 tags."
                })
                content = client.request(enrich.SYSTEM_PROMPT, prompt, f"repair_labels:{number}")
                parsed = enrich.parse_staged_assessments(content, targets, event_ids, extraction, [])
                for i in wanted & parsed.keys():
                    if targets[i]["uid"] in current:
                        save(current[targets[i]["uid"]], parsed[i], "saved_extraction")
            except Exception as exc:
                print(f"  Batch {number}: label recovery deferred to smaller extraction: {exc}")

        remaining = [u for u in utterances if u["uid"] in pending and u["uid"] not in saved]
        # Keep noncontiguous source intervals separate; context is never a substitute for missing targets.
        batches = []
        for u in remaining:
            if (not batches or len(batches[-1]) >= args.batch_size
                    or positions[u["uid"]] != positions[batches[-1][-1]["uid"]] + 1):
                batches.append([])
            batches[-1].append(u)
        for batch in batches:
            if args.limit is not None and count >= args.limit:
                break
            count += 1
            print(f"  Recovering {len(batch)} utterances at {batch[0]['t']}")
            assess(batch)

        remaining = pending - saved.keys()
        records = []
        for u in utterances:
            if u["uid"] in saved:
                records.append(saved[u["uid"]])
            elif u["uid"] not in pending and latest.get(u["uid"], {}).get("assessment_version") == enrich.ASSESSMENT_VERSION:
                records.append({**latest[u["uid"]], **u})
            else:
                row = unresolved_record(u, run_dir.name, "Not yet attempted by 1e", latest.get(u["uid"]))
                if not progress["producer_finished"] and u["uid"] not in pending:
                    row["recovery"].update(status="awaiting_1d", error="1d has not produced an assessment yet")
                records.append(row)
        if checksums != {path: file_digest(Path(path)) for path in checksums}:
            raise RuntimeError("Stable source inputs changed during recovery; cached repairs retained, exports not committed")
        unresolved = sum(r.get("recovery", {}).get("status") == "unresolved" for r in records)
        report = {"stage": "1e", "version": RECOVERY_VERSION, "snapshot": digest([identity, work_id]),
                  "work_snapshot": work_id, **progress,
                  "source_run": run_dir.name, "created_at": now(), "input_hashes": checksums,
                  "complete": progress["producer_finished"] and not remaining, "total_utterances": len(utterances),
                  "recovery_targets": len(pending), "remaining": len(remaining),
                  "recovered": sum(r.get("recovery", {}).get("status") == "recovered" for r in records),
                  "unresolved": unresolved, "no_utterances_rejected": len(records) == len(utterances)}
        report["labels_only_recoveries"] = sum(r.get("recovery", {}).get("method") == "labels_only" for r in records)
        report["awaiting_1d"] = sum(r.get("recovery", {}).get("status") == "awaiting_1d" for r in records)
        publish(root, records, events_from_records(records), report)
        print(f"  1e retained {len(records):,} utterances; {unresolved} explicitly unassessed; {len(remaining)} pending")
        return report


def follow_recovery(mission, args, recover_fn=None, sleep_fn=None):
    """Pin the producer run; poll only complete diagnostics, preserving per-row repairs."""
    recover_fn = recover_fn or recover
    sleep_fn = sleep_fn or time.sleep
    if not args.source_run:
        args.source_run = select_run(mission.transcript_enrichment / "diagnostics").name
    diagnostics = mission.transcript_enrichment / "diagnostics" / args.source_run / "batches.jsonl"
    previous_marker = None
    while True:
        marker = digest([line.decode("utf-8") for line in completed_lines(diagnostics)])
        if marker != previous_marker:
            # Use the marker from BEFORE recovery: batches arriving during a slow
            # model request must still trigger the next sweep, including the final batch.
            report = recover_fn(mission, args)
            previous_marker = marker
            # Explicit retries happen once per invocation, not on every producer append.
            args.retry_unresolved = False
            if report["complete"] or args.limit is not None:
                return report
            print(f"  Following 1d: {report['completed_batches']}/{report['total_batches']} batches logged; "
                  f"waiting {args.poll_seconds:g}s (Ctrl+C to stop; repairs are saved)", flush=True)
        sleep_fn(args.poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", default="artemis-ii", choices=["artemis-ii"])
    parser.add_argument("--source-run", help="1d diagnostics directory name (default: latest run)")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--context-lines", type=int, default=10)
    parser.add_argument("--follow", action="store_true", help="Repair completed failed batches while 1d runs; wait for its final batch")
    parser.add_argument("--poll-seconds", type=float, default=30, help="Seconds between completed-batch checks in --follow mode")
    add_model_arguments(parser)
    args = parser.parse_args()
    if (args.batch_size < 1 or args.context_lines < 0 or args.max_tokens < 1 or args.poll_seconds <= 0
            or (args.limit is not None and args.limit < 1)):
        parser.error("batch-size, max-tokens, limit and poll-seconds must be positive; context-lines must be nonnegative")
    report = (follow_recovery(MISSIONS[args.mission], args) if args.follow else recover(MISSIONS[args.mission], args))
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
