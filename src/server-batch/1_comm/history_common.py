"""Persistence, artifact manifests and HGX requests shared by transcript stages."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import transcript_common as transcript


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    if not path.exists():
        return "missing"
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


@contextmanager
def stage_lock(root: Path):
    """Only one writer per stage; OS releases the lock even on process death."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "writer.lock").open("a+b") as fh:
        fh.seek(0)
        if not fh.read(1):
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Another process is writing {root}") from exc
        try:
            yield
        finally:
            fh.seek(0)
            if os.name == "nt":
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=["hgx"], default="hgx")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", choices=["auto", "low", "medium", "high"], default="low")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--hide-reasoning", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--limit", type=int, help="Process at most N pending work items; resumable, not a complete export")


class ModelClient:
    def __init__(self, args, diagnostic_dir: Path):
        self.args = args
        self.diagnostic_dir = diagnostic_dir
        endpoint, _, model = transcript.llm_settings(args.backend, args.model)
        self.settings = {"backend": args.backend, "endpoint": endpoint, "model": model,
                         "reasoning_effort": args.reasoning_effort, "max_tokens": args.max_tokens,
                         "temperature": 0.1}

    def request(self, system: str, prompt: str, key: str, response_format: dict | None = None) -> str:
        details = {"requested_at": now(), "settings": self.settings,
                   "system_prompt": system, "user_prompt": prompt}
        if response_format is not None:
            details["response_format"] = response_format
        try:
            _, content, stats = transcript.stream_llm_assessment(
                prompt, backend=self.args.backend, model=self.args.model,
                reasoning_effort=self.args.reasoning_effort, max_tokens=self.args.max_tokens,
                temperature=0.1, quiet=self.args.quiet, hide_reasoning=self.args.hide_reasoning,
                system_prompt=system, output_format="JSON", diagnostics=details,
                **({"response_format": response_format} if response_format is not None else {}),
            )
            details.update(content=content, stats=stats)
            if stats.get("finish_reason") != "stop" or not stats.get("done_received") or details.get("stream_issues"):
                raise ValueError("Model response was incomplete or truncated")
            return content
        except BaseException as exc:
            details["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            # Individual request files preserve the exact retrieval transcript and partial failures.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            atomic_json(self.diagnostic_dir / f"{digest(key)[:16]}_{stamp}.json", details)


def events_from_records(records: list[dict]) -> list[dict]:
    events = []
    for row in records:
        for i, milestone in enumerate(row.get("milestones", [])):
            events.append({**milestone, "id": "candidate:" + digest([row["uid"], i, milestone])[:20],
                           "source_uid": row["uid"], "channel": row["channel"], "t": row["t"],
                           "time_basis": "report", "text": row["text"]})
    return events


def write_csvs(root: Path, records: list[dict]) -> None:
    """Processing artifacts only; explicit blanks preserve unassessed fallback values."""
    def cell(value):
        return str(value if value is not None else "").replace("|", "/").replace("\n", " ").replace("\r", " ")
    for channel, filename in (("comm", "comm_enriched.csv"), ("pao", "PAO_enriched.csv")):
        lines = []
        for row in records:
            if row["channel"] != channel:
                continue
            values = [row["t"]]
            if channel == "comm":
                values += [row.get(k, "") for k in ("s", "d", "src")]
            values += [row.get("importance"), row.get("category"), ", ".join(row.get("tags", [])),
                       row.get("topic", ""), row.get("event_id", ""), row["text"]]
            lines.append("|".join(cell(v) for v in values))
        path = root / filename
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        tmp.replace(path)


def publish(root: Path, records: list[dict], events: list[dict], report: dict) -> None:
    """Manifest is committed last; consumers verify all artifact hashes."""
    atomic_jsonl(root / "utterances.jsonl", records)
    atomic_jsonl(root / "milestones.jsonl", events)
    write_csvs(root, records)
    report = {**report, "artifacts": {name: file_digest(root / name) for name in
              ("utterances.jsonl", "milestones.jsonl", "comm_enriched.csv", "PAO_enriched.csv")}}
    atomic_json(root / "report.json", report)


def require_artifacts(root: Path) -> dict:
    path = root / "report.json"
    if not path.exists():
        raise RuntimeError(f"Run the preceding stage first: missing {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("complete"):
        raise RuntimeError(f"Preceding stage is incomplete: {path}")
    for name, checksum in report.get("input_hashes", {}).items():
        if file_digest(Path(name)) != checksum:
            raise RuntimeError(f"Preceding stage input changed; rerun that stage: {name}")
    for name, checksum in report["artifacts"].items():
        if file_digest(root / name) != checksum:
            raise RuntimeError(f"Artifact changed since its manifest was written: {root / name}")
    return report


def require_reviewed_history(mission) -> tuple[Path, dict]:
    """Use canonical verified history; upstream manifests must still match."""
    report = require_artifacts(mission.transcript_investigation)
    discovery = require_artifacts(mission.transcript_discovery)
    if (report["discovery_snapshot"] != discovery["snapshot"]
            or report["discovery_artifacts"] != discovery["artifacts"]):
        raise RuntimeError("Discovery changed since investigation; rerun 1f before building or publishing the hierarchy")
    return mission.transcript_investigation, report
