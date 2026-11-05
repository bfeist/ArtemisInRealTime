"""Recovery, evidence investigation, resumption and downstream gating regressions."""
import argparse
import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config import MissionConfig

recover = importlib.import_module("1_comm.1e_recover_enrichment")
investigate = importlib.import_module("1_comm.1f_investigate_history")
hierarchy = importlib.import_module("1_comm.1g_mission_context")
common = importlib.import_module("1_comm.history_common")
sources = importlib.import_module("1_comm.history_sources")
enrich = common.enrich
runner = importlib.import_module("run_all")


class FakeClient:
    settings = {"backend": "test", "model": "fixture"}

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def request(self, system, prompt, key):
        try:
            decoded = json.loads(prompt)
        except json.JSONDecodeError:
            decoded = prompt
        self.calls.append((system, decoded, key))
        if not self.replies:
            raise RuntimeError("No more model fixture responses")
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response) if not isinstance(response, str) else response


class EmptyContext:
    def __init__(self, mission=None):
        self.events, self.planned_blocks, self.crew_photos = [], [], []

    def query_window(self, *args):
        return []

    def query_event_ids(self, *args):
        return set()


def options(**extra):
    return argparse.Namespace(**{**dict(source_run=None, batch_size=25, context_lines=5,
        retry_unresolved=False, limit=None, coverage_size=40, max_rounds=5,
        reference_jsonl=[], follow=False, poll_seconds=0.01), **extra})


class HistoryPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch("config.DATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        pdf_patcher = patch.object(investigate, "reference_pdfs", return_value=[])
        pdf_patcher.start()
        self.addCleanup(pdf_patcher.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test")
        self.mission.ensure_dirs()
        self.root = self.mission.processed_transcripts
        (self.mission.web_dir / "PAO.csv").write_text(
            "2026-04-01T17:00:00Z|We are configured for that conference.\n"
            "2026-04-01T17:01:00Z|We will start the conference later.\n", encoding="utf-8")
        self.utterances = enrich.load_all_utterances(self.mission)
        self.rows = [{**u, "importance": 3, "category": "systems", "tags": ["conference"],
                      "topic": "Conference setup", "crew": [], "milestones": [],
                      "assessment_version": enrich.ASSESSMENT_VERSION, "run_id": "old"}
                     for u in self.utterances]
        self.run = self.root / "enrichment_diagnostics" / "20261101T032509Z_fixture"
        self.run.mkdir(parents=True)

    def diagnostics(self, failed_indices=(0,), complete=True):
        common.atomic_json(self.run / "run.json", {"batches": 1, "run_id": self.run.name})
        missing = [{"uid": self.utterances[i]["uid"], "index": i, "t": self.utterances[i]["t"]} for i in failed_indices]
        summary = {"batch": 1, "expected": 2, "parsed": 2 - len(missing), "missing": missing,
                   "stages_file": "batch_00001_stages.json", "detail_file": "batch_00001.json"}
        common.atomic_jsonl(self.run / "batches.jsonl", [summary] if complete else [])
        common.atomic_jsonl(self.root / "enriched_utterances.jsonl", self.rows)
        return summary

    def extraction(self):
        return {"threads": [{"id": "t1", "subject": "conference", "statements": [
            {"indices": [0], "kind": "report", "summary": "Configured", "crew": []},
            {"indices": [1], "kind": "plan", "summary": "Start later", "crew": []}]}],
            "developments": [{"id": "d1", "thread_id": "t1", "report_indices": [0], "anchor_i": 0,
                "title": "Conference configured", "status": "completed", "evidence": "configured", "crew": []}]}

    def label(self, i=0):
        return {"i": i, "imp": 3, "cat": "systems", "tags": ["conference"], "topic": "Conference configured"}

    def citation(self, index=0, quote="configured", stance="support"):
        return {"source_id": "u:" + self.utterances[index]["uid"], "quote": quote, "stance": stance}

    def claim(self, index=0, quote="configured", verdict="supported", dimension="event"):
        return {"claim": "Conference configured", "dimension": dimension, "verdict": verdict,
                "citations": [self.citation(index, quote, "contradict" if verdict == "contradicted" else "support")]}

    def finish_event(self):
        return {"action": "finish", "result": {"verdict": "corrected", "reason": "Setup does not prove start",
            "claims": [self.claim(1, "will start", "contradicted")], "duplicate_of": None,
            "events": [{"title": "Conference configured", "status": "completed", "crew": [],
                "anchor_source_id": self.citation()["source_id"], "event_time": None, "claims": [self.claim()]}]}}

    def coverage(self):
        return {"action": "finish", "result": {"reviewed_uids": [u["uid"] for u in self.utterances],
            "label_changes": [], "discoveries": [], "unresolved_uids": [], "reason": "Reviewed in context"}}

    def label_reconciliation(self):
        result = self.coverage()
        result["result"]["reviewed_uids"] = [self.utterances[0]["uid"]]
        result["result"]["label_changes"] = [{"uid": self.utterances[0]["uid"], "importance": 3,
            "category": "systems", "tags": ["conference"], "topic": "Conference configured; execution not established",
            "crew": [], "reason": "Global investigation established setup only", "citations": [self.citation()]}]
        return result

    def index(self, candidates=()):
        index = sources.SourceIndex(self.utterances, EmptyContext(), candidates=candidates)
        self.addCleanup(index.close)
        return index

    def publish_recovery(self, candidates=None):
        common.publish(self.root / "recovery", self.rows, candidates or [],
                       {"complete": True, "snapshot": "fixture"})

    def candidate(self, **extra):
        return {"id": "candidate:conference", "title": "Conference underway", "status": "completed",
                "source_uid": self.utterances[0]["uid"], "t": self.utterances[0]["t"], **extra}

    def test_recovery_uses_failure_ids_even_when_current_schema_cache_exists(self):
        self.diagnostics()
        _, _, _, _, pending, _, _, _ = recover.recovery_plan(self.mission)
        self.assertEqual(pending, {self.utterances[0]["uid"]})

    def test_recovery_refuses_a_still_running_or_interrupted_run(self):
        self.diagnostics(complete=False)
        with self.assertRaisesRegex(RuntimeError, "still running or was interrupted"):
            recover.recovery_plan(self.mission)

    def live_diagnostics(self, partial_second=True):
        summary = self.diagnostics()
        summary.update(expected=1, parsed=0)
        common.atomic_json(self.run / "run.json", {"batches": 2, "run_id": self.run.name})
        second = {"batch": 2, "expected": 1, "parsed": 1, "missing": []}
        diagnostic_line = json.dumps(second).encode() + b"\n"
        ledger_line = json.dumps({**self.rows[1], "run_id": self.run.name, "batch": 2}).encode() + b"\n"
        (self.run / "batches.jsonl").write_bytes(json.dumps(summary).encode() + b"\n" + (diagnostic_line[:12] if partial_second else b""))
        (self.root / "enriched_utterances.jsonl").write_bytes(json.dumps(self.rows[0]).encode() + b"\n" +
                                                           (ledger_line[:12] if partial_second else b""))
        return diagnostic_line, ledger_line

    def test_follow_ignores_partial_lines_and_reuses_repairs_when_1d_finishes(self):
        diagnostic_line, ledger_line = self.live_diagnostics()
        extraction = {"threads": [{"id": "t1", "subject": "conference", "statements": [
            {"indices": [0], "kind": "report", "summary": "Configured", "crew": []}]}], "developments": []}
        client = FakeClient([extraction, self.label()])
        before = (self.root / "enriched_utterances.jsonl").read_bytes()
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            live = recover.recover(self.mission, options(follow=True), client)
        self.assertEqual((self.root / "enriched_utterances.jsonl").read_bytes(), before)
        self.assertFalse(live["complete"])
        self.assertEqual(live["completed_batches"], 1)
        self.assertEqual(live["recovery_targets"], 1)
        self.assertEqual(live["awaiting_1d"], 1)
        self.assertEqual(len(client.calls), 2)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            common.require_artifacts(self.root / "recovery")
        with (self.root / "enriched_utterances.jsonl").open("ab") as fh:
            fh.write(ledger_line[12:])
        with (self.run / "batches.jsonl").open("ab") as fh:
            fh.write(diagnostic_line[12:])
        before = (self.root / "enriched_utterances.jsonl").read_bytes()
        resumed = FakeClient()
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            final = recover.recover(self.mission, options(), resumed)
        self.assertTrue(final["complete"])
        self.assertEqual(final["work_snapshot"], live["work_snapshot"])
        self.assertEqual(final["awaiting_1d"], 0)
        self.assertFalse(resumed.calls)
        self.assertEqual((self.root / "enriched_utterances.jsonl").read_bytes(), before)
        common.require_artifacts(self.root / "recovery")

    def test_follow_does_not_process_targets_the_producer_has_not_attempted(self):
        self.diagnostics(complete=False)
        (self.root / "enriched_utterances.jsonl").write_bytes(b'{"uid":"unfinished')
        client = FakeClient()
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = recover.recover(self.mission, options(follow=True), client)
        self.assertFalse(report["complete"])
        self.assertEqual(report["recovery_targets"], 0)
        self.assertEqual(report["awaiting_1d"], 2)
        self.assertFalse(client.calls)

    def test_live_recovery_accepts_producer_appends_during_a_model_request(self):
        diagnostic_line, ledger_line = self.live_diagnostics(partial_second=False)
        extraction = {"threads": [{"id": "t1", "subject": "conference", "statements": [
            {"indices": [0], "kind": "report", "summary": "Configured", "crew": []}]}], "developments": []}
        test = self
        class GrowingClient(FakeClient):
            def request(inner, system, prompt, key):
                if not inner.calls:
                    with (test.root / "enriched_utterances.jsonl").open("ab") as fh:
                        fh.write(ledger_line)
                    with (test.run / "batches.jsonl").open("ab") as fh:
                        fh.write(diagnostic_line)
                return super().request(system, prompt, key)
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            live = recover.recover(self.mission, options(follow=True), GrowingClient([extraction, self.label()]))
            idle = FakeClient()
            final = recover.recover(self.mission, options(follow=True), idle)
        self.assertFalse(live["complete"])
        self.assertTrue(final["complete"])
        self.assertEqual(live["work_snapshot"], final["work_snapshot"])
        self.assertFalse(idle.calls)

    def test_follow_notices_the_final_batch_arriving_during_a_model_request(self):
        diagnostic_line, _ = self.live_diagnostics(partial_second=False)
        calls, sleeps = [], []
        def sweep(mission, args):
            calls.append(args.source_run)
            if len(calls) == 1:
                with (self.run / "batches.jsonl").open("ab") as fh:
                    fh.write(diagnostic_line)
                return {"complete": False, "completed_batches": 1, "total_batches": 2}
            return {"complete": True}
        def pause(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 3:
                self.fail("The final batch was missed")
        with contextlib.redirect_stdout(io.StringIO()):
            report = recover.follow_recovery(self.mission, options(follow=True), sweep, pause)
        self.assertTrue(report["complete"])
        self.assertEqual(calls, [self.run.name, self.run.name])
        self.assertEqual(len(sleeps), 1)

    def test_follow_waits_without_reprocessing_when_no_complete_batch_arrives(self):
        diagnostic_line, _ = self.live_diagnostics(partial_second=False)
        calls, sleeps = [], []
        def sweep(mission, args):
            calls.append(args.retry_unresolved)
            return {"complete": len(calls) == 2, "completed_batches": len(calls), "total_batches": 2}
        def pause(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                with (self.run / "batches.jsonl").open("ab") as fh:
                    fh.write(diagnostic_line)
            if len(sleeps) > 3:
                self.fail("Follow did not make progress")
        with contextlib.redirect_stdout(io.StringIO()):
            recover.follow_recovery(self.mission, options(follow=True, retry_unresolved=True), sweep, pause)
        self.assertEqual(calls, [True, False])
        self.assertEqual(len(sleeps), 2)

    def test_saved_extraction_repairs_only_labels_and_resumes_without_calls(self):
        self.diagnostics()
        common.atomic_json(self.run / "batch_00001_stages.json", {"stages": {
            "extraction": {"extraction": self.extraction()}}})
        common.atomic_json(self.run / "batch_00001.json", {"target_batch": self.utterances,
                                                         "parse_issues": [{"reason": "invalid_label_fields"}]})
        original = (self.root / "enriched_utterances.jsonl").read_bytes()
        client = FakeClient([self.label()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = recover.recover(self.mission, options(), client)
            resumed = FakeClient()
            recover.recover(self.mission, options(), resumed)
        self.assertTrue(report["complete"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(resumed.calls), 0)
        self.assertIn("repair_only_indices", client.calls[0][1])
        rows = common.read_jsonl(self.root / "recovery" / "utterances.jsonl")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["recovery"]["method"], "saved_extraction")
        self.assertEqual(rows[1]["topic"], self.rows[1]["topic"])
        self.assertEqual((self.root / "enriched_utterances.jsonl").read_bytes(), original)
        self.assertFalse((self.mission.web_dir / "PAO_enriched.csv").exists())

    def test_exhausted_recovery_retains_every_source_without_fabricated_labels(self):
        self.diagnostics((0, 1))
        client = FakeClient([RuntimeError("offline")] * 5)
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = recover.recover(self.mission, options(), client)
        self.assertTrue(report["complete"])
        self.assertEqual(report["unresolved"], 2)
        self.assertEqual(len(client.calls), 5)  # Batch, then extraction + label-only for each target
        rows = common.read_jsonl(self.root / "recovery" / "utterances.jsonl")
        self.assertEqual({r["uid"] for r in rows}, {u["uid"] for u in self.utterances})
        self.assertTrue(all(r["importance"] is None and r["category"] == "unassessed" for r in rows))
        self.assertTrue(all(r["recovery"]["previous_assessment"] for r in rows))

    def test_label_only_recovery_keeps_labels_and_defers_missing_developments(self):
        self.diagnostics()
        client = FakeClient([RuntimeError("Extraction failed"), self.label()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = recover.recover(self.mission, options(), client)
        self.assertEqual(report["labels_only_recoveries"], 1)
        row = common.read_jsonl(self.root / "recovery" / "utterances.jsonl")[0]
        self.assertEqual(row["category"], "systems")
        self.assertEqual(row["recovery"]["extraction_status"], "unresolved")
        self.assertEqual(row["milestones"], [])

    def test_limit_is_incomplete_and_recovery_input_mutation_is_detected(self):
        self.diagnostics((0, 1))
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = recover.recover(self.mission, options(batch_size=1, limit=1), FakeClient([RuntimeError("offline")]))
        self.assertFalse(report["complete"])
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            common.require_artifacts(self.root / "recovery")
        self.publish_recovery()
        with (self.root / "recovery" / "utterances.jsonl").open("a") as f:
            f.write("{}\n")
        with self.assertRaisesRegex(RuntimeError, "Artifact changed"):
            common.require_artifacts(self.root / "recovery")

    def test_search_finds_nonlocal_sources_and_read_preserves_channel_context(self):
        distant = {**self.utterances[0], "uid": "distant", "text": "Conference was cancelled hours later.",
                   "t": "2026-04-02T10:00:00Z", "ts": enrich.parse_utc_ts("2026-04-02T10:00:00Z")}
        index = sources.SourceIndex([*self.utterances, distant], EmptyContext())
        self.addCleanup(index.close)
        self.assertEqual(index.search("cancelled")[0]["uid"], "distant")
        self.assertEqual(len(index.read("u:distant", 2, 0)), 3)
        window = index.window(self.utterances[0]["t"], distant["t"], limit=1)
        self.assertEqual(window["total"], 3)
        self.assertEqual(window["next_offset"], 1)

    def test_fabricated_quotes_candidates_and_plans_cannot_establish_events(self):
        index = self.index([self.candidate()])
        supplied = dict(index.docs)
        with self.assertRaisesRegex(ValueError, "absent"):
            investigate.validate_citation(self.citation(quote="conference underway"), supplied)
        with self.assertRaisesRegex(ValueError, "absent"):
            investigate.validate_citation({"source_id": "candidate:conference", "quote": "Conference", "stance": "support"}, supplied)
        supplied["plan"] = {"id": "plan", "kind": "reference", "text": "Conference planned", "planned_only": True}
        with self.assertRaisesRegex(ValueError, "planned"):
            investigate.validate_citation({"source_id": "plan", "quote": "Conference", "stance": "support"}, supplied)

    def test_pdf_sources_have_page_citations_and_do_not_invent_report_times(self):
        import pypdf
        pdf_path = Path(self.tmp.name) / "report.pdf"
        pdf_path.write_bytes(b"fixture")
        text = "Conference completed at 2026-04-01T17:00:00Z."
        reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)])
        with patch.object(pypdf, "PdfReader", return_value=reader):
            index = sources.SourceIndex(self.utterances, EmptyContext(), self.mission, pdf_paths=[(pdf_path, False)])
        self.addCleanup(index.close)
        doc, = index.search("completed", kind="document")
        self.assertIsNone(doc["t"])
        self.assertEqual(doc["page"], 1)
        self.assertTrue(doc["origin"].endswith("#page=1"))
        event_claim = {"claim": "Conference completed", "dimension": "event", "verdict": "supported", "citations": [
            {"source_id": doc["id"], "quote": "Conference completed", "stance": "support"}]}
        result = self.finish_event()["result"]
        result["events"][0].update(anchor_source_id=doc["id"], claims=[event_claim])
        with self.assertRaisesRegex(ValueError, "undated document"):
            investigate.validate_event_result(result, {"candidate": self.candidate()}, index.docs, {})
        result["events"][0]["event_time"] = "2026-04-01T17:00:00Z"
        result["events"][0]["claims"].append({"claim": "Execution time", "dimension": "time", "verdict": "supported",
            "citations": [{"source_id": doc["id"], "quote": "2026-04-01T17:00:00Z", "stance": "support"}]})
        investigate.validate_event_result(result, {"candidate": self.candidate()}, index.docs, {})
        outcome = {"task_id": "event:candidate:conference", "state": "done", "result": result}
        events, _ = investigate.final_events([self.candidate()], {outcome["task_id"]: outcome}, index)
        self.assertIsNone(events[0]["report_utc"])
        self.assertEqual(events[0]["t"], "2026-04-01T17:00:00Z")

    def test_event_requires_separate_crew_time_claims_and_positive_rejection_evidence(self):
        index = self.index()
        task = {"candidate": self.candidate()}
        result = self.finish_event()["result"]
        result["events"][0]["crew"] = ["koch"]
        with self.assertRaisesRegex(ValueError, "crew involvement"):
            investigate.validate_event_result(result, task, index.docs, {})
        result["events"][0]["crew"] = []
        result["events"][0]["event_time"] = self.utterances[0]["t"]
        with self.assertRaisesRegex(ValueError, "time claim"):
            investigate.validate_event_result(result, task, index.docs, {})
        result = {"verdict": "rejected", "reason": "No search hit", "events": [], "claims": [self.claim()]}
        with self.assertRaisesRegex(ValueError, "positive contradicting"):
            investigate.validate_event_result(result, task, index.docs, {})

    def test_coverage_cannot_omit_utterances_or_silently_approve_fallbacks(self):
        index = self.index()
        task = {"targets": [{**self.rows[0], "category": "unassessed"}, self.rows[1]]}
        result = self.coverage()["result"]
        with self.assertRaisesRegex(ValueError, "silently approved"):
            investigate.validate_coverage(result, task, index.docs)
        result["unresolved_uids"] = [self.rows[0]["uid"]]
        investigate.validate_coverage(result, task, index.docs)
        result["reviewed_uids"].pop()
        with self.assertRaisesRegex(ValueError, "every target"):
            investigate.validate_coverage(result, task, index.docs)

    def test_bare_coverage_and_label_results_finish_without_retries(self):
        index = self.index()
        for task_type in ("coverage", "labels"):
            with self.subTest(task_type=task_type):
                task = {"id": task_type + ":fixture", "type": task_type, "targets": self.rows}
                result = self.coverage()["result"]
                client = FakeClient([result])
                outcome = investigate.investigate_task(task, index, client, {}, 2)
                self.assertEqual(outcome["state"], "done")
                self.assertEqual(outcome["result"], result)
                self.assertEqual(len(client.calls), 1)

    def test_bare_event_result_still_requires_original_source_search(self):
        candidate = self.candidate()
        index = self.index([candidate])
        task = {"id": "event:" + candidate["id"], "type": "event", "candidate": candidate}
        result = self.finish_event()["result"]
        client = FakeClient([result, {"action": "search", "query": "conference"}, result])
        outcome = investigate.investigate_task(task, index, client, {candidate["id"]: candidate}, 3)
        self.assertEqual(outcome["state"], "done")
        self.assertEqual(outcome["result"], result)
        self.assertIn("Search the mission", client.calls[1][1]["retrieval_transcript"][-1]["validation_error"])

    def test_bare_result_keeps_citation_and_discovery_validation(self):
        index = self.index()
        task = {"id": "coverage:fixture", "type": "coverage", "targets": self.rows}
        invalid_quote = self.label_reconciliation()["result"]
        invalid_quote["reviewed_uids"] = [r["uid"] for r in self.rows]
        invalid_quote["label_changes"][0]["citations"][0]["quote"] = "invented quotation"
        invalid_discovery = self.coverage()["result"]
        invalid_discovery["discoveries"] = [{"title": "Conference started", "status": "started",
            "crew": [], "source_uid": self.rows[0]["uid"], "evidence": "invented evidence"}]
        for result, expected in ((invalid_quote, "Citation is absent"),
                                 (invalid_discovery, "Discovery needs a valid target")):
            with self.subTest(expected=expected):
                client = FakeClient([result, self.coverage()])
                outcome = investigate.investigate_task(task, index, client, {}, 2)
                self.assertEqual(outcome["state"], "done")
                self.assertIn(expected, client.calls[1][1]["retrieval_transcript"][-1]["validation_error"])
                if expected == "Citation is absent":
                    error = client.calls[1][1]["retrieval_transcript"][-1]["validation_error"]
                    self.assertIn("invented quotation", error)
                    self.assertIn("Read the source", error)

    def test_partial_result_or_unknown_action_is_not_accepted_as_finish(self):
        index = self.index()
        task = {"id": "coverage:fixture", "type": "coverage", "targets": self.rows}
        partial = self.coverage()["result"]
        del partial["unresolved_uids"]
        unknown = {**self.coverage()["result"], "action": "approve"}
        client = FakeClient([partial, unknown])
        outcome = investigate.investigate_task(task, index, client, {}, 2)
        self.assertEqual(outcome["state"], "unresolved")
        self.assertIn('"action":"finish"', outcome["error"])

    def test_investigator_searches_and_corrects_claims_with_exact_citations(self):
        index = self.index([self.candidate()])
        task = {"id": "event:candidate:conference", "type": "event", "candidate": self.candidate()}
        client = FakeClient([self.finish_event(), {"action": "search", "query": "conference"}, self.finish_event()])
        outcome = investigate.investigate_task(task, index, client, {"candidate:conference": self.candidate()}, 4)
        self.assertEqual(outcome["state"], "done")
        self.assertIn("Search the mission", client.calls[1][1]["retrieval_transcript"][-1]["validation_error"])
        events, gaps = investigate.final_events([self.candidate()], {task["id"]: outcome}, index)
        self.assertFalse(gaps)
        self.assertEqual(events[0]["title"], "Conference configured")
        self.assertIsNone(events[0]["event_time"])
        self.assertEqual(events[0]["time_basis"], "report")

    def test_end_to_end_review_resume_hierarchy_and_processing_only_outputs(self):
        self.publish_recovery([self.candidate()])
        client = FakeClient([self.coverage(), {"action": "search", "query": "conference"},
                             self.finish_event(), self.label_reconciliation()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = investigate.run_investigations(self.mission, options(), client)
            resumed = FakeClient()
            again = investigate.run_investigations(self.mission, options(), resumed)
        self.assertTrue(report["complete"])
        self.assertEqual(report["verified_events"], 1)
        self.assertEqual(report["label_reconciliation_tasks"], 1)
        reviewed = common.read_jsonl(self.root / "investigated_history" / "utterances.jsonl")
        self.assertEqual(reviewed[0]["topic"], "Conference configured; execution not established")
        self.assertEqual(len(resumed.calls), 0)
        self.assertEqual(report["snapshot"], again["snapshot"])
        with patch.object(hierarchy, "MissionContextIndex", EmptyContext):
            tree = hierarchy.build_mission_activity_hierarchy(self.mission)
        self.assertEqual(tree["summary"]["raw_milestone_detections"], 1)
        self.assertEqual(tree["merged_milestones"][0]["title"], "Conference configured")
        self.assertEqual(tree["merged_milestones"][0]["crew"], [])
        self.assertFalse(any(self.mission.web_dir.glob("*enriched*")))
        self.assertFalse((self.mission.web_dir / "mission_activities.json").exists())

    def test_pending_investigation_blocks_hierarchy_and_transport_failure_resumes(self):
        self.publish_recovery([self.candidate()])
        client = FakeClient([self.coverage(), RuntimeError("offline")])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = investigate.run_investigations(self.mission, options(), client)
        self.assertFalse(report["complete"])
        with patch.object(hierarchy, "MissionContextIndex", EmptyContext):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                hierarchy.build_mission_activity_hierarchy(self.mission)
        client = FakeClient([{"action": "search", "query": "conference"}, self.finish_event(), self.label_reconciliation()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = investigate.run_investigations(self.mission, options(), client)
        self.assertTrue(report["complete"])
        self.assertEqual(len(client.calls), 3)

    def test_retry_settings_reuse_checkpoint_and_apply_only_to_retried_tasks(self):
        self.publish_recovery([self.candidate()])
        first = FakeClient([self.coverage(), *["invalid JSON"] * 5])
        retry = FakeClient([{"action": "search", "query": "conference"}, self.finish_event()])
        retry.settings = {**retry.settings, "reasoning_effort": "medium", "max_tokens": 32768}
        base = FakeClient([self.label_reconciliation()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            original = investigate.run_investigations(self.mission, options(), first)
            with patch.object(investigate, "ModelClient", return_value=retry) as make_retry:
                report = investigate.run_investigations(self.mission, options(
                    retry_unresolved=True, reasoning_effort="low", max_tokens=16384,
                    retry_reasoning_effort="medium", retry_max_tokens=32768), base)
        self.assertEqual(original["exhausted_tasks"], 1)
        self.assertEqual(report["snapshot"], original["snapshot"])
        self.assertEqual(report["verified_events"], 1)
        self.assertEqual([call[1]["task"]["type"] for call in retry.calls], ["event", "event"])
        self.assertEqual([call[1]["task"]["type"] for call in base.calls], ["labels"])
        retry_args = make_retry.call_args.args[0]
        self.assertEqual((retry_args.reasoning_effort, retry_args.max_tokens), ("medium", 32768))
        history = common.read_jsonl(self.root / "investigated_history" / "investigations.jsonl")
        event = next(row for row in history if row["type"] == "event")
        self.assertEqual(event["settings"], retry.settings)

    def test_selective_review_recovery_preserves_unresolved_event_checkpoint(self):
        self.publish_recovery([self.candidate()])
        failed = FakeClient(["invalid JSON"] * 15)
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            original = investigate.run_investigations(self.mission, options(), failed)
        work = self.root / "investigated_history" / "work" / original["snapshot"]
        event_path = work / ("task_" + common.digest("event:candidate:conference") + ".json")
        event_before = event_path.read_bytes()
        replies = []
        for row in self.rows:
            result = self.coverage()
            result["result"]["reviewed_uids"] = [row["uid"]]
            replies.append(result)
        retry = FakeClient([*replies, self.label_reconciliation()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = investigate.run_investigations(self.mission, options(
                retry_unresolved=True, retry_types=["coverage", "labels"], retry_batch_size=1), retry)
        self.assertEqual(report["snapshot"], original["snapshot"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["unresolved_labels"], 0)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertEqual([call[1]["task"]["type"] for call in retry.calls], ["coverage", "coverage", "labels"])
        reviewed = common.read_jsonl(self.root / "investigated_history" / "utterances.jsonl")
        self.assertTrue(all(r["label_review"]["status"] == "reviewed" and r["crew"] == [] for r in reviewed))
        resumed = FakeClient()
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            investigate.run_investigations(self.mission, options(
                retry_unresolved=True, retry_types=["coverage", "labels"], retry_batch_size=1), resumed)
        self.assertEqual(resumed.calls, [])

    def test_split_review_limits_and_transport_failures_reuse_successful_children(self):
        for stop in ("limit", "transport"):
            with self.subTest(stop=stop):
                task = {"id": "coverage:" + stop, "type": "coverage", "targets": self.rows}
                paths = []
                def first_attempt(child, path):
                    paths.append(path)
                    if len(paths) == 2:
                        return None  # Both a limit and a transport failure leave no outcome.
                    result = self.coverage()["result"]
                    result["reviewed_uids"] = [child["targets"][0]["uid"]]
                    outcome = {"state": "done", "result": result}
                    common.atomic_json(path, outcome)
                    return outcome
                self.assertIsNone(investigate.retry_review(task, None, 1, self.root,
                                                         first_attempt, FakeClient.settings))
                saved = paths[0].read_bytes()
                calls = []
                def resume(child, path):
                    calls.append(child)
                    result = self.coverage()["result"]
                    result["reviewed_uids"] = [child["targets"][0]["uid"]]
                    outcome = {"state": "done", "result": result}
                    common.atomic_json(path, outcome)
                    return outcome
                outcome = investigate.retry_review(task, None, 1, self.root, resume, FakeClient.settings)
                self.assertEqual(len(calls), 1)
                self.assertEqual(outcome["result"]["unresolved_uids"], [])
                self.assertEqual(paths[0].read_bytes(), saved)

    def test_split_new_label_reviews_use_retry_settings(self):
        self.publish_recovery([self.candidate()])
        failed = FakeClient([self.coverage(), *["invalid JSON"] * 10])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            original = investigate.run_investigations(self.mission, options(), failed)
        retry = FakeClient([{"action": "search", "query": "conference"},
                            self.finish_event(), self.label_reconciliation()])
        retry.settings = {**retry.settings, "reasoning_effort": "low", "max_tokens": 32768}
        base = FakeClient()
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            with patch.object(investigate, "ModelClient", return_value=retry):
                report = investigate.run_investigations(self.mission, options(
                    retry_unresolved=True, retry_types=["event", "labels"], retry_batch_size=1,
                    retry_reasoning_effort="low", retry_max_tokens=32768,
                    reasoning_effort="low", max_tokens=16384), base)
        self.assertEqual(report["snapshot"], original["snapshot"])
        self.assertEqual(report["unresolved_labels"], 0)
        self.assertEqual(base.calls, [])
        self.assertEqual([c[1]["task"]["type"] for c in retry.calls], ["event", "event", "labels"])

    def test_split_review_failed_group_does_not_exclude_successful_group(self):
        task = {"id": "coverage:partial", "type": "coverage", "targets": self.rows}
        index = self.index()
        one = self.coverage()
        one["result"]["reviewed_uids"] = [self.rows[1]["uid"]]
        client = FakeClient(["invalid JSON", "invalid JSON", one])
        def attempt(child, path):
            outcome = investigate.investigate_task(child, index, client, {}, 2)
            common.atomic_json(path, outcome)
            return outcome
        outcome = investigate.retry_review(task, None, 1, self.root, attempt, client.settings)
        self.assertEqual(outcome["state"], "done")
        self.assertEqual(outcome["result"]["unresolved_uids"], [self.rows[0]["uid"]])
        self.assertEqual([s["state"] for s in outcome["recovery"]["subtasks"]], ["unresolved", "done"])
        # A subsequent pass retries only the failed UID and keeps the prior successful review.
        one["result"]["reviewed_uids"] = [self.rows[0]["uid"]]
        client.replies.append(one)
        recovered = investigate.retry_review(task, outcome, 1, self.root, attempt, client.settings)
        self.assertEqual(recovered["result"]["unresolved_uids"], [])
        self.assertEqual(len(client.calls), 4)

    def test_selective_recovery_refuses_a_fresh_snapshot(self):
        self.publish_recovery()
        client = FakeClient()
        with patch.object(enrich, "MissionContextIndex", EmptyContext):
            with self.assertRaisesRegex(RuntimeError, "existing checkpoint snapshot"):
                investigate.run_investigations(self.mission, options(
                    retry_unresolved=True, retry_types=["coverage", "labels"], retry_batch_size=5), client)
        self.assertEqual(client.calls, [])

    def test_review_tag_error_identifies_uid_and_limit_for_repair(self):
        invalid = self.label_reconciliation()
        invalid["result"]["label_changes"][0]["tags"] = ["one", "two", "three", "four", "five"]
        client = FakeClient([invalid, self.label_reconciliation()])
        task = {"id": "labels:tags", "type": "labels", "targets": self.rows[:1]}
        outcome = investigate.investigate_task(task, self.index(), client, {}, 2)
        self.assertEqual(outcome["state"], "done")
        self.assertEqual(client.calls[0][0], investigate.LABEL_RECONCILIATION_PROMPT)
        error = client.calls[1][1]["retrieval_transcript"][-1]["validation_error"]
        self.assertIn(self.rows[0]["uid"], error)
        self.assertIn("tags must be 1..4", error)

    def test_label_findings_supply_cited_originals_outside_target_window(self):
        index = self.index()
        source = index.docs[self.citation()["source_id"]]
        reply = self.label_reconciliation()
        reply["result"]["label_changes"][0]["citations"].append(self.citation(1, "will start"))
        task = {"id": "labels:originals", "type": "labels", "targets": self.rows[:1],
                "investigation_findings": {self.rows[0]["uid"]: [
                    {"result": {"claims": [self.claim(1, "will start")]}}]}}
        client = FakeClient([reply])
        with patch.object(index, "read", return_value=[source]):
            outcome = investigate.investigate_task(task, index, client, {}, 2)
        self.assertEqual(outcome["state"], "done")
        supplied = client.calls[0][1]["retrieval_transcript"][0]["initial_sources"]
        self.assertIn(self.citation(1)["source_id"], {r["id"] for r in supplied})
        self.assertTrue(all(r["kind"] != "candidate" for r in supplied))

    def test_distinct_verified_stages_are_not_fuzzy_merged_by_hierarchy(self):
        ms = [{"id": str(i), "title": "Conference", "t": u["t"], "status": status,
               "crew": [], "verification": "supported"}
              for i, (u, status) in enumerate(zip(self.utterances, ["started", "completed"]))]
        self.assertEqual(len(hierarchy.merge_milestone_reports(ms)), 2)

    def test_duplicate_cycles_stay_unresolved_and_valid_links_preserve_aliases(self):
        candidates = [self.candidate(id="a"), self.candidate(id="b")]
        index = self.index(candidates)
        def duplicate(other):
            return {"state": "done", "result": {"verdict": "supported", "reason": "Same report",
                "claims": [self.claim()], "events": [], "duplicate_of": other, "duplicate_status": "completed"}}
        outcomes = {"event:a": duplicate("b"), "event:b": duplicate("a")}
        events, gaps = investigate.final_events(candidates, outcomes, index)
        self.assertFalse(events)
        self.assertEqual(len(gaps), 2)
        outcomes["event:b"] = {"task_id": "event:b", "state": "done", "result": self.finish_event()["result"]}
        events, gaps = investigate.final_events(candidates, outcomes, index)
        self.assertFalse(gaps)
        self.assertEqual(len(events), 1)
        self.assertEqual(set(events[0]["candidate_ids"]), {"a", "b"})

    def test_coverage_discovers_omitted_event_and_investigates_it(self):
        self.publish_recovery()
        coverage = self.coverage()
        coverage["result"]["discoveries"] = [{"title": "Conference configured", "status": "completed", "crew": [],
            "source_uid": self.utterances[0]["uid"], "evidence": "configured"}]
        client = FakeClient([coverage, {"action": "search", "query": "conference"},
                             self.finish_event(), self.label_reconciliation()])
        with patch.object(enrich, "MissionContextIndex", EmptyContext), contextlib.redirect_stdout(io.StringIO()):
            report = investigate.run_investigations(self.mission, options(), client)
        self.assertEqual(report["verified_events"], 1)
        candidates = common.read_jsonl(self.root / "investigated_history" / "candidates.jsonl")
        self.assertTrue(candidates[0]["id"].startswith("discovered:"))

    def test_planned_activity_is_investigated_and_not_confirmed_by_nearby_talk(self):
        self.publish_recovery()
        class PlannedContext(EmptyContext):
            def __init__(inner, mission=None):
                super().__init__()
                inner.planned_blocks = [{"id": "conference", "start_ts": self.utterances[0]["ts"],
                    "end_ts": self.utterances[1]["ts"], "start_utc": self.utterances[0]["t"],
                    "end_utc": self.utterances[1]["t"], "title": "Conference", "description": "Scheduled conference",
                    "flight_day": "FD01", "phase": "Test", "category": "media_event"}]
        unresolved = {"action": "finish", "result": {"verdict": "unresolved", "reason": "Only setup and a future plan",
            "claims": [{"claim": "Conference occurred", "dimension": "event", "verdict": "unresolved", "citations": []}],
            "events": [], "duplicate_of": None}}
        client = FakeClient([self.coverage(), {"action": "search", "query": "conference"}, unresolved])
        with patch.object(enrich, "MissionContextIndex", PlannedContext), contextlib.redirect_stdout(io.StringIO()):
            report = investigate.run_investigations(self.mission, options(), client)
        self.assertTrue(report["complete"])
        self.assertEqual(report["unresolved_events"], 1)
        with patch.object(hierarchy, "MissionContextIndex", PlannedContext):
            tree = hierarchy.build_mission_activity_hierarchy(self.mission)
        self.assertEqual(tree["flight_days"][0]["blocks"][0]["status"], "planned_unconfirmed")

    def test_runner_registers_order_and_forwards_recovery_investigation_options(self):
        ids = [s[0] for s in runner.STEPS]
        self.assertEqual(ids[ids.index("1d"):ids.index("1d") + 4], ["1d", "1e", "1f", "1g"])
        for step in ["1e", "1f"]:
            with patch.object(runner.importlib, "import_module") as load, patch.object(runner.importlib, "reload"), \
                 patch("sys.argv", []), contextlib.redirect_stdout(io.StringIO()):
                argv = []
                load.return_value.main.side_effect = lambda: argv.extend(__import__("sys").argv)
                self.assertTrue(runner.run_step(step, "fake", "test", "test", "strata", "custom",
                    force=True, source_run="run", history_limit=2, retry_unresolved=True, follow=True,
                    poll_seconds=12, retry_reasoning_effort="medium", retry_max_tokens=32768,
                    retry_types=["coverage", "labels"], retry_batch_size=5))
            self.assertIn("--backend", argv)
            self.assertNotIn("--force", argv)
            self.assertEqual("--source-run" in argv, step == "1e")
            self.assertIn("--retry-unresolved", argv)
            self.assertEqual("--retry-reasoning-effort" in argv, step == "1f")
            self.assertEqual("--retry-max-tokens" in argv, step == "1f")
            self.assertEqual("--retry-types" in argv, step == "1f")
            self.assertEqual("--retry-batch-size" in argv, step == "1f")
            self.assertEqual("--follow" in argv, step == "1e")
            if step == "1e":
                self.assertEqual(argv[argv.index("--poll-seconds") + 1], "12")

    def test_runner_continues_to_recovery_only_after_a_completed_failed_run(self):
        calls = []
        def fake_step(step_id, *args, **kwargs):
            calls.append((step_id, kwargs))
            if step_id == "1d":
                self.diagnostics()
                return False
            return True
        with patch.object(runner, "MISSIONS", {"artemis-ii": self.mission}), \
             patch.object(runner, "run_step", side_effect=fake_step), \
             patch("sys.argv", ["run_all.py", "--mission", "artemis-ii", "--step", "1d", "1e"]), \
             contextlib.redirect_stdout(io.StringIO()):
            runner.main()
        self.assertEqual([c[0] for c in calls], ["1d", "1e"])
        self.assertEqual(calls[1][1]["source_run"], self.run.name)
        with patch.object(runner, "MISSIONS", {"artemis-ii": self.mission}), \
             patch.object(runner, "run_step", return_value=False) as run, \
             patch("sys.argv", ["run_all.py", "--mission", "artemis-ii", "--step", "1d", "1e"]), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                runner.main()
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
