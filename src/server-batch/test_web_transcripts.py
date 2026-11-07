"""Publication preserves source transcripts, assessments, and playback metadata."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import MissionConfig

web = importlib.import_module("1_comm.1i_web_transcripts")
common = importlib.import_module("1_comm.history_common")


class WebTranscriptsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch("config.DATA_DIR", Path(temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test")
        for key in ("BULK_ASSETS_DIR", "PDS_ASSETS_DIR", "YT_VIDEO_DIR"):
            storage = patch("config." + key, Path(temporary.name) / key)
            storage.start()
            self.addCleanup(storage.stop)
        self.mission.ensure_dirs()
        self.mission.transcript_inputs.mkdir(parents=True)
        (self.mission.transcript_inputs / "comm.csv").write_text(
            "2026-04-01T18:00:00Z|3|2|pcd/test.m4a|[Koch] A | B\n", encoding="utf-8")
        (self.mission.transcript_inputs / "PAO.csv").write_text(
            "2026-04-01T18:01:00Z|Unassessed commentary\n", encoding="utf-8")
        # New processing inputs take precedence over stale legacy web files.
        (self.mission.web_dir / "comm.csv").write_text("invalid stale CSV\n")
        common.atomic_json(self.mission.web_dir / "comm.json", [{
            "t": "2026-04-01T18:00:00Z", "s": 3, "d": 2, "src": "pcd/test.m4a",
            "speaker": "Koch", "text": "A | B",
        }])
        self.utterances = common.transcript.load_all_utterances(self.mission)
        self.record = {**self.utterances[0], "category": "science", "importance": 4,
                       "topic": "Observation", "tags": ["moon"], "model_provenance": "private"}
        common.publish(self.mission.transcript_enrichment, [self.record], [], {"complete": True})

    def read(self):
        return json.loads((self.mission.web_dir / "transcripts.json").read_text(encoding="utf-8"))

    def test_partial_enrichment_retains_every_source_and_separate_speaker(self):
        report = web.publish_transcripts(self.mission)
        payload = self.read()
        self.assertEqual((report["comm_entries"], report["pao_entries"], report["categorized_entries"]), (1, 1, 1))
        row = payload["comm"][0]
        self.assertEqual((row["s"], row["d"], row["src"], row["speaker"], row["text"]),
                         (3, 2, "pcd/test.m4a", "Koch", "A | B"))
        self.assertEqual(row["category"], "science")
        self.assertNotIn("model_provenance", row)
        self.assertNotIn("category", payload["pao"][0])

    def test_canonical_labels_are_published(self):
        reviewed = {**self.record, "category": "systems"}
        common.publish(self.mission.transcript_enrichment, [reviewed], [], {"complete": True})
        web.publish_transcripts(self.mission)
        self.assertEqual(self.read()["comm"][0]["category"], "systems")

    def test_publication_keeps_source_and_contribution_independent_of_subject(self):
        record = {**self.record, "category": "systems", "importance": 1,
                  "statement_kinds": ["acknowledgment"], "thread_context": [{"subject": "Camera power"}]}
        common.publish(self.mission.transcript_enrichment, [record], [], {"complete": True})
        web.publish_transcripts(self.mission)
        row = self.read()["comm"][0]
        self.assertEqual((row["source"], row["category"], row["statement_kinds"]),
                         ("PCD", "systems", ["acknowledgment"]))
        self.assertNotIn("thread_context", row)

    def test_invalid_canonical_artifact_cannot_replace_existing_publication(self):
        web.publish_transcripts(self.mission)
        before = (self.mission.web_dir / "transcripts.json").read_bytes()
        common.publish(self.mission.transcript_enrichment, [self.record], [], {"complete": True})
        (self.mission.transcript_enrichment / "comm_enriched.csv").write_text("tampered")
        with self.assertRaisesRegex(RuntimeError, "Artifact changed"):
            web.publish_transcripts(self.mission)
        self.assertEqual((self.mission.web_dir / "transcripts.json").read_bytes(), before)

    def test_legacy_web_inputs_still_load(self):
        for filename in ("comm.csv", "PAO.csv"):
            legacy = self.mission.web_dir / filename
            legacy.write_bytes((self.mission.transcript_inputs / filename).read_bytes())
            (self.mission.transcript_inputs / filename).unlink()
        web.publish_transcripts(self.mission)
        self.assertEqual(len(self.read()["comm"]), 1)
        self.assertEqual(len(self.read()["pao"]), 1)


if __name__ == "__main__":
    unittest.main()
