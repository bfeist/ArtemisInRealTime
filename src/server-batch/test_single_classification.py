"""Constrained classification, canonical pipeline and interrupted-run regressions."""
import argparse
import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from config import MissionConfig

classifier = importlib.import_module("1_comm.single_classification")
stage = importlib.import_module("1_comm.1d_enrich_transcripts")
discovery = importlib.import_module("1_comm.1e_discover_history")
investigate = importlib.import_module("1_comm.1f_investigate_history")
common = importlib.import_module("1_comm.history_common")
hierarchy = importlib.import_module("1_comm.1g_mission_context")
timeline = importlib.import_module("1_comm.1h_web_timeline")
transcripts = importlib.import_module("1_comm.1i_web_transcripts")
runner = importlib.import_module("run_all")


def row(i, **extra):
    return {"i": i, "imp": 2, "cat": "systems", "tags": ["camera"], "topic": "Camera status",
            "kinds": ["report" if i == 0 else "acknowledgment"], "thread": "camera_power", "pao_type": "not_applicable", **extra}


def pao_row(i, **extra):
    return row(i, **{"pao_type": "commentary", **extra})


class Client:
    settings = {"backend": "hgx", "model": "LLM-Alpha-512K", "reasoning_effort": "low", "max_tokens": 16384}

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def request(self, system, prompt, key, response_format=None):
        try:
            payload = json.loads(prompt)
        except ValueError:
            payload = prompt
        self.calls.append({"system": system, "prompt": payload, "schema": response_format})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return json.dumps(reply)


