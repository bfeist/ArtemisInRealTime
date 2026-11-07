"""Publish enriched comm/PAO for the transcript viewer without running models.

Read canonical classifications (1d); take text/timing/audio from current source inputs.
The enriched CSV projections and all model diagnostics remain processing files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS, MissionConfig
from history_common import transcript, file_digest, read_jsonl, require_artifacts, stage_lock
from shared.pds import atomic_json

ASSESSMENT_FIELDS = ("importance", "category", "tags", "topic", "event_id", "statement_kinds")


def publish_transcripts(mission: MissionConfig) -> dict:
    root = mission.processed_transcripts / "1i_web_transcripts"
    with stage_lock(root):
        source = mission.transcript_enrichment
        manifest = require_artifacts(source)
        ledger_path = source / "utterances.jsonl"

        inputs = [ledger_path, mission.transcript_input("comm.csv"),
                  mission.transcript_input("PAO.csv"), mission.web_dir / "comm.json"]
        checksums = {str(path): file_digest(path) for path in inputs}
        ledger = {row["uid"]: row for row in read_jsonl(ledger_path)}
        structured_path = mission.web_dir / "comm.json"
        structured = json.loads(structured_path.read_text(encoding="utf-8")) if structured_path.exists() else []
        speakers = {(row["t"], row.get("src"), float(row.get("s", 0))): row.get("speaker")
                    for row in structured}
        result = {"schema_version": 1, "source_stage": source.name, "comm": [], "pao": []}
        assessed = 0
        for utterance in transcript.load_all_utterances(mission):
            row = {"t": utterance["t"], "text": utterance["text"],
                   "source": transcript.transcript_source(utterance)}
            if utterance["channel"] == "comm":
                row.update(s=float(utterance["s"]), d=float(utterance["d"]), src=utterance["src"])
                speaker = speakers.get((row["t"], row["src"], row["s"]))
                if speaker:
                    row["speaker"] = speaker
                    # The interim CSV includes a speaker prefix; JSON stores it separately.
                    prefix = f"[{speaker}] "
                    if row["text"].startswith(prefix):
                        row["text"] = row["text"][len(prefix):]
            assessment = ledger.get(utterance["uid"], {})
            row.update({key: assessment[key] for key in ASSESSMENT_FIELDS
                        if assessment.get(key) is not None})
            assessed += bool(row.get("category"))
            result[utterance["channel"]].append(row)
        if not result["comm"] and not result["pao"]:
            raise RuntimeError("Refusing to publish empty transcripts")
        if checksums != {str(path): file_digest(path) for path in inputs}:
            raise RuntimeError("Transcript inputs changed during publication; rerun 1i")
        if require_artifacts(source) != manifest:
            raise RuntimeError("Assessments changed during publication; rerun 1i")
        atomic_json(mission.web_dir / "transcripts.json", result)
        report = {"source_stage": source.name, "input_hashes": checksums,
                  "comm_entries": len(result["comm"]), "pao_entries": len(result["pao"]),
                  "categorized_entries": assessed}
        atomic_json(root / "publication_report.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", choices=list(MISSIONS), default="artemis-ii")
    args = parser.parse_args()
    print(json.dumps(publish_transcripts(MISSIONS[args.mission]), indent=2))


if __name__ == "__main__":
    main()
