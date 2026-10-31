"""Step 1e — Build hierarchical mission context and per-crewmember activity timeline.

Consumes the outputs of Step 1d:
  - {data_dir}/{mission}/processed/transcripts/enriched_utterances.jsonl
  - {data_dir}/{mission}/processed/transcripts/detected_milestones.jsonl

Along with MissionContextIndex (pre-mission Overview Timeline PDF schedule blocks &
DFTOs, Preliminary Lunar Science Report sessions, LTP targets, itinerary.json,
photos.json, videoYt.json, videoPDS.json) to:
  1. Merge widely separated repeated reports of the same milestone into unified
     lifecycle records (started -> in_progress -> completed/confirmed).
  2. Reconcile pre-mission as-planned schedule blocks against as-executed comm/PAO
     utterances, milestones, and crew camera bursts.
  3. Build a multi-level hierarchical activity list:
       Flight Day (FD01..FD10)
         -> Activity Block (planned vs. as-executed window, status, delta)
           -> Sub-Blocks (fine-grained topic/LTP/Point-DFTO intervals)
           -> Per-Crewmember Activity Breakdown (wiseman, glover, koch, hansen)
           -> Deduplicated Milestones & Evidence Quotes

Outputs:
  - {data_dir}/{mission}/processed/transcripts/mission_activity_tree.json
  - {data_dir}/{mission}/web/mission_activities.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS, MissionConfig  # noqa: E402
from mission_context import (  # noqa: E402
    CREW_INFO,
    CREW_SLUGS,
    EVENT_REFERENCE_KINDS,
    LAUNCH_UTC,
    LTP_TARGETS,
    OVERVIEW_TIMELINE_BLOCKS,
    OVERVIEW_TIMELINE_EVENTS,
    SCIENCE_SESSIONS,
    MissionContextIndex,
    _met_utc,
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
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """Merge repeated or staged reports of the same milestone across batches."""
    sorted_ms = sorted(raw_milestones, key=lambda m: _parse_ts(m["t"]))
    merged: list[dict] = []

    for item in sorted_ms:
        ts = _parse_ts(item["t"])
        ev_id = item.get("event_id") or ""
        title = item.get("title", "").strip()
        status = item.get("status", "observed")
        crew = normalize_crew_list(item.get("crew")) or infer_crew_members(
            f"{title} {item.get('evidence', '')} {item.get('text', '')}"
        )

        match_group = None
        for grp in reversed(merged):
            if ts - grp["last_ts"] > max_gap_seconds:
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

        if match_group is not None:
            match_group["last_ts"] = max(match_group["last_ts"], ts)
            match_group["last_reported_t"] = _fmt_utc(match_group["last_ts"])
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


def _build_sub_blocks(
    utterances: list[dict],
    milestones: list[dict],
    point_events: list[dict],
    crew_photos: list[dict],
    max_gap_s: float = 1200.0,
) -> list[dict]:
    """Segment a block's utterances, milestones, and reference point events into sub-blocks."""
    sub_blocks: list[dict] = []
    substantive = [u for u in utterances if u.get("importance", 3) >= 3 or u.get("milestones")]
    if not substantive and not milestones and not point_events:
        return sub_blocks

    # Group substantive utterances into contiguous sub-windows (gap <= 20 min and compatible category)
    clusters: list[list[dict]] = []
    for u in substantive:
        if (
            clusters
            and (u["ts"] - clusters[-1][-1]["ts"] <= max_gap_s)
            and (u.get("category") == clusters[-1][-1].get("category") or len(clusters[-1]) < 4)
        ):
            clusters[-1].append(u)
        else:
            clusters.append([u])

    used_ms_ids: set[str] = set()
    for idx, cl in enumerate(clusters, 1):
        st_ts = cl[0]["ts"]
        et_ts = cl[-1]["ts"]
        cl_ms = [
            m for m in milestones
            if m["id"] not in used_ms_ids and (st_ts - 300) <= m["first_ts"] <= (et_ts + 300)
        ]
        for m in cl_ms:
            used_ms_ids.add(m["id"])
            st_ts = min(st_ts, m["first_ts"])
            et_ts = max(et_ts, m["last_ts"])

        # Pick best descriptive title for sub-block
        best_u = max(cl, key=lambda x: (x.get("importance", 3), len(x.get("milestones", []))))
        title = (
            cl_ms[0]["title"]
            if cl_ms
            else (best_u.get("topic") or best_u.get("category", "Activity"))
        )
        crew_set: list[str] = []
        for u in cl:
            for c in u.get("crew") or infer_crew_members(f"{u.get('topic', '')} {u.get('text', '')}"):
                if c not in crew_set:
                    crew_set.append(c)
        for m in cl_ms:
            for c in m.get("crew", []):
                if c not in crew_set:
                    crew_set.append(c)
        if "all" in crew_set and len(crew_set) > 1:
            crew_set = [c for c in crew_set if c != "all"]

        photos_in_sub = [p for p in crew_photos if st_ts <= p["ts"] <= et_ts]
        tags_seen: list[str] = []
        for u in cl:
            for tg in u.get("tags", []):
                if tg not in tags_seen:
                    tags_seen.append(tg)

        sub_blocks.append({
            "id": f"sub_{idx:02d}",
            "title": title,
            "category": best_u.get("category", "systems"),
            "start_utc": _fmt_utc(st_ts),
            "end_utc": _fmt_utc(et_ts),
            "duration_s": round(max(0.0, et_ts - st_ts), 1),
            "crew": crew_set or ["all"],
            "tags": tags_seen[:8],
            "utterance_count": len(cl),
            "photo_count": len(photos_in_sub),
            "milestones": [
                {
                    "id": m["id"],
                    "title": m["title"],
                    "status": m["status"],
                    "status_progression": m["status_progression"],
                    "t": m["first_reported_t"],
                    "crew": m["crew"],
                    "evidence": m["primary_evidence"],
                    "report_count": m["report_count"],
                }
                for m in cl_ms
            ],
        })

    # Add any remaining milestones in this block that didn't fall inside an utterance cluster
    for m in milestones:
        if m["id"] in used_ms_ids:
            continue
        sub_blocks.append({
            "id": f"sub_{len(sub_blocks) + 1:02d}",
            "title": m["title"],
            "category": "milestone",
            "start_utc": m["first_reported_t"],
            "end_utc": m["last_reported_t"],
            "duration_s": round(max(0.0, m["last_ts"] - m["first_ts"]), 1),
            "crew": m["crew"] or ["all"],
            "tags": [],
            "utterance_count": m["report_count"],
            "photo_count": 0,
            "milestones": [
                {
                    "id": m["id"],
                    "title": m["title"],
                    "status": m["status"],
                    "status_progression": m["status_progression"],
                    "t": m["first_reported_t"],
                    "crew": m["crew"],
                    "evidence": m["primary_evidence"],
                    "report_count": m["report_count"],
                }
            ],
        })

    # Include reference science/LTP/itinerary/DFTO sub-events not already covered by a cluster
    for ev in point_events:
        ev_start = ev["start_ts"]
        ev_end = max(ev["end_ts"], ev_start)
        overlaps = any(
            _parse_ts(sb["start_utc"]) - 300 <= ev_start <= _parse_ts(sb["end_utc"]) + 300
            for sb in sub_blocks
        )
        if overlaps and ev.get("kind") == "FLIGHT_PLAN":
            continue
        ev_crew = ev.get("crew") or infer_crew_members(ev.get("label", "")) or ["all"]
        photos_in_ev = [p for p in crew_photos if ev_start <= p["ts"] <= ev_end]
        planned_utc = ev.get("planned_utc")
        delta_min = (
            round((ev_start - _parse_ts(planned_utc)) / 60.0, 1)
            if planned_utc and ev.get("as_executed")
            else None
        )
        sub_entry: dict = {
            "id": f"sub_{len(sub_blocks) + 1:02d}_{ev['id']}",
            "title": ev["label"],
            "category": "science" if ev["kind"] in ("SCIENCE_REPORT", "LTP_TARGET") else "milestone",
            "start_utc": _fmt_utc(ev_start),
            "end_utc": _fmt_utc(ev_end),
            "duration_s": round(max(0.0, ev_end - ev_start), 1),
            "crew": ev_crew,
            "tags": [ev["kind"].lower()],
            "utterance_count": 0,
            "photo_count": len(photos_in_ev),
            "milestones": [],
        }
        if planned_utc:
            sub_entry["planned_start_utc"] = planned_utc
            sub_entry["schedule_delta_min"] = delta_min
        if ev.get("crew_roles"):
            sub_entry["crew_roles"] = ev["crew_roles"]
        sub_blocks.append(sub_entry)

    sub_blocks.sort(key=lambda sb: sb["start_utc"])
    return sub_blocks