class SingleClassificationTest(unittest.TestCase):
    def setUp(self):
        self.targets = [
            {"uid": "question", "channel": "comm", "src": "first_oe1.wav", "t": "2026-04-01T17:00:00Z", "text": "The cameras are powered on."},
            {"uid": "answer", "channel": "comm", "src": "second_oe1.wav", "t": "2026-04-01T17:00:01Z", "text": "I sure did."},
        ]

    def parse(self, rows):
        return classifier.parse(json.dumps({"labels": rows}), self.targets)

    def test_subject_and_contribution_are_independent_and_source_is_metadata(self):
        parsed, issues = self.parse([row(0), row(1, imp=1)])
        self.assertEqual(issues, [])
        self.assertEqual(parsed[1]["category"], "systems")
        self.assertEqual(parsed[1]["statement_kinds"], ["acknowledgment"])
        self.assertEqual(parsed[1]["source"], "OE-1")
        self.assertEqual(parsed[1]["thread_context"][0]["source_uids"], ["question"])
        self.assertNotIn("milestones", parsed[1])
        self.assertNotIn("crew", parsed[1])

    def test_invalid_rows_remain_pending_without_guessing_categories(self):
        for extra in ({"cat": "pao_filler"}, {"tags": []}, {"kinds": ["social"]},
                      {"crew": ["koch"]}, {"milestones": []}, {"imp": True},
                      {"cat": "unspecified", "tags": []}, {"cat": "social", "tags": []}):
            with self.subTest(extra=extra):
                parsed, issues = self.parse([row(0), row(1, **extra)])
                self.assertEqual(set(parsed), {0})
                self.assertTrue(issues)

    def test_isolated_unspecified_is_valid_and_different_stream_does_not_supply_subject(self):
        self.targets[1]["src"] = "different_oe2.wav"
        parsed, issues = self.parse([row(0), row(1, cat="unspecified", tags=[])])
        self.assertEqual(issues, [])
        self.assertEqual(parsed[1]["category"], "unspecified")
        self.assertEqual(parsed[1]["thread_context"][0]["source_uids"], [])

    def test_duplicate_missing_and_out_of_range_indices_cannot_fake_coverage(self):
        parsed, issues = self.parse([row(0), row(0), row(True), row(20)])
        self.assertEqual(parsed, {})
        self.assertTrue(any(i["reason"] == "duplicate_classification_index" for i in issues))

    def test_countdown_requires_plan_and_is_exposed_as_hint(self):
        self.targets[1]["text"] = "80 seconds to apogee raised burn ignition."
        parsed, issues = self.parse([row(0), row(1, kinds=["report"])])
        self.assertNotIn(1, parsed)
        self.assertTrue(any(i["reason"] == "countdown_requires_plan" for i in issues))
        parsed, _ = self.parse([row(0), row(1, kinds=["plan"])])
        self.assertIn(1, parsed)
        prompt = json.loads(classifier.build_prompt(self.targets, []))
        self.assertEqual(prompt["targets"][1]["temporal_hint"], "future_countdown")
        self.assertFalse(classifier.future_countdown("The countdown started Monday."))

    def test_only_rejected_rows_are_retried_with_original_context(self):
        replies = [json.dumps({"labels": [row(0), row(1, tags=[])]}),
                   json.dumps({"labels": [row(0, kinds=["acknowledgment"])]})]
        calls = []

        def request(system, prompt, schema, attempt):
            calls.append((json.loads(prompt), schema))
            return replies.pop(0)

        parsed, issues = classifier.assess(self.targets, [], request)
        self.assertEqual(set(parsed), {"question", "answer"})
        self.assertEqual([r["uid"] for r in calls[1][0]["targets"]], ["answer"])
        self.assertEqual(calls[1][1]["json_schema"]["schema"]["properties"]["labels"]["minItems"], 1)
        self.assertIn("question", {r["uid"] for r in calls[1][0]["context_only"]})

    def test_observation_and_explicit_suspected_cause_keep_both_kinds(self):
        self.targets[1]["text"] = "I think the tape is drowning out lunar color. I am not able to see that color."
        parsed, issues = self.parse([row(0), row(1, kinds=["report"])])
        self.assertNotIn(1, parsed)
        self.assertEqual(issues[0]["required_kinds"], ["hypothesis", "report"])
        parsed, issues = self.parse([row(0), row(1, kinds=["report", "hypothesis"])])
        self.assertIn(1, parsed)
        self.assertFalse(issues)
        self.assertIsNone(classifier.epistemic_hint("I think the view is beautiful."))

    def test_transport_forwards_schema_and_hgx_model(self):
        enrich = common.transcript
        schema = classifier.response_format(2)
        with patch.object(enrich, "HGX_URL", "https://hgx.invalid/v1"), patch.object(enrich, "HGX_API_KEY", "fixture"), \
             patch.object(enrich.urllib.request, "urlopen", return_value=io.BytesIO(
                 b'data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n'
                 b'data: [DONE]\n')) as opened:
            enrich.stream_llm_assessment("test", backend="hgx", model="LLM-Alpha-512K", quiet=True,
                                         response_format=schema, reasoning_effort="low")
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://hgx.invalid/v1/chat/completions")
        self.assertEqual(payload["response_format"], schema)
        self.assertEqual(payload["model"], "LLM-Alpha-512K")
        self.assertEqual(payload["reasoning_effort"], "low")

    def test_social_turn_can_have_its_own_thread(self):
        self.targets[1]["text"] = "Happy birthday!"
        parsed, issues = self.parse([row(0), row(1, cat="social", tags=[], thread="birthday")])
        self.assertFalse(issues)
        self.assertEqual(parsed[1]["category"], "social")

    def test_errors_identify_each_rejected_field(self):
        parsed, issues = self.parse([row(0), row(1, tags=[], topic="", imp=True, kinds=[{}])])
        self.assertEqual(set(parsed), {0})
        self.assertEqual(set(issues[0]["fields"]), {"tags", "topic", "imp", "kinds"})
        self.assertEqual(issues[0]["uid"], "answer")

    def test_subject_check_survives_partial_acceptance_and_retry(self):
        client = Client({"labels": [row(0), row(1, tags=[])]},
                        {"labels": [row(0, cat="social", tags=[], kinds=["acknowledgment"])]})
        def request(system, prompt, schema, attempt):
            return client.request(system, prompt, str(attempt), response_format=schema)
        accepted, errors = classifier.assess(self.targets, [], request)
        self.assertEqual(set(accepted), {"question"})
        self.assertEqual(errors[0]["reason"], "lost_thread_subject")

    def test_sources_never_share_thread_links(self):
        targets = [{**self.targets[0], "uid": str(i), "channel": channel, "src": src}
                   for i, (channel, src) in enumerate([
                       ("pao", ""), ("comm", "first_oe1.wav"),
                       ("comm", "first_oe2.wav"), ("comm", "pcd/test.m4a")])]
        parsed, issues = classifier.parse(json.dumps({"labels": [row(i, pao_type="commentary" if i == 0 else "not_applicable") for i in range(4)]}), targets)
        self.assertFalse(issues)
        self.assertEqual([r["source"] for r in parsed.values()], ["PAO", "OE-1", "OE-2", "PCD"])
        self.assertTrue(all(not r["thread_context"][0]["source_uids"] for r in parsed.values()))

    def test_pao_type_is_independent_of_subject_and_never_assigned_to_comm(self):
        self.targets[0]["channel"] = "pao"
        parsed, issues = self.parse([pao_row(0, cat="mission_background", pao_type="filler"), row(1)])
        self.assertFalse(issues)
        self.assertEqual((parsed[0]["category"], parsed[0]["pao_type"]), ("mission_background", "filler"))
        self.assertNotIn("pao_type", parsed[1])
        parsed, issues = self.parse([pao_row(0), row(1, pao_type="filler")])
        self.assertEqual(set(parsed), {0})
        self.assertIn("pao_type", issues[0]["fields"])

    def test_pao_requires_broadcast_type_but_cannot_use_it_as_subject(self):
        self.targets[0]["channel"] = "pao"
        for extra in ({"pao_type": "not_applicable"}, {"pao_type": "invented"}, {"cat": "pao_filler"}):
            with self.subTest(extra=extra):
                parsed, issues = self.parse([pao_row(0, **extra), row(1)])
                self.assertNotIn(0, parsed)
                self.assertTrue(issues)


