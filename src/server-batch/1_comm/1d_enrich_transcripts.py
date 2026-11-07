"""Step 1d: classify transcripts once, independently of event discovery."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS
from history_common import (ModelClient, add_model_arguments, atomic_json,
                            digest, transcript, file_digest, now, publish, stage_lock)
import single_classification as classifier
from assessment_reuse import dependency_key, validate_plan, logical_batches, inherited_assessments, semantic


def classify(mission, args, client=None):
    if args.backend != "hgx":
        raise ValueError("1d requires the HGX provider")
    root = mission.transcript_enrichment
    with stage_lock(root):
        utterances = transcript.load_all_utterances(mission)
        if not utterances:
            raise RuntimeError("No source transcripts to classify")
        paths = [mission.transcript_input(name) for name in ("comm.csv", "PAO.csv")]
        inputs = {str(p): file_digest(p) for p in paths}
        plan = validate_plan(args, utterances, "1d") if client is None else {}
        client = client or ModelClient(args, root / "requests")
        identity = digest([inputs, client.settings, args.batch_size, args.context_lines, classifier.SYSTEM_PROMPT, classifier.response_format(args.batch_size)])
        work = root / "work" / identity
        work.mkdir(parents=True, exist_ok=True)
        print(f"  HGX single classification; checkpoints: {work}", flush=True)
        accepted = inherited_assessments(plan["baseline_dataset_manifest"], utterances, args.context_lines) if plan.get("baseline_dataset_manifest") else {}
        inherited_count = len(accepted)
        used = 0
        # Exact supplied group/context dependencies survive unrelated file changes.
        cache = root / "semantic_cache"
        cache.mkdir(parents=True, exist_ok=True)
        for start, targets in logical_batches(utterances, args.batch_size):
            before, after = transcript.source_context(utterances, start, start + len(targets) - 1, args.context_lines)
            known = [{**u, **accepted[u["uid"]]} for u in before + targets if u["uid"] in accepted]
            key = dependency_key(targets, before + targets + after, client.settings,
                                 [classifier.SYSTEM_PROMPT, [[semantic(r), {k: r.get(k) for k in
                                    ("category", "statement_kinds", "thread_context")}] for r in known]],
                                 classifier.response_format(len(targets)))
            path = cache / (key + ".json")
            cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if "rows" in cached:
                current = before + targets + after
                aliases = dict(zip(cached["context_uids"], [r["uid"] for r in current]))
                saved = {aliases.get(uid, uid): {**label,
                    "thread_context": [{**thread, "source_uids": [aliases.get(uid, uid) for uid in thread.get("source_uids", [])]}
                        for thread in label.get("thread_context", [])]} for uid, label in cached["rows"].items()}
            else:
                saved = cached
            if not set(saved) <= {u["uid"] for u in targets}:
                raise RuntimeError("Unexpected source IDs in classification checkpoint")
            accepted.update(saved)
            saved.update({u["uid"]: accepted[u["uid"]] for u in targets if u["uid"] in accepted})
            remaining = [u for u in targets if u["uid"] not in saved]
            if not remaining or (args.limit is not None and used >= args.limit):
                continue
            used += 1
            print(f"  Classifying {start + 1}..{start + len(targets)} ({len(remaining)} pending)", flush=True)

            def request(system, prompt, schema, attempt):
                return client.request(system, prompt, f"{identity}:{start}:{attempt}", response_format=schema)

            def checkpoint(rows, issues):
                saved.update(rows)
                accepted.update(rows)
                atomic_json(path, {"context_uids": [r["uid"] for r in before + targets + after], "rows": saved})
                atomic_json(work / f"batch_{start:06d}.json", saved)
                atomic_json(work / f"issues_{start:06d}.json", issues)

            try:
                classifier.assess(remaining, before + targets + after, request, checkpoint,
                    known=known)
            except Exception as exc:
                atomic_json(work / f"error_{start:06d}.json", {"error": str(exc), "at": now()})
                break
        report = {"stage": "1d", "snapshot": identity, "settings": client.settings, "input_hashes": inputs,
                  "model_requests": getattr(client, "request_count", len(getattr(client, "calls", []))),
                  "complete": len(accepted) == len(utterances), "classified": len(accepted),
                  "inherited_legacy_assessments": inherited_count,
                  "pending": len(utterances) - len(accepted)}
        atomic_json(work / "progress.json", report)
        if not report["complete"]:
            print(f"  {report['pending']} labels pending; repeat this command to resume", flush=True)
            return report
        if inputs != {str(p): file_digest(p) for p in paths}:
            raise RuntimeError("Classification inputs changed; outputs not committed")
        records = [{**u, **accepted[u["uid"]]} for u in utterances]
        publish(root, records, [], report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", default="artemis-ii", choices=["artemis-ii"])
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--context-lines", type=int, default=5)
    add_model_arguments(parser)
    args = parser.parse_args()
    if args.batch_size < 1 or args.context_lines < 0 or (args.limit is not None and args.limit < 1):
        parser.error("batch-size/limit must be positive; context-lines must be nonnegative")
    if not classify(MISSIONS[args.mission], args)["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
