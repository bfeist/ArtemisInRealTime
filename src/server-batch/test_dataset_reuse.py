"""Compatibility and semantic reuse, with no live transport."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
import argparse
from unittest.mock import patch

dataset = importlib.import_module("1_comm.transcript_dataset")
reuse = importlib.import_module("1_comm.assessment_reuse")
fixtures = importlib.import_module("test_single_classification")


class DatasetTest(unittest.TestCase):
    def test_real_client_caps_actual_calls_including_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            options = argparse.Namespace(backend="hgx", model=None, reasoning_effort="low", max_tokens=100,
                quiet=True, hide_reasoning=True, model_request_limit=1)
            client = fixtures.common.ModelClient(options, Path(directory))
            with patch.object(fixtures.common.transcript, "stream_llm_assessment",
                    return_value=(None, "{}", {"finish_reason": "stop", "done_received": True})) as request:
                client.request("system", "prompt", "first")
                with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                    client.request("system", "repair", "second")
            self.assertEqual(request.call_count, 1)

    def test_import_preserves_labels_projection_evidence_and_checks_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy, web = root / "legacy", root / "web"
            web.mkdir()
            row = {"uid": "legacy", "channel": "pao", "t": "2026-04-01T00:00:00Z", "text": "[[Inaudible, on O/E-2 loop]] Hello"}
            nested = {"uid": "nested", "channel": "comm", "t": row["t"], "text": "[CAPCOM [Hansen]] Copy.", "src": "recording.aac", "s": "1.0", "d": "2.0"}
            values = {"1ea_labeled_history/utterances.jsonl": [row, nested],
                "1da_classification/labels.jsonl": [{"uid": "legacy", "category": "systems"}, {"uid": "nested", "category": "systems"}],
                "1f_investigation/milestones.jsonl": [{"id": "event"}],
                "1f_investigation/investigations.jsonl": [{"id": "claim", "verdict": "unresolved"}],
                "1f_investigation/unresolved_events.jsonl": [{"id": "claim"}]}
            for name, records in values.items():
                path = legacy / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            dataset.write(legacy / "1f_investigation/report.json", {"version": 1, "complete": True, "verified_events": 1,
                "unresolved_events": 1, "input_hashes": {str(root / "absent"): "old"}})
            projection = {"comm": [{**nested, "text": "Copy.", "speaker": "CAPCOM [Hansen]", "s": 1.0, "d": 2.0}], "pao": [{**row, "pao_type": "filler"}]}
            dataset.write(web / "transcripts.json", projection)
            before = {p: dataset.checksum(p) for p in legacy.rglob("*") if p.is_file()}
            with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
                report = dataset.import_legacy(legacy, web, root / "dataset")
            self.assertTrue(report["complete"])
            self.assertEqual(report["legacy_history_version"], 1)
            self.assertFalse(report["v3_verification_claimed"])
            loaded = dataset.read_dataset(root / "dataset/manifest.json")
            self.assertEqual(loaded["published_transcripts"], projection)
            self.assertEqual(before, {p: dataset.checksum(p) for p in before})
            (root / "dataset/assessments.jsonl").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                dataset.read_dataset(root / "dataset/manifest.json")

    def test_context_and_evidence_impact_is_selective(self):
        records = [{"uid": str(i), "channel": "pao", "t": f"2026-04-01T00:{i:02}:00Z",
                    "text": str(i)} for i in range(20)]
        changed = [{**r, "src": "new/path", "s": 99} for r in records]
        self.assertEqual(reuse.impact(records, changed)["estimated_classification_requests"], 0)
        changed[10] = {**changed[10], "text": "corrected"}
        report = reuse.impact(records, changed, 1,
            [{"id": "a", "search_dependencies": ["photos:camera"]},
             {"id": "b", "source_uids": ["3"]}], ["photos:camera"])
        self.assertEqual(report["affected_context"], ["10", "11", "9"])
        self.assertEqual([c["id"] for c in report["stale_investigations"]], ["a"])


class ModelReuseTest(fixtures.CanonicalPipelineTest):
    def test_investigation_reuses_playback_only_changes_but_reviews_new_corpus(self):
        self.classify()
        self.run_stage(fixtures.discovery.discover, fixtures.Client(self.extraction()))
        client = fixtures.Client({"action": "search", "query": "cameras"}, self.finish())
        self.assertTrue(self.run_stage(fixtures.investigate.run_investigations, client)["complete"])
        playback = [{**r, "src": "new/cache/path.aac", "s": "99"} for r in self.original]
        idle = fixtures.Client()
        with patch.object(fixtures.common.transcript, "load_all_utterances", return_value=playback):
            self.assertTrue(self.run_stage(fixtures.investigate.run_investigations, idle)["complete"])
        self.assertEqual(idle.calls, [])
        dataset.write(self.mission.web_dir / "photos.json", [{"id": "new", "date": "2026-04-01T17:00:00Z",
            "title": "Camera", "description": "New original caption evidence"}])
        self.assertTrue(self.run_stage(fixtures.discovery.discover, fixtures.Client())["complete"])
        review = fixtures.Client({"action": "search", "query": "cameras"}, self.finish())
        self.assertTrue(self.run_stage(fixtures.investigate.run_investigations, review)["complete"])
        self.assertEqual(len(review.calls), 2)

    def test_discovery_reuses_when_unrelated_photo_file_changes(self):
        self.classify()
        first = fixtures.Client(self.extraction())
        self.assertTrue(self.run_stage(fixtures.discovery.discover, first)["complete"])
        dataset.write(self.mission.web_dir / "photos.json", [{"id": "unrelated", "date": "2026-04-12T00:00:00Z"}])
        idle = fixtures.Client()
        self.assertTrue(self.run_stage(fixtures.discovery.discover, idle)["complete"])
        self.assertEqual(idle.calls, [])

    # Inherit setup/helpers only; the original suite remains separately runnable.
    def test_fifty_rows_one_request_then_formatting_and_offset_zero(self):
        path = self.mission.transcript_inputs / "PAO.csv"
        path.write_text("".join(f"2026-04-01T17:00:{i:02}Z|Camera status {i}.\n" for i in range(50)), encoding="utf-8")
        client = fixtures.Client({"labels": [fixtures.pao_row(i, kinds=["report"]) for i in range(50)]})
        self.assertTrue(self.run_stage(fixtures.stage.classify, client)["complete"])
        self.assertEqual(len(client.calls), 1)
        path.write_text(path.read_text(encoding="utf-8").replace("\n", "\r\n"), encoding="utf-8")
        idle = fixtures.Client()
        self.assertTrue(self.run_stage(fixtures.stage.classify, idle)["complete"])
        self.assertEqual(idle.calls, [])

    def test_insertion_does_not_discard_later_partition(self):
        path = self.mission.transcript_inputs / "PAO.csv"
        lines = [f"2026-04-01T17:{minute}:00Z|Camera status {minute}.\n" for minute in ("00", "30")]
        path.write_text("".join(lines), encoding="utf-8")
        reply = {"labels": [fixtures.pao_row(0, kinds=["report"])]}
        client = fixtures.Client(reply, reply)
        self.assertTrue(self.run_stage(fixtures.stage.classify, client)["complete"])
        path.write_text(lines[0] + "2026-04-01T17:00:01Z|Additional camera report.\n" + lines[1], encoding="utf-8")
        changed = fixtures.Client({"labels": [fixtures.pao_row(0, kinds=["report"]), fixtures.pao_row(1, kinds=["report"])]})
        self.assertTrue(self.run_stage(fixtures.stage.classify, changed)["complete"])
        self.assertEqual(len(changed.calls), 1)

    def test_utc_spelling_changes_rebind_legacy_uid_without_model(self):
        self.classify()
        path = self.mission.transcript_inputs / "PAO.csv"
        path.write_text(path.read_text(encoding="utf-8").replace("Z|", "+00:00|"), encoding="utf-8")
        idle = fixtures.Client()
        self.assertTrue(self.run_stage(fixtures.stage.classify, idle)["complete"])
        self.assertEqual(idle.calls, [])

    def test_accepted_legacy_import_seeds_first_classification_without_requests(self):
        legacy, web = self.base / "old", self.base / "old_web"
        web.mkdir()
        values = {"1ea_labeled_history/utterances.jsonl": self.original,
            "1da_classification/labels.jsonl": [{"uid": r["uid"], "category": "systems", "importance": 2,
                "topic": "Camera", "tags": ["camera"], "statement_kinds": ["report"], "thread_context": []} for r in self.original],
            "1f_investigation/milestones.jsonl": [], "1f_investigation/investigations.jsonl": [],
            "1f_investigation/unresolved_events.jsonl": []}
        for name, records in values.items():
            path = legacy / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        dataset.write(legacy / "1f_investigation/report.json", {"version": 1, "complete": True})
        dataset.write(web / "transcripts.json", {"comm": [], "pao": [{**r, "pao_type": "commentary"} for r in self.original]})
        manifest = self.base / "import/manifest.json"
        dataset.import_legacy(legacy, web, manifest.parent)
        plan = self.base / "plan.json"
        dataset.write(plan, {"contract": "transcript-impact-v1", "semantic_revision": reuse.digest([reuse.semantic(r) for r in self.original]),
            "stages": ["1d"], "max_requests": 1, "baseline_dataset_manifest": str(manifest)})
        self.options.work_plan, self.options.limit = plan, 1
        idle = fixtures.Client()
        with patch.object(fixtures.stage, "ModelClient", return_value=idle):
            report = self.run_stage(fixtures.stage.classify, None)
        self.assertTrue(report["complete"])
        self.assertEqual(report["inherited_legacy_assessments"], 2)
        self.assertEqual(idle.calls, [])


# Avoid rerunning the inherited pipeline cases as part of this focused suite.
for _name in list(vars(fixtures.CanonicalPipelineTest)):
    if _name.startswith("test_"):
        setattr(ModelReuseTest, _name, None)

if __name__ == "__main__":
    unittest.main()