class EmptyContext:
    def __init__(self, mission=None):
        self.events, self.planned_blocks, self.crew_photos = [], [], []

    def query_window(self, *args):
        return []

    def query_event_ids(self, *args):
        return set()


class CanonicalPipelineTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        for key in ("DATA_DIR", "BULK_ASSETS_DIR", "PDS_ASSETS_DIR", "YT_VIDEO_DIR"):
            storage = patch("config." + key, self.base / key)
            storage.start()
            self.addCleanup(storage.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test")
        self.mission.ensure_dirs()
        self.mission.transcript_inputs.mkdir(parents=True)
        (self.mission.transcript_inputs / "PAO.csv").write_text(
            "2026-04-01T17:00:00Z|The cameras are powered on.\n"
            "2026-04-01T17:00:01Z|Copy.\n", encoding="utf-8")
        self.original = common.transcript.load_all_utterances(self.mission)
        self.options = argparse.Namespace(backend="hgx", batch_size=50, context_lines=5,
            limit=None, max_rounds=5, reference_jsonl=[], retry_unresolved=False)
        for obj, name, value in ((common.transcript, "MissionContextIndex", EmptyContext),
                                 (hierarchy, "MissionContextIndex", EmptyContext),
                                 (investigate, "reference_pdfs", lambda: [])):
            mocked = patch.object(obj, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        # Unexpected network calls must fail; every model interaction uses fixtures.
        network = patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected live request"))
        network.start()
        self.addCleanup(network.stop)

    def run_stage(self, fn, client):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(self.mission, self.options, client)

    def classify(self):
        return self.run_stage(stage.classify, Client({"labels": [pao_row(0), pao_row(1)]}))

    def extraction(self):
        return {"threads": [{"id": "camera", "subject": "Camera power", "statements": [
            {"indices": [0], "kind": "report", "summary": "Power confirmed", "crew": []},
            {"indices": [1], "kind": "acknowledgment", "summary": "Copy", "crew": []}]}],
            "developments": [{"id": "power", "thread_id": "camera", "report_indices": [0],
                "anchor_i": 0, "title": "Camera power confirmed", "status": "confirmed",
                "evidence": "cameras are powered on", "crew": []}]}

    def finish(self):
        claim = {"claim": "Camera power confirmed", "dimension": "event", "verdict": "supported",
            "citations": [{"source_id": "u:" + self.original[0]["uid"],
                           "quote": "cameras are powered on", "stance": "support"}]}
        return {"action": "finish", "result": {"verdict": "supported", "reason": "Direct report",
            "claims": [claim], "duplicate_of": None, "events": [{"title": "Camera power confirmed",
                "status": "confirmed", "crew": [], "anchor_source_id": "u:" + self.original[0]["uid"],
                "event_time": None, "claims": [claim]}]}}

    def labels(self, root):
        return [{k: r[k] for k in ("category", "importance", "topic", "tags", "source",
                                   "statement_kinds", "thread_context", "pao_type")}
                for r in common.read_jsonl(root / "utterances.jsonl")]

    def test_fresh_pipeline_classifies_once_and_publishes_verified_history(self):
        classified = self.classify()
        self.assertTrue(classified["complete"])
        labels = self.labels(self.mission.transcript_enrichment)
        dc = Client(self.extraction())
        self.assertTrue(self.run_stage(discovery.discover, dc)["complete"])
        ic = Client({"action": "search", "query": "cameras"}, self.finish())
        result = self.run_stage(investigate.run_investigations, ic)
        self.assertEqual(result["verified_events"], 1)
        for root in (self.mission.transcript_discovery, self.mission.transcript_investigation):
            self.assertEqual(self.labels(root), labels)
        self.assertEqual(len(dc.calls), 1)
        self.assertEqual([c["prompt"]["task"]["type"] for c in ic.calls], ["event", "event"])
        self.assertTrue(all(c["schema"] is None for c in dc.calls + ic.calls))
        tree = hierarchy.build_mission_activity_hierarchy(self.mission)
        common.atomic_json(self.mission.transcript_hierarchy / "mission_activity_tree.json", tree)
        timeline.publish_timeline(self.mission)
        transcripts.publish_transcripts(self.mission)
        self.assertEqual(tree["summary"]["total_assessed_utterances"], 2)
        self.assertEqual(tree["summary"]["merged_unique_milestones"], 1)
        release = json.loads((self.mission.processed_transcripts / "1i_web_transcripts/publication_report.json").read_text())["release_path"]
        public = json.loads((Path(release) / "transcripts.json").read_text())
        self.assertEqual(public["pao"][1]["category"], "systems")
        self.assertTrue((self.mission.web_dir / "timeline.json").exists())
        for fn in (stage.classify, discovery.discover, investigate.run_investigations):
            idle = Client()
            self.assertTrue(self.run_stage(fn, idle)["complete"])
            self.assertFalse(idle.calls)

    def test_interruption_saves_valid_labels_before_retry_and_resumes_only_missing(self):
        sentinel = self.mission.web_dir / "transcripts.json"
        sentinel.write_text("previous publication")
        interrupted = Client({"labels": [pao_row(0), pao_row(1, tags=[])]}, KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_stage(stage.classify, interrupted)
        checkpoint, = self.mission.transcript_enrichment.glob("work/*/batch_*.json")
        self.assertEqual(set(json.loads(checkpoint.read_text())), {self.original[0]["uid"]})
        self.assertFalse((self.mission.transcript_enrichment / "report.json").exists())
        resumed = Client({"labels": [pao_row(0, kinds=["acknowledgment"])]})
        self.assertTrue(self.run_stage(stage.classify, resumed)["complete"])
        self.assertEqual([r["uid"] for r in resumed.calls[0]["prompt"]["targets"]],
                         [self.original[1]["uid"]])
        self.assertEqual(sentinel.read_text(), "previous publication")

    def test_discovery_interruption_keeps_valid_extractions_and_never_changes_labels(self):
        self.classify()
        self.options.batch_size = 1
        first = self.extraction()
        first["threads"][0]["statements"].pop()
        client = Client(first, KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_stage(discovery.discover, client)
        second = {"threads": [{"id": "copy", "subject": "Camera power", "statements": [
            {"indices": [0], "kind": "acknowledgment", "summary": "Copy", "crew": []}]}], "developments": []}
        resumed = Client(second)
        self.assertTrue(self.run_stage(discovery.discover, resumed)["complete"])
        self.assertEqual(len(resumed.calls), 1)
        self.assertNotIn("[0] 2026-04-01T17:00:00Z", resumed.calls[0]["prompt"])
        self.assertEqual(self.labels(self.mission.transcript_discovery), self.labels(self.mission.transcript_enrichment))

    def test_failed_discovery_splits_and_resume_reuses_successful_groups(self):
        self.classify()
        first = self.extraction()
        first["threads"][0]["statements"].pop()
        client = Client({"threads": [], "developments": []}, first, RuntimeError("offline"))
        self.assertFalse(self.run_stage(discovery.discover, client)["complete"])
        second = {"threads": [{"id": "copy", "subject": "Camera power", "statements": [
            {"indices": [0], "kind": "acknowledgment", "summary": "Copy", "crew": []}]}], "developments": []}
        resumed = Client(second)
        self.assertTrue(self.run_stage(discovery.discover, resumed)["complete"])
        self.assertEqual(len(resumed.calls), 1)
        idle = Client()
        self.assertTrue(self.run_stage(discovery.discover, idle)["complete"])
        self.assertFalse(idle.calls)

    def test_model_failure_blocks_downstream_then_resumes_verification_without_classifying(self):
        self.classify()
        self.run_stage(discovery.discover, Client(self.extraction()))
        self.assertFalse(self.run_stage(investigate.run_investigations, Client(RuntimeError("offline")))["complete"])
        with self.assertRaisesRegex(RuntimeError, "missing"):
            hierarchy.build_mission_activity_hierarchy(self.mission)
        resumed = Client({"action": "search", "query": "cameras"}, self.finish())
        self.assertTrue(self.run_stage(investigate.run_investigations, resumed)["complete"])
        self.assertEqual([c["prompt"]["task"]["type"] for c in resumed.calls], ["event", "event"])

    def test_limit_does_not_publish_an_incomplete_dataset(self):
        self.options.batch_size, self.options.limit = 1, 1
        first = Client({"labels": [pao_row(0)]})
        self.assertFalse(self.run_stage(stage.classify, first)["complete"])
        with self.assertRaisesRegex(RuntimeError, "missing"):
            self.run_stage(discovery.discover, Client())
        resumed = Client({"labels": [pao_row(0, kinds=["acknowledgment"])]})
        self.assertTrue(self.run_stage(stage.classify, resumed)["complete"])
        self.assertEqual(len(resumed.calls), 1)

    def test_runner_uses_canonical_stages_after_both_source_inputs(self):
        ids = [s[0] for s in runner.STEPS]
        self.assertEqual(ids[ids.index("1d"):ids.index("1i") + 1], ["1d", "1e", "1f", "1g", "1h", "1i"])
        self.assertLess(ids.index("2k"), ids.index("1d"))
        self.assertLess(ids.index("1c"), ids.index("1d"))
        self.assertLess(ids.index("4e"), ids.index("1d"))
        self.assertLess(ids.index("6a"), ids.index("1d"))
        self.assertEqual(runner.STEP_GROUPS["comm-history"], ["1d", "1e", "1f", "1g", "1h", "1i"])
        self.assertNotIn("1da", ids)
        self.assertNotIn("1ea", ids)

    def test_runner_executes_fresh_history_group_with_mocked_hgx(self):
        replies = [{"labels": [pao_row(0), pao_row(1)]}, self.extraction(),
                   {"action": "search", "query": "cameras"}, self.finish()]
        calls = []
        def stream(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return "", json.dumps(replies.pop(0)), {"finish_reason": "stop", "done_received": True}
        reuse = importlib.import_module("1_comm.assessment_reuse")
        plan = self.base / "work-plan.json"
        plan.write_text(json.dumps({"contract":"transcript-impact-v1", "stages":["1d","1e","1f"],
            "max_requests":10, "semantic_revision":reuse.digest([reuse.semantic(r) for r in self.original])}))
        with patch.dict("config.MISSIONS", {"artemis-ii": self.mission}), \
             patch("mission_context.MissionContextIndex", EmptyContext), \
             patch("history_sources.reference_pdfs", return_value=[]), \
             patch.object(common.transcript, "stream_llm_assessment", side_effect=stream), \
             patch.object(sys, "argv", ["run_all.py", "--mission", "artemis-ii", "--step", "comm-history",
                 "--backend", "hgx", "--model", "LLM-Alpha-512K", "--reasoning-effort", "low",
                 "--work-plan", str(plan), "--history-limit", "10"]), \
             contextlib.redirect_stdout(io.StringIO()):
            runner.main()
        self.assertFalse(replies)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(k["backend"] == "hgx" and k["reasoning_effort"] == "low" for _, k in calls))
        self.assertEqual(calls[0][1]["response_format"], classifier.response_format(2))
        self.assertTrue((self.mission.web_dir / "timeline.json").exists())
        report = json.loads((self.mission.processed_transcripts / "1i_web_transcripts/publication_report.json").read_text())
        self.assertTrue((Path(report["release_path"]) / "transcripts.json").exists())
        self.assertFalse((self.mission.web_dir / "active-release.json").exists())

    def test_interrupted_verification_retains_completed_event_tasks(self):
        source = self.mission.transcript_inputs / "PAO.csv"
        source.write_text(source.read_text() + "2026-04-01T17:00:02Z|Recording has started.\n", encoding="utf-8")
        self.original = common.transcript.load_all_utterances(self.mission)
        self.run_stage(stage.classify, Client({"labels": [pao_row(0), pao_row(1), pao_row(2, kinds=["report"])]}))
        extraction = self.extraction()
        extraction["threads"].append({"id": "recording", "subject": "Recording", "statements": [
            {"indices": [2], "kind": "report", "summary": "Recording started", "crew": []}]})
        extraction["developments"].append({"id": "recording", "thread_id": "recording", "report_indices": [2],
            "anchor_i": 2, "title": "Recording started", "status": "started", "evidence": "Recording has started", "crew": []})
        self.run_stage(discovery.discover, Client(extraction))
        first = Client({"action": "search", "query": "cameras"}, self.finish(), KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_stage(investigate.run_investigations, first)
        saved, = self.mission.transcript_investigation.glob("work/*/task_*.json")
        saved_bytes = saved.read_bytes()
        finish = self.finish()
        claim = {"claim": "Recording started", "dimension": "event", "verdict": "supported", "citations": [
            {"source_id": "u:" + self.original[2]["uid"], "quote": "Recording has started", "stance": "support"}]}
        finish["result"]["claims"] = [claim]
        finish["result"]["events"][0].update(title="Recording started", status="started", claims=[claim],
            anchor_source_id="u:" + self.original[2]["uid"])
        resumed = Client({"action": "search", "query": "Recording"}, finish)
        report = self.run_stage(investigate.run_investigations, resumed)
        self.assertEqual(report["verified_events"], 2)
        self.assertEqual(saved.read_bytes(), saved_bytes)
        self.assertEqual(len(resumed.calls), 2)
        self.assertTrue(all(c["prompt"]["task"]["candidate"]["title"] == "Recording started" for c in resumed.calls))

    def test_changed_sources_block_consumers_without_modifying_publication(self):
        self.classify()
        self.run_stage(discovery.discover, Client(self.extraction()))
        sentinel = self.mission.web_dir / "transcripts.json"
        sentinel.write_text("completed publication")
        source = self.mission.transcript_inputs / "PAO.csv"
        source.write_text(source.read_text() + "2026-04-01T18:00:00Z|New material\n")
        with self.assertRaisesRegex(RuntimeError, "input changed"):
            transcripts.publish_transcripts(self.mission)
        with self.assertRaisesRegex(RuntimeError, "input changed"):
            self.run_stage(investigate.run_investigations, Client())
        self.assertEqual(sentinel.read_text(), "completed publication")

    def test_identical_simultaneous_words_retain_distinct_recording_sources(self):
        source = self.mission.transcript_inputs / "comm.csv"
        source.write_text("".join(f"2026-04-01T17:00:00Z|0|1|{src}|Copy.\n" for src in
            ("a_oe1.wav", "b_oe2.wav", "pcd/test.m4a", "a_oe1.wav")), encoding="utf-8")
        utterances = common.transcript.load_all_utterances(self.mission)
        comm = [u for u in utterances if u["channel"] == "comm"]
        self.assertEqual(len(comm), 3)
        self.assertEqual(len({u["uid"] for u in comm}), 3)
        self.assertEqual({common.transcript.transcript_source(u) for u in comm}, {"OE-1", "OE-2", "PCD"})
        self.assertEqual(comm[0]["uid"], common.transcript.make_uid("comm", comm[0]["t"], "Copy."))

    def test_exhausted_event_work_is_retained_and_retried_only_explicitly(self):
        self.classify()
        self.run_stage(discovery.discover, Client(self.extraction()))
        failed = self.run_stage(investigate.run_investigations, Client(*(["invalid JSON"] * 5)))
        self.assertTrue(failed["complete"])
        self.assertEqual((failed["verified_events"], failed["unresolved_events"]), (0, 1))
        idle = Client()
        self.run_stage(investigate.run_investigations, idle)
        self.assertFalse(idle.calls)
        self.options.retry_unresolved = True
        repaired = self.run_stage(investigate.run_investigations,
            Client({"action": "search", "query": "cameras"}, self.finish()))
        self.assertEqual(repaired["snapshot"], failed["snapshot"])
        self.assertEqual((repaired["verified_events"], repaired["unresolved_events"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
