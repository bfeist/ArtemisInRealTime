"""Step 1d — Enrich transcripts and discover granular mission milestones.

Merges space-to-ground comm and public affairs commentary from processed/transcripts/inputs/
chronologically, groups them into context windows alongside mission itinerary events
(web/itinerary.json), and streams assessments from LLM-Alpha-512K (GLM-5.3-Flash) via
HGX_URL, or qwen3.8-flash-next via a local Strata instance (--backend strata).

Live-streams both the model's reasoning tokens and structured JSONL output to the
terminal as it runs, and persists every assessed utterance immediately to an
append-only, crash-resumable JSONL ledger:
  {data_dir}/{mission}/processed/transcripts/1d_enrichment/enriched_utterances.jsonl

Derived milestone stream (one record per development, with source evidence):
  {data_dir}/{mission}/processed/transcripts/1d_enrichment/detected_milestones.jsonl

Also writes enriched processing outputs:
  {data_dir}/{mission}/processed/transcripts/1d_enrichment/comm_enriched.csv
  {data_dir}/{mission}/processed/transcripts/1d_enrichment/PAO_enriched.csv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    HGX_API_KEY, HGX_URL, MISSIONS, STRATA_API_KEY, STRATA_MODEL, STRATA_URL,
    MissionConfig,
)
from mission_context import (  # noqa: E402
    MissionContextIndex,
    infer_crew_members,
    normalize_crew_list,
)

from transcript_stages import (  # noqa: E402
    CATEGORIES, EXTRACTION_SYSTEM_PROMPT, LABEL_SYSTEM_PROMPT, MILESTONE_STATUSES,
    anchored_milestones, build_label_prompt, involved_crew, parse_extraction,
    update_conversation_state,
)

DEFAULT_MODEL = "LLM-Alpha-512K"
ASSESSMENT_VERSION = 2
PROMPT_VERSION = 5  # Increment for prompt-builder changes, independently of the schema.
PROVENANCE_FIELDS = (
    "backend", "model", "model_provenance", "provenance_recorded_at",
    "run_id", "batch", "assessed_at", "prompt_version", "system_prompt_sha256",
    "user_prompt_sha256", "generation_settings", "stage_provenance",
)
SYSTEM_PROMPT = LABEL_SYSTEM_PROMPT


# ANSI colors for live terminal streaming
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_YELLOW = "\033[33m"
C_GREEN = "\033[32m"
C_MAGENTA = "\033[35m"
C_BOLD = "\033[1m"


def make_uid(channel: str, utc_str: str, text: str) -> str:
    """Deterministic unique ID for an utterance."""
    h = hashlib.sha1(f"{channel}|{utc_str}|{text}".encode("utf-8")).hexdigest()[:10]
    return f"{channel}:{utc_str}:{h}"


def parse_utc_ts(utc_str: str) -> float:
    return datetime.fromisoformat(utc_str.replace("Z", "+00:00")).timestamp()


def load_all_utterances(mission: MissionConfig) -> list[dict]:
    """Merge processing CSVs chronologically, with legacy web/ input fallback."""
    utterances: list[dict] = []
    seen_uids: set[str] = set()

    comm_path = mission.transcript_input("comm.csv")
    if comm_path.exists():
        with open(comm_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split("|", 4)
                if len(parts) < 5:
                    continue
                t_str, s_str, d_str, src, text = parts
                text = text.strip()
                if not t_str or not text:
                    continue
                uid = make_uid("comm", t_str, text)
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
                utterances.append({
                    "uid": uid,
                    "channel": "comm",
                    "t": t_str,
                    "ts": parse_utc_ts(t_str),
                    "s": s_str,
                    "d": d_str,
                    "src": src,
                    "text": text,
                })

    pao_path = mission.transcript_input("PAO.csv")
    if pao_path.exists():
        with open(pao_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split("|", 1)
                if len(parts) < 2:
                    continue
                t_str, text = parts
                text = text.strip()
                if not t_str or not text:
                    continue
                uid = make_uid("pao", t_str, text)
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
                utterances.append({
                    "uid": uid,
                    "channel": "pao",
                    "t": t_str,
                    "ts": parse_utc_ts(t_str),
                    "s": "",
                    "d": "",
                    "src": "",
                    "text": text,
                })

    utterances.sort(key=lambda u: (u["ts"], 0 if u["channel"] == "comm" else 1))
    return utterances


def load_existing_ledger(ledger_path: Path) -> dict[str, dict]:
    """Load already-assessed utterances keyed by uid."""
    done: dict[str, dict] = {}
    if not ledger_path.exists():
        return done
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                uid = rec.get("uid")
                if uid:
                    done[uid] = rec
            except json.JSONDecodeError:
                continue
    return done


def build_user_prompt(
    target_batch: list[dict],
    context_before: list[dict],
    events_context: list[str],
    ledger: dict[str, dict] | None = None,
    context_after: list[dict] | None = None,
    conversation_state: dict | None = None,
) -> str:
    lines: list[str] = []
    if events_context:
        lines.append("=== SUPPLEMENTARY MISSION REFERENCES (not an exhaustive event list) ===")
        lines.extend(events_context)
        lines.append("")

    if conversation_state and any(conversation_state.values()):
        lines.extend([
            "=== PRIOR VALIDATED CONVERSATION STATE (historical context only) ===",
            "Continue from these recent threads and ALREADY CAPTURED developments. "
            "Preserve statement kinds: plans and hypotheses are not execution. "
            "Suppress only a recap of the same action AND stage. Approval to load, "
            "chill down, loading start, and loading completion are distinct developments; "
            "capturing approval or preparation must not suppress a later execution report. "
            "Use this state for interpretation, not as evidence for new developments. "
            "All output indices and exact evidence refer only to CURRENT targets. "
            "Reconstruct the current targets without outputting the prior state.",
            json.dumps(conversation_state, separators=(",", ":")),
            "",
        ])

    if context_before:
        lines.append("=== PRECEDING CONTEXT (for interpretation only — do NOT output JSON for these) ===")
        for u in context_before:
            ch = u["channel"].upper()
            lines.append(f"[CTX] {u['t']} [{ch}] {u['text']}")
            for milestone in (ledger or {}).get(u["uid"], {}).get("milestones", []):
                lines.append(f"[ALREADY CAPTURED] {milestone['title']} ({milestone['status']})")
        lines.append("")

    lines.append(f"=== TARGET UTTERANCES TO EXTRACT (cover each [0]..[{len(target_batch) - 1}]) ===")
    for idx, u in enumerate(target_batch):
        ch = u["channel"].upper()
        lines.append(f"[{idx}] {u['t']} [{ch}] {u['text']}")

    if context_after:
        lines.append("")
        lines.append("=== FOLLOWING CONTEXT (for interpretation only — do NOT output JSON for these) ===")
        for u in context_after:
            lines.append(f"[CTX] {u['t']} [{u['channel'].upper()}] {u['text']}")

    lines.append("")
    lines.append("Extract threads, statements and anchored developments for all targets. Return only the extraction JSON object.")
    return "\n".join(lines)


def llm_settings(backend: str, model: str | None = None) -> tuple[str, str, str]:
    """Resolve the API base URL, credentials and model without mixing backends."""
    if backend == "hgx":
        return HGX_URL, HGX_API_KEY, model or DEFAULT_MODEL
    if backend == "strata":
        return STRATA_URL, STRATA_API_KEY, model or STRATA_MODEL
    raise ValueError(f"Unknown LLM backend: {backend}")


def stream_llm_assessment(
    user_prompt: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_tokens: int = 65536,
    temperature: float = 0.2,
    quiet: bool = False,
    diagnostics: dict | None = None,
    backend: str = "hgx",
    hide_reasoning: bool = False,
    system_prompt: str | None = None,
    output_format: str = "JSONL",
) -> tuple[str, str, dict]:
    """Stream reasoning and content from the selected API and print live."""
    base_url, api_key, model = llm_settings(backend, model)
    if backend == "hgx" and (not base_url or not api_key):
        raise RuntimeError("HGX_URL and HGX_API_KEY must be set in .env")
    if not base_url:
        raise RuntimeError("STRATA_URL must be set in .env (API base URL, including any /v1 prefix)")

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt if system_prompt is not None else SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if reasoning_effort and reasoning_effort != "auto":
        payload["reasoning_effort"] = reasoning_effort

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )

    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    phase = "init"
    t0 = time.monotonic()
    first_token_t: float | None = None
    reasoning_tokens_est = 0
    content_tokens_est = 0
    usage_info: dict = {}
    finish_reason = None
    done_received = False
    raw_stream = []
    stream_issues = []
    if diagnostics is not None:
        # Shared lists retain the partial response if reading raises or is interrupted.
        diagnostics.update(raw_stream=raw_stream, stream_issues=stream_issues)

    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            raw_stream.append(line)
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                done_received = True
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError as exc:
                stream_issues.append({"reason": "invalid_stream_json", "error": str(exc), "raw": data_str})
                continue

            if not isinstance(chunk, dict):
                stream_issues.append({"reason": "invalid_stream_chunk", "raw": data_str})
                continue
            if chunk.get("error"):
                raise RuntimeError(f"LLM stream error: {chunk['error']}")

            if chunk.get("usage"):
                usage_info = chunk["usage"]

            choices = chunk.get("choices") or []
            if not choices:
                continue
            if choices[0].get("finish_reason") is not None:
                finish_reason = choices[0]["finish_reason"]
            delta = choices[0].get("delta") or {}

            r_tok = delta.get("reasoning") or delta.get("reasoning_content") or ""
            c_tok = delta.get("content") or ""

            if r_tok:
                if first_token_t is None:
                    first_token_t = time.monotonic()
                if phase != "reasoning":
                    phase = "reasoning"
                    if not quiet and not hide_reasoning:
                        sys.stdout.write(f"\n{C_CYAN}{C_BOLD}── [LLM REASONING] ──{C_RESET}\n{C_DIM}")
                reasoning_parts.append(r_tok)
                reasoning_tokens_est += 1
                if not quiet and not hide_reasoning:
                    sys.stdout.write(r_tok)
                    sys.stdout.flush()

            if c_tok:
                if first_token_t is None:
                    first_token_t = time.monotonic()
                if phase != "content":
                    if phase == "reasoning" and not quiet and not hide_reasoning:
                        sys.stdout.write(f"{C_RESET}\n")
                    phase = "content"
                    if not quiet:
                        sys.stdout.write(f"\n{C_GREEN}{C_BOLD}── [LLM OUTPUT ({output_format})] ──{C_RESET}\n")
                content_parts.append(c_tok)
                content_tokens_est += 1
                if not quiet:
                    sys.stdout.write(c_tok)
                    sys.stdout.flush()

    if not quiet:
        sys.stdout.write(f"{C_RESET}\n")
        sys.stdout.flush()

    elapsed = time.monotonic() - t0
    ttft = (first_token_t - t0) if first_token_t else elapsed
    total_gen = usage_info.get("completion_tokens") or (reasoning_tokens_est + content_tokens_est)
    tok_per_s = total_gen / max(0.001, elapsed - ttft) if elapsed > ttft else 0.0

    stats = {
        "elapsed_s": round(elapsed, 2),
        "ttft_s": round(ttft, 2),
        "reasoning_chunks": reasoning_tokens_est,
        "content_chunks": content_tokens_est,
        "completion_tokens": total_gen,
        "prompt_tokens": usage_info.get("prompt_tokens"),
        "tok_per_s": round(tok_per_s, 1),
        "finish_reason": finish_reason,
        "done_received": done_received,
    }
    return "".join(reasoning_parts), "".join(content_parts), stats


def normalize_milestones(
    raw: object, text: str, event_ids: set[str], diagnostics: list[dict] | None = None,
) -> list[dict] | None:
    """Validate transcript evidence; discard invalid catalog links, not discoveries."""
    if not isinstance(raw, list):
        if diagnostics is not None:
            diagnostics.append({"reason": "milestones_not_array", "value": raw})
        return None
    milestones = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            if diagnostics is not None:
                diagnostics.append({"reason": "milestone_not_object", "milestone_index": index, "value": item})
            return None
        title = item.get("title")
        status = item.get("status")
        evidence = item.get("evidence")
        if (
            not isinstance(title, str) or not title.strip()
            or not isinstance(status, str) or status not in MILESTONE_STATUSES
            or not isinstance(evidence, str) or not evidence.strip()
            or evidence.strip() not in text
        ):
            if diagnostics is not None:
                reasons = []
                if not isinstance(title, str) or not title.strip():
                    reasons.append("invalid_title")
                if not isinstance(status, str) or status not in MILESTONE_STATUSES:
                    reasons.append("invalid_status")
                if not isinstance(evidence, str) or not evidence.strip():
                    reasons.append("invalid_evidence")
                elif evidence.strip() not in text:
                    reasons.append("evidence_not_in_utterance")
                diagnostics.append({"reason": "invalid_milestone", "milestone_index": index,
                                    "errors": reasons, "value": item})
            return None
        milestone = {"title": title.strip(), "status": status, "evidence": evidence.strip()}
        event_id = item.get("event_id")
        if isinstance(event_id, str) and event_id in event_ids:
            milestone["event_id"] = event_id
        elif event_id and diagnostics is not None:
            diagnostics.append({"reason": "discarded_event_id", "milestone_index": index, "value": event_id})
        m_crew = normalize_crew_list(item.get("crew"))
        if m_crew:
            milestone["crew"] = m_crew
        key = (milestone["title"].casefold(), status, milestone["evidence"])
        if key not in seen:
            milestones.append(milestone)
            seen.add(key)
    return milestones


def parse_jsonl_assessments(
    content: str, target_batch: list[dict], event_ids: set[str],
    diagnostics: list[dict] | None = None,
) -> dict[int, dict]:
    """Parse nested JSONL and leave invalid assessments pending for a later run."""
    results: dict[int, dict] = {}
    decoder = json.JSONDecoder()
    for line_number, line in enumerate(content.splitlines(), 1):
        line = line.strip().rstrip(",")
        if not line or line.startswith("```"):
            continue
        obj = None
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                pass
        if obj is None:
            start = line.find("{")
            if start >= 0:
                try:
                    obj, _ = decoder.raw_decode(line[start:])
                except json.JSONDecodeError:
                    pass
        if not isinstance(obj, dict):
            if diagnostics is not None:
                diagnostics.append({"reason": "invalid_json_object", "line": line_number, "raw": line})
            continue

        idx = obj.get("i", obj.get("id", obj.get("idx")))
        if type(idx) is not int or not (0 <= idx < len(target_batch)):
            if diagnostics is not None:
                diagnostics.append({"reason": "invalid_index", "line": line_number, "value": idx, "raw": line})
            continue

        milestone_issues = []
        milestones = normalize_milestones(obj.get("milestones"), target_batch[idx]["text"], event_ids, milestone_issues)
        if diagnostics is not None:
            diagnostics.extend({**issue, "line": line_number, "index": idx} for issue in milestone_issues)
        if milestones is None:
            print(f"  Invalid milestone data for [{idx}]; leaving utterance pending.")
            continue

        imp = obj.get("imp", obj.get("importance", obj.get("score", 3)))
        try:
            imp = max(1, min(5, int(imp)))
        except (TypeError, ValueError):
            imp = 3

        cat = str(obj.get("cat", obj.get("category", "pao_commentary"))).strip().lower()
        if cat not in CATEGORIES:
            if diagnostics is not None:
                diagnostics.append({"reason": "defaulted_category", "line": line_number, "index": idx, "value": cat})
            cat = "pao_commentary"

        raw_tags = obj.get("tags", [])
        if isinstance(raw_tags, list):
            tags = [str(t).strip().lower() for t in raw_tags if str(t).strip()][:5]
        else:
            tags = []

        topic = str(obj.get("topic", "")).strip()
        crew = normalize_crew_list(obj.get("crew")) or infer_crew_members(target_batch[idx]["text"])
        # Preserve the existing CSV column as the first optional catalog link.
        event_id = next((m["event_id"] for m in milestones if m.get("event_id")), "")
        if idx in results and diagnostics is not None:
            diagnostics.append({"reason": "duplicate_index", "line": line_number, "index": idx, "raw": line})
        results[idx] = {
            "importance": imp,
            "category": cat,
            "tags": tags,
            "topic": topic,
            "crew": crew,
            "event_id": event_id,
            "milestones": milestones,
        }
    return results


def parse_staged_assessments(
    content: str, target_batch: list[dict], event_ids: set[str], extraction: dict,
    diagnostics: list[dict] | None = None,
) -> dict[int, dict]:
    """Validate labels and join the extraction's crew and milestones in Python."""
    issues = diagnostics if diagnostics is not None else []
    milestones = anchored_milestones(extraction, len(target_batch))
    crew = involved_crew(extraction, len(target_batch))
    rows, seen, invalid = {}, set(), set()
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            issues.append({"reason": "invalid_label_json", "line": line_number, "raw": line})
            continue
        idx = row.get("i") if isinstance(row, dict) else None
        if type(idx) is not int or not 0 <= idx < len(target_batch):
            issues.append({"reason": "invalid_label_index", "line": line_number, "value": idx})
            continue
        if idx in seen:
            invalid.add(idx)
            rows.pop(idx, None)
            issues.append({"reason": "duplicate_label_index", "index": idx, "line": line_number})
            continue
        seen.add(idx)
        tags = row.get("tags")
        min_tags = 0 if row.get("cat") == "pao_filler" else 1
        if (type(row.get("imp")) is not int or not 1 <= row["imp"] <= 5
                or not isinstance(row.get("cat"), str) or row["cat"] not in CATEGORIES
                or not isinstance(tags, list) or not min_tags <= len(tags) <= 4
                or any(not isinstance(t, str) or not t.strip() for t in tags)
                or not isinstance(row.get("topic"), str) or not row["topic"].strip()):
            invalid.add(idx)
            issues.append({"reason": "invalid_label_fields", "index": idx, "line": line_number})
            continue
        if "crew" in row or "milestones" in row:
            issues.append({"reason": "ignored_label_attribution", "index": idx, "line": line_number})
        rows[idx] = {key: row[key] for key in ("i", "imp", "cat", "tags", "topic")}
        if milestones[idx]:
            # Labeling must not rewrite an extracted observation as a causal claim.
            rows[idx]["topic"] = milestones[idx][0]["title"]
        rows[idx].update(crew=crew[idx], milestones=milestones[idx])
    joined = "\n".join(json.dumps(rows[i], ensure_ascii=False) for i in sorted(rows) if i not in invalid)
    parsed = parse_jsonl_assessments(joined, target_batch, event_ids, issues)
    for idx, assessment in parsed.items():
        # The legacy parser's name inference must not expand validated attribution.
        assessment["crew"] = crew[idx]
    return parsed


