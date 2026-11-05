"""Organize completed 1d–1g history data; preview by default, --apply to move.

Obsolete data is staged in _delete_obsolete_history until the caller deletes it.
Run with ingestion stopped. Transcript/media sources are never moved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path

from config import MISSIONS

common = importlib.import_module("1_comm.history_common")
recovery = importlib.import_module("1_comm.1e_recover_enrichment")
investigation = importlib.import_module("1_comm.1f_investigate_history")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def contained(path, root):
    """Resolve every move target before touching it, including existing junctions."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise RuntimeError(f"Refusing to move outside the transcript directory: {path}")
    return resolved


def request_paths(root, outcomes):
    """Keep the final attempt's request for each round of each retained checkpoint."""
    prefixes = {}
    for outcome in outcomes:
        transcript = outcome.get("retrieval", [])
        rounds = len(transcript) - (outcome["state"] != "done")
        cutoff = datetime.fromisoformat(outcome["at"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        for number in range(max(0, rounds)):
            prefixes[common.digest(f"{outcome['task_id']}:{number}")[:16]] = cutoff
    selected = {}
    for path in root.glob("*.json"):
        prefix, _, stamp = path.stem.partition("_")
        if prefix in prefixes and stamp <= prefixes[prefix]:
            if prefix not in selected or path.name > selected[prefix].name:
                selected[prefix] = path
    return set(selected.values())


def migrate(mission, apply=False, recovery_batch_size=25, recovery_context_lines=10,
            coverage_size=40, max_rounds=8):
    root = mission.processed_transcripts.resolve()
    old_recovery = root / "recovery"
    old_history = root / "investigated_history"
    if not old_recovery.exists() or not old_history.exists():
        raise RuntimeError("Expected the legacy recovery and investigated_history folders; migration is one-time")
    recovered = common.require_artifacts(old_recovery)
    investigated = common.require_artifacts(old_history)
    if (investigated["recovery_snapshot"] != recovered["snapshot"]
            or investigated["recovery_artifacts"] != recovered["artifacts"]):
        raise RuntimeError("Investigation does not match recovery")
    recovery_work = old_recovery / "work" / recovered["work_snapshot"]
    history_work = old_history / "work" / investigated["snapshot"]
    recovery_meta = load(recovery_work / "snapshot.json")
    history_meta = load(history_work / "snapshot.json")
    recovery_batch_size = recovery_meta.get("batch_size", recovery_batch_size)
    recovery_context_lines = recovery_meta.get("context_lines", recovery_context_lines)
    coverage_size = history_meta.get("coverage_size", coverage_size)
    max_rounds = history_meta.get("max_rounds", max_rounds)
    source_run = recovered["source_run"]
    moves = []
    obsolete = []

    def move(source, target):
        contained(source, root)
        contained(target, root)
        if not source.exists():
            return
        if target.exists():
            # MissionConfig may already have created empty stage directories.
            if not target.is_dir() or any(target.iterdir()):
                raise RuntimeError(f"Destination is not empty: {target}")
        moves.append((source, target))

    for name in ("enriched_utterances.jsonl", "detected_milestones.jsonl", "comm_enriched.csv", "PAO_enriched.csv"):
        move(root / name, mission.transcript_enrichment / name)
    move(root / "enrichment_diagnostics", mission.transcript_enrichment / "diagnostics")
    move(old_recovery, mission.transcript_recovery)
    move(old_history, mission.transcript_investigation)
    move(root / "hierarchy", mission.transcript_hierarchy)
    for name in ("mission_activity_tree.json", "mission_activities.json"):
        move(root / name, mission.transcript_hierarchy / name)

    for name in ("enrichment_benchmarks", "model_comparisons", "provenance_backups"):
        if (root / name).exists():
            obsolete.append(root / name)
    for path in (root / "enrichment_diagnostics").iterdir():
        if path.name != source_run:
            obsolete.append(path)
    for stage, keep in ((old_recovery, recovery_work), (old_history, history_work)):
        for path in (stage / "work").iterdir():
            if path != keep:
                obsolete.append(path)
        obsolete.extend(p for p in stage.iterdir() if p.is_dir() and p.name.endswith("backups"))
    obsolete.extend(p for p in (root / "hierarchy").glob("backups"))

    # Published investigations are the exact expected task set; old label-task
    # checkpoints and abandoned review attempts are superseded data.
    outcomes = common.read_jsonl(old_history / "investigations.jsonl")
    keep_checkpoints = {history_work / f"task_{common.digest(o['task_id'])}.json" for o in outcomes}
    children = []
    for outcome in outcomes:
        path = history_work / f"task_{common.digest(outcome['task_id'])}.json"
        if not path.exists() or load(path) != outcome:
            raise RuntimeError(f"Published investigation lacks its matching checkpoint: {path}")
        for child in outcome.get("recovery", {}).get("subtasks", []):
            child_path = history_work / child["checkpoint"]
            contained(child_path, history_work)
            if child_path.exists():
                children.append(load(child_path))
                keep_checkpoints.add(child_path)
    for path in history_work.rglob("*.json"):
        if path.name != "snapshot.json" and path not in keep_checkpoints:
            obsolete.append(path)
    keep_requests = request_paths(old_history / "requests", outcomes + children)
    obsolete.extend(p for p in (old_history / "requests").glob("*.json") if p not in keep_requests)
    for path in (old_recovery / "requests").glob("*.json"):
        if load(path).get("error"):
            obsolete.append(path)
    obsolete = sorted(set(obsolete))
    quarantine = root / "_delete_obsolete_history"
    if quarantine.exists():
        raise RuntimeError(f"Previous deletion staging folder already exists: {quarantine}")
    for path in obsolete:
        contained(path, root)
        contained(quarantine / path.relative_to(root), root)
    for source, target in moves:
        print(f"MOVE {source.relative_to(root)} -> {target.relative_to(root)}")
    files = [f for p in obsolete for f in (p.rglob("*") if p.is_dir() else [p]) if f.is_file()]
    reclaimed = sum(p.stat().st_size for p in files)
    print(f"Obsolete: {len(files):,} files, {reclaimed / 1024**3:.2f} GiB; {len(keep_requests):,} current investigation requests retained")
    if not apply:
        return {"obsolete_files": len(files), "obsolete_bytes": reclaimed}

    receipt_dir = mission.data_dir / "processed" / "transcript-history-cleanup"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_path = receipt_dir / f"{stamp}.json"
    receipt = {"complete": False, "root": str(root), "moves": [[str(a), str(b)] for a, b in moves],
               "obsolete": [str(p.relative_to(root)) for p in obsolete],
               "obsolete_files": len(files), "obsolete_bytes": reclaimed,
               "source_run": source_run, "recovery_before": recovered, "investigation_before": investigated}
    common.atomic_json(receipt_path, receipt)
    # Reject active writers. Ingestion must stay stopped during the migration;
    # Windows requires closing the lock files before moving their directories.
    for stage in (old_recovery, old_history, root / "hierarchy"):
        if stage.exists():
            with common.stage_lock(stage):
                pass
    for path in obsolete:
        target = quarantine / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()  # Only an empty directory was permitted by the preflight.
        source.rename(target)

    # Rebase the recovery namespace using the exact original generation settings.
    # Row fingerprints use the unchanged batch contents/hashes, so saved repairs
    # remain valid despite the directory and checkpoint-namespace changes.
    _, _, _, _, _, checksums, identity, progress = recovery.recovery_plan(mission, source_run)
    work_id = common.digest([progress["cache_seed"], recovery_meta["settings"], recovery_batch_size,
                             recovery_context_lines, common.file_digest(Path(recovery.__file__)),
                             common.file_digest(Path(common.enrich.__file__)),
                             common.file_digest(Path(recovery.__file__).with_name("transcript_stages.py"))])
    new_recovery_work = mission.transcript_recovery / "work" / work_id
    (mission.transcript_recovery / "work" / recovered["work_snapshot"]).rename(new_recovery_work)
    recovered = {**recovered, **progress, "input_hashes": checksums,
                 "snapshot": common.digest([identity, work_id]), "work_snapshot": work_id}
    common.atomic_json(mission.transcript_recovery / "report.json", recovered)
    common.atomic_json(new_recovery_work / "snapshot.json", {
        **recovery_meta, "inputs": checksums, "batch_size": recovery_batch_size,
        "context_lines": recovery_context_lines})
    new_id = common.digest({"version": investigation.INVESTIGATION_VERSION,
        "recovery": [recovered["snapshot"], recovered["artifacts"]], "sources": history_meta["sources"],
        "settings": history_meta["settings"], "coverage_size": coverage_size, "max_rounds": max_rounds,
        "investigator_code": investigation.V1_INVESTIGATOR_CODE,
        "retrieval_code": common.file_digest(Path(investigation.__file__).with_name("history_sources.py"))})
    new_history_work = mission.transcript_investigation / "work" / new_id
    (mission.transcript_investigation / "work" / investigated["snapshot"]).rename(new_history_work)
    investigated = {**investigated, "snapshot": new_id, "recovery_snapshot": recovered["snapshot"],
                    "source_snapshot": str(new_history_work / "sources.jsonl")}
    common.atomic_json(mission.transcript_investigation / "report.json", investigated)
    common.atomic_json(new_history_work / "snapshot.json", {
        **history_meta, "recovery": recovered, "coverage_size": coverage_size, "max_rounds": max_rounds})
    for name in ("mission_activity_tree.json", "mission_activities.json"):
        path = mission.transcript_hierarchy / name
        if path.exists():
            tree = load(path)
            tree["sources"].update(investigation_snapshot=new_id,
                enriched_transcripts="1f_investigation/utterances.jsonl",
                detected_milestones="1f_investigation/milestones.jsonl")
            common.atomic_json(path, tree)
    common.require_artifacts(mission.transcript_recovery)
    common.require_artifacts(mission.transcript_investigation)
    receipt.update(complete=True, recovery_snapshot=recovered["snapshot"], investigation_snapshot=new_id,
                   deletion_folder=str(quarantine))
    common.atomic_json(receipt_path, receipt)
    print(f"Verified relocated artifacts. Receipt: {receipt_path}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", default="artemis-ii", choices=MISSIONS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--recovery-batch-size", type=int, default=25)
    parser.add_argument("--recovery-context-lines", type=int, default=10)
    parser.add_argument("--coverage-size", type=int, default=40)
    parser.add_argument("--max-rounds", type=int, default=8)
    args = parser.parse_args()
    if (args.recovery_batch_size < 1 or args.recovery_context_lines < 0
            or args.coverage_size < 1 or args.max_rounds < 2):
        parser.error("Batch sizes must be positive, context lines nonnegative, and max-rounds at least 2")
    migrate(MISSIONS[args.mission], args.apply, args.recovery_batch_size,
            args.recovery_context_lines, args.coverage_size, args.max_rounds)


if __name__ == "__main__":
    main()
