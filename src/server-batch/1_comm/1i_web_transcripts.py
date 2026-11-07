"""Publish enriched comm/PAO for the transcript viewer without running models.

Read canonical classifications (1d); take text/timing/audio from current source inputs.
Publishes transcripts.json and enriched COMM/PAO CSVs. Model diagnostics stay in processing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS, MissionConfig
from history_common import transcript, file_digest, read_jsonl, require_artifacts, stage_lock, write_csvs
from shared.pds import atomic_json
from shared.pao_audio import apply_pao_audio

ASSESSMENT_FIELDS = ("importance", "category", "tags", "topic", "event_id", "statement_kinds")


def prepared_pao(mission, transcripts, manifest_path=None, receipts=None):
    path = Path(manifest_path) if manifest_path else mission.processed_transcripts / "pao_audio/manifest.json"
    if manifest_path or path.exists():
        payload, audio_receipts, checksum = apply_pao_audio(transcripts, path)
        return payload, audio_receipts, {"pao_audio_manifest_sha256": checksum}
    return transcripts, receipts, {}


def publish_transcripts(mission: MissionConfig, *, dataset_manifest=None, release_id=None, activate=False, include_exports=False, sync_path=None, legacy_baseline=None, pao_audio_manifest=None) -> dict:
    from shared.web_release import stage_release, activate_release
    if dataset_manifest:
        from transcript_dataset import read_dataset
        dataset = read_dataset(Path(dataset_manifest))
        if not dataset["report"].get("complete"):
            raise RuntimeError("Imported dataset mapping is incomplete")
        assessments = {row['uid']: row for row in dataset.get('assessments', [])}
        originals = {row['uid']: row for row in dataset['utterances']}
        records = [{**assessments.get(mapping['uid'], {}), **originals[mapping['uid']],
                    **dataset['published_transcripts'][mapping['channel']][mapping['position']]}
                   for mapping in dataset['entry_mapping']] if include_exports else []
        payload, receipts, pao_provenance = prepared_pao(mission, dataset["published_transcripts"],
            pao_audio_manifest, dataset['manifest'].get('paoPolicy', {}).get('audioDerivatives', {}))
        destination, report = stage_release(
            mission, payload, release_id=release_id, sync_path=sync_path,
            pao_audio_receipts=receipts,
            exports=(lambda target: write_csvs(target, records)) if include_exports else None,
            provenance={"dataset_manifest_sha256": file_digest(Path(dataset_manifest)),
                        "dataset_contract": dataset["manifest"].get("contract"), **pao_provenance})
        if activate:
            activate_release(mission, destination.name, legacy_baseline=legacy_baseline)
        return {**report, "release_path": str(destination), "activated": activate}
    root = mission.processed_transcripts / "1i_web_transcripts"
    with stage_lock(root):
        source = mission.transcript_enrichment
        manifest = require_artifacts(source)
        ledger_path = source / "utterances.jsonl"

        inputs = [ledger_path, mission.transcript_input("comm.csv"),
                  mission.transcript_input("PAO.csv"), mission.transcript_inputs / "comm.json"]
        checksums = {str(path): file_digest(path) for path in inputs}
        ledger = {row["uid"]: row for row in read_jsonl(ledger_path)}
        structured_path = mission.transcript_inputs / "comm.json"
        structured = json.loads(structured_path.read_text(encoding="utf-8")) if structured_path.exists() else []
        speakers = {(row["t"], row.get("src"), float(row.get("s", 0))): row.get("speaker")
                    for row in structured}
        result = {"schema_version": 1, "source_stage": source.name, "comm": [], "pao": []}
        assessed, csv_records = 0, []
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
            csv_records.append({**assessment, **utterance})
            row.update({key: assessment[key] for key in ASSESSMENT_FIELDS
                        if assessment.get(key) is not None})
            if utterance["channel"] == "pao" and assessment.get("pao_type") is not None:
                row["pao_type"] = assessment["pao_type"]
            assessed += bool(row.get("category"))
            result[utterance["channel"]].append(row)
        if not result["comm"] and not result["pao"]:
            raise RuntimeError("Refusing to publish empty transcripts")
        if checksums != {str(path): file_digest(path) for path in inputs}:
            raise RuntimeError("Transcript inputs changed during publication; rerun 1i")
        if require_artifacts(source) != manifest:
            raise RuntimeError("Assessments changed during publication; rerun 1i")
        result, receipts, pao_provenance = prepared_pao(mission, result, pao_audio_manifest)
        destination, report = stage_release(
            mission, result, release_id=release_id,
            pao_audio_receipts=receipts,
            exports=(lambda target: write_csvs(target, csv_records)) if include_exports else None,
            sync_path=sync_path, provenance={"assessment_ledger_sha256": file_digest(ledger_path), **pao_provenance})
        report.update(source_stage=source.name, input_hashes=checksums,
                      comm_entries=len(result["comm"]), pao_entries=len(result["pao"]),
                      categorized_entries=assessed, release_path=str(destination), activated=activate)
        if activate:
            activate_release(mission, destination.name, legacy_baseline=legacy_baseline)
        atomic_json(root / "publication_report.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", choices=list(MISSIONS), default="artemis-ii")
    parser.add_argument("--dataset-manifest", type=Path, help="Verified model-free imported dataset")
    parser.add_argument("--release-id")
    parser.add_argument("--activate", action="store_true", help="Atomically switch active pointer after verification")
    parser.add_argument("--activate-release", help="Verify and activate an existing release (also supports rollback)")
    parser.add_argument("--legacy-baseline", help="Preserved baseline release required for first activation over legacy assets")
    parser.add_argument("--include-exports", action="store_true")
    parser.add_argument("--pao-audio-manifest", type=Path, help="Prepared 2r audio mappings; auto-reused when present")
    parser.add_argument("--sync-path", type=Path, help="Private verified audio-evidence mappings; projected against local part manifest")
    args = parser.parse_args()
    if args.activate_release:
        from shared.web_release import activate_release
        print(json.dumps(activate_release(MISSIONS[args.mission], args.activate_release, legacy_baseline=args.legacy_baseline), indent=2))
        return
    print(json.dumps(publish_transcripts(MISSIONS[args.mission], dataset_manifest=args.dataset_manifest, release_id=args.release_id, activate=args.activate, include_exports=args.include_exports, sync_path=args.sync_path, legacy_baseline=args.legacy_baseline, pao_audio_manifest=args.pao_audio_manifest), indent=2))


if __name__ == "__main__":
    main()
