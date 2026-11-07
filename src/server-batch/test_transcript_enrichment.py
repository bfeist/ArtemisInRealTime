"""HGX transport and source context regression checks."""
import contextlib
import importlib
import io
import json
import sys
import tempfile
from pathlib import Path
from config import MissionConfig
import unittest
from unittest.mock import patch

enrich = importlib.import_module("1_comm.transcript_common")
context = importlib.import_module("1_comm.mission_context")
runner = importlib.import_module("run_all")


def milestone():
    return {"title": "Crew walkout", "status": "started"}


class TranscriptEnrichmentTest(unittest.TestCase):
    text = "The crew is walking out now."

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        storage = patch("config.DATA_DIR", Path(temporary.name))
        storage.start()
        self.addCleanup(storage.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test")
        self.mission.web_dir.mkdir(parents=True)

    def test_stream_records_truncation_and_malformed_chunks(self):
        response = io.BytesIO(
            b'data: broken\n'
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n')
        diagnostics = {}
        with patch.object(enrich, "HGX_URL", "https://example.invalid"), \
             patch.object(enrich, "HGX_API_KEY", "test-secret"), \
             patch.object(enrich.urllib.request, "urlopen", return_value=response):
            _, content, stats = enrich.stream_llm_assessment("prompt", quiet=True, diagnostics=diagnostics)
        self.assertEqual(content, "partial")
        self.assertEqual(stats["finish_reason"], "length")
        self.assertFalse(stats["done_received"])
        self.assertEqual(diagnostics["stream_issues"][0]["reason"], "invalid_stream_json")
        self.assertEqual(len(diagnostics["raw_stream"]), 3)
        self.assertNotIn("test-secret", json.dumps(diagnostics))


    def test_stream_keeps_partial_output_when_interrupted(self):
        def chunks():
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'
            raise KeyboardInterrupt()
        diagnostics = {}
        with patch.object(enrich, "HGX_URL", "https://example.invalid"), \
             patch.object(enrich, "HGX_API_KEY", "test-secret"), \
             patch.object(enrich.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = chunks()
            with self.assertRaises(KeyboardInterrupt):
                enrich.stream_llm_assessment("prompt", quiet=True, diagnostics=diagnostics)
        self.assertIn("partial", diagnostics["raw_stream"][0])


    def test_runner_pins_hgx_and_model_for_every_model_stage(self):
        for step in ("1d", "1e", "1f"):
            with self.subTest(step=step), patch.object(runner.importlib, "import_module") as load, \
                 patch.object(runner.importlib, "reload"), patch("sys.argv", []), \
                 contextlib.redirect_stdout(io.StringIO()):
                seen = []
                load.return_value.main.side_effect = lambda: seen.extend(sys.argv)
                self.assertTrue(runner.run_step(step, "fake", "test", "artemis-ii", "hgx", "LLM-Alpha-512K"))
                self.assertEqual(seen, ["run_all.py", "--mission", "artemis-ii",
                                        "--backend", "hgx", "--model", "LLM-Alpha-512K"])


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