def staged_llm_assessment(
    user_prompt: str, target_batch: list[dict], event_ids: set[str], *,
    backend: str = "hgx", model: str | None = None,
    extraction_reasoning_effort: str = "auto", reasoning_effort: str = "low",
    max_tokens: int = 65536, temperature: float = 0.2, quiet: bool = False,
    hide_reasoning: bool = False, diagnostics: dict | None = None,
    parse_issues: list[dict] | None = None,
) -> tuple[str, dict[int, dict], dict]:
    """Extract once, validate, then label without rediscovering developments."""
    diagnostics = diagnostics if diagnostics is not None else {}
    parse_issues = parse_issues if parse_issues is not None else []
    stages = diagnostics.setdefault("stages", {})
    extraction = None
    parsed = {}
    content = ""

    def request_stage(name, prompt, system_prompt, effort):
        stage = {
            "system_prompt": system_prompt, "user_prompt": prompt,
            "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "user_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "generation_settings": {"reasoning_effort": effort, "max_tokens": max_tokens,
                                    "temperature": temperature},
        }
        stages[name] = stage
        if not quiet:
            print(f"\n{C_BOLD}{name}: reasoning_effort={effort}{C_RESET}")
        _, content, stats = stream_llm_assessment(
            user_prompt=prompt, backend=backend, model=model, reasoning_effort=effort,
            max_tokens=max_tokens, temperature=temperature, quiet=quiet,
            hide_reasoning=hide_reasoning, diagnostics=stage,
            system_prompt=system_prompt, output_format="JSONL" if name == "labeling" else "JSON",
        )
        stage.update(raw_content=content, stats=stats)
        return stage, content, stats

    def validate_extraction(content, stage):
        stats = stage["stats"]
        if (stats.get("finish_reason") not in (None, "stop")
                or stats.get("done_received") is False or stage.get("stream_issues")):
            raise ValueError("extraction stream did not finish cleanly")
        return parse_extraction(content, target_batch, event_ids)

    for name, prompt, system_prompt, effort in (
        ("extraction", user_prompt, EXTRACTION_SYSTEM_PROMPT, extraction_reasoning_effort),
        ("labeling", None, SYSTEM_PROMPT, reasoning_effort),
    ):
        if name == "labeling":
            prompt = build_label_prompt(target_batch, extraction)
        stage, content, stats = request_stage(name, prompt, system_prompt, effort)
        if name == "extraction":
            try:
                extraction = validate_extraction(content, stage)
            except ValueError as exc:
                stage["validation_error"] = str(exc)
                if (stats.get("finish_reason") not in (None, "stop")
                        or stats.get("done_received") is False or stage.get("stream_issues")):
                    parse_issues.append({"reason": "invalid_extraction", "error": str(exc)})
                    raise
                repair_prompt = user_prompt + "\n\n" + json.dumps({
                    "task": "Correct the previous extraction using the original source above and the validation error. Return the complete corrected extraction JSON only; preserve supported facts and uncertainty, and remove unsupported developments.",
                    "validation_error": str(exc), "previous_extraction": content,
                }, ensure_ascii=False)
                stage, content, stats = request_stage(
                    "extraction_repair", repair_prompt, EXTRACTION_SYSTEM_PROMPT, "low")
                try:
                    extraction = validate_extraction(content, stage)
                except ValueError as repair_error:
                    stage["validation_error"] = str(repair_error)
                    parse_issues.append({"reason": "invalid_extraction", "error": str(repair_error)})
                    raise
                parse_issues.append({"reason": "repaired_extraction", "error": str(exc)})
            stage["extraction"] = extraction
        else:
            parsed = parse_staged_assessments(content, target_batch, event_ids, extraction, parse_issues)
    stage_stats = [stage["stats"] for stage in stages.values()]
    elapsed = sum(s["elapsed_s"] for s in stage_stats)
    completion = sum(s["completion_tokens"] for s in stage_stats)
    stats = {
        "elapsed_s": round(elapsed, 2), "ttft_s": stage_stats[0]["ttft_s"],
        "completion_tokens": completion,
        "prompt_tokens": (sum(s["prompt_tokens"] for s in stage_stats)
                          if all(s.get("prompt_tokens") is not None for s in stage_stats) else None),
        "tok_per_s": round(completion / max(0.001, elapsed), 1),
        "finish_reason": stage_stats[-1].get("finish_reason"),
        "done_received": stage_stats[-1].get("done_received"),
        "stages": {name: stage["stats"] for name, stage in stages.items()},
    }
    return content, parsed, stats


