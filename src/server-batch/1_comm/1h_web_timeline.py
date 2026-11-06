"""Step 1h: publish one mission timeline for global state and optional crew tracks.

The timeline contains the complete activity hierarchy and a shared milestone
lookup. Transcript text and evidence stay in their existing processing assets.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS, MissionConfig  # noqa: E402
from history_common import atomic_json, file_digest, require_artifacts, stage_lock  # noqa: E402

SCHEMA_VERSION = 1
BLOCK_FIELDS = (
    "id", "flight_day", "planned_block_id", "planned_flight_day", "phase", "title",
    "category", "status", "status_scope", "time_source", "start_utc", "end_utc",
    "planned_start_utc", "planned_end_utc", "confirmed_start_utc", "confirmed_end_utc",
    "confirmed_time_scope", "schedule_delta_min", "crew", "utterance_count",
    "milestone_count", "photo_count",
)
CREW_FIELDS = (
    "start_utc", "end_utc", "activity", "category", "status", "source_type", "milestone_ids",
    "reported_start_utc", "reported_end_utc", "event_time", "time_basis", "shared_with_all", "topics", "tags",
)
SUB_BLOCK_FIELDS = (
    "id", "title", "category", "start_utc", "end_utc", "crew", "tags",
    "context_only", "reference_only", "status", "planned_start_utc",
)


def pick(value: dict, fields) -> dict:
    return {key: value[key] for key in fields if key in value}


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def milestone_view(milestone: dict) -> dict:
    result = {
        **pick(milestone, ("title", "status", "time_basis", "crew")),
        "t": milestone["start_utc"], "reported_t": milestone["first_reported_t"],
    }
    for key, default in (("end_utc", milestone["start_utc"]),
                         ("last_reported_t", milestone["first_reported_t"]),
                         ("related_planned_block_ids", [])):
        if milestone.get(key, default) != default:
            result[key] = milestone[key]
    return result


def build_assets(tree: dict) -> tuple[dict, dict[str, bytes]]:
    """Keep all activities in one document; store each milestone only once."""
    if tree.get("schema_version") != 2:
        raise ValueError("1h requires the version 2 hierarchy; rerun 1g")
    if not tree.get("flight_days"):
        raise ValueError("Refusing to publish an empty mission timeline")
    milestones = {m["id"]: milestone_view(m) for m in tree["merged_milestones"]}
    if len(milestones) != len(tree["merged_milestones"]):
        raise ValueError("Duplicate milestone IDs in hierarchy")
    seen_blocks, seen_utterances, seen_milestones = set(), set(), set()
    crew_rows = {slug: [] for slug in tree["crew_timelines"]}
    days = []
    for day in tree["flight_days"]:
        blocks = []
        for block in day["blocks"]:
            block_id = block["id"]
            if block_id in seen_blocks:
                raise ValueError(f"Duplicate block ID: {block_id}")
            seen_blocks.add(block_id)
            owned_uids = block["utterance_ids"]
            if len(set(owned_uids)) != len(owned_uids) or seen_utterances.intersection(owned_uids):
                raise ValueError(f"Duplicate utterance ownership: {block_id}")
            seen_utterances.update(owned_uids)
            owned_mids = [m["id"] for sub in block["sub_blocks"] for m in sub["milestones"]]
            if len(set(owned_mids)) != len(owned_mids) or seen_milestones.intersection(owned_mids):
                raise ValueError(f"Duplicate milestone ownership: {block_id}")
            seen_milestones.update(owned_mids)
            constituent_ids = [m["id"] for m in block["constituent_activities"]]
            if not set(constituent_ids) <= milestones.keys():
                raise ValueError(f"Unknown constituent milestone in {block_id}")
            blocks.append({
                **pick(block, BLOCK_FIELDS), "description": block["description"],
                "reference_events": block["reference_events"],
                "constituent_milestone_ids": constituent_ids,
                "sub_blocks": [{**pick(sub, SUB_BLOCK_FIELDS),
                                "milestone_ids": [m["id"] for m in sub["milestones"]]}
                               for sub in block["sub_blocks"]],
            })
            for slug, entries in block["crew_activities"].items():
                for entry in entries:
                    if not set(entry["milestone_ids"]) <= milestones.keys():
                        raise ValueError(f"Unknown crew milestone in {block_id}")
                    crew_rows[slug].append({"block_id": block_id, "flight_day": day["flight_day"],
                                            **pick(entry, CREW_FIELDS)})
        days.append({**{k: v for k, v in day.items() if k != "blocks"}, "blocks": blocks})
    if seen_milestones != milestones.keys():
        raise ValueError("Hierarchy milestone coverage is incomplete")
    if len(seen_utterances) != tree["summary"]["linked_utterances"]:
        raise ValueError("Hierarchy utterance count differs from its summary; rerun 1g")
    assets, crew = {}, {}
    for slug, info in tree["crew_timelines"].items():
        payload = encode({"schema_version": SCHEMA_VERSION, "crew": slug, "items": crew_rows[slug]})
        path = f"timeline/crew/{hashlib.sha256(payload).hexdigest()}.json"
        assets[path] = payload
        crew[slug] = {
            **pick(info, ("name", "role", "activity_count")),
            "timeline": {"path": path, "bytes": len(payload),
                         "gzip_bytes": len(gzip.compress(payload, mtime=0))},
        }
    timeline = {
        "schema_version": SCHEMA_VERSION,
        **pick(tree, ("mission_id", "mission_name", "launch_utc", "splashdown_utc", "summary")),
        "crew": crew, "milestones": milestones, "flight_days": days,
    }
    return timeline, assets


def checked_inputs(mission: MissionConfig, tree: dict) -> tuple[dict, dict]:
    investigation = require_artifacts(mission.transcript_investigation)
    recovery = require_artifacts(mission.transcript_recovery)
    sources = tree["sources"]
    if (sources["investigation_snapshot"] != investigation["snapshot"]
            or sources["investigation_artifacts"] != investigation["artifacts"]):
        raise RuntimeError("Investigation changed since hierarchy; rerun 1g before 1h")
    if (investigation["recovery_snapshot"] != recovery["snapshot"]
            or investigation["recovery_artifacts"] != recovery["artifacts"]):
        raise RuntimeError("Recovery changed since investigation; rerun 1f and 1g before 1h")
    if tree["mission_id"] != mission.slug:
        raise ValueError("Hierarchy belongs to a different mission")
    return investigation, recovery


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def publish_timeline(mission: MissionConfig) -> dict:
    with stage_lock(mission.transcript_web_timeline), stage_lock(mission.transcript_hierarchy):
        source = mission.transcript_hierarchy / "mission_activity_tree.json"
        payload = source.read_bytes()
        hierarchy_hash = hashlib.sha256(payload).hexdigest()
        tree = json.loads(payload)
        inputs = checked_inputs(mission, tree)
        timeline, assets = build_assets(tree)
        timeline_bytes = encode(timeline)
        for relative, data in assets.items():
            destination = mission.web_dir / relative
            if not destination.exists() or destination.read_bytes() != data:
                write_bytes(destination, data)
        # Publish crew files first and atomically switch the main document last.
        if file_digest(source) != hierarchy_hash or checked_inputs(mission, tree) != inputs:
            raise RuntimeError("History inputs changed during publication; rerun 1h")
        write_bytes(mission.web_dir / "timeline.json", timeline_bytes)
        report = {
            "schema_version": SCHEMA_VERSION, "hierarchy_sha256": hierarchy_hash,
            "timeline_bytes": len(timeline_bytes),
            "timeline_gzip_bytes": len(gzip.compress(timeline_bytes, mtime=0)),
            "crew_asset_count": len(assets), "crew_asset_bytes": sum(map(len, assets.values())),
        }
        atomic_json(mission.transcript_web_timeline / "publication_report.json", report)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 1h: Publish mission timeline and optional crew tracks")
    parser.add_argument("--mission", choices=["artemis-ii"], default="artemis-ii")
    args = parser.parse_args()
    report = publish_timeline(MISSIONS[args.mission])
    print(f"Mission timeline: {report['timeline_bytes']:,} bytes ({report['timeline_gzip_bytes']:,} gzipped)")
    print(f"Optional crew files: {report['crew_asset_count']}")
    print(f"Published: {MISSIONS[args.mission].web_dir / 'timeline.json'}")


if __name__ == "__main__":
    main()