def _build_crew_breakdown(
    utterances: list[dict],
    milestones: list[dict],
    sub_blocks: list[dict],
    default_crew: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Summarize per-crewmember tasks and milestones inside an activity block."""
    breakdown: dict[str, list[dict]] = {slug: [] for slug in CREW_SLUGS}
    for sb in sub_blocks:
        if sb.get("crew_roles"):
            for cr in sb["crew_roles"]:
                slug = cr.get("crew")
                if slug in breakdown:
                    role = cr.get("role", "observer")
                    entry: dict = {
                        "start_utc": cr.get("start_utc", sb["start_utc"]),
                        "end_utc": cr.get("end_utc", sb["end_utc"]),
                        "activity": sb["title"],
                        "role": role,
                        "category": sb["category"],
                        "shared_with_all": role in ("crew_swap", "cabin_support_sitrep"),
                        "milestones": [m["title"] for m in sb.get("milestones", [])],
                    }
                    if cr.get("audio"):
                        entry["audio_ref"] = cr["audio"]
                    breakdown[slug].append(entry)
            continue

        targets = sb.get("crew") or default_crew or ["all"]
        expanded = list(CREW_SLUGS) if "all" in targets else [c for c in targets if c in CREW_SLUGS]
        for slug in expanded:
            breakdown[slug].append({
                "start_utc": sb["start_utc"],
                "end_utc": sb["end_utc"],
                "activity": sb["title"],
                "category": sb["category"],
                "shared_with_all": "all" in targets,
                "milestones": [m["title"] for m in sb.get("milestones", [])],
            })
    return breakdown


def build_mission_activity_hierarchy(mission: MissionConfig) -> dict:
    """Build the complete Flight Day -> Block -> Sub-Block & Per-Crew hierarchy."""
    ctx = MissionContextIndex(mission)

    ledger_path = mission.processed_transcripts / "enriched_utterances.jsonl"
    ms_path = mission.processed_transcripts / "detected_milestones.jsonl"

    raw_utterances = _load_jsonl(ledger_path)
    # Deduplicate by uid keeping latest assessment
    by_uid: dict[str, dict] = {}
    for rec in raw_utterances:
        uid = rec.get("uid")
        if uid:
            rec["ts"] = _parse_ts(rec["t"])
            if "crew" not in rec or not rec["crew"]:
                rec["crew"] = infer_crew_members(f"{rec.get('topic', '')} {rec.get('text', '')}")
            by_uid[uid] = rec
    utterances = sorted(by_uid.values(), key=lambda u: u["ts"])

    raw_milestones = _load_jsonl(ms_path)
    if not raw_milestones and utterances:
        for u in utterances:
            for idx, m in enumerate(u.get("milestones", [])):
                raw_milestones.append({
                    **m,
                    "id": f"milestone:{u['uid']}:{idx}",
                    "source_uid": u["uid"],
                    "channel": u.get("channel", ""),
                    "t": u["t"],
                    "text": u.get("text", ""),
                })
    merged_milestones = merge_milestone_reports(raw_milestones)

    # Track which utterances and milestones get assigned to planned blocks
    assigned_utt_uids: set[str] = set()
    assigned_ms_ids: set[str] = set()

    blocks_out: list[dict] = []

    for blk in ctx.planned_blocks:
        p_start = blk["start_ts"]
        p_end = blk["end_ts"]
        b_utts = [u for u in utterances if p_start <= u["ts"] <= p_end]
        for u in b_utts:
            assigned_utt_uids.add(u["uid"])

        # Match milestones by catalog event_id inside this block OR by timestamp overlap
        block_pt_events = [
            ev for ev in ctx.events
            if ev["end_ts"] >= p_start and ev["start_ts"] <= p_end and ev.get("kind") in EVENT_REFERENCE_KINDS
        ]
        block_ev_ids = {ev["id"] for ev in block_pt_events}

        b_ms = [
            m for m in merged_milestones
            if m["id"] not in assigned_ms_ids
            and (
                (m.get("event_id") and m["event_id"] in block_ev_ids)
                or (p_start <= m["first_ts"] <= p_end)
            )
        ]
        for m in b_ms:
            assigned_ms_ids.add(m["id"])

        b_photos = [p for p in ctx.crew_photos if p_start <= p["ts"] <= p_end]

        # Reconcile as-planned vs as-executed timestamps & status
        as_exec_ev_ts: list[float] = []
        for ev in block_pt_events:
            if ev.get("as_executed"):
                as_exec_ev_ts.extend([ev["start_ts"], ev["end_ts"]])

        observed_ts: list[float] = (
            [u["ts"] for u in b_utts]
            + [m["first_ts"] for m in b_ms]
            + [m["last_ts"] for m in b_ms]
            + [p["ts"] for p in b_photos]
            + as_exec_ev_ts
        )
        if any(m["status"] == "cancelled" for m in b_ms) or "Canceled" in blk["description"]:
            status = "cancelled" if not b_ms else b_ms[0]["status"]
        elif b_ms or b_utts or b_photos or as_exec_ev_ts:
            status = "executed"
        else:
            status = "planned_unconfirmed"

        exec_start_ts = min(observed_ts) if observed_ts else p_start
        exec_end_ts = max(observed_ts) if observed_ts else p_end
        delta_min = round((exec_start_ts - p_start) / 60.0, 1) if observed_ts else None

        sub_blocks = _build_sub_blocks(b_utts, b_ms, block_pt_events, b_photos)

        # Collect participating crew
        crew_participants: list[str] = []
        for ev in block_pt_events:
            for c in ev.get("crew", []):
                if c not in crew_participants:
                    crew_participants.append(c)
        for sb in sub_blocks:
            for c in sb.get("crew", []):
                if c not in crew_participants:
                    crew_participants.append(c)
        if "all" in crew_participants and len(crew_participants) > 1:
            crew_participants = [c for c in crew_participants if c != "all"]

        crew_activities = _build_crew_breakdown(b_utts, b_ms, sub_blocks, crew_participants or ["all"])

        blocks_out.append({
            "id": blk["id"],
            "flight_day": blk["flight_day"],
            "phase": blk["phase"],
            "title": blk["title"],
            "description": blk["description"],
            "category": blk["category"],
            "status": status,
            "time_source": "transcript_or_media_confirmed" if observed_ts else "planned_pdf_only",
            "planned_start_utc": blk["start_utc"],
            "planned_end_utc": blk["end_utc"],
            "start_utc": _fmt_utc(exec_start_ts),
            "end_utc": _fmt_utc(exec_end_ts),
            "schedule_delta_min": delta_min,
            "crew": crew_participants or ["all"],
            "utterance_count": len(b_utts),
            "milestone_count": len(b_ms),
            "photo_count": len(b_photos),
            "reference_events": [
                {"id": ev["id"], "kind": ev["kind"], "t": ev["t"], "label": ev["label"]}
                for ev in block_pt_events
            ],
            "sub_blocks": sub_blocks,
            "crew_activities": crew_activities,
        })

    # Synthesize discovered blocks for any unassigned utterances/milestones outside planned PDF blocks
    unassigned_utts = [u for u in utterances if u["uid"] not in assigned_utt_uids]
    unassigned_ms = [m for m in merged_milestones if m["id"] not in assigned_ms_ids]
    if unassigned_utts or unassigned_ms:
        all_ts = (
            [u["ts"] for u in unassigned_utts]
            + [m["first_ts"] for m in unassigned_ms]
            + [m["last_ts"] for m in unassigned_ms]
        )
        st_ts = min(all_ts)
        et_ts = max(all_ts)
        disc_photos = [p for p in ctx.crew_photos if st_ts <= p["ts"] <= et_ts]
        sub_blocks = _build_sub_blocks(unassigned_utts, unassigned_ms, [], disc_photos)
        crew_participants = []
        for sb in sub_blocks:
            for c in sb.get("crew", []):
                if c not in crew_participants:
                    crew_participants.append(c)
        if "all" in crew_participants and len(crew_participants) > 1:
            crew_participants = [c for c in crew_participants if c != "all"]

        blocks_out.append({
            "id": "discovered_transcript_ops",
            "flight_day": "FD01" if st_ts < _parse_ts(LAUNCH_UTC) else "UNSCHEDULED",
            "phase": "Prelaunch / Discovered Ops",
            "title": sub_blocks[0]["title"] if sub_blocks else "Discovered Transcript Operations",
            "description": "Transcript-discovered milestones and operations outside pre-flight PDF schedule blocks.",
            "category": "milestone",
            "status": "executed",
            "time_source": "transcript_discovered",
            "planned_start_utc": None,
            "planned_end_utc": None,
            "start_utc": _fmt_utc(st_ts),
            "end_utc": _fmt_utc(et_ts),
            "schedule_delta_min": None,
            "crew": crew_participants or ["all"],
            "utterance_count": len(unassigned_utts),
            "milestone_count": len(unassigned_ms),
            "photo_count": len(disc_photos),
            "reference_events": [],
            "sub_blocks": sub_blocks,
            "crew_activities": _build_crew_breakdown(unassigned_utts, unassigned_ms, sub_blocks),
        })

    blocks_out.sort(key=lambda b: b["start_utc"])

    # Organize into Flight Days (Level 1) + Mission-wide Per-Crewmember Timelines
    fd_map: dict[str, list[dict]] = {}
    for b in blocks_out:
        fd_map.setdefault(b["flight_day"], []).append(b)

    flight_days: list[dict] = []
    for fd, fd_blocks in sorted(fd_map.items()):
        flight_days.append({
            "flight_day": fd,
            "start_utc": fd_blocks[0]["start_utc"],
            "end_utc": max(b["end_utc"] for b in fd_blocks),
            "phases": list(dict.fromkeys(b["phase"] for b in fd_blocks)),
            "block_count": len(fd_blocks),
            "executed_block_count": sum(1 for b in fd_blocks if b["status"] == "executed"),
            "milestone_count": sum(b["milestone_count"] for b in fd_blocks),
            "utterance_count": sum(b["utterance_count"] for b in fd_blocks),
            "blocks": fd_blocks,
        })

    crew_timelines: dict[str, dict] = {}
    for slug in CREW_SLUGS:
        entries: list[dict] = []
        for b in blocks_out:
            for item in b["crew_activities"].get(slug, []):
                entries.append({
                    "flight_day": b["flight_day"],
                    "block_id": b["id"],
                    "block_title": b["title"],
                    **item,
                })
        crew_timelines[slug] = {
            **CREW_INFO[slug],
            "activity_count": len(entries),
            "timeline": entries,
        }

    return {
        "mission_id": mission.slug,
        "mission_name": mission.name,
        "launch_utc": mission.launch_utc or LAUNCH_UTC,
        "splashdown_utc": mission.splashdown_utc,
        "sources": {
            "preflight_planned_pdf": "pdfs/artemis-ii-overview-timeline-public-final.pdf (as-planned, not as-executed)",
            "science_report_pdf": "pdfs/Artemis II_Preliminary Science Report.pdf (as-flown science sessions & LTP)",
            "enriched_transcripts": str(ledger_path.name),
            "detected_milestones": str(ms_path.name),
        },
        "summary": {
            "total_assessed_utterances": len(utterances),
            "raw_milestone_detections": len(raw_milestones),
            "merged_unique_milestones": len(merged_milestones),
            "flight_days": len(flight_days),
            "activity_blocks": len(blocks_out),
            "executed_blocks": sum(1 for b in blocks_out if b["status"] == "executed"),
        },
        "crew_timelines": crew_timelines,
        "merged_milestones": [
            {k: v for k, v in m.items() if k not in ("first_ts", "last_ts")}
            for m in merged_milestones
        ],
        "flight_days": flight_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 1e — Build hierarchical mission activity tree and per-crewmember timelines"
    )
    parser.add_argument("--mission", default="artemis-ii", choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    tree = build_mission_activity_hierarchy(mission)

    proc_out = mission.processed_transcripts / "mission_activity_tree.json"
    web_out = mission.web_dir / "mission_activities.json"

    proc_out.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
    web_out.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")

    s = tree["summary"]
    print(f"\n=== Step 1e: Mission Context & Hierarchical Activity Tree — {mission.name} ===")
    print(f"  Assessed utterances : {s['total_assessed_utterances']:,}")
    print(f"  Milestones (merged) : {s['merged_unique_milestones']:,} (from {s['raw_milestone_detections']:,} raw reports)")
    print(f"  Activity blocks     : {s['activity_blocks']} across {s['flight_days']} Flight Days ({s['executed_blocks']} executed)")
    for slug, cinfo in tree["crew_timelines"].items():
        print(f"  Crew track [{slug:7s}] : {cinfo['activity_count']} sub-activities ({cinfo['name']})")
    print(f"  Saved processed tree: {proc_out}")
    print(f"  Saved web output    : {web_out}")


if __name__ == "__main__":
    main()
