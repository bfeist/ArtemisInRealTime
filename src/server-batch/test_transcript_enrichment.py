"""Regression checks for transcript-led milestone discovery and cache migration."""

import contextlib
import importlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import MissionConfig

enrich = importlib.import_module("1_comm.1d_enrich_transcripts")
context = importlib.import_module("1_comm.mission_context")
phase_1e = importlib.import_module("1_comm.1e_mission_context")
runner = importlib.import_module("run_all")


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

    def test_parser_explains_rejections_and_duplicate_indices(self):
        issues = []
        content = "\n".join([
            "broken JSON", json.dumps(assessment(9, [])),
            json.dumps(assessment(0, [milestone(evidence="Invented quote")])),
            json.dumps(assessment(0, [])), json.dumps(assessment(0, [])),
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            parsed = enrich.parse_jsonl_assessments(content, self.batch, set(), issues)
        self.assertEqual(list(parsed), [0])
        self.assertEqual([x["reason"] for x in issues], [
            "invalid_json_object", "invalid_index", "invalid_milestone", "duplicate_index"])
        self.assertEqual(issues[2]["line"], 3)
        self.assertIn("evidence_not_in_utterance", issues[2]["errors"])

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

    def test_valid_extraction_with_malformed_stream_chunk_gets_no_repair_or_labels(self):
        extraction = self.fixture_extraction([assessment(0, [milestone()])], self.batch)
        diagnostics = {}
        issues = []

        def corrupt_stream(**kwargs):
            kwargs["diagnostics"].setdefault("stream_issues", []).append({
                "reason": "invalid_stream_json", "raw": "broken stream chunk",
            })
            return "", json.dumps(extraction), {
                "elapsed_s": 1, "ttft_s": 0.1, "completion_tokens": 100,
                "prompt_tokens": 50, "tok_per_s": 100,
                "finish_reason": "stop", "done_received": True,
            }

        with patch.object(enrich, "stream_llm_assessment", side_effect=corrupt_stream) as stream:
            with self.assertRaisesRegex(ValueError, "stream did not finish cleanly"):
                enrich.staged_llm_assessment("source transcript", self.batch, set(), quiet=True,
                                             diagnostics=diagnostics, parse_issues=issues)
        stream.assert_called_once()
        self.assertEqual(set(diagnostics["stages"]), {"extraction"})
        self.assertEqual(diagnostics["stages"]["extraction"]["stats"]["finish_reason"], "stop")
        self.assertTrue(diagnostics["stages"]["extraction"]["stats"]["done_received"])
        self.assertEqual(issues[0]["reason"], "invalid_extraction")

    def test_backend_request_uses_selected_model_endpoint_and_credentials(self):
        cases = [
            ("hgx", None, "https://hgx.invalid/v1/chat/completions", "LLM-Alpha-512K", "Bearer hgx-secret"),
            ("strata", None, "http://localhost:9999/v1/chat/completions", "iq3_s", None),
            ("strata", "custom-model", "http://localhost:9999/v1/chat/completions", "custom-model", "Bearer strata-secret"),
        ]
        for backend, model, url, expected_model, auth in cases:
            with self.subTest(backend=backend, model=model), \
                 patch.object(enrich, "HGX_URL", "https://hgx.invalid/v1"), \
                 patch.object(enrich, "HGX_API_KEY", "hgx-secret"), \
                 patch.object(enrich, "STRATA_URL", "http://localhost:9999/v1/"), \
                 patch.object(enrich, "STRATA_MODEL", "iq3_s"), \
                 patch.object(enrich, "STRATA_API_KEY", "strata-secret" if model else ""), \
                 patch.object(enrich.urllib.request, "urlopen", return_value=io.BytesIO(
                     b'data: {"choices":[{"delta":{"reasoning_content":"thinking","content":"result"},"finish_reason":"stop"}]}\n'
                     b'data: [DONE]\n')) as urlopen:
                reasoning, content, stats = enrich.stream_llm_assessment(
                    "prompt", backend=backend, model=model, quiet=True)
                request = urlopen.call_args.args[0]
                self.assertEqual(request.full_url, url)
                self.assertEqual(request.get_header("Authorization"), auth)
                payload = json.loads(request.data)
                self.assertEqual(payload["model"], expected_model)
                self.assertTrue(payload["stream"])
                self.assertNotIn("reasoning_effort", payload)
                self.assertEqual((reasoning, content), ("thinking", "result"))
                self.assertTrue(stats["done_received"])

    def test_strata_does_not_require_hgx_configuration(self):
        with patch.object(enrich, "HGX_URL", ""), \
             patch.object(enrich, "HGX_API_KEY", ""), \
             patch.object(enrich, "STRATA_URL", "http://localhost:9999/v1"), \
             patch.object(enrich, "STRATA_API_KEY", ""), \
             patch.object(enrich.urllib.request, "urlopen", return_value=io.BytesIO(b'data: [DONE]\n')):
            enrich.stream_llm_assessment("prompt", backend="strata", quiet=True)

    def test_missing_strata_url_fails_without_requesting_hgx(self):
        with patch.object(enrich, "STRATA_URL", ""), \
             patch.object(enrich.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(RuntimeError, "STRATA_URL must be set"):
                enrich.stream_llm_assessment("prompt", backend="strata", quiet=True)
            urlopen.assert_not_called()

    def test_runner_passes_backend_options_only_to_llm_step(self):
        for step_id in ("1d", "1e"):
            with self.subTest(step_id=step_id), \
                 patch.object(runner.importlib, "import_module") as load, \
                 patch.object(runner.importlib, "reload"), \
                 patch("sys.argv", []), contextlib.redirect_stdout(io.StringIO()):
                seen = []
                load.return_value.main.side_effect = lambda: seen.extend(sys.argv)
                self.assertTrue(runner.run_step(step_id, "fake_module", "test", "artemis-ii",
                                                "strata", "custom-model", ["old-model"], True))
                expected = ["run_all.py", "--mission", "artemis-ii"]
                if step_id == "1d":
                    expected += ["--backend", "strata", "--model", "custom-model",
                                 "--reassess-model", "old-model", "--hide-reasoning"]
                self.assertEqual(seen, expected)

    def test_runner_cli_force_reaches_only_enrichment(self):
        for force_args in ([], ["--force"]):
            reasoning_args = ["--extraction-reasoning-effort", "medium", "--reasoning-effort", "low"]
            with self.subTest(force_args=force_args), \
                 patch.object(runner, "MISSIONS", {"artemis-ii": self.mission}), \
                 patch.object(runner.importlib, "import_module") as load, \
                 patch.object(runner.importlib, "reload"), \
                 patch.object(sys, "argv", ["run_all.py", "--mission", "artemis-ii", "--step", "1d", "1e", *force_args, *reasoning_args]), \
                 contextlib.redirect_stdout(io.StringIO()):
                seen = []
                load.return_value.main.side_effect = lambda: seen.append(sys.argv.copy())
                runner.main()
                self.assertEqual(seen, [
                    ["run_all.py", "--mission", "artemis-ii", "--backend", "hgx", *force_args, *reasoning_args],
                    ["run_all.py", "--mission", "artemis-ii"],
                ])

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

    def fixture_extraction(self, rows, target_batch):
        """Build a complete extraction while preserving each fixture's discoveries."""
        statements = []
        developments = []
        row_by_index = {row["i"]: row for row in rows}
        for index, utterance in enumerate(target_batch):
            row = row_by_index.get(index, {})
            statements.append({
                "indices": [index], "kind": "report", "summary": utterance["text"],
                "crew": row.get("crew", context.infer_crew_members(utterance["text"])),
            })
            for milestone_index, item in enumerate(row.get("milestones", [])):
                developments.append({
                    **item, "id": f"development_{index}_{milestone_index}",
                    "thread_id": "fixture", "report_indices": [index], "anchor_i": index,
                    "crew": item.get("crew", []),
                })
        return {
            "threads": [{"id": "fixture", "subject": "Fixture mission reports", "statements": statements}],
            "developments": developments,
        }

    def run_cli(self, rows, *args, extraction=None, repair_extraction=None,
                extraction_stats=None, label_error=None, label_stats=None,
                keep_label_extras=False, expected_exit=None):
        original_assessor = enrich.staged_llm_assessment
        target_batch = []
        extraction_calls = 0

        def assess_batch(**kwargs):
            nonlocal target_batch, extraction_calls
            target_batch = kwargs["target_batch"]
            extraction_calls = 0
            return original_assessor(**kwargs)

        def response(**kwargs):
            nonlocal extraction_calls
            if kwargs["output_format"] == "JSON":
                extraction_calls += 1
                structure = extraction if extraction is not None else self.fixture_extraction(rows, target_batch)
                if extraction_calls > 1 and repair_extraction is not None:
                    structure = repair_extraction
                content = json.dumps(structure)
            else:
                if label_error is not None:
                    raise label_error
                content = "\n".join(json.dumps({key: value for key, value in row.items()
                                              if keep_label_extras or key not in {"crew", "milestones"}})
                                    for row in rows)
            stats = {"elapsed_s": 1, "ttft_s": 0.1, "completion_tokens": 100,
                     "prompt_tokens": 50, "tok_per_s": 100,
                     "finish_reason": "stop", "done_received": True}
            if kwargs["output_format"] == "JSONL":
                stats.update(label_stats or {})
            elif extraction_calls == 1:
                stats.update(extraction_stats or {})
            return "", content, stats

        with patch.object(enrich, "MISSIONS", {"test": self.mission}), \
             patch.object(enrich, "staged_llm_assessment", side_effect=assess_batch), \
             patch.object(enrich, "stream_llm_assessment", side_effect=response) as stream, \
             patch("sys.argv", ["1d", "--mission", "test", "--quiet", *args]), \
             contextlib.redirect_stdout(io.StringIO()):
            if expected_exit is None:
                enrich.main()
            else:
                with self.assertRaises(SystemExit) as raised:
                    enrich.main()
                self.assertEqual(raised.exception.code, expected_exit)
        return stream

    def read_milestones(self):
        path = self.mission.processed_transcripts / "detected_milestones.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_cli_invalid_extraction_gets_one_correction_then_stops_without_labeling(self):
        (self.mission.web_dir / "PAO.csv").write_text(
            f"2026-04-01T18:00:00Z|{self.text}\n2026-04-01T18:01:00Z|Hatch closed.\n")
        rows = [assessment(0, [milestone(evidence="Invented quote")])]
        stream = self.run_cli(rows, "--test-batches", "1", expected_exit=1)
        self.assertEqual(stream.call_count, 2)
        self.assertTrue(all(call.kwargs["output_format"] == "JSON" for call in stream.call_args_list))
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        detail = json.loads((run_dir / summary["detail_file"]).read_text(encoding="utf-8"))
        self.assertEqual(summary["parsed"], 0)
        self.assertEqual([x["index"] for x in summary["missing"]], [0, 1])
        self.assertTrue(all(x["uid"].startswith("pao:") for x in summary["missing"]))
        invalid = next(issue for issue in detail["parse_issues"] if issue["reason"] == "invalid_extraction")
        self.assertIn("evidence_not_in_utterance", invalid["error"])
        self.assertIn(self.text, detail["user_prompt"])
        stages = json.loads((run_dir / summary["stages_file"]).read_text(encoding="utf-8"))["stages"]
        self.assertEqual(set(stages), {"extraction", "extraction_repair"})
        self.assertIn("Invented quote", stages["extraction"]["raw_content"])
        self.assertIn("validation_error", stages["extraction"])
        self.assertIn("Invented quote", stages["extraction_repair"]["raw_content"])
        # Rejected and omitted utterances stay pending for resume.
        self.assertEqual(enrich.load_existing_ledger(
            self.mission.processed_transcripts / "enriched_utterances.jsonl"), {})

    def test_cli_successful_extraction_correction_records_all_three_requests(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        rows = [assessment(0, [milestone()])]
        repaired = self.fixture_extraction(rows, enrich.load_all_utterances(self.mission))
        stream = self.run_cli(rows, extraction={"threads": [], "developments": []},
                              repair_extraction=repaired)
        self.assertEqual(stream.call_count, 3)
        self.assertEqual([call.kwargs["output_format"] for call in stream.call_args_list],
                         ["JSON", "JSON", "JSONL"])
        self.assertEqual(stream.call_args_list[1].kwargs["reasoning_effort"], "low")
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(summary["stats"]["completion_tokens"], 300)
        self.assertEqual(summary["stats"]["prompt_tokens"], 150)
        self.assertEqual(summary["stats"]["elapsed_s"], 3)
        self.assertEqual(summary["issue_counts"]["repaired_extraction"], 1)
        stages = json.loads((run_dir / summary["stages_file"]).read_text(encoding="utf-8"))["stages"]
        self.assertEqual(set(stages), {"extraction", "extraction_repair", "labeling"})
        original_error = stages["extraction"]["validation_error"]
        self.assertIn(original_error, stages["extraction_repair"]["user_prompt"])
        self.assertIn(self.text, stages["extraction_repair"]["user_prompt"])
        correction = json.loads(stages["extraction_repair"]["user_prompt"].rsplit("\n\n", 1)[1])
        self.assertEqual(correction["previous_extraction"], stages["extraction"]["raw_content"])
        self.assertEqual(correction["validation_error"], original_error)
        self.assertEqual(stages["extraction_repair"]["extraction"], repaired)
        self.assertEqual(json.loads(stages["labeling"]["user_prompt"])["extraction"], repaired)
        record, = enrich.load_existing_ledger(
            self.mission.processed_transcripts / "enriched_utterances.jsonl").values()
        self.assertEqual(len(record["milestones"]), 1)
        self.assertEqual(set(record["stage_provenance"]), set(stages))
        for stage_name, call in zip(("extraction", "extraction_repair", "labeling"), stream.call_args_list):
            provenance = record["stage_provenance"][stage_name]
            self.assertEqual(provenance["user_prompt_sha256"],
                             hashlib.sha256(call.kwargs["user_prompt"].encode()).hexdigest())
            self.assertEqual(provenance["system_prompt_sha256"],
                             hashlib.sha256(call.kwargs["system_prompt"].encode()).hexdigest())

    def test_cli_unclean_extraction_stream_is_not_eligible_for_correction(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        stream = self.run_cli([assessment(0, [milestone()])],
                              extraction_stats={"finish_reason": "length"}, expected_exit=1)
        stream.assert_called_once()
        self.assertEqual(enrich.load_existing_ledger(
            self.mission.processed_transcripts / "enriched_utterances.jsonl"), {})
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        stages = json.loads((run_dir / summary["stages_file"]).read_text(encoding="utf-8"))["stages"]
        self.assertEqual(set(stages), {"extraction"})

    def test_cli_logs_request_failure_and_interruption(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        http_error = enrich.urllib.error.HTTPError(
            "https://example.invalid", 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"rate limit"}'))
        for error in (RuntimeError("connection lost"), KeyboardInterrupt(), http_error):
            with self.subTest(error=type(error).__name__):
                with patch.object(enrich, "MISSIONS", {"test": self.mission}), \
                     patch.object(enrich, "stream_llm_assessment", side_effect=error), \
                     patch("sys.argv", ["1d", "--mission", "test", "--quiet", "--test-batches", "1"]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if isinstance(error, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            enrich.main()
                    else:
                        with self.assertRaises(SystemExit) as raised:
                            enrich.main()
                        self.assertEqual(raised.exception.code, 1)
        logs = list((self.mission.processed_transcripts / "enrichment_diagnostics").glob(
            "*/batch_[0-9][0-9][0-9][0-9][0-9].json"))
        self.assertEqual(len(logs), 3)
        errors = [json.loads(p.read_text(encoding="utf-8"))["error"] for p in logs]
        self.assertEqual({e["type"] for e in errors}, {"RuntimeError", "KeyboardInterrupt", "HTTPError"})
        saved_http_error = next(e for e in errors if e["type"] == "HTTPError")
        self.assertEqual(saved_http_error["http_status"], 429)
        self.assertEqual(saved_http_error["body"], '{"error":"rate limit"}')

    def test_complete_batch_has_summary_without_problem_file(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        self.run_cli([assessment(0, [milestone()])], "--test-batches", "1")
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(summary["parsed"], 1)
        self.assertEqual(summary["missing"], [])
        self.assertNotIn("detail_file", summary)
        stages = json.loads((run_dir / summary["stages_file"]).read_text(encoding="utf-8"))["stages"]
        self.assertEqual(set(stages), {"extraction", "labeling"})
        self.assertEqual(len(stages["extraction"]["extraction"]["developments"]), 1)
        self.assertEqual(summary["stats"]["completion_tokens"], 200)
        self.assertEqual(summary["stats"]["elapsed_s"], 2)

    def test_cli_incomplete_labels_preserve_valid_rows_and_report_failure(self):
        (self.mission.web_dir / "PAO.csv").write_text(
            f"2026-04-01T18:00:00Z|{self.text}\n2026-04-01T18:01:00Z|Hatch closed.\n")
        stream = self.run_cli([assessment(0, [milestone()])], "--test-batches", "1", expected_exit=1)
        self.assertEqual(stream.call_count, 2)
        ledger = enrich.load_existing_ledger(self.mission.processed_transcripts / "enriched_utterances.jsonl")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(next(iter(ledger.values()))["text"], self.text)
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(summary["parsed"], 1)
        self.assertEqual([item["index"] for item in summary["missing"]], [1])
        stages = json.loads((run_dir / summary["stages_file"]).read_text(encoding="utf-8"))["stages"]
        self.assertEqual(set(stages), {"extraction", "labeling"})
        self.assertIn("extraction", stages["extraction"])
        self.assertTrue((run_dir / summary["detail_file"]).exists())

    def test_cli_label_request_failure_retains_extraction_and_no_assessments(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        stream = self.run_cli([assessment(0, [milestone()])], "--test-batches", "1",
                              label_error=RuntimeError("labeling connection lost"), expected_exit=1)
        self.assertEqual(stream.call_count, 2)
        self.assertEqual(enrich.load_existing_ledger(
            self.mission.processed_transcripts / "enriched_utterances.jsonl"), {})
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(summary["error"]["type"], "RuntimeError")
        self.assertEqual(summary["parsed"], 0)
        stages = json.loads((run_dir / summary["stages_file"]).read_text(encoding="utf-8"))["stages"]
        self.assertEqual(len(stages["extraction"]["extraction"]["developments"]), 1)
        self.assertEqual(json.loads(stages["labeling"]["user_prompt"])["extraction"],
                         stages["extraction"]["extraction"])

    def test_cli_complete_labels_with_unclean_stream_keep_rows_but_fail(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        diagnostics_root = self.mission.processed_transcripts / "enrichment_diagnostics"
        for stream_end in ({"finish_reason": "length"}, {"done_received": False}):
            with self.subTest(stream_end=stream_end):
                prior_runs = set(diagnostics_root.glob("*"))
                self.run_cli([assessment(0, [milestone()])], "--test-batches", "1", "--force",
                             label_stats=stream_end, expected_exit=1)
                ledger = enrich.load_existing_ledger(
                    self.mission.processed_transcripts / "enriched_utterances.jsonl")
                self.assertEqual(len(ledger), 1)
                self.assertEqual(len(next(iter(ledger.values()))["milestones"]), 1)
                run_dir, = set(diagnostics_root.glob("*")) - prior_runs
                summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(summary["parsed"], 1)
                self.assertEqual(summary["missing"], [])
                for key, value in stream_end.items():
                    self.assertEqual(summary["stats"][key], value)
                self.assertTrue((run_dir / summary["detail_file"]).exists())

    def test_cli_extra_label_index_fails_even_with_complete_valid_target_rows(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        self.run_cli([assessment(0, [milestone()]), assessment(99, [])], expected_exit=1)
        ledger = enrich.load_existing_ledger(self.mission.processed_transcripts / "enriched_utterances.jsonl")
        self.assertEqual(len(ledger), 1)
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(summary["parsed"], 1)
        self.assertEqual(summary["missing"], [])
        self.assertIn("invalid_label_index", summary["issue_counts"])

    def test_cli_ignored_label_attribution_is_diagnostic_but_not_failure(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        self.run_cli([assessment(0, [milestone()])], keep_label_extras=True)
        ledger = enrich.load_existing_ledger(self.mission.processed_transcripts / "enriched_utterances.jsonl")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(len(next(iter(ledger.values()))["milestones"]), 1)
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        summary = json.loads((run_dir / "batches.jsonl").read_text(encoding="utf-8"))
        self.assertIn("ignored_label_attribution", summary["issue_counts"])

    def test_sparse_reassessment_keeps_each_cached_antecedent_in_separate_batch_context(self):
        texts = ["Context for first pending report.", "First pending report.",
                 "Context for second pending report.", "Second pending report.", "Trailing cached context."]
        (self.mission.web_dir / "PAO.csv").write_text("".join(
            f"2026-04-01T18:0{i}:00Z|{text}\n" for i, text in enumerate(texts)))
        self.run_cli([assessment(i, []) for i in range(len(texts))])
        ledger_path = self.mission.processed_transcripts / "enriched_utterances.jsonl"
        history = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        for index in (1, 3):
            history[index]["model"] = "old-model"
        ledger_path.write_text("".join(json.dumps(row) + "\n" for row in history), encoding="utf-8")

        stream = self.run_cli([assessment(0, [])], "--reassess-model", "old-model")
        extraction_calls = [call for call in stream.call_args_list if call.kwargs["output_format"] == "JSON"]
        labeling_calls = [call for call in stream.call_args_list if call.kwargs["output_format"] == "JSONL"]
        self.assertEqual(len(extraction_calls), 2)
        self.assertEqual(len(labeling_calls), 2)
        for pending_index, extraction_call, labeling_call in zip((1, 3), extraction_calls, labeling_calls):
            targets = json.loads(labeling_call.kwargs["user_prompt"])["targets"]
            self.assertEqual([target["text"] for target in targets], [texts[pending_index]])
            self.assertIn(
                f"[CTX] 2026-04-01T18:0{pending_index - 1}:00Z [PAO] {texts[pending_index - 1]}",
                extraction_call.kwargs["user_prompt"],
            )
        refreshed = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(refreshed), len(history) + 2)

    def test_strata_cli_records_backend_and_model_and_resumes_existing_cache(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        with patch.object(enrich, "STRATA_URL", "http://localhost:9999/v1"), \
             patch.object(enrich, "STRATA_MODEL", "iq3_s"):
            stream = self.run_cli([assessment(0, [milestone()])], "--backend", "strata")
        self.assertEqual(stream.call_args.kwargs["backend"], "strata")
        self.assertEqual(stream.call_args.kwargs["model"], "iq3_s")
        run_dir, = (self.mission.processed_transcripts / "enrichment_diagnostics").iterdir()
        metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["backend"], "strata")
        self.assertEqual(metadata["endpoint"], "http://localhost:9999/v1")
        ledger = enrich.load_existing_ledger(self.mission.processed_transcripts / "enriched_utterances.jsonl")
        record, = ledger.values()
        self.assertEqual(record["model"], "iq3_s")
        self.assertEqual(record["backend"], "strata")
        self.assertEqual(record["run_id"], run_dir.name)
        self.assertEqual(record["batch"], 1)
        self.assertEqual(record["model_provenance"], "request")
        self.assertEqual(record["prompt_version"], enrich.PROMPT_VERSION)
        self.assertEqual(record["system_prompt_sha256"], hashlib.sha256(enrich.SYSTEM_PROMPT.encode()).hexdigest())
        self.assertEqual(record["user_prompt_sha256"], hashlib.sha256(stream.call_args.kwargs["user_prompt"].encode()).hexdigest())
        self.assertEqual(record["generation_settings"], metadata["generation_settings"])
        self.assertEqual(record["generation_settings"], {
            "temperature": 0.2, "reasoning_effort": "low", "max_tokens": 65536})
        self.assertEqual(stream.call_count, 2)
        stages = record["stage_provenance"]
        for name, call in zip(("extraction", "labeling"), stream.call_args_list):
            self.assertEqual(stages[name]["system_prompt_sha256"],
                             hashlib.sha256(call.kwargs["system_prompt"].encode()).hexdigest())
            self.assertEqual(stages[name]["user_prompt_sha256"],
                             hashlib.sha256(call.kwargs["user_prompt"].encode()).hexdigest())
        self.assertEqual(stages["extraction"]["generation_settings"]["reasoning_effort"], "auto")
        self.assertEqual(stages["labeling"]["generation_settings"]["reasoning_effort"], "low")
        self.assertTrue(record["assessed_at"].endswith("+00:00"))
        event, = self.read_milestones()
        self.assertEqual(event["assessment"]["run_id"], run_dir.name)
        self.assertEqual(event["assessment"]["model"], "iq3_s")
        self.run_cli([]).assert_not_called()
        tree = phase_1e.build_mission_activity_hierarchy(self.mission)
        self.assertEqual(tree["summary"]["raw_milestone_detections"], 1)
        self.assertEqual(tree["merged_milestones"][0]["reports"][0]["assessment"], event["assessment"])

    def test_model_targeted_reassessment_preserves_history_and_other_models(self):
        (self.mission.web_dir / "PAO.csv").write_text(
            "2026-04-01T18:00:00Z|First utterance.\n"
            "2026-04-01T18:01:00Z|Second utterance.\n"
            "2026-04-01T18:02:00Z|Third utterance.\n")
        self.run_cli([assessment(i, []) for i in range(3)])
        path = self.mission.processed_transcripts / "enriched_utterances.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[1].update(backend="strata", model="iq3_s")
        records[2].pop("model")
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        self.run_cli([assessment(0, [])], "--reassess-model", "iq3_s")
        history = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(history[:3], records)
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1]["uid"], records[1]["uid"])
        self.assertEqual(history[-1]["model"], enrich.DEFAULT_MODEL)
        self.run_cli([assessment(0, [])], "--reassess-model", "unknown")
        history = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(history), 5)
        self.assertEqual(history[-1]["uid"], records[2]["uid"])
        self.run_cli([], "--reassess-model", "iq3_s", "unknown").assert_not_called()

    def test_prompt_revision_and_settings_distinguish_later_pass_without_schema_change(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        self.run_cli([assessment(0, [])])
        revised_prompt_version = enrich.PROMPT_VERSION + 1
        with patch.object(enrich, "SYSTEM_PROMPT", enrich.SYSTEM_PROMPT + "\nRevised instructions."), \
             patch.object(enrich, "PROMPT_VERSION", revised_prompt_version):
            self.run_cli([assessment(0, [])], "--force", "--reasoning-effort", "low", "--max-tokens", "4096")
        path = self.mission.processed_transcripts / "enriched_utterances.jsonl"
        old, new = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(old["assessment_version"], new["assessment_version"])
        self.assertNotEqual(old["run_id"], new["run_id"])
        self.assertNotEqual(old["system_prompt_sha256"], new["system_prompt_sha256"])
        self.assertEqual(new["prompt_version"], revised_prompt_version)
        self.assertGreater(new["prompt_version"], old["prompt_version"])
        self.assertEqual(new["generation_settings"]["reasoning_effort"], "low")
        self.assertEqual(new["generation_settings"]["max_tokens"], 4096)

    def test_complete_assessments_with_truncated_stream_still_get_diagnostics(self):
        run_dir = self.mission.processed_transcripts / "diagnostic_test"
        run_dir.mkdir()
        u = {"uid": "test", "t": "2026-04-01T18:00:00Z", "channel": "pao", "text": self.text}
        with contextlib.redirect_stdout(io.StringIO()):
            enrich.write_batch_diagnostics(
                run_dir, 1, [u], "prompt", "response", {"finish_reason": "length", "done_received": True},
                {0: {}}, [], {})
        detail = json.loads((run_dir / "batch_00001.json").read_text(encoding="utf-8"))
        self.assertEqual(detail["missing"], [])
        self.assertEqual(detail["stats"]["finish_reason"], "length")

    def test_cli_migrates_legacy_cache_and_resumes_without_duplicate_events(self):
        (self.mission.web_dir / "PAO.csv").write_text(f"2026-04-01T18:00:00Z|{self.text}\n")
        u = enrich.load_all_utterances(self.mission)[0]
        ledger = self.mission.processed_transcripts / "enriched_utterances.jsonl"
        ledger.write_text(json.dumps({**u, "event_id": "new:walkout"}) + "\n")
        rows = [assessment(0, [milestone(), milestone("Departure for pad", "They are departing for the pad.")])]
        stream = self.run_cli(rows, "--test-batches", "1")
        self.assertEqual(stream.call_count, 2)
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
