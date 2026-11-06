"""Publication and presentation-only contract regressions for 1h."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config import MissionConfig

web = importlib.import_module("1_comm.1h_web_timeline")
hierarchy = importlib.import_module("1_comm.1g_mission_context")
common = importlib.import_module("1_comm.history_common")
runner = importlib.import_module("run_all")


class WebTimelineTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch("config.DATA_DIR", Path(temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test",
                                     launch_utc="2026-04-01T22:35:12Z")
        self.mission.ensure_dirs()
        start = datetime(2026, 4, 1, 23, tzinfo=timezone.utc)
        rows = [{
            "uid": f"row-{i}", "t": (start + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
            "channel": "comm", "text": "PRIVATE transcript quote", "importance": 4,
            "topic": f"Operation {i}: lunar observation 月", "crew": ["koch"] if i % 2 else [],
            "category": "science" if i % 2 else "systems", "tags": ["observation"],
            "milestones": [], "label_review": {"status": "reviewed"},
        } for i in range(90)]
        events = [{
            "id": f"milestone-{i}", "title": f"Development {i}", "t": rows[i]["t"],
            "event_time": "2026-04-01T22:45:00Z" if i == 0 else None,
            "source_uid": rows[i]["uid"], "channel": "comm", "evidence": rows[i]["text"],
            "verification": "supported", "status": "completed", "crew": rows[i]["crew"],
            "claims": [{"claim": "PRIVATE investigation claim"}], "candidate_ids": [f"candidate:{i}"],
        } for i in range(2)]
        common.publish(self.mission.transcript_recovery, rows, events, {"complete": True, "snapshot": "recovery"})
        recovery = common.require_artifacts(self.mission.transcript_recovery)
        common.publish(self.mission.transcript_investigation, rows, events, {
            "complete": True, "snapshot": "investigation", "recovery_snapshot": "recovery",
            "recovery_artifacts": recovery["artifacts"], "unresolved_events": 1, "unresolved_labels": 0,
        })
        plan = {
            "id": "plan-1", "flight_day": "FD01", "title": "Science block", "phase": "Outbound",
            "description": "A planned activity", "category": "science",
            "start_utc": "2026-04-01T22:35:12Z", "end_utc": "2026-04-02T01:00:00Z",
            "start_ts": hierarchy._parse_ts("2026-04-01T22:35:12Z"),
            "end_ts": hierarchy._parse_ts("2026-04-02T01:00:00Z"),
        }
        context = SimpleNamespace(events=[], crew_photos=[], planned_blocks=[plan])
        with patch.object(hierarchy, "MissionContextIndex", return_value=context):
            self.tree = hierarchy.build_mission_activity_hierarchy(self.mission)
        common.atomic_json(self.mission.transcript_hierarchy / "mission_activity_tree.json", self.tree)

    def read(self, ref):
        return json.loads((self.mission.web_dir / ref["path"]).read_bytes())

    def test_single_timeline_preserves_all_activity_and_omits_evidence(self):
        report = web.publish_timeline(self.mission)
        timeline_path = self.mission.web_dir / "timeline.json"
        payload = timeline_path.read_bytes()
        self.assertEqual(report["timeline_gzip_bytes"], len(gzip.compress(payload, mtime=0)))
        index = json.loads(payload)
        block = index["flight_days"][0]["blocks"][0]
        self.assertEqual(block["status"], "planned_unconfirmed")
        self.assertIsNone(block["confirmed_start_utc"])
        self.assertEqual(block["planned_start_utc"], "2026-04-01T22:35:12Z")
        self.assertEqual(index["summary"]["unresolved_events"], 1)
        self.assertNotIn("crew_timelines", index)
        self.assertNotIn("detail", block)
        self.assertEqual(len(block["sub_blocks"]), len(self.tree["flight_days"][0]["blocks"][0]["sub_blocks"]))
        milestones = index["milestones"]
        self.assertEqual(milestones["milestone-0"]["time_basis"], "explicit_event_time")
        self.assertEqual(milestones["milestone-0"]["reported_t"], "2026-04-01T23:00:00Z")
        self.assertEqual(milestones["milestone-0"]["t"], "2026-04-01T22:45:00.000Z")
        self.assertEqual(milestones["milestone-0"]["crew"], [])
        self.assertEqual(milestones["milestone-1"]["time_basis"], "report_time")
        self.assertEqual({mid for sub in block["sub_blocks"] for mid in sub["milestone_ids"]}, set(milestones))
        self.assertTrue(all("milestones" not in sub for sub in block["sub_blocks"]))
        self.assertEqual(self.read(index["crew"]["glover"]["timeline"])["items"], [])
        koch = self.read(index["crew"]["koch"]["timeline"])["items"]
        self.assertTrue(koch)
        self.assertTrue(all(item["time_basis"] == "report_time" for item in koch))
        self.assertTrue(all(item["block_id"] == block["id"] for item in koch))
        self.assertEqual(report["crew_asset_count"], 4)
        self.assertEqual(report["hierarchy_sha256"], common.file_digest(
            self.mission.transcript_hierarchy / "mission_activity_tree.json"))

        forbidden = {"evidence", "reports", "claims", "text", "utterance_ids", "source_uid",
                     "report_utterance_ids", "context_utterance_ids", "substantive_utterance_ids",
                     "candidate_ids", "provenance"}

        def check(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(index)
        for file in (self.mission.web_dir / "timeline/crew").glob("*.json"):
            data = file.read_bytes()
            self.assertEqual(file.stem, hashlib.sha256(data).hexdigest())
            self.assertNotIn(b"PRIVATE", data)
            check(json.loads(data))

    def test_deterministic_rebuild_and_old_assets_remain_available(self):
        first = web.publish_timeline(self.mission)
        timeline_path = self.mission.web_dir / "timeline.json"
        original = timeline_path.read_bytes()
        previous = {p: p.read_bytes() for p in (self.mission.web_dir / "timeline/crew").glob("*.json")}
        self.assertEqual(web.publish_timeline(self.mission), first)
        self.assertEqual(timeline_path.read_bytes(), original)
        self.tree["flight_days"][0]["blocks"][0]["description"] = "Updated description"
        common.atomic_json(self.mission.transcript_hierarchy / "mission_activity_tree.json", self.tree)
        web.publish_timeline(self.mission)
        self.assertNotEqual(timeline_path.read_bytes(), original)
        for path, data in previous.items():
            self.assertEqual(path.read_bytes(), data)

    def test_stale_or_incomplete_upstream_does_not_replace_public_timeline(self):
        web.publish_timeline(self.mission)
        timeline_path = self.mission.web_dir / "timeline.json"
        original = timeline_path.read_bytes()
        report_path = self.mission.transcript_investigation / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for change in ({"snapshot": "changed"}, {"complete": False}):
            common.atomic_json(report_path, {**report, **change})
            with self.assertRaises(RuntimeError):
                web.publish_timeline(self.mission)
            self.assertEqual(timeline_path.read_bytes(), original)

    def test_upstream_change_during_publication_preserves_public_timeline(self):
        web.publish_timeline(self.mission)
        timeline_path = self.mission.web_dir / "timeline.json"
        original = timeline_path.read_bytes()
        check = web.checked_inputs
        calls = 0

        def changed(mission, tree):
            nonlocal calls
            calls += 1
            result = check(mission, tree)
            return result if calls == 1 else ({"snapshot": "changed"}, result[1])

        with patch.object(web, "checked_inputs", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "changed during publication"):
                web.publish_timeline(self.mission)
        self.assertEqual(timeline_path.read_bytes(), original)

    def test_invalid_hierarchy_fails_before_replacing_timeline(self):
        web.publish_timeline(self.mission)
        path = self.mission.web_dir / "timeline.json"
        original = path.read_bytes()
        for field, value in (("schema_version", 1), ("flight_days", []), ("merged_milestones", [])):
            with self.subTest(field=field):
                common.atomic_json(self.mission.transcript_hierarchy / "mission_activity_tree.json",
                                   {**self.tree, field: value})
                with self.assertRaises(ValueError):
                    web.publish_timeline(self.mission)
                self.assertEqual(path.read_bytes(), original)

    def test_runner_includes_publication_after_hierarchy(self):
        steps = [item[0] for item in runner.STEPS]
        self.assertEqual(steps[steps.index("1g") + 1], "1h")
        self.assertEqual(runner.STEP_GROUPS["comm-history"], ["1e", "1f", "1g", "1h", "1i"])


if __name__ == "__main__":
    unittest.main()
