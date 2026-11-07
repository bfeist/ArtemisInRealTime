"""Step 1e: discover candidate developments without changing transcript labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS
from history_common import (ModelClient, add_model_arguments, atomic_json, digest,
                            events_from_records, file_digest, publish, read_jsonl, require_artifacts, stage_lock, transcript)
from transcript_stages import EXTRACTION_SYSTEM_PROMPT, anchored_milestones, involved_crew, parse_extraction


def discover(mission, args, client=None):
    root = mission.transcript_discovery
    with stage_lock(root):
        upstream = require_artifacts(mission.transcript_enrichment)
        records = read_jsonl(mission.transcript_enrichment / "utterances.jsonl")
        context = transcript.MissionContextIndex(mission)
        paths = [mission.web_dir / name for name in
                 ("photos.json", "itinerary.json", "videoYt.json", "videoPDS.json")]
        inputs = {**upstream.get("input_hashes", {}), **{str(p): file_digest(p) for p in paths},
                  str(mission.transcript_enrichment / "report.json"):
                      file_digest(mission.transcript_enrichment / "report.json")}
        client = client or ModelClient(args, root / "requests")
        identity = digest([inputs, client.settings, args.batch_size, args.context_lines, EXTRACTION_SYSTEM_PROMPT])
        work = root / "work" / identity
        work.mkdir(parents=True, exist_ok=True)
        accepted, errors, used, stopped = {}, [], 0, False

        def extract(targets, before, after):
            # A failed large request is split; successful groups are committed immediately.
            nonlocal used, stopped
            path = work / (digest([r["uid"] for r in targets]) + ".json")
            event_ids = context.query_event_ids(targets[0]["ts"], targets[-1]["ts"])
            if path.exists():
                extraction = parse_extraction(path.read_text(encoding="utf-8"), targets, event_ids)
            else:
                if stopped or (args.limit is not None and used >= args.limit):
                    return
                used += 1
                prompt = transcript.build_user_prompt(targets, before,
                    context.query_window(targets[0]["ts"], targets[-1]["ts"]),
                    ledger=accepted, context_after=after)
                try:
                    raw = client.request(EXTRACTION_SYSTEM_PROMPT, prompt, "discover:" + path.stem)
                    extraction = parse_extraction(raw, targets, event_ids)
                    atomic_json(path, extraction)
                except Exception as exc:
                    rejected = isinstance(exc, ValueError)
                    issue = {"uids": [u["uid"] for u in targets], "error": str(exc), "split": rejected}
                    errors.append(issue)
                    atomic_json(work / ("error_" + path.name), issue)
                    if not rejected:
                        stopped = True
                    if rejected and len(targets) > 1:
                        mid = len(targets) // 2
                        extract(targets[:mid], before, targets[mid:] + after)
                        extract(targets[mid:], before + targets[:mid], after)
                    return
            milestones = anchored_milestones(extraction, len(targets))
            crew = involved_crew(extraction, len(targets))
            for i, row in enumerate(targets):
                accepted[row["uid"]] = {**row, "crew": crew[i], "milestones": milestones[i]}

        # Resume by replaying saved groups before requesting any remaining targets.
        # Split groups use deterministic UID keys and local extraction indices.
        for start in range(0, len(records), args.batch_size):
            targets = records[start:start + args.batch_size]
            before, after = transcript.source_context(records, start, start + len(targets) - 1, args.context_lines)

            def resume(group, preceding, following):
                path = work / (digest([r["uid"] for r in group]) + ".json")
                split = work / ("error_" + path.name)
                split_group = split.exists() and json.loads(split.read_text(encoding="utf-8")).get("split")
                if path.exists() or len(group) == 1 or not split_group:
                    extract(group, preceding, following)
                else:
                    mid = len(group) // 2
                    resume(group[:mid], preceding, group[mid:] + following)
                    resume(group[mid:], preceding + group[:mid], following)

            resume(targets, before, after)
        report = {"stage": "1e", "snapshot": identity, "settings": client.settings,
                  "input_hashes": inputs, "complete": len(accepted) == len(records),
                  "total_utterances": len(records), "pending": len(records) - len(accepted), "errors": errors}
        atomic_json(work / "progress.json", report)
        if report["complete"]:
            if require_artifacts(mission.transcript_enrichment) != upstream or any(
                    file_digest(Path(name)) != checksum for name, checksum in inputs.items()):
                raise RuntimeError("Discovery inputs changed; checkpoints retained, exports not committed")
            rows = [accepted[r["uid"]] for r in records]
            publish(root, rows, events_from_records(rows), {**report, "errors": []})
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", default="artemis-ii", choices=["artemis-ii"])
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--context-lines", type=int, default=5)
    add_model_arguments(parser)
    args = parser.parse_args()
    if args.batch_size < 1 or args.context_lines < 0 or (args.limit is not None and args.limit < 1):
        parser.error("batch-size/limit must be positive; context-lines must be nonnegative")
    if not discover(MISSIONS[args.mission], args)["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
