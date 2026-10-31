"""Step 1d — Enrich transcripts and discover granular mission milestones.

Merges space-to-ground comm (web/comm.csv) and public affairs commentary (web/PAO.csv)
chronologically, groups them into context windows alongside mission itinerary events
(web/itinerary.json), and streams assessments from LLM-Alpha-512K (GLM-5.3-Flash) via
HGX_URL.

Live-streams both the model's reasoning tokens and structured JSONL output to the
terminal as it runs, and persists every assessed utterance immediately to an
append-only, crash-resumable JSONL ledger:
  {data_dir}/{mission}/processed/transcripts/enriched_utterances.jsonl

Derived milestone stream (one record per development, with source evidence):
  {data_dir}/{mission}/processed/transcripts/detected_milestones.jsonl

Also writes enriched web outputs:
  {data_dir}/{mission}/web/comm_enriched.csv
  {data_dir}/{mission}/web/PAO_enriched.csv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import HGX_API_KEY, HGX_URL, MISSIONS, MissionConfig  # noqa: E402
from mission_context import (  # noqa: E402
    MissionContextIndex,
    infer_crew_members,
    normalize_crew_list,
)

DEFAULT_MODEL = "LLM-Alpha-512K"
ASSESSMENT_VERSION = 2
MILESTONE_STATUSES = {
    "started", "in_progress", "completed", "confirmed", "observed",
    "anomaly", "resolved", "cancelled",
}

CATEGORIES = {
    "maneuver",
    "systems",
    "photography",
    "science",
    "crew_life",
    "milestone",
    "media_event",
    "pao_commentary",
    "pao_filler",
}

SYSTEM_PROMPT = """You are an expert NASA mission flight controller and archivist analyzing transcripts from the crewed Artemis II lunar flyby mission.

You will receive a chronological window of utterances from two channels:
- [COMM]: Authoritative operational audio, including space-to-ground communications between Orion crew and Mission Control Houston (OE-1/OE-2 voice loops) as well as onboard crew Personal Communication Device (PCD) voice recordings made inside the cabin (e.g., lunar flyby observations during farside Loss of Signal and in-cabin test notes, often prefixed with `[Speaker]`).
- [PAO]: NASA Public Affairs Office broadcast commentary.

Your goal is to assess EVERY target utterance in the window:
1. Determine whether PAO commentary is actually helpful mission guidance (explaining live maneuvers, callouts, spacecraft systems, or crew activity) versus filler (studio interviews, PR fluff, repetitive recaps, banter).
2. Categorize and tag both COMM and PAO utterances so users can filter conversations by high-level category or search by specific technical/mission topics.
3. Discover granular mission milestones directly from the transcripts, whether or not they appear in any reference list. Milestone discovery is independent of category and importance; a routine operational step can still be a useful timeline entry.

MILESTONE EXTRACTION RULES:
- Capture concrete mission developments: crew walkout, departure for the pad, pad arrival, boarding, hatch closure, readiness approvals, countdown holds/resumes, maneuver start/cutoff/results, separation, deployments, configuration changes, checkouts, science/imaging session starts and completions, specific observations, communications loss/restoration, anomalies/workarounds/resolution, entry and recovery steps.
- Preserve distinct stages rather than collapsing them into one broad event. One utterance may confirm multiple developments; return each separately.
- Use COMM and live PAO reports as evidence. Capture an event even when only PAO reports it. A report of a completed event is valid; a future plan, prediction, generic explanation, historical anecdote, or recap that merely repeats an already captured development is not a new milestone. Do not turn countdown numbers, routine acknowledgments, or every checklist readback into events.
- Extract only developments actually supported by the target utterance, using neighboring utterances to resolve context. Copy a short exact supporting quote into evidence. Do not infer execution from a schedule, photo caption, video title, or a matching reference time.
- The references are supplementary context, NOT a whitelist. Missing references or missing IDs must NEVER suppress a transcript-supported milestone. Photo filenames and video IDs identify media, not mission events.
- Use a catalog event_id only when the milestone directly matches a reference explicitly labeled event_id. Otherwise omit event_id; never invent an ID or use new:<slug>. Discovery does not depend on ID assignment.
- Prefer the clearest execution/confirmation over repetitive commentary about the same development. Preceding context may show milestones already captured; do not repeat those, but capture new stages, results, or changed conditions. Do not skip a target development solely because a preceding utterance mentions it without a captured milestone.
- Within a batch, put each development on ONE best-supported utterance. For example, "Here they are" followed by "The crew is taking their first steps outside" is one walkout milestone on the second utterance, not two starts. Repeated observations of an ongoing activity are not separate starts.
- Titles must not add an action, destination, hardware, or outcome that the utterance and context do not establish. Do not reinterpret ambiguous words as a specific operational step. Following context can clarify what the crew is doing, but never extract a milestone solely from a context-only utterance.
- The utterance timestamp locates the REPORT, not necessarily the event itself. Do not invent a precise event time from an approximate or retrospective report.