def write_batch_diagnostics(
    run_dir: Path, batch_number: int, target_batch: list[dict], user_prompt: str,
    content: str, stats: dict, parsed: dict[int, dict], issues: list[dict],
    stream_diagnostics: dict, error: dict | None = None,
) -> None:
    """Append batch health metrics; retain full inputs/responses for problem batches."""
    missing = [{"index": i, "uid": u["uid"], "t": u["t"], "channel": u["channel"]}
               for i, u in enumerate(target_batch) if i not in parsed]
    stages = stream_diagnostics.get("stages", {})
    stage_issues = [issue for stage in stages.values() for issue in stage.get("stream_issues", [])]
    stream_issues = stream_diagnostics.get("stream_issues", []) + stage_issues
    summary = {
        "logged_at": datetime.now(timezone.utc).isoformat(), "batch": batch_number,
        "first_utc": target_batch[0]["t"], "last_utc": target_batch[-1]["t"],
        "expected": len(target_batch), "parsed": len(parsed), "missing": missing,
        "stats": stats, "issue_counts": dict(Counter(
            issue["reason"] for issue in issues + stream_issues)),
        "error": error,
    }
    if stages:
        stage_path = run_dir / f"batch_{batch_number:05d}_stages.json"
        stage_path.write_text(json.dumps({
            "stages": {name: {key: value for key, value in stage.items() if key != "raw_stream"}
                       for name, stage in stages.items()},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["stages_file"] = stage_path.name
    problem = (missing or issues or stream_issues or error
               or stats.get("finish_reason") not in (None, "stop")
               or stats.get("done_received") is False)
    if problem:
        detail_path = run_dir / f"batch_{batch_number:05d}.json"
        detail_path.write_text(json.dumps({
            **summary, "system_prompt": (list(stages.values())[-1]["system_prompt"] if stages else SYSTEM_PROMPT),
            "user_prompt": (list(stages.values())[-1]["user_prompt"] if stages else user_prompt),
            "target_batch": target_batch, "raw_content": content,
            "parse_issues": issues, "stream": stream_diagnostics,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["detail_file"] = detail_path.name
        print(f"  WARNING: Batch {batch_number}: {len(missing)} unassessed; diagnostics: {detail_path}")
    with (run_dir / "batches.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")


def export_detected_milestones(
    path: Path, utterances: list[dict], ledger: dict[str, dict],
) -> int:
    """Rebuild the derived event stream from latest assessments, including on resume."""
    count = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for u in utterances:
            record = ledger.get(u["uid"], {})
            for index, milestone in enumerate(record.get("milestones", [])):
                digest = hashlib.sha1(f"{u['uid']}|{index}".encode("utf-8")).hexdigest()[:16]
                event = {
                    **milestone,
                    "id": f"milestone:{digest}",
                    "source_uid": u["uid"],
                    "channel": u["channel"],
                    "t": u["t"],
                    "time_basis": "report",
                    "text": u["text"],
                    "assessment": {key: record[key] for key in PROVENANCE_FIELDS if key in record},
                }
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                count += 1
    tmp.replace(path)
    return count


def export_enriched_csvs(mission: MissionConfig, utterances: list[dict], ledger: dict[str, dict]) -> None:
    """Write processing CSVs; publication into web/ is a separate operation."""
    comm_out = mission.transcript_enrichment / "comm_enriched.csv"
    pao_out = mission.transcript_enrichment / "PAO_enriched.csv"

    comm_count = 0
    pao_count = 0
    with (
        open(comm_out, "w", encoding="utf-8") as f_comm,
        open(pao_out, "w", encoding="utf-8") as f_pao,
    ):
        for u in utterances:
            rec = ledger.get(u["uid"])
            if not rec:
                continue
            imp = str(rec.get("importance", 3))
            cat = rec.get("category", "")
            tags_str = ",".join(rec.get("tags", []))
            topic = rec.get("topic", "").replace("|", " ")
            event_id = rec.get("event_id", "").replace("|", " ")
            if u["channel"] == "comm":
                # t|s|d|src|imp|cat|tags|topic|event_id|text
                f_comm.write(
                    f"{u['t']}|{u['s']}|{u['d']}|{u['src']}|{imp}|{cat}|{tags_str}|{topic}|{event_id}|{u['text']}\n"
                )
                comm_count += 1
            else:
                # t|imp|cat|tags|topic|event_id|text
                f_pao.write(f"{u['t']}|{imp}|{cat}|{tags_str}|{topic}|{event_id}|{u['text']}\n")
                pao_count += 1

    print(f"  Exported {comm_count} rows → {comm_out}")
    print(f"  Exported {pao_count} rows → {pao_out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich comm.csv and PAO.csv and discover granular mission milestones"
    )
    parser.add_argument("--mission", default="artemis-ii", choices=list(MISSIONS.keys()))
    parser.add_argument("--backend", choices=["hgx", "strata"], default="hgx",
                        help="LLM backend (default: hgx; strata uses STRATA_URL)")
    parser.add_argument("--model", default=None, help="Override the selected backend's default model")
    parser.add_argument("--batch-size", type=int, default=50, help="Utterances per LLM request (default: 50)")
    parser.add_argument("--context-lines", type=int, default=5, help="Context lines before and after each batch (default: 5)")
    parser.add_argument("--test-batches", type=int, default=None, help="Run only N batches for testing/benchmarking")
    parser.add_argument("--start-utc", type=str, default=None, help="Optional ISO UTC timestamp to start from (e.g. 2026-04-01T22:30:00Z)")
    parser.add_argument("--extraction-reasoning-effort", default="auto", choices=["auto", "low", "medium", "high"],
                        help="Reasoning for conversation extraction (default: auto)")
    parser.add_argument("--reasoning-effort", type=str, default="low", choices=["auto", "low", "medium", "high"],
                        help="Reasoning for utterance labeling (default: low)")
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--force", action="store_true", help="Re-assess utterances even if already in ledger")
    parser.add_argument("--reassess-model", nargs="+", default=[], metavar="MODEL",
                        help="Also reassess cached results from these API model IDs; unknown matches unlabeled records")
    parser.add_argument("--quiet", action="store_true", help="Disable live token-by-token streaming printout")
    parser.add_argument("--hide-reasoning", action="store_true",
                        help="Hide reasoning in the terminal while keeping live JSONL output and summaries")
    args = parser.parse_args()
    if args.batch_size < 1 or args.context_lines < 0 or (args.test_batches is not None and args.test_batches < 1):
        parser.error("batch-size and test-batches must be positive; context-lines must be nonnegative")
    endpoint, _api_key, model = llm_settings(args.backend, args.model)
    system_prompt_sha256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    generation_settings = {
        "reasoning_effort": args.reasoning_effort, "max_tokens": args.max_tokens,
        "temperature": 0.2,
    }
    extraction_generation_settings = {**generation_settings, "reasoning_effort": args.extraction_reasoning_effort}

    # Enable UTF-8 output and ANSI escape sequences on Windows terminal
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        os.system("")

    mission = MISSIONS[args.mission]
    utterances = load_all_utterances(mission)
    ctx_index = MissionContextIndex(mission)

    ledger_path = mission.transcript_enrichment / "enriched_utterances.jsonl"
    milestones_path = mission.transcript_enrichment / "detected_milestones.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing_ledger(ledger_path)
    # Recover the derived stream after a crash and remove superseded detections.
    export_detected_milestones(milestones_path, utterances, existing)

    print(f"\n{C_BOLD}=== Step 1d: Enrich Comm + PAO Transcripts — {mission.name} ==={C_RESET}")
    print(f"  Backend         : {args.backend}")
    print(f"  Endpoint        : {endpoint}")
    print(f"  Model           : {model}")
    print(f"  Stages          : extraction ({args.extraction_reasoning_effort}) -> labeling ({args.reasoning_effort})")
    print("  Carryover       : 6 recent threads + 20 captured developments")
    print(f"  Total utterances: {len(utterances):,} (comm + PAO merged chronologically)")
    print(f"  Reference index : {len(ctx_index.events):,} timeline/science/LTP/video events + {len(ctx_index.crew_photo_ts):,} crew photos")
    print(f"  Already in cache: {len(existing):,} ({ledger_path.name})")
    print(f"  Schema version  : {ASSESSMENT_VERSION} (older assessments will be revisited)")

    # Filter starting point if --start-utc is specified
    start_idx = 0
    if args.start_utc:
        target_ts = parse_utc_ts(args.start_utc)
        start_idx = len(utterances)
        for i, u in enumerate(utterances):
            if u["ts"] >= target_ts:
                start_idx = i
                break
        print(f"  Starting at UTC : {args.start_utc} (utterance index {start_idx})")

    # Build batches of unassessed utterances while preserving preceding context
    pending_indices = [
        i for i in range(start_idx, len(utterances))
        if (args.force
            or existing.get(utterances[i]["uid"], {}).get("assessment_version") != ASSESSMENT_VERSION
            or (existing.get(utterances[i]["uid"], {}).get("model") or "unknown") in args.reassess_model)
    ]

    if not pending_indices:
        print("  All utterances in range are already assessed!")
        export_enriched_csvs(mission, utterances, existing)
        return

    batches: list[list[int]] = []
    for source_index in pending_indices:
        if (not batches or len(batches[-1]) >= args.batch_size
                or source_index != batches[-1][-1] + 1):
            if args.test_batches and len(batches) >= args.test_batches:
                break
            batches.append([])
        batches[-1].append(source_index)

    print(f"  Batches to run  : {len(batches)} (batch_size={args.batch_size})\n")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    diagnostics_dir = mission.transcript_enrichment / "diagnostics" / run_id
    diagnostics_dir.mkdir(parents=True)
    (diagnostics_dir / "run.json").write_text(json.dumps({
        "backend": args.backend, "endpoint": endpoint, "model": model,
        "run_id": run_id, "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": system_prompt_sha256,
        "generation_settings": generation_settings,
        "extraction_generation_settings": extraction_generation_settings,
        "extraction_system_prompt": EXTRACTION_SYSTEM_PROMPT,
        "extraction_system_prompt_sha256": hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "assessment_version": ASSESSMENT_VERSION,
        "arguments": vars(args), "pending_at_start": len(pending_indices),
        "batches": len(batches), "system_prompt": SYSTEM_PROMPT,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  Diagnostics     : {diagnostics_dir}\n")
    batch_stats: list[dict] = []
    failed_batches = 0
    conversation_state: dict = {}
    previous_last_i: int | None = None

    with open(ledger_path, "a", encoding="utf-8") as ledger_fh:
        for b_num, idx_list in enumerate(batches, 1):
            first_i = idx_list[0]
            ctx_start = max(0, first_i - args.context_lines)
            context_before = utterances[ctx_start:first_i]
            target_batch = [utterances[i] for i in idx_list]
            context_after = utterances[idx_list[-1] + 1 : idx_list[-1] + 1 + args.context_lines]

            w_start_ts = target_batch[0]["ts"]
            w_end_ts = target_batch[-1]["ts"]
            nearby_events = ctx_index.query_window(w_start_ts, w_end_ts)

            if previous_last_i is None or first_i != previous_last_i + 1:
                # Resume with preceding saved milestones; never carry future cached rows.
                preceding = utterances[max(0, first_i - 300):first_i]
                conversation_state = update_conversation_state(
                    {}, {"threads": []}, preceding,
                    {i: existing[u["uid"]] for i, u in enumerate(preceding) if u["uid"] in existing},
                )
            user_prompt = build_user_prompt(
                target_batch, context_before, nearby_events, existing, context_after,
                conversation_state,
            )

            print(
                f"{C_YELLOW}{C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n"
                f"{C_YELLOW}{C_BOLD}Batch {b_num}/{len(batches)} | {target_batch[0]['t']} → {target_batch[-1]['t']} "
                f"({len(target_batch)} utterances: "
                f"{sum(1 for u in target_batch if u['channel']=='comm')} COMM, "
                f"{sum(1 for u in target_batch if u['channel']=='pao')} PAO){C_RESET}"
            )
            if nearby_events:
                print(f"{C_MAGENTA}Active Multi-Source Mission Context:{C_RESET}")
                for ev in nearby_events:
                    print(f"  {C_MAGENTA}{ev}{C_RESET}")

            print(f"\n{C_BOLD}Input Utterances:{C_RESET}")
            for local_i, u in enumerate(target_batch):
                badge = f"{C_CYAN}[COMM]{C_RESET}" if u["channel"] == "comm" else f"{C_YELLOW}[PAO ]{C_RESET}"
                print(f"  [{local_i:2d}] {u['t'][11:19]} {badge} {u['text']}")

            stream_diagnostics: dict = {}
            parse_issues: list[dict] = []
            event_ids = ctx_index.query_event_ids(w_start_ts, w_end_ts)
            request_started = time.monotonic()
            try:
                content, parsed, stats = staged_llm_assessment(
                    user_prompt=user_prompt,
                    target_batch=target_batch,
                    event_ids=event_ids,
                    backend=args.backend,
                    model=model,
                    extraction_reasoning_effort=args.extraction_reasoning_effort,
                    reasoning_effort=args.reasoning_effort,
                    max_tokens=args.max_tokens,
                    temperature=generation_settings["temperature"],
                    quiet=args.quiet,
                    hide_reasoning=args.hide_reasoning,
                    diagnostics=stream_diagnostics,
                    parse_issues=parse_issues,
                )
            except (Exception, KeyboardInterrupt) as exc:
                error = {"type": type(exc).__name__, "message": str(exc),
                         "traceback": traceback.format_exc()}
                if isinstance(exc, urllib.error.HTTPError):
                    error.update(http_status=exc.code, body=exc.read().decode("utf-8", errors="replace"))
                write_batch_diagnostics(
                    diagnostics_dir, b_num, target_batch, user_prompt, "",
                    {"elapsed_s": round(time.monotonic() - request_started, 2)}, {}, parse_issues,
                    stream_diagnostics, error,
                )
                if isinstance(exc, KeyboardInterrupt):
                    raise
                print(f"\n  ERROR assessing batch: {exc}; targets remain pending. Continuing with the next batch.")
                failed_batches += 1
                continue

            assessed_at = datetime.now(timezone.utc).isoformat()
            stage_provenance = {
                name: {key: stage[key] for key in (
                    "system_prompt_sha256", "user_prompt_sha256", "generation_settings")}
                for name, stage in stream_diagnostics["stages"].items()
            }
            label_prompt = stream_diagnostics["stages"]["labeling"]["user_prompt"]
            user_prompt_sha256 = stage_provenance["labeling"]["user_prompt_sha256"]
            saved_in_batch = 0
            milestones_in_batch = 0
            for local_i, u in enumerate(target_batch):
                assessment = parsed.get(local_i)
                if not assessment:
                    continue
                record = {
                    "uid": u["uid"],
                    "channel": u["channel"],
                    "t": u["t"],
                    "s": u["s"],
                    "d": u["d"],
                    "src": u["src"],
                    "importance": assessment["importance"],
                    "category": assessment["category"],
                    "tags": assessment["tags"],
                    "topic": assessment["topic"],
                    "crew": assessment.get("crew", []),
                    "event_id": assessment.get("event_id", ""),
                    "milestones": assessment["milestones"],
                    "assessment_version": ASSESSMENT_VERSION,
                    "backend": args.backend,
                    "model": model,
                    "model_provenance": "request",
                    "run_id": run_id,
                    "batch": b_num,
                    "assessed_at": assessed_at,
                    "prompt_version": PROMPT_VERSION,
                    "system_prompt_sha256": system_prompt_sha256,
                    "user_prompt_sha256": user_prompt_sha256,
                    "generation_settings": generation_settings,
                    "stage_provenance": stage_provenance,
                    "text": u["text"],
                }
                ledger_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                milestones_in_batch += len(record["milestones"])
                existing[u["uid"]] = record
                saved_in_batch += 1
            ledger_fh.flush()
            extraction_stage = stream_diagnostics["stages"].get(
                "extraction_repair", stream_diagnostics["stages"]["extraction"])
            conversation_state = update_conversation_state(
                conversation_state, extraction_stage["extraction"], target_batch, parsed)
            previous_last_i = idx_list[-1]
            export_detected_milestones(milestones_path, utterances, existing)

            stats["parsed"] = saved_in_batch
            stats["expected"] = len(target_batch)
            stats["milestones"] = milestones_in_batch
            batch_stats.append(stats)
            write_batch_diagnostics(
                diagnostics_dir, b_num, target_batch, label_prompt, content, stats,
                parsed, parse_issues, stream_diagnostics,
            )
            stream_problem = any(
                stage.get("stream_issues")
                or stage["stats"].get("finish_reason") not in (None, "stop")
                or stage["stats"].get("done_received") is False
                for stage in stream_diagnostics["stages"].values()
            )
            invalid_labels = any(issue["reason"] not in {"ignored_label_attribution", "repaired_extraction"}
                                 for issue in parse_issues)
            if saved_in_batch != len(target_batch) or stream_problem or invalid_labels:
                print("  Batch validation failed. Valid rows saved; missing targets remain pending. Continuing with the next batch.")
                failed_batches += 1

            print(
                f"\n{C_BOLD}Batch {b_num} Summary:{C_RESET} "
                f"Parsed {saved_in_batch}/{len(target_batch)} utterances ({milestones_in_batch} milestones discovered) | "
                f"Elapsed: {stats['elapsed_s']}s (TTFT: {stats['ttft_s']}s) | "
                f"Tokens: {stats.get('prompt_tokens') or '?'} prompt + {stats['completion_tokens']} completion "
                f"({stats['tok_per_s']} tok/s)\n"
            )

    if batch_stats:
        total_time = sum(s["elapsed_s"] for s in batch_stats)
        total_parsed = sum(s["parsed"] for s in batch_stats)
        avg_tps = sum(s["tok_per_s"] for s in batch_stats) / len(batch_stats)
        utt_per_sec = total_parsed / max(0.001, total_time)
        est_full_hours = (len(utterances) / max(0.01, utt_per_sec)) / 3600
        print(f"{C_BOLD}=== Performance Summary ==={C_RESET}")
        print(f"  Assessed       : {total_parsed} utterances in {total_time:.1f}s ({utt_per_sec:.2f} utterances/s)")
        print(f"  Avg Gen Speed  : {avg_tps:.1f} tokens/s")
        print(f"  Est. Full Run  : {est_full_hours:.2f} hours (single stream) for all {len(utterances):,} utterances")

    if not args.test_batches:
        export_enriched_csvs(mission, utterances, existing)
    if failed_batches:
        print(f"  {failed_batches} batch(es) had errors. Rerun to assess pending targets; downstream steps will wait.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
