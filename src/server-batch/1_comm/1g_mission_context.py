"""Step 1g — Build hierarchical mission context from investigated history.

Consumes the completed outputs of Step 1f under:
  {data_dir}/{mission}/processed/transcripts/1f_investigation/

Along with MissionContextIndex (pre-mission Overview Timeline PDF schedule blocks &
DFTOs, Preliminary Lunar Science Report sessions, LTP targets, itinerary.json,
photos.json, videoYt.json, videoPDS.json) to:
  1. Arrange investigated milestones, preserving explicit duplicate resolution
     and distinct lifecycle stages from 1f.
  2. Link pre-mission as-planned schedule blocks to investigated execution
     evidence, retaining nearby conversation and photos as context.
  3. Build a multi-level hierarchical activity list:
       Flight Day (FD01..FD10)
         -> Activity Block (planned vs. as-executed window, status, delta)
           -> Sub-Blocks (fine-grained topic/LTP/Point-DFTO intervals)
           -> Per-Crewmember Activity Breakdown (wiseman, glover, koch, hansen)
           -> Deduplicated Milestones & Evidence Quotes

Outputs:
  - {data_dir}/{mission}/processed/transcripts/1g_hierarchy/mission_activity_tree.json
  - {data_dir}/{mission}/processed/transcripts/1g_hierarchy/mission_activities.json
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS, MissionConfig  # noqa: E402
from history_common import require_artifacts, atomic_json, stage_lock  # noqa: E402
from mission_context import (  # noqa: E402
    CREW_INFO,
    CREW_SLUGS,
    EVENT_REFERENCE_KINDS,
    LAUNCH_UTC,
    MissionContextIndex,
    _parse_ts,
    infer_crew_members,
    normalize_crew_list,
)

_STOPWORDS = {
    "a", "an", "and", "at", "by", "complete", "completed", "confirmed", "for",
    "from", "in", "into", "is", "of", "on", "or", "start", "started", "begins",
    "begin", "the", "to", "with", "now", "operation", "operations", "ops",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fmt_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _title_tokens(title: str) -> set[str]:
    tokens = {tok for tok in _TOKEN_RE.findall(title.lower()) if tok not in _STOPWORDS and len(tok) > 1}
    # Normalize common synonyms
    syn = {
        "astroband": "astrovan",
        "depart": "departure",
        "departs": "departure",
        "departing": "departure",
        "board": "boarding",
        "boards": "boarding",
        "walking": "walkout",
        "steps": "walkout",
        "arrive": "arrival",
        "arrives": "arrival",
    }
    return {syn.get(t, t) for t in tokens}


def _titles_match(t1: str, t2: str) -> bool:
    if t1.strip().casefold() == t2.strip().casefold():
        return True
    toks1 = _title_tokens(t1)
    toks2 = _title_tokens(t2)
    if not toks1 or not toks2:
        return False
    inter = len(toks1 & toks2)
    union = len(toks1 | toks2)
    smaller = min(len(toks1), len(toks2))
    return (inter / union >= 0.6) or (smaller >= 2 and inter == smaller)


def merge_milestone_reports(
    raw_milestones: list[dict],
    max_gap_seconds: float = 6 * 3600,
) -> list[dict]:
    """Arrange reports without merging distinct investigated events or stages."""
    sorted_ms = sorted(raw_milestones, key=lambda m: _parse_ts(m.get("event_time") or m["t"]))
    merged: list[dict] = []

    for item in sorted_ms:
        ts = _parse_ts(item.get("event_time") or item["t"])
        ev_id = item.get("event_id") or ""
        title = item.get("title", "").strip()
        status = item.get("status", "observed")
        crew = normalize_crew_list(item.get("crew"))
        if not item.get("verification") and not crew:
            crew = infer_crew_members(f"{title} {item.get('evidence', '')} {item.get('text', '')}")

        match_group = None
        for grp in reversed(merged):
            if ts - grp["last_ts"] > max_gap_seconds:
                continue
            if item.get("verification") and grp["id"] != item["id"]:
                # 1f already resolved duplicates. Never undo its stage/occurrence distinctions.
                continue
            if ev_id and grp.get("event_id") == ev_id:
                match_group = grp
                break
            if (not ev_id or not grp.get("event_id")) and _titles_match(title, grp["title"]):
                match_group = grp
                break

        report_entry = {
            "id": item.get("id", ""),
            "source_uid": item.get("source_uid", ""),
            "channel": item.get("channel", ""),
            "t": item["t"],
            "status": status,
            "title": title,
            "evidence": item.get("evidence", ""),
        }
        if item.get("assessment"):
            report_entry["assessment"] = item["assessment"]
        for key in ("claims", "investigation_id", "verification", "event_time", "time_basis"):
            if key in item:
                report_entry[key] = item[key]

        if match_group is not None:
            match_group["last_ts"] = max(match_group["last_ts"], ts)
            match_group["first_reported_t"] = min(match_group["first_reported_t"], item["t"], key=_parse_ts)
            match_group["last_reported_t"] = max(match_group["last_reported_t"], item["t"], key=_parse_ts)
            match_group["end_utc"] = _fmt_utc(match_group["last_ts"])
            if status not in match_group["status_progression"]:
                match_group["status_progression"].append(status)
            match_group["status"] = _resolve_lifecycle_status(match_group["status_progression"])
            if not match_group.get("event_id") and ev_id:
                match_group["event_id"] = ev_id
            for c in crew:
                if c not in match_group["crew"]:
                    match_group["crew"].append(c)
            if "all" in match_group["crew"] and len(match_group["crew"]) > 1:
                match_group["crew"] = [c for c in match_group["crew"] if c != "all"]
            match_group["reports"].append(report_entry)
            match_group["report_count"] = len(match_group["reports"])
        else:
            merged.append({
                "id": item.get("id") or f"ms_{len(merged) + 1:04d}",
                "title": title,
                "event_id": ev_id,
                "status": status,
                "status_progression": [status],
                "first_reported_t": item["t"],
                "last_reported_t": item["t"],
                "first_ts": ts,
                "last_ts": ts,
                "start_utc": _fmt_utc(ts),
                "end_utc": _fmt_utc(ts),
                "event_time": item.get("event_time"),
                "time_basis": "explicit_event_time" if item.get("event_time") else "report_time",
                "candidate_ids": item.get("candidate_ids", [item.get("candidate_id")]),
                "crew": crew,
                "primary_evidence": item.get("evidence", ""),
                "primary_channel": item.get("channel", ""),
                "report_count": 1,
                "reports": [report_entry],
            })

    return merged


def _resolve_lifecycle_status(progression: list[str]) -> str:
    priority = [
        "cancelled",
        "resolved",
        "anomaly",
        "completed",
        "confirmed",
        "in_progress",
        "started",
        "observed",
    ]
    prog_set = set(progression)
    for st in priority:
        if st in prog_set:
            return st
    return progression[-1] if progression else "observed"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _is_substantive(utterance: dict) -> bool:
    return utterance.get("importance", 3) >= 3 or bool(utterance.get("milestones"))


def _crew_slugs(crew) -> list[str]:
    normalized = normalize_crew_list(crew)
    return list(CREW_SLUGS) if "all" in normalized else normalized


def _report_uids(milestone: dict) -> list[str]:
    return list(dict.fromkeys(r["source_uid"] for r in milestone["reports"]
                             if r.get("channel") in {"comm", "pao"} and r.get("source_uid")))


def _milestone_view(milestone: dict) -> dict:
    return {"id": milestone["id"], "title": milestone["title"], "status": milestone["status"],
            "status_progression": milestone["status_progression"], "t": milestone["start_utc"],
            "reported_t": milestone["first_reported_t"], "last_reported_t": milestone["last_reported_t"],
            "event_time": milestone.get("event_time"), "time_basis": milestone["time_basis"],
            "crew": milestone["crew"], "evidence": milestone["primary_evidence"],
            "report_count": milestone["report_count"], "report_utterance_ids": _report_uids(milestone),
            "related_planned_block_ids": milestone.get("related_planned_block_ids", [])}


def _build_sub_blocks(
    utterances: list[dict], milestones: list[dict], point_events: list[dict],
    crew_photos: list[dict], max_gap_s: float = 1200.0,
) -> list[dict]:
    """Keep every utterance as evidence or context, and place events on their own time basis."""
    ordered = sorted(utterances, key=lambda u: u["ts"])
    clusters = []
    for u in ordered:
        if not _is_substantive(u):
            continue
        previous = clusters[-1] if clusters else None
        if (previous and u.get("category") == previous["seed"].get("category")
                and u["ts"] - previous["rows"][0]["ts"] <= max_gap_s):
            previous["rows"].append(u)
        else:
            clusters.append({"rows": [u], "seed": u, "milestones": []})
    seed_starts = [c["rows"][0]["ts"] for c in clusters]
    context_clusters = []
    for u in ordered:
        if _is_substantive(u):
            continue
        near = bisect_right(seed_starts, u["ts"]) - 1
        candidates = [i for i in (near, near + 1) if 0 <= i < len(clusters)
                      and max(clusters[i]["rows"][-1]["ts"], u["ts"])
                      - min(clusters[i]["rows"][0]["ts"], u["ts"]) <= max_gap_s]
        if candidates:
            chosen = min(candidates, key=lambda i: abs(clusters[i]["seed"]["ts"] - u["ts"]))
            clusters[chosen]["rows"].append(u)
            clusters[chosen]["rows"].sort(key=lambda r: r["ts"])
        elif context_clusters and u["ts"] - context_clusters[-1]["rows"][0]["ts"] <= max_gap_s:
            context_clusters[-1]["rows"].append(u)
        else:
            context_clusters.append({"rows": [u], "seed": None, "milestones": []})
    clusters.extend(context_clusters)
    clusters.sort(key=lambda c: c["rows"][0]["ts"])
    starts = [c["rows"][0]["ts"] for c in clusters]
    uid_clusters = {u["uid"]: i for i, c in enumerate(clusters) for u in c["rows"]}
    standalone = []
    for milestone in milestones:
        when = milestone["first_ts"]
        near = bisect_right(starts, when) - 1
        anchored = {uid_clusters[uid] for uid in _report_uids(milestone) if uid in uid_clusters}
        candidates = anchored | {i for i in (near, near + 1) if 0 <= i < len(clusters)}
        def distance(i):
            rows = clusters[i]["rows"]
            return max(rows[0]["ts"] - when, when - rows[-1]["ts"], 0)
        candidates = [i for i in candidates if distance(i) <= 300]
        if candidates:
            chosen = min(candidates, key=lambda i: (i not in anchored, distance(i), i))
            clusters[chosen]["milestones"].append(milestone)
        else:
            standalone.append(milestone)

    sub_blocks = []
    for i, cluster in enumerate(clusters, 1):
        rows, events = cluster["rows"], cluster["milestones"]
        best = max(rows, key=lambda u: (bool(_is_substantive(u)), u.get("importance", 3)))
        times = [u["ts"] for u in rows] + [m["first_ts"] for m in events] + [m["last_ts"] for m in events]
        st, et = min(times), max(times)
        participants = {c for u in rows for c in _crew_slugs(u.get("crew"))}
        participants.update(c for m in events for c in _crew_slugs(m.get("crew")))
        tags = list(dict.fromkeys(tag for u in rows for tag in u.get("tags", [])))
        sub_blocks.append({
            "id": f"sub_{i:04d}",
            "title": events[0]["title"] if len(events) == 1 else best.get("topic") or best.get("category", "Conversation"),
            "category": best.get("category", "systems"), "start_utc": _fmt_utc(st), "end_utc": _fmt_utc(et),
            "duration_s": round(et - st, 3), "crew": [c for c in CREW_SLUGS if c in participants], "tags": tags[:8],
            "utterance_count": len(rows), "utterance_ids": [u["uid"] for u in rows],
            "substantive_utterance_ids": [u["uid"] for u in rows if _is_substantive(u)],
            "context_utterance_ids": [u["uid"] for u in rows if not _is_substantive(u)],
            "context_only": not events and not any(_is_substantive(u) for u in rows),
            "photo_count": sum(st <= p["ts"] <= et for p in crew_photos),
            "milestones": [_milestone_view(m) for m in events],
        })
    for milestone in standalone:
        sub_blocks.append({
            "id": "event_" + milestone["id"], "title": milestone["title"], "category": "milestone",
            "start_utc": milestone["start_utc"], "end_utc": milestone["end_utc"],
            "duration_s": round(milestone["last_ts"] - milestone["first_ts"], 3), "crew": milestone["crew"],
            "tags": [], "utterance_count": 0, "utterance_ids": [], "substantive_utterance_ids": [],
            "context_utterance_ids": [], "context_only": False, "photo_count": 0,
            "milestones": [_milestone_view(milestone)],
        })
    for event in point_events:
        st, et = event["start_ts"], max(event["end_ts"], event["start_ts"])
        overlaps = any(_parse_ts(s["start_utc"]) - 300 <= st <= _parse_ts(s["end_utc"]) + 300 for s in sub_blocks)
        if overlaps and event.get("kind") == "FLIGHT_PLAN":
            continue
        sub_blocks.append({
            "id": "reference_" + event["id"], "title": event["label"],
            "category": "science" if event["kind"] in {"SCIENCE_REPORT", "LTP_TARGET"} else "milestone",
            "start_utc": _fmt_utc(st), "end_utc": _fmt_utc(et), "duration_s": round(et - st, 3),
            "crew": [], "tags": [event["kind"].lower()], "utterance_count": 0, "utterance_ids": [],
            "substantive_utterance_ids": [], "context_utterance_ids": [], "context_only": False,
            "photo_count": sum(st <= p["ts"] <= et for p in crew_photos), "milestones": [],
            "reference_only": True,
            "status": "planned_unconfirmed" if event.get("planned_only") else "reference_unverified",
            **({"planned_start_utc": event["planned_utc"]} if event.get("planned_utc") else {}),
        })
    return sorted(sub_blocks, key=lambda s: (s["start_utc"], s["id"]))


def _build_crew_breakdown(utterances, milestones, sub_blocks=None, default_crew=None):
    """Use each source activity's attribution; never copy a cluster title to its participant union."""
    breakdown = {slug: [] for slug in CREW_SLUGS}
    represented = set()
    for milestone in milestones:
        uids = _report_uids(milestone)
        for slug in _crew_slugs(milestone["crew"]):
            breakdown[slug].append({
                "start_utc": milestone["start_utc"], "end_utc": milestone["end_utc"],
                "activity": milestone["title"], "category": "milestone", "status": milestone["status"],
                "source_type": "investigated_milestone", "milestone_ids": [milestone["id"]],
                "milestones": [milestone["title"]], "utterance_ids": uids,
                "reported_start_utc": milestone["first_reported_t"], "reported_end_utc": milestone["last_reported_t"],
                "event_time": milestone.get("event_time"), "time_basis": milestone["time_basis"],
                "shared_with_all": "all" in milestone["crew"],
            })
            represented.update((uid, slug) for uid in uids)
    row_entries = {slug: [] for slug in CREW_SLUGS}
    for u in sorted(utterances, key=lambda u: u["ts"]):
        if not _is_substantive(u):
            continue
        for slug in _crew_slugs(u.get("crew")):
            if (u["uid"], slug) in represented:
                continue
            entries = row_entries[slug]
            title = u.get("topic") or u.get("category", "Conversation")
            tags = u.get("tags", [])
            shared_tags = [tag for tag in entries[-1]["tags"] if tag in tags] if entries else []
            same_topic = bool(entries and entries[-1]["topics"] == [title])
            if (entries and entries[-1]["category"] == u.get("category") and (same_topic or shared_tags)
                    and u["ts"] - _parse_ts(entries[-1]["end_utc"]) <= 300
                    and u["ts"] - _parse_ts(entries[-1]["start_utc"]) <= 1200):
                entry = entries[-1]
                entry["end_utc"] = entry["reported_end_utc"] = u["t"]
                entry["utterance_ids"].append(u["uid"])
                if title not in entry["topics"]:
                    entry["topics"].append(title)
                entry["tags"] = shared_tags
                entry["shared_with_all"] &= "all" in u.get("crew", [])
                if len(entry["topics"]) > 1:
                    subject = ", ".join(shared_tags[:2])
                    entry["activity"] = u.get("category", "mission").replace("_", " ").capitalize() + " discussion"
                    if subject:
                        entry["activity"] += ": " + subject
            else:
                entries.append({
                    "start_utc": u["t"], "end_utc": u["t"], "activity": title, "category": u.get("category"),
                    "topics": [title], "tags": list(tags),
                    "status": "reported_discussion", "source_type": "reviewed_utterance", "milestone_ids": [],
                    "milestones": [], "utterance_ids": [u["uid"]], "reported_start_utc": u["t"],
                    "reported_end_utc": u["t"], "event_time": None, "time_basis": "report_time",
                    "shared_with_all": "all" in u.get("crew", []),
                })
    for slug in CREW_SLUGS:
        breakdown[slug].extend(row_entries[slug])
        breakdown[slug].sort(key=lambda e: (_parse_ts(e["start_utc"]), e["activity"]))
    return breakdown


