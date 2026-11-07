"""Regressions for chronology, source attribution and complete transcript context in 1g."""
import importlib
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import MissionConfig

hierarchy = importlib.import_module("1_comm.1g_mission_context")
common = importlib.import_module("1_comm.history_common")


class MissionHierarchyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch("config.DATA_DIR", Path(temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test",
                                     launch_utc="2026-04-01T22:35:12Z")
        for key in ("BULK_ASSETS_DIR", "PDS_ASSETS_DIR", "YT_VIDEO_DIR"):
            storage = patch("config." + key, Path(temporary.name) / key)
            storage.start()
            self.addCleanup(storage.stop)
        self.mission.ensure_dirs()
        self.root = self.mission.processed_transcripts

    def row(self, name, when, crew=(), importance=3, topic=None, channel="comm"):
        return {"uid": name, "t": when, "channel": channel, "text": topic or name,
                "topic": topic or name, "crew": list(crew), "importance": importance,
                "category": "systems", "tags": ["test"], "milestones": [],
                "label_review": {"status": "reviewed"}}

    def event(self, name, row, *, event_time=None, crew=(), status="completed", lead=None):
        return {"id": name, "title": name, "t": row["t"], "event_time": event_time,
                "source_uid": row["uid"], "channel": row["channel"], "evidence": row["text"],
                "verification": "supported", "status": status, "crew": list(crew), "claims": [],
                "candidate_ids": ["lead:plan:" + lead] if lead else ["candidate:" + name]}

    def plan(self, name, start, end, day="FD01"):
        return {"id": name, "title": name, "description": name, "phase": "Test", "category": "systems",
                "flight_day": day, "start_utc": start, "end_utc": end,
                "start_ts": hierarchy._parse_ts(start), "end_ts": hierarchy._parse_ts(end)}

    def build(self, rows, events=(), plans=()):
        recovery = self.mission.transcript_discovery
        common.publish(recovery, rows, events, {"complete": True, "snapshot": "recovery"})
        recovery_report = common.require_artifacts(recovery)
        common.publish(self.mission.transcript_investigation, rows, events,
                       {"complete": True, "snapshot": "investigation", "discovery_snapshot": "recovery",
                        "discovery_artifacts": recovery_report["artifacts"], "unresolved_events": 0,
                        "unresolved_labels": 0})
        context = SimpleNamespace(events=[], crew_photos=[], planned_blocks=list(plans))
        with patch.object(hierarchy, "MissionContextIndex", return_value=context):
            return hierarchy.build_mission_activity_hierarchy(self.mission)

    @staticmethod
    def blocks(tree):
        return [b for day in tree["flight_days"] for b in day["blocks"]]

    def test_discovered_activity_is_partitioned_by_day_and_gap(self):
        rows = [self.row("prelaunch", "2026-03-30T12:00:00Z"),
                self.row("day1", "2026-04-01T23:00:00Z"),
                self.row("day2a", "2026-04-02T23:10:00Z"),
                self.row("day2b", "2026-04-03T03:10:00Z")]
        tree = self.build(rows)
        self.assertEqual(tree["summary"]["flight_days"], 2)
        self.assertEqual(tree["summary"]["prelaunch_days"], 1)
        blocks = self.blocks(tree)
        self.assertEqual(len(blocks), 4)  # The four-hour gap also splits activity within FD02.
        self.assertEqual({b["flight_day"] for b in blocks}, {"PRELAUNCH-2026-03-30", "FD01", "FD02"})
        self.assertTrue(all(b["id"] != "discovered_transcript_ops" for b in blocks))
        for day in tree["flight_days"]:
            for b in day["blocks"]:
                self.assertGreaterEqual(hierarchy._parse_ts(b["start_utc"]), hierarchy._parse_ts(day["boundary_start_utc"]))
                self.assertLessEqual(hierarchy._parse_ts(b["end_utc"]), hierarchy._parse_ts(day["boundary_end_utc"]))

    def test_delayed_recap_keeps_audio_time_but_places_event_on_execution_day(self):
        row = self.row("recap", "2026-04-08T06:07:23Z", topic="Recap of launch")
        event = self.event("Launch", row, event_time="2026-04-01T22:35:12Z", crew=["all"])
        tree = self.build([row], [event])
        event_blocks = [b for b in self.blocks(tree) if b["milestone_count"]]
        self.assertEqual(event_blocks[0]["flight_day"], "FD01")
        view = next(m for s in event_blocks[0]["sub_blocks"] for m in s["milestones"])
        self.assertEqual(hierarchy._parse_ts(view["t"]), hierarchy._parse_ts(event["event_time"]))
        self.assertEqual(view["reported_t"], row["t"])
        self.assertEqual(view["report_utterance_ids"], [row["uid"]])
        report_block = next(b for b in self.blocks(tree) if row["uid"] in b["utterance_ids"])
        self.assertEqual(report_block["flight_day"], "FD07")
        merged = tree["merged_milestones"][0]
        self.assertEqual(merged["first_reported_t"], row["t"])
        self.assertEqual(merged["reports"][0]["t"], row["t"])

    def test_report_time_fallback_is_explicit_and_does_not_create_execution_start(self):
        row = self.row("report", "2026-04-01T23:01:00Z")
        plan = self.plan("checkout", "2026-04-01T23:00:00Z", "2026-04-02T00:00:00Z")
        tree = self.build([row], [self.event("Checkout complete", row, lead="checkout")], [plan])
        block = next(b for b in self.blocks(tree) if b["planned_block_id"] == "checkout")
        self.assertEqual(block["status"], "executed")
        self.assertIsNone(block["confirmed_start_utc"])
        self.assertIsNone(block["schedule_delta_min"])
        self.assertEqual(tree["merged_milestones"][0]["time_basis"], "report_time")

    def test_own_lead_can_execute_outside_planned_window_with_negative_delta(self):
        rows = [self.row("start_report", "2026-04-02T03:00:00Z"),
                self.row("end_report", "2026-04-02T03:01:00Z")]
        plan = self.plan("burn", "2026-04-01T23:00:00Z", "2026-04-01T23:10:00Z")
        events = [self.event("Burn started", rows[0], event_time="2026-04-01T22:50:00Z", status="started", lead="burn"),
                  self.event("Burn completed", rows[1], event_time="2026-04-01T23:20:00Z", lead="burn")]
        tree = self.build(rows, events, [plan])
        block = next(b for b in self.blocks(tree) if b["planned_block_id"] == "burn")
        self.assertEqual(block["milestone_count"], 2)
        self.assertEqual(block["schedule_delta_min"], -10)
        self.assertEqual(hierarchy._parse_ts(block["start_utc"]), hierarchy._parse_ts(events[0]["event_time"]))
        self.assertEqual(hierarchy._parse_ts(block["end_utc"]), hierarchy._parse_ts(events[1]["event_time"]))

    def test_mixed_cancelled_burn_and_completed_meal_is_partially_executed(self):
        row = self.row("meal", "2026-04-01T23:05:00Z")
        plan = self.plan("burn_and_meal", "2026-04-01T23:00:00Z", "2026-04-02T00:00:00Z")
        events = [self.event("Meal completed", row, event_time=row["t"], lead=plan["id"]),
                  self.event("Burn cancelled", row, event_time=row["t"], status="cancelled", lead=plan["id"])]
        tree = self.build([row], events, [plan])
        block = next(b for b in self.blocks(tree) if b["planned_block_id"] == plan["id"])
        self.assertEqual(block["status"], "partially_executed")
        self.assertEqual({m["status"] for m in block["constituent_activities"]}, {"completed", "cancelled"})
        self.assertEqual(tree["summary"]["partially_executed_blocks"], 1)

    def test_crew_tracks_use_each_activity_attribution_not_cluster_participants(self):
        rows = [self.row("glover", "2026-04-01T23:00:00Z", ["glover"], topic="Glover gave an okay sign"),
                self.row("koch", "2026-04-01T23:01:00Z", ["koch"], topic="Koch completed her leak check"),
                self.row("unknown", "2026-04-01T23:02:00Z", importance=2, topic="Copy")]
        events = [self.event(rows[0]["topic"], rows[0], crew=["glover"]),
                  self.event(rows[1]["topic"], rows[1], crew=["koch"])]
        tree = self.build(rows, events)
        koch = tree["crew_timelines"]["koch"]["timeline"]
        self.assertTrue(koch)
        self.assertTrue(all("Glover" not in entry["activity"] for entry in koch))
        self.assertTrue(all("Koch" not in entry["activity"] for entry in tree["crew_timelines"]["glover"]["timeline"]))
        self.assertEqual(tree["crew_timelines"]["hansen"]["activity_count"], 0)
        self.assertEqual(tree["crew_timelines"]["wiseman"]["activity_count"], 0)
        # Even an intentionally mixed old-style cluster must not spread its title to others.
        merged = hierarchy.merge_milestone_reports(events)
        attributed = hierarchy._build_crew_breakdown([], merged,
            [{"title": rows[0]["topic"], "crew": ["glover", "koch"], "milestones": []}])
        self.assertEqual(attributed["koch"][0]["activity"], rows[1]["topic"])

    def test_low_importance_unknown_crew_comm_and_pao_are_linked_exactly_once(self):
        rows = [self.row("copy", "2026-04-01T23:00:00Z", importance=1),
                self.row("coordination", "2026-04-01T23:01:00Z", importance=2),
                self.row("operation", "2026-04-01T23:02:00Z", ["hansen"], importance=4),
                self.row("pao", "2026-04-01T23:03:00Z", importance=1, channel="pao")]
        tree = self.build(rows)
        subs = [s for b in self.blocks(tree) for s in b["sub_blocks"]]
        self.assertEqual(Counter(uid for s in subs for uid in s["utterance_ids"]), Counter(r["uid"] for r in rows))
        self.assertEqual({uid for s in subs for uid in s["context_utterance_ids"]}, {"copy", "coordination", "pao"})
        self.assertEqual(tree["summary"]["linked_utterances"], 4)
        self.assertEqual(tree["summary"]["context_utterances"], 3)
        self.assertEqual(tree["summary"]["substantive_utterances"], 1)

    def test_adjacent_crew_discussions_keep_all_topics_and_own_sources(self):
        rows = [self.row("koch1", "2026-04-01T23:00:00Z", ["koch"], topic="Koch checks oxygen supply"),
                self.row("koch2", "2026-04-01T23:01:00Z", ["koch"], topic="Koch checks cooling supply"),
                self.row("glover", "2026-04-01T23:02:00Z", ["glover"], topic="Glover checks supply")]
        tree = self.build(rows)
        entries = tree["crew_timelines"]["koch"]["timeline"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["utterance_ids"], ["koch1", "koch2"])
        self.assertEqual(entries[0]["topics"], [rows[0]["topic"], rows[1]["topic"]])
        self.assertEqual(entries[0]["activity"], "Systems discussion: test")
        self.assertEqual(tree["crew_timelines"]["glover"]["timeline"][0]["utterance_ids"], ["glover"])

    def test_undated_late_recap_links_old_plan_without_recreating_it_in_later_day(self):
        row = self.row("late_report", "2026-04-08T06:00:00Z")
        plan = self.plan("old_burn", "2026-04-01T23:00:00Z", "2026-04-02T00:00:00Z")
        event = self.event("Old burn was cancelled", row, status="cancelled", lead=plan["id"])
        tree = self.build([row], [event], [plan])
        originals = [b for b in self.blocks(tree) if b["planned_block_id"] == plan["id"]]
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0]["flight_day"], "FD01")
        report = next(b for b in self.blocks(tree) if b["milestone_count"])
        self.assertEqual(report["flight_day"], "FD07")
        view = next(m for s in report["sub_blocks"] for m in s["milestones"])
        self.assertEqual(view["time_basis"], "report_time")
        self.assertEqual(view["related_planned_block_ids"], [plan["id"]])

    def test_planned_block_crossing_day_boundary_is_split_without_duplicate_context(self):
        rows = [self.row("before", "2026-04-02T17:30:00Z"), self.row("boundary", "2026-04-02T17:35:00Z")]
        plans = [self.plan("sleep", "2026-04-02T16:00:00Z", "2026-04-02T18:00:00Z"),
                 self.plan("wake", "2026-04-02T17:35:00Z", "2026-04-02T20:00:00Z", "FD02")]
        tree = self.build(rows, plans=plans)
        owners = {uid: b["flight_day"] for b in self.blocks(tree) for uid in b["utterance_ids"]}
        self.assertEqual(owners, {"before": "FD01", "boundary": "FD02"})
        self.assertEqual(sum(b["utterance_count"] for b in self.blocks(tree)), 2)


if __name__ == "__main__":
    unittest.main()
