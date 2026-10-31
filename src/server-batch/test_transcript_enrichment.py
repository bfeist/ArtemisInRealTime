"""Regression checks for transcript-led milestone discovery and cache migration."""

import contextlib
import importlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import MissionConfig

enrich = importlib.import_module("1_comm.1d_enrich_transcripts")
context = importlib.import_module("1_comm.mission_context")
phase_1e = importlib.import_module("1_comm.1e_mission_context")


def assessment(index, milestones):
    return {"i": index, "imp": 3, "cat": "crew_life", "tags": ["crew"],
            "topic": "Crew departs for launch pad", "milestones": milestones}


def milestone(title="Crew walkout", evidence="The crew is walking out now.", **extra):
    return {"title": title, "status": "started", "evidence": evidence, **extra}


class TranscriptEnrichmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch("config.DATA_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test")
        self.mission.ensure_dirs()
        self.text = "The crew is walking out now. They are departing for the pad."
        self.batch = [{"text": self.text}]

    def parse(self, row, event_ids=None, prefix=""):
        return enrich.parse_jsonl_assessments(prefix + json.dumps(row), self.batch, event_ids or set())

    def test_multiple_uncatalogued_steps_survive_without_importance_gate(self):
        row = assessment(0, [milestone(), milestone("Departure for pad", "They are departing for the pad.")])
        parsed = self.parse(row)[0]
        self.assertEqual(len(parsed["milestones"]), 2)
        self.assertEqual(parsed["event_id"], "")
        self.assertEqual(parsed["importance"], 3)
        self.assertEqual(parsed["category"], "crew_life")

    def test_invalid_media_or_invented_link_does_not_suppress_discovery(self):
        for event_id in ("ksc-20260401-ph-kls01_0244", "yt:clip", "new:crew_walkout"):
            with self.subTest(event_id=event_id):
                parsed = self.parse(assessment(0, [milestone(event_id=event_id)]))[0]
                self.assertEqual(len(parsed["milestones"]), 1)
                self.assertNotIn("event_id", parsed["milestones"][0])
                self.assertEqual(parsed["event_id"], "")

    def test_nested_json_fallback_preserves_valid_catalog_link(self):
        parsed = self.parse(assessment(0, [milestone(event_id="crew_walkout")]),
                            {"crew_walkout"}, prefix="result: ")[0]
        self.assertEqual(parsed["event_id"], "crew_walkout")

    def test_unsupported_evidence_and_missing_schema_remain_pending(self):
        rows = [assessment(0, [milestone(evidence="Hatch closed.")]),
                assessment(0, [milestone(status="planned")]),
                {"i": 0, "event_id": "new:walkout"}]
        with contextlib.redirect_stdout(io.StringIO()):
            for row in rows:
                self.assertEqual(self.parse(row), {})
        self.assertEqual(self.parse(assessment(0, []))[0]["milestones"], [])

    def test_prompt_marks_captured_context_and_keeps_references_supplementary(self):
        u = {"uid": "source", "t": "2026-04-01T18:00:00Z", "channel": "pao", "text": self.text}
        prompt = enrich.build_user_prompt([u], [u], ["[PHOTO CAPTION] media_id=photo"],
                                           {"source": {"milestones": [milestone()]}}, [u])
        self.assertIn("not an exhaustive event list", prompt)
        self.assertIn("[ALREADY CAPTURED] Crew walkout (started)", prompt)
        self.assertIn("FOLLOWING CONTEXT (for interpretation only", prompt)

    def test_reference_ids_exclude_media(self):
        (self.mission.web_dir / "itinerary.json").write_text(json.dumps({"events": [
            {"id": "walkout", "t": "2026-04-01T18:00:00Z", "name": "Crew walkout"}]}))
        (self.mission.web_dir / "videoYt.json").write_text(json.dumps([
            {"id": "clip", "startTime": "2026-04-01T18:00:00Z", "title": "Walkout"}]))
        (self.mission.web_dir / "photos.json").write_text(json.dumps([
            {"id": "photo", "date": "2026-04-01T18:00:00Z", "description": "Crew walkout"}]))
        index = context.MissionContextIndex(self.mission)
        ts = enrich.parse_utc_ts("2026-04-01T18:00:00Z")
        self.assertEqual(index.query_event_ids(ts, ts), {"walkout"})
        lines = "\n".join(index.query_window(ts, ts))
        self.assertIn('event_id="walkout"', lines)
        self.assertIn('media_id="yt:clip"', lines)
        self.assertIn('media_id="photo"', lines)

    def run_cli(self, rows, *args):
        response = ("", "\n".join(json.dumps(row) for row in rows),
                    {"elapsed_s": 1, "ttft_s": 0.1, "completion_tokens": 100, "tok_per_s": 100})
        with patch.object(enrich, "MISSIONS", {"test": self.mission}), \
             patch.object(enrich, "stream_llm_assessment", return_value=response) as stream, \
             patch("sys.argv", ["1d", "--mission", "test", "--quiet", *args]), \
             contextlib.redirect_stdout(io.StringIO()):
            enrich.main()
        return stream

    def read_milestones(self):
        path = self.mission.processed_transcripts / "detected_milestones.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_cli_migrates_legacy_cache_and_resumes_without_duplicate_events(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        u = enrich.load_all_utterances(self.mission)[0]
        ledger = self.mission.processed_transcripts / "enriched_utterances.jsonl"
        ledger.write_text(json.dumps({**u, "event_id": "new:walkout"}) + "\n")
        rows = [assessment(0, [milestone(), milestone("Departure for pad", "They are departing for the pad.")])]
        stream = self.run_cli(rows, "--test-batches", "1")
        stream.assert_called_once()
        events = self.read_milestones()
        self.assertEqual(len(events), 2)
        self.assertEqual(len({e["id"] for e in events}), 2)
        self.assertTrue(all(e["time_basis"] == "report" and e["source_uid"] == u["uid"] for e in events))
        self.assertEqual(enrich.load_existing_ledger(ledger)[u["uid"]]["assessment_version"], 2)
        self.run_cli([]).assert_not_called()
        self.assertEqual(self.read_milestones(), events)

    def test_force_removes_superseded_detections_and_keeps_other_cached_rows(self):
        (self.mission.web_dir / "PAO.csv").write_text(
            f"2026-04-01T18:00:00Z|{self.text}\n2026-04-01T18:01:00Z|Hatch closed.\n")
        self.run_cli([assessment(0, [milestone()]), assessment(1, [
            milestone("Hatch closure", "Hatch closed.", status="completed")])])
        first = self.read_milestones()[0]
        self.run_cli([assessment(0, [])], "--force", "--start-utc", "2026-04-01T18:01:00Z")
        self.assertEqual(self.read_milestones(), [first])
        csv = (self.mission.web_dir / "PAO_enriched.csv").read_text(encoding="utf-8")
        self.assertEqual(len(csv.splitlines()), 2)
        self.run_cli([], "--force", "--start-utc", "2026-04-12T00:00:00Z").assert_not_called()
        self.assertEqual(self.read_milestones(), [first])

    def test_crew_attribution_and_1e_hierarchy_synthesis(self):
        # Verify 1e merges widely separated duplicate reports and builds per-crew hierarchy
        (self.mission.web_dir / "PAO.csv").write_text(
            "2026-04-01T17:10:34Z|Victor Glover just had a good suit leak check.\n"
            "2026-04-01T17:45:00Z|Earlier Victor Glover completed his suit leak check.\n"
            "2026-04-04T04:30:00Z|Victor Glover and Christina Koch are performing the window inspection at Window 2.\n"
        )
        rows = [
            {
                "i": 0, "imp": 4, "cat": "systems", "tags": ["leak check"],
                "topic": "Victor Glover completes suit leak check",
                "crew": ["glover"],
                "milestones": [{
                    "title": "Victor Glover suit leak check",
                    "status": "started",
                    "evidence": "Victor Glover just had a good suit leak check.",
                    "crew": ["glover"],
                }],
            },
            {
                "i": 1, "imp": 3, "cat": "pao_commentary", "tags": ["leak check"],
                "topic": "Recap of Glover suit leak check",
                "milestones": [{
                    "title": "Suit leak check by Victor Glover",
                    "status": "completed",
                    "evidence": "Earlier Victor Glover completed his suit leak check.",
                }],
            },
            {
                "i": 2, "imp": 5, "cat": "science", "tags": ["window quality"],
                "topic": "Glover and Koch perform window quality DFTO",
                "milestones": [{
                    "title": "Window quality inspection",
                    "status": "in_progress",
                    "evidence": "Victor Glover and Christina Koch are performing the window inspection at Window 2.",
                    "event_id": "sci_fd03_window_quality_dfto",
                }],
            },
        ]
        self.run_cli(rows, "--test-batches", "1")
        tree = phase_1e.build_mission_activity_hierarchy(self.mission)
        # Two leak-check reports 35 min apart merge into 1 milestone with progression ['started', 'completed']
        self.assertEqual(tree["summary"]["raw_milestone_detections"], 3)
        self.assertEqual(tree["summary"]["merged_unique_milestones"], 2)
        merged_leak = next(m for m in tree["merged_milestones"] if "leak check" in m["title"].lower())
        self.assertEqual(merged_leak["status"], "completed")
        self.assertEqual(merged_leak["status_progression"], ["started", "completed"])
        self.assertEqual(merged_leak["crew"], ["glover"])
        self.assertGreater(tree["crew_timelines"]["glover"]["activity_count"], 0)
        self.assertGreater(tree["crew_timelines"]["koch"]["activity_count"], 0)


if __name__ == "__main__":
    unittest.main()
