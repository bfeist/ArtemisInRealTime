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
        client = client or ModelClient(args, root / "requests")
        identity = digest([inputs, client.settings, args.batch_size, args.context_lines, classifier.SYSTEM_PROMPT, classifier.response_format(args.batch_size)])
        work = root / "work" / identity
        work.mkdir(parents=True, exist_ok=True)
        print(f"  HGX single classification; checkpoints: {work}", flush=True)
        accepted, used = {}, 0
        for start in range(0, len(utterances), args.batch_size):
            targets = utterances[start:start + args.batch_size]
            path = work / f"batch_{start:06d}.json"
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not set(saved) <= {u["uid"] for u in targets}:
                raise RuntimeError("Unexpected source IDs in classification checkpoint")
            accepted.update(saved)
            remaining = [u for u in targets if u["uid"] not in saved]
            if not remaining or (args.limit is not None and used >= args.limit):
                continue
            used += 1
            before, after = transcript.source_context(utterances, start, start + len(targets) - 1, args.context_lines)
            print(f"  Classifying {start + 1}..{start + len(targets)} ({len(remaining)} pending)", flush=True)

            def request(system, prompt, schema, attempt):
                return client.request(system, prompt, f"{identity}:{start}:{attempt}", response_format=schema)

            def checkpoint(rows, issues):
                saved.update(rows)
                accepted.update(rows)
                atomic_json(path, saved)
                atomic_json(work / f"issues_{start:06d}.json", issues)

            try:
                classifier.assess(remaining, before + targets + after, request, checkpoint,
                    known=[{**u, **accepted[u["uid"]]} for u in before + targets if u["uid"] in accepted])
            except Exception as exc:
                atomic_json(work / f"error_{start:06d}.json", {"error": str(exc), "at": now()})
                break
        report = {"stage": "1d", "snapshot": identity, "settings": client.settings, "input_hashes": inputs,
                  "complete": len(accepted) == len(utterances), "classified": len(accepted),
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