For EACH target utterance `[idx]`, output a single JSON object on its own line (JSONL format, no markdown code fences) with these keys:
  "i": integer index matching `[idx]`
  "imp": integer 1 to 5 importance/usefulness score:
     - For PAO:
       5 = Primary/Critical: Live callouts of burns, milestones, or direct explanation of active crew/ground comm happening right now.
       4 = Helpful Guidance: Useful mission status update, explaining current/upcoming timeline ops or hardware in context.
       3 = Contextual: Relevant mission background during quiet comm periods.
       2 = Repetitive/Low-value: Generic explanations repeated many times.
       1 = Filler: Studio interviews, guest chatter, PR fluff, talking over crew comm without adding operational value.
     - For COMM:
       5 = Highlight: Major burn/milestone callout, vivid lunar/Earth observation, anomaly, or standout crew moment.
       4 = Substantive: Technical systems, photography setup, science observation, or detailed crew-life discussion.
       3 = Routine Ops: Standard procedure/checklist readbacks, status checks.
       2 = Housekeeping: Comm checks, handovers, brief acknowledgments ("Loud and clear").
       1 = Fragment/Noise: Single-word "Copy", mic clip, unintelligible fragment.
  "cat": one of ["maneuver", "systems", "photography", "science", "crew_life", "milestone", "media_event", "pao_commentary", "pao_filler"]
  "tags": array of 1 to 4 short lowercase topic strings (e.g. ["icps separation", "thrusters"], ["nikon camera", "400mm lens", "window view"], ["hygiene", "water dispenser"], ["studio interview"])
  "topic": concise 4-10 word description of what is being discussed or explained
  "crew": OPTIONAL array of involved crewmember slugs from ["wiseman", "glover", "koch", "hansen", "all"] when identifiable from the utterance or context
  "milestones": array (empty when none) of objects with:
    "title": concise, specific name for this development (e.g. "Crew departs for launch pad")
    "status": one of ["started", "in_progress", "completed", "confirmed", "observed", "anomaly", "resolved", "cancelled"]
    "evidence": short exact quote from THIS target utterance supporting the development
    "event_id": OPTIONAL exact catalog event_id from the references, only if directly matched
    "crew": OPTIONAL array of involved crewmember slugs from ["wiseman", "glover", "koch", "hansen", "all"]

Examples of milestones (not an exhaustive list):
- "The crew is walking out now and will soon depart for the pad." -> [{"title":"Crew walkout","status":"started","evidence":"The crew is walking out now"}] (departure is still future).
- "The crew has arrived at the pad and hatch closure is complete." -> two completed milestones, "Crew arrival at launch pad" and "Hatch closure", with separate supporting quotes, even without catalog IDs.
- "Tomorrow the crew will perform a cabin depress test." -> [].
- "We have ignition." -> a started milestone even if no maneuver appears in the references.

