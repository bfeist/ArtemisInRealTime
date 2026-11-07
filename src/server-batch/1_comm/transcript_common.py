"""Source loading, discovery context and HGX transport for transcript stages."""
from __future__ import annotations
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime

from config import HGX_API_KEY, HGX_URL, MissionConfig
from mission_context import MissionContextIndex
from transcript_stages import source_context, transcript_source

DEFAULT_MODEL = "LLM-Alpha-512K"
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
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
    comm_sources: dict[str, str] = {}

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
                source = transcript_source({"channel": "comm", "src": src})
                # Keep established IDs while distinguishing identical turns on different loops.
                first_source = comm_sources.setdefault(uid, source)
                if source != first_source:
                    uid = make_uid("comm", t_str, f"{source}|{text}")
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


def build_user_prompt(
    target_batch: list[dict],
    context_before: list[dict],
    events_context: list[str],
    ledger: dict[str, dict] | None = None,
    context_after: list[dict] | None = None,
) -> str:
    lines: list[str] = []
    if events_context:
        lines.append("=== SUPPLEMENTARY MISSION REFERENCES (not an exhaustive event list) ===")
        lines.extend(events_context)
        lines.append("")

    if context_before:
        lines.append("=== PRECEDING CONTEXT (for interpretation only — do NOT output JSON for these) ===")
        for u in context_before:
            ch = f"{u['channel'].upper()} source={transcript_source(u)}"
            lines.append(f"[CTX] {u['t']} [{ch}] {u['text']}")
            for milestone in (ledger or {}).get(u["uid"], {}).get("milestones", []):
                lines.append(f"[ALREADY CAPTURED] {milestone['title']} ({milestone['status']})")
        lines.append("")

    lines.append(f"=== TARGET UTTERANCES TO EXTRACT (cover each [0]..[{len(target_batch) - 1}]) ===")
    for idx, u in enumerate(target_batch):
        ch = f"{u['channel'].upper()} source={transcript_source(u)}"
        lines.append(f"[{idx}] {u['t']} [{ch}] {u['text']}")

    if context_after:
        lines.append("")
        lines.append("=== FOLLOWING CONTEXT (for interpretation only — do NOT output JSON for these) ===")
        for u in context_after:
            lines.append(f"[CTX] {u['t']} [{u['channel'].upper()} source={transcript_source(u)}] {u['text']}")

    lines.append("")
    lines.append("Extract threads, statements and anchored developments for all targets. Return only the extraction JSON object.")
    return "\n".join(lines)


def llm_settings(backend: str = "hgx", model: str | None = None) -> tuple[str, str, str]:
    if backend != "hgx":
        raise ValueError("Transcript stages require the HGX provider")
    return HGX_URL, HGX_API_KEY, model or DEFAULT_MODEL


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
    response_format: dict | None = None,
) -> tuple[str, str, dict]:
    """Stream reasoning and content from the selected API and print live."""
    base_url, api_key, model = llm_settings(backend, model)
    if backend == "hgx" and (not base_url or not api_key):
        raise RuntimeError("HGX_URL and HGX_API_KEY must be set in .env")

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt if system_prompt is not None else "Return the requested JSON object."},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if reasoning_effort and reasoning_effort != "auto":
        payload["reasoning_effort"] = reasoning_effort
    if response_format is not None:
        payload["response_format"] = response_format

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
