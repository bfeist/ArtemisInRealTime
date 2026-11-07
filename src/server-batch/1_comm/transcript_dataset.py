"""Explicit, model-free bridge to immutable legacy transcript/evidence artifacts.

This contract deliberately does not claim compatibility with investigation v3.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil

CONTRACT = "legacy-transcript-import-v1"


def freeze_inventory(legacy_root, web_root, output, hashes=False, hash_scope="transcript"):
    """Inventory all legacy stage evidence plus current public assets read-only.

    Output must live outside the scanned roots; existing receipts are never replaced.
    Hashing can be expensive and is explicitly selected by --hashes.
    """
    legacy_root, web_root, output = map(Path, (legacy_root, web_root, output))
    if output.exists():
        raise FileExistsError(output)
    selected = [p for p in legacy_root.iterdir() if p.is_dir() and p.name.startswith("1")]
    selected.append(web_root)
    resolved = output.resolve()
    if any(p.resolve() == resolved or p.resolve() in resolved.parents for p in selected):
        raise ValueError("Inventory output must be outside scanned stage/web roots")
    entries = []
    for directory in selected:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            before = path.stat()
            entry = {"path": str(path), "bytes": before.st_size,
                     "mtime_ns": before.st_mtime_ns}
            selected_hash = hashes and (hash_scope == "all" or directory != web_root or path.suffix.lower() in {".json", ".jsonl", ".csv"})
            if selected_hash:
                entry["sha256"] = checksum(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError(f"Artifact changed during freeze inventory: {path}")
            entries.append(entry)
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"contract": "legacy-freeze-inventory-v1", "read_only": True,
        "hashes": hashes, "hash_scope": hash_scope if hashes else "none",
        "hash_scope_description": "all selected legacy stage/work/request files and web JSON/JSONL/CSV; other web media metadata only" if hash_scope == "transcript" else "all inventoried files",
        "hashed_files": sum("sha256" in r for r in entries),
        "files": len(entries), "bytes": sum(r["bytes"] for r in entries),
        "entries": entries}
    write(output, receipt)
    return {k: v for k, v in receipt.items() if k != "entries"}


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_dataset(manifest_path, verify=True):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract") != CONTRACT:
        raise ValueError("Unsupported transcript dataset contract")
    required = {"utterances", "assessments", "milestones", "investigations", "unresolved", "history_report", "published_transcripts", "entry_mapping"}
    if not required <= manifest.get("artifacts", {}).keys() or not manifest.get("report", {}).get("complete"):
        raise ValueError("Incomplete transcript dataset")
    root = manifest_path.parent.resolve()
    paths = {}
    for name, artifact in manifest["artifacts"].items():
        path = (root / artifact["path"]).resolve()
        if root not in path.parents:
            raise ValueError("Dataset artifact escapes dataset directory")
        if verify and checksum(path) != artifact["sha256"]:
            raise ValueError(f"Dataset artifact hash mismatch: {name}")
        paths[name] = path
    result = {"manifest": manifest, "report": manifest["report"]}
    for name, path in paths.items():
        result[name] = rows(path) if path.suffix == ".jsonl" else json.loads(path.read_text(encoding="utf-8"))
    return result


def audit_evidence(dataset):
    """Check legacy event IDs and citation resolution without re-verifying claims."""
    sources = {r["id"]: r for r in dataset.get("evidence_sources", [])}
    events = dataset["milestones"]
    checked, unresolved = 0, []
    for event in events:
        for claim in event.get("claims", []):
            for citation in claim.get("citations", []):
                checked += 1
                sid, quote = citation["source_id"], citation["quote"]
                if sid not in sources or quote not in sources[sid]["text"]:
                    unresolved.append({"event_id": event["id"], "source_id": sid,
                        "reason": "missing_source" if sid not in sources else "quote_mismatch"})
    return {"events": len(events), "unique_event_ids": len({r["id"] for r in events}),
        "citations_checked": checked, "source_records": len(sources),
        "unresolved_citations": unresolved, "new_verification_claimed": False,
        "legacy_verification_version": dataset["history_report"].get("version")}


def import_legacy(legacy_root, web_root, output):
    """Copy evidence, map exact identities and preserve the accepted public projection.

    Never changes legacy files or replaces an existing dataset, even after failure.
    The manifest is written last so incomplete imports cannot be published.
    """
    legacy_root, web_root, output = map(Path, (legacy_root, web_root, output))
    if output.exists():
        raise FileExistsError(output)
    sources = {
        "utterances": legacy_root / "1ea_labeled_history/utterances.jsonl",
        "assessments": legacy_root / "1da_classification/labels.jsonl",
        "milestones": legacy_root / "1f_investigation/milestones.jsonl",
        "investigations": legacy_root / "1f_investigation/investigations.jsonl",
        "unresolved": legacy_root / "1f_investigation/unresolved_events.jsonl",
        "history_report": legacy_root / "1f_investigation/report.json",
        "legacy_history_utterances": legacy_root / "1f_investigation/utterances.jsonl",
        "published_transcripts": web_root / "transcripts.json",
    }
    history = json.loads(sources["history_report"].read_text(encoding="utf-8"))
    upstream = []
    for stage in ("1da_classification", "1ea_labeled_history", "1f_investigation"):
        report_path = legacy_root / stage / "report.json"
        if not report_path.exists():
            continue
        producing = json.loads(report_path.read_text(encoding="utf-8"))
        if not producing.get("complete"):
            raise ValueError(f"Incomplete producing legacy report: {report_path}")
        for name, expected in producing.get("artifacts", {}).items():
            artifact = report_path.parent / name
            if not artifact.is_file() or checksum(artifact) != expected:
                raise ValueError(f"Legacy recorded artifact hash mismatch: {artifact}")
        for name, expected in producing.get("input_hashes", {}).items():
            path = Path(name)
            upstream.append({"stage": stage, "path": name, "expected_sha256": expected,
                "status": "missing" if not path.is_file() else "verified" if checksum(path) == expected else "changed"})
        sources[stage + "_report"] = report_path
    publication_report = legacy_root / "1i_web_transcripts/publication_report.json"
    if publication_report.exists():
        produced = json.loads(publication_report.read_text(encoding="utf-8"))
        for name, expected in produced.get("artifacts", {}).items():
            path = web_root / name
            if not path.is_file() or checksum(path) != expected:
                raise ValueError(f"Legacy publication artifact hash mismatch: {path}")
        sources["publication_report"] = publication_report
    # Recover source snapshots as evidence, without rewriting historical paths/hashes.
    snapshot = Path(history.get("source_snapshot", ""))
    if snapshot.is_file():
        sources["evidence_sources"] = snapshot
    utterances = rows(sources["utterances"])
    label_rows = rows(sources["assessments"])
    assessments = {r["uid"]: r for r in label_rows}
    if len(assessments) != len(label_rows) or len({r["uid"] for r in utterances}) != len(utterances):
        raise ValueError("Duplicate legacy utterance/assessment UID")
    published = json.loads(sources["published_transcripts"].read_text(encoding="utf-8"))
    pao_projection = { (r["t"], r["text"]): {"pao_commentary": "commentary", "pao_filler": "filler"}.get(r.get("category"), "other")
        for r in rows(sources["legacy_history_utterances"]) if r.get("channel") == "pao" } if sources["legacy_history_utterances"].exists() else {}
    if not sources["legacy_history_utterances"].exists():
        del sources["legacy_history_utterances"]
    projection_mismatches = [i for i, r in enumerate(published.get("pao", []))
        if pao_projection.get((r["t"], r["text"])) != r.get("pao_type")] if pao_projection else []
    pao_provenance = {"contract": "legacy-category-projection", "source_stage": "1f_investigation",
        "rule": {"pao_commentary": "commentary", "pao_filler": "filler", "otherwise": "other"},
        "checked_entries": len(published.get("pao", [])) if pao_projection else 0,
        "mismatches": projection_mismatches, "original_program": "unknown",
        "new_constrained_classification": False}
    index = {}
    for row in utterances:
        # Literal bracket annotations and nested speaker names remain exact text.
        index.setdefault((row["channel"], row["t"], row["text"]), []).append((row, None))
    mappings, unmatched, ambiguous = [], [], []
    for channel in ("comm", "pao"):
        for position, row in enumerate(published.get(channel, [])):
            candidates = index.get((channel, row["t"], row["text"]), [])
            if channel == "comm" and row.get("speaker"):
                # Reconstruct the exact old1i transformation using the published
                # speaker, including embedded brackets; never guess its boundary.
                prefixed = f"[{row['speaker']}] {row['text']}"
                candidates = candidates + index.get((channel, row["t"], prefixed), [])
            matches = [r for r, speaker in candidates if (not speaker or speaker == row.get("speaker"))
                and (channel != "comm" or (r.get("src", "") == row.get("src", "")
                    and float(r.get("s") or 0) == float(row.get("s") or 0)
                    and float(r.get("d") or 0) == float(row.get("d") or 0)))]
            if not matches:
                unmatched.append({"channel": channel, "position": position})
                continue
            uids = sorted({r["uid"] for r in matches})
            if len(uids) != 1:
                ambiguous.append({"channel": channel, "position": position, "uids": uids})
                continue
            uid = uids[0]
            mappings.append({"channel": channel, "position": position, "uid": uid,
                "assessment": uid if uid in assessments else None,
                "pao_type_provenance": ("legacy-category-projection verified against preserved1f rows" if pao_projection else "legacy-published-projection; generating rule not recovered")
                    if channel == "pao" else None})
    missing = [r["uid"] for r in mappings if not r["assessment"]]
    missing_fields = {field: sum(field not in r for r in assessments.values()) for field in
        ("category", "importance", "tags", "topic", "statement_kinds", "thread_context", "pao_type")}
    report = {"model_requests": 0, "published_entries": sum(len(published.get(c, [])) for c in ("comm", "pao")),
        "mapped_entries": len(mappings), "unmatched": unmatched, "ambiguous": ambiguous,
        "missing_assessments": missing, "legacy_history_version": history.get("version"),
        "assessment_missing_fields": missing_fields,
        "pao_type_provenance": pao_provenance,
        "verified_events": history.get("verified_events"), "unresolved_events": history.get("unresolved_events"),
        "legacy_contract_preserved": True, "v3_verification_claimed": False,
        "source_rows": len(utterances), "unpublished_source_rows": len(utterances) - len(mappings),
        "upstream_provenance": upstream,
        "complete": not (unmatched or ambiguous or missing or projection_mismatches)}
    output.mkdir(parents=True)
    artifacts = {}
    for name, source in sources.items():
        before = checksum(source)
        target = output / (name + source.suffix)
        shutil.copyfile(source, target)
        if checksum(target) != before or checksum(source) != before:
            raise RuntimeError(f"Artifact changed during import: {source}")
        artifacts[name] = {"path": target.name, "sha256": before, "source": str(source), "bytes": target.stat().st_size}
    write(output / "entry_mapping.json", mappings)
    artifacts["entry_mapping"] = {"path": "entry_mapping.json", "sha256": checksum(output / "entry_mapping.json")}
    write(output / "manifest.json", {"contract": CONTRACT, "artifacts": artifacts, "report": report})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--web-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--inventory", action="store_true", help="Inventory legacy stage/work/request and web files without importing")
    parser.add_argument("--hashes", action="store_true", help="Include SHA256 for every inventory file")
    parser.add_argument("--hash-scope", choices=["transcript", "all"], default="transcript")
    args = parser.parse_args()
    operation = freeze_inventory if args.inventory else import_legacy
    options = {"hashes": args.hashes, "hash_scope": args.hash_scope} if args.inventory else {}
    print(json.dumps(operation(args.legacy_root, args.web_root, args.output, **options), indent=2))