def _day_windows(mission, planned_blocks, timestamps):
    """Prelaunch uses UTC calendar dates; mission days follow the planned crew-day boundaries."""
    launch = _parse_ts(mission.launch_utc or LAUNCH_UTC)
    starts = {"FD01": launch}
    for block in planned_blocks:
        day = block["flight_day"]
        if day != "FD01" and block["start_ts"] > launch:
            starts[day] = min(starts.get(day, block["start_ts"]), block["start_ts"])
    ordered = sorted(starts.items(), key=lambda v: v[1])
    latest = max([launch, *timestamps])
    while ordered[-1][1] + 86400 <= latest:
        number = int(ordered[-1][0][2:]) + 1
        ordered.append((f"FD{number:02d}", ordered[-1][1] + 86400))
    windows = []
    earliest = min([launch, *timestamps])
    if earliest < launch:
        date = datetime.fromtimestamp(earliest, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        st = date.timestamp()
        while st < launch:
            windows.append({"id": "PRELAUNCH-" + _fmt_utc(st)[:10], "kind": "prelaunch",
                            "start_ts": st, "end_ts": min(st + 86400, launch)})
            st += 86400
    for i, (day, st) in enumerate(ordered):
        windows.append({"id": day, "kind": "flight_day", "start_ts": st,
                        "end_ts": ordered[i + 1][1] if i + 1 < len(ordered) else st + 86400})
    return windows


def _group_status(confirmations):
    states = {m["status"] for m in confirmations}
    executed = bool(states & {"started", "in_progress", "completed"})
    if executed and "cancelled" in states:
        return "partially_executed"
    if executed:
        return "executed"
    return "cancelled" if "cancelled" in states else "planned_unconfirmed"


def _render_block(prototype, day, rows, milestones, context, confirmations=(), planned_window=None, block_id=None):
    times = [u["ts"] for u in rows] + [m["first_ts"] for m in milestones] + [m["last_ts"] for m in milestones]
    if planned_window:
        times.extend(planned_window)
    st, et = min(times), max(times)
    photos = [p for p in context.crew_photos if st <= p["ts"] <= et]
    references = []
    for event in context.events:
        if (planned_window and event.get("kind") in EVENT_REFERENCE_KINDS
                and event["start_ts"] < planned_window[1]
                and max(event["end_ts"], event["start_ts"] + 0.001) > planned_window[0]):
            references.append({**event, "start_ts": max(event["start_ts"], planned_window[0]),
                               "end_ts": min(max(event["end_ts"], event["start_ts"]), planned_window[1])})
    subs = _build_sub_blocks(rows, milestones, references, photos)
    participants = {c for u in rows for c in _crew_slugs(u.get("crew"))}
    participants.update(c for m in milestones for c in _crew_slugs(m["crew"]))
    planned = prototype is not None
    starts = [m["first_ts"] for m in confirmations if m.get("event_time") and m["status"] == "started"]
    ends = [m["last_ts"] for m in confirmations if m.get("event_time") and m["status"] == "completed"]
    confirmed_start, confirmed_end = min(starts) if starts else None, max(ends) if ends else None
    return {
        "id": block_id, "flight_day": day["id"],
        "planned_block_id": prototype["id"] if planned else None,
        "planned_flight_day": prototype["flight_day"] if planned else None,
        "phase": prototype["phase"] if planned else ("Prelaunch" if day["kind"] == "prelaunch" else "Unscheduled operations"),
        "title": prototype["title"] if planned else ("Prelaunch activity" if day["kind"] == "prelaunch" else "Unscheduled mission activity"),
        "description": prototype["description"] if planned else "Investigated developments and conversation outside planned activity windows.",
        "category": prototype["category"] if planned else "milestone" if milestones else "conversation",
        "status": _group_status(confirmations) if planned else "observed" if milestones else "reported_discussion",
        "status_scope": "planned_activity_group" if planned else "observed_collection",
        "time_source": "planned_and_evidence_window" if planned else "event_and_report_times",
        "start_utc": _fmt_utc(st), "end_utc": _fmt_utc(et),
        "planned_start_utc": prototype["start_utc"] if planned else None,
        "planned_end_utc": prototype["end_utc"] if planned else None,
        "confirmed_start_utc": _fmt_utc(confirmed_start) if confirmed_start is not None else None,
        "confirmed_end_utc": _fmt_utc(confirmed_end) if confirmed_end is not None else None,
        "confirmed_time_scope": "constituent_activity_evidence",
        "schedule_delta_min": round((confirmed_start - prototype["start_ts"]) / 60, 1)
                              if planned and confirmed_start is not None else None,
        "constituent_activities": [_milestone_view(m) for m in confirmations],
        "crew": [c for c in CREW_SLUGS if c in participants],
        "utterance_count": len(rows), "utterance_ids": [u["uid"] for u in rows],
        "milestone_count": len(milestones), "photo_count": len(photos),
        "reference_events": [{"id": e["id"], "kind": e["kind"], "t": e["t"], "label": e["label"]} for e in references],
        "sub_blocks": subs, "crew_activities": _build_crew_breakdown(rows, milestones),
    }


def _validate_hierarchy(blocks, days, utterances, milestones):
    """Fail before writing if assembly loses evidence, duplicates context, or misattributes crew."""
    expected_uids = {u["uid"] for u in utterances}
    expected_ids = {m["id"] for m in milestones}
    for name, values, expected in (
        ("block utterances", [uid for b in blocks for uid in b["utterance_ids"]], expected_uids),
        ("sub-block utterances", [uid for b in blocks for s in b["sub_blocks"] for uid in s["utterance_ids"]], expected_uids),
        ("milestones", [m["id"] for b in blocks for s in b["sub_blocks"] for m in s["milestones"]], expected_ids),
    ):
        counts = Counter(values)
        if counts.keys() != expected or any(n != 1 for n in counts.values()):
            raise RuntimeError(f"Hierarchy lost or duplicated {name}")
    by_day = {d["id"]: d for d in days}
    events = {m["id"]: m for m in milestones}
    rows = {u["uid"]: u for u in utterances}
    for b in blocks:
        day = by_day[b["flight_day"]]
        if not day["start_ts"] - 0.001 <= _parse_ts(b["start_utc"]) <= _parse_ts(b["end_utc"]) <= day["end_ts"] + 0.001:
            raise RuntimeError(f"Block crosses its day boundary: {b['id']}")
        for slug, entries in b["crew_activities"].items():
            for entry in entries:
                if entry["source_type"] == "investigated_milestone":
                    if any(slug not in _crew_slugs(events[key]["crew"]) for key in entry["milestone_ids"]):
                        raise RuntimeError(f"Crew activity lacks its own attribution: {entry['activity']}")
                elif any(slug not in _crew_slugs(rows[uid].get("crew")) for uid in entry["utterance_ids"]):
                    raise RuntimeError(f"Crew discussion lacks its own attribution: {entry['activity']}")


def build_mission_activity_hierarchy(mission: MissionConfig) -> dict:
    """Assemble evidence chronology and linked report context without re-investigating 1f."""
    ctx = MissionContextIndex(mission)
    history_root = mission.transcript_investigation
    investigation_report = require_artifacts(history_root)
    recovery_report = require_artifacts(mission.transcript_recovery)
    if (investigation_report["recovery_snapshot"] != recovery_report["snapshot"]
            or investigation_report["recovery_artifacts"] != recovery_report["artifacts"]):
        raise RuntimeError("Recovery changed since investigation; rerun 1f before 1g")
    by_uid = {r["uid"]: {**r, "ts": _parse_ts(r["t"])} for r in _load_jsonl(history_root / "utterances.jsonl") if r.get("uid")}
    utterances = sorted((r for r in by_uid.values() if r.get("label_review", {}).get("status") == "reviewed"), key=lambda r: r["ts"])
    raw_milestones = _load_jsonl(history_root / "milestones.jsonl")
    if any(m.get("verification") not in {"supported", "corrected"} for m in raw_milestones):
        raise RuntimeError("1g accepts only source-investigated milestones")
    milestones = merge_milestone_reports(raw_milestones)
    planned = sorted(ctx.planned_blocks, key=lambda b: (b["start_ts"], b["id"]))
    plans = {b["id"]: b for b in planned}
    timestamps = [u["ts"] for u in utterances] + [m["first_ts"] for m in milestones]
    timestamps += [t for b in planned for t in (b["start_ts"], b["end_ts"])]
    days = _day_windows(mission, planned, timestamps)
    day_starts = [d["start_ts"] for d in days]
    def day_for(when):
        return days[bisect_right(day_starts, when) - 1]
    def plan_for(when):
        return next((b["id"] for b in planned if b["start_ts"] <= when < b["end_ts"]), None)
    grouped = {}
    def group(plan_id, day):
        return grouped.setdefault((plan_id, day["id"]), {"rows": [], "milestones": [], "day": day, "window": None})
    for block in planned:
        for day in days:
            st, et = max(block["start_ts"], day["start_ts"]), min(block["end_ts"], day["end_ts"])
            if st < et:
                group(block["id"], day)["window"] = (st, et)
    unassigned = defaultdict(list)
    for u in utterances:
        owner, day = plan_for(u["ts"]), day_for(u["ts"])
        if owner is None:
            unassigned[day["id"]].append((u["ts"], "utterance", u["uid"], u))
        else:
            group(owner, day)["rows"].append(u)
    confirmations = defaultdict(list)
    for m in milestones:
        leads = [cid[len("lead:plan:"):] for cid in m.get("candidate_ids", [])
                 if isinstance(cid, str) and cid.startswith("lead:plan:") and cid[len("lead:plan:"):] in plans]
        for key in leads:
            confirmations[key].append(m)
        when, day = m["first_ts"], day_for(m["first_ts"])
        m["related_planned_block_ids"] = leads
        # An undated recap remains at report time; its old plan is a relationship,
        # not evidence that the planned activity occurred again on the report day.
        dated_leads = leads if m.get("event_time") else [k for k in leads if plans[k]["start_ts"] <= when < plans[k]["end_ts"]]
        owner = min(dated_leads, key=lambda k: max(plans[k]["start_ts"] - when, when - plans[k]["end_ts"], 0)) if dated_leads else plan_for(when)
        if owner is None:
            unassigned[day["id"]].append((when, "milestone", m["id"], m))
        else:
            group(owner, day)["milestones"].append(m)
    blocks = []
    for (plan_id, day_id), value in grouped.items():
        prototype = plans[plan_id]
        identifier = plan_id if day_id == prototype["flight_day"] else plan_id + "__" + day_id.lower()
        blocks.append(_render_block(prototype, value["day"], value["rows"], value["milestones"], ctx,
                                    confirmations[plan_id], value["window"], identifier))
    day_lookup = {d["id"]: d for d in days}
    for day_id, points in unassigned.items():
        chunks = []
        for point in sorted(points, key=lambda p: p[:3]):
            if not chunks or point[0] - chunks[-1][-1][0] > 1800 or point[0] - chunks[-1][0][0] > 7200:
                chunks.append([])
            chunks[-1].append(point)
        for i, chunk in enumerate(chunks, 1):
            rows = [p[3] for p in chunk if p[1] == "utterance"]
            events = [p[3] for p in chunk if p[1] == "milestone"]
            blocks.append(_render_block(None, day_lookup[day_id], rows, events, ctx,
                                        block_id=f"discovered_{day_id.lower()}_{i:03d}"))
    blocks.sort(key=lambda b: (_parse_ts(b["start_utc"]), b["id"]))
    _validate_hierarchy(blocks, days, utterances, milestones)
    day_blocks = defaultdict(list)
    for b in blocks:
        day_blocks[b["flight_day"]].append(b)
    flight_days = []
    for day in days:
        values = day_blocks[day["id"]]
        if not values:
            continue
        flight_days.append({
            "flight_day": day["id"], "kind": day["kind"], "boundary_start_utc": _fmt_utc(day["start_ts"]),
            "boundary_end_utc": _fmt_utc(day["end_ts"]),
            "start_utc": min((b["start_utc"] for b in values), key=_parse_ts),
            "end_utc": max((b["end_utc"] for b in values), key=_parse_ts),
            "phases": list(dict.fromkeys(b["phase"] for b in values)), "block_count": len(values),
            "executed_block_count": sum(b["status"] == "executed" for b in values),
            "partially_executed_block_count": sum(b["status"] == "partially_executed" for b in values),
            "milestone_count": sum(b["milestone_count"] for b in values),
            "utterance_count": sum(b["utterance_count"] for b in values), "blocks": values,
        })
    crew_timelines = {}
    for slug in CREW_SLUGS:
        entries = [{"flight_day": b["flight_day"], "block_id": b["id"], "block_title": b["title"], **entry}
                   for b in blocks for entry in b["crew_activities"][slug]]
        entries.sort(key=lambda e: (_parse_ts(e["start_utc"]), e["activity"]))
        crew_timelines[slug] = {**CREW_INFO[slug], "activity_count": len(entries), "timeline": entries}
    if require_artifacts(history_root) != investigation_report or require_artifacts(mission.transcript_recovery) != recovery_report:
        raise RuntimeError("History inputs changed while building hierarchy; retry after preceding stages finish")
    return {
        "schema_version": 2, "mission_id": mission.slug, "mission_name": mission.name,
        "launch_utc": mission.launch_utc or LAUNCH_UTC, "splashdown_utc": mission.splashdown_utc,
        "sources": {
            "preflight_planned_pdf": "pdfs/artemis-ii-overview-timeline-public-final.pdf (as-planned, not as-executed)",
            "science_report_pdf": "pdfs/Artemis II_Preliminary Science Report.pdf (retrospective evidence)",
            "enriched_transcripts": "1f_investigation/utterances.jsonl",
            "detected_milestones": "1f_investigation/milestones.jsonl",
            "investigation_snapshot": investigation_report["snapshot"],
            "investigation_artifacts": investigation_report["artifacts"],
        },
        "summary": {
            "total_assessed_utterances": len(utterances), "linked_utterances": len(utterances),
            "substantive_utterances": sum(_is_substantive(u) for u in utterances),
            "context_utterances": sum(not _is_substantive(u) for u in utterances),
            "raw_milestone_detections": len(raw_milestones), "merged_unique_milestones": len(milestones),
            "flight_days": sum(d["kind"] == "flight_day" for d in flight_days),
            "prelaunch_days": sum(d["kind"] == "prelaunch" for d in flight_days), "timeline_days": len(flight_days),
            "activity_blocks": len(blocks), "planned_activity_groups": len(planned),
            "executed_blocks": sum(b["status"] == "executed" for b in blocks),
            "partially_executed_blocks": sum(b["status"] == "partially_executed" for b in blocks),
            "unresolved_events": investigation_report["unresolved_events"], "unresolved_labels": investigation_report["unresolved_labels"],
        },
        "crew_timelines": crew_timelines,
        "merged_milestones": [{k: v for k, v in m.items() if k not in {"first_ts", "last_ts"}} for m in milestones],
        "flight_days": flight_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 1g — Build hierarchical mission activity tree from investigated history"
    )
    parser.add_argument("--mission", default="artemis-ii", choices=["artemis-ii"])
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    with stage_lock(mission.transcript_hierarchy):
        tree = build_mission_activity_hierarchy(mission)
        proc_out = mission.transcript_hierarchy / "mission_activity_tree.json"
        activities_out = mission.transcript_hierarchy / "mission_activities.json"
        atomic_json(proc_out, tree)
        atomic_json(activities_out, tree)

    s = tree["summary"]
    print(f"\n=== Step 1g: Mission Context & Hierarchical Activity Tree — {mission.name} ===")
    print(f"  Assessed utterances : {s['total_assessed_utterances']:,}")
    print(f"  Milestones (merged) : {s['merged_unique_milestones']:,} (from {s['raw_milestone_detections']:,} raw reports)")
    print(f"  Activity blocks     : {s['activity_blocks']} across {s['flight_days']} Flight Days "
          f"({s['executed_blocks']} executed, {s['partially_executed_blocks']} partially executed)")
    print(f"  Prelaunch dates     : {s['prelaunch_days']}")
    print(f"  Linked utterances   : {s['linked_utterances']:,} ({s['context_utterances']:,} context)")
    for slug, cinfo in tree["crew_timelines"].items():
        print(f"  Crew track [{slug:7s}] : {cinfo['activity_count']} timeline entries ({cinfo['name']})")
    print(f"  Saved processed tree: {proc_out}")
    print(f"  Saved activity JSON : {activities_out}")


if __name__ == "__main__":
    main()