Output ONLY valid JSONL lines, one line per target `[idx]` in ascending order."""

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
    """Load, deduplicate, and merge web/comm.csv and web/PAO.csv sorted chronologically."""
    utterances: list[dict] = []
    seen_uids: set[str] = set()

    comm_path = mission.web_dir / "comm.csv"
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

    pao_path = mission.web_dir / "PAO.csv"
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
) -> str:
    lines: list[str] = []
    if events_context:
        lines.append("=== SUPPLEMENTARY MISSION REFERENCES (not an exhaustive event list) ===")
        lines.extend(events_context)
        lines.append("")

    if context_before:
        lines.append("=== PRECEDING CONTEXT (for interpretation only — do NOT output JSON for these) ===")
        for u in context_before:
            ch = u["channel"].upper()
            lines.append(f"[CTX] {u['t']} [{ch}] {u['text']}")
            for milestone in (ledger or {}).get(u["uid"], {}).get("milestones", []):
                lines.append(f"[ALREADY CAPTURED] {milestone['title']} ({milestone['status']})")
        lines.append("")

    lines.append(f"=== TARGET UTTERANCES TO ASSESS (output 1 JSONL line for each [0]..[{len(target_batch) - 1}]) ===")
    for idx, u in enumerate(target_batch):
        ch = u["channel"].upper()
        lines.append(f"[{idx}] {u['t']} [{ch}] {u['text']}")

    if context_after:
        lines.append("")
        lines.append("=== FOLLOWING CONTEXT (for interpretation only — do NOT output JSON for these) ===")
        for u in context_after:
            lines.append(f"[CTX] {u['t']} [{u['channel'].upper()}] {u['text']}")

    return "\n".join(lines)


def stream_llm_assessment(
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str | None = None,
    max_tokens: int = 65536,
    temperature: float = 0.2,
    quiet: bool = False,
) -> tuple[str, str, dict]:
    """Stream reasoning and content from HGX_URL and print live to terminal."""
    if not HGX_URL or not HGX_API_KEY:
        raise RuntimeError("HGX_URL and HGX_API_KEY must be set in .env")

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if reasoning_effort and reasoning_effort != "auto":
        payload["reasoning_effort"] = reasoning_effort

    req = urllib.request.Request(
        f"{HGX_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {HGX_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )

    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    phase = "init"
    t0 = time.monotonic()
    first_token_t: float | None = None
    reasoning_tokens_est = 0
    content_tokens_est = 0
    usage_info: dict = {}

    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if chunk.get("usage"):
                usage_info = chunk["usage"]

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}

            r_tok = delta.get("reasoning") or delta.get("reasoning_content") or ""
            c_tok = delta.get("content") or ""

            if r_tok:
                if first_token_t is None:
                    first_token_t = time.monotonic()
                if phase != "reasoning":
                    phase = "reasoning"
                    if not quiet:
                        sys.stdout.write(f"\n{C_CYAN}{C_BOLD}── [LLM REASONING] ──{C_RESET}\n{C_DIM}")
                reasoning_parts.append(r_tok)
                reasoning_tokens_est += 1
                if not quiet:
                    sys.stdout.write(r_tok)
                    sys.stdout.flush()

            if c_tok:
                if first_token_t is None:
                    first_token_t = time.monotonic()
                if phase != "content":
                    if phase == "reasoning" and not quiet:
                        sys.stdout.write(f"{C_RESET}\n")
                    phase = "content"
                    if not quiet:
                        sys.stdout.write(f"\n{C_GREEN}{C_BOLD}── [LLM OUTPUT (JSONL)] ──{C_RESET}\n")
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
    }
    return "".join(reasoning_parts), "".join(content_parts), stats


def normalize_milestones(raw: object, text: str, event_ids: set[str]) -> list[dict] | None:
    """Validate transcript evidence; discard invalid catalog links, not discoveries."""
    if not isinstance(raw, list):
        return None
    milestones = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
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
            return None
        milestone = {"title": title.strip(), "status": status, "evidence": evidence.strip()}
        event_id = item.get("event_id")
        if isinstance(event_id, str) and event_id in event_ids:
            milestone["event_id"] = event_id
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
) -> dict[int, dict]:
    """Parse nested JSONL and leave invalid assessments pending for a later run."""
    results: dict[int, dict] = {}
    decoder = json.JSONDecoder()
    for line in content.splitlines():
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
            continue

        idx = obj.get("i", obj.get("id", obj.get("idx")))
        if type(idx) is not int or not (0 <= idx < len(target_batch)):
            continue

        milestones = normalize_milestones(obj.get("milestones"), target_batch[idx]["text"], event_ids)
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
                }
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                count += 1
    tmp.replace(path)
    return count


def export_enriched_csvs(mission: MissionConfig, utterances: list[dict], ledger: dict[str, dict]) -> None:
    """Write web/comm_enriched.csv and web/PAO_enriched.csv with classification columns."""
    comm_out = mission.web_dir / "comm_enriched.csv"
    pao_out = mission.web_dir / "PAO_enriched.csv"

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
    parser.add_argument("--batch-size", type=int, default=30, help="Utterances per LLM request (default: 30)")
    parser.add_argument("--context-lines", type=int, default=5, help="Context lines before and after each batch (default: 5)")
    parser.add_argument("--test-batches", type=int, default=None, help="Run only N batches for testing/benchmarking")
    parser.add_argument("--start-utc", type=str, default=None, help="Optional ISO UTC timestamp to start from (e.g. 2026-04-01T22:30:00Z)")
    parser.add_argument("--reasoning-effort", type=str, default="auto", choices=["auto", "low", "medium", "high"])
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--force", action="store_true", help="Re-assess utterances even if already in ledger")
    parser.add_argument("--quiet", action="store_true", help="Disable live token-by-token streaming printout")
    args = parser.parse_args()

    # Enable UTF-8 output and ANSI escape sequences on Windows terminal
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        os.system("")

    mission = MISSIONS[args.mission]
    utterances = load_all_utterances(mission)
    ctx_index = MissionContextIndex(mission)

    ledger_path = mission.processed_transcripts / "enriched_utterances.jsonl"
    milestones_path = mission.processed_transcripts / "detected_milestones.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing_ledger(ledger_path)
    # Recover the derived stream after a crash and remove superseded detections.
    export_detected_milestones(milestones_path, utterances, existing)

    print(f"\n{C_BOLD}=== Step 1d: Enrich Comm + PAO Transcripts — {mission.name} ==={C_RESET}")
    print(f"  Endpoint        : {HGX_URL}")
    print(f"  Model           : {DEFAULT_MODEL} (reasoning_effort={args.reasoning_effort})")
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
        if args.force or existing.get(utterances[i]["uid"], {}).get("assessment_version") != ASSESSMENT_VERSION
    ]

    if not pending_indices:
        print("  All utterances in range are already assessed!")
        export_enriched_csvs(mission, utterances, existing)
        return

    batches: list[list[int]] = []
    for b_start in range(0, len(pending_indices), args.batch_size):
        batches.append(pending_indices[b_start : b_start + args.batch_size])
        if args.test_batches and len(batches) >= args.test_batches:
            break

    print(f"  Batches to run  : {len(batches)} (batch_size={args.batch_size})\n")

    batch_stats: list[dict] = []

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

            user_prompt = build_user_prompt(target_batch, context_before, nearby_events, existing, context_after)

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

            try:
                _reasoning, content, stats = stream_llm_assessment(
                    user_prompt=user_prompt,
                    reasoning_effort=args.reasoning_effort,
                    max_tokens=args.max_tokens,
                    quiet=args.quiet,
                )
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="replace")
                print(f"\n  ERROR HTTP {err.code}: {body}")
                break
            except Exception as exc:
                print(f"\n  ERROR calling LLM: {exc}")
                break

            event_ids = ctx_index.query_event_ids(w_start_ts, w_end_ts)
            parsed = parse_jsonl_assessments(content, target_batch, event_ids)
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
                    "text": u["text"],
                }
                ledger_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                milestones_in_batch += len(record["milestones"])
                existing[u["uid"]] = record
                saved_in_batch += 1
            ledger_fh.flush()
            export_detected_milestones(milestones_path, utterances, existing)

            stats["parsed"] = saved_in_batch
            stats["expected"] = len(target_batch)
            stats["milestones"] = milestones_in_batch
            batch_stats.append(stats)

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


if __name__ == "__main__":
    main()
