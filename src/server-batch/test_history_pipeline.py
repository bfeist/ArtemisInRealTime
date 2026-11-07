"""Original source retrieval and evidence-verification regressions."""
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

investigate = importlib.import_module("1_comm.1f_investigate_history")
hierarchy = importlib.import_module("1_comm.1g_mission_context")
common = importlib.import_module("1_comm.history_common")
sources = importlib.import_module("1_comm.history_sources")
enrich = common.transcript


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
        for key in ("BULK_ASSETS_DIR", "PDS_ASSETS_DIR", "YT_VIDEO_DIR"):
            storage = patch("config." + key, Path(self.tmp.name) / key)
            storage.start()
            self.addCleanup(storage.stop)
        self.mission.ensure_dirs()
        self.root = self.mission.processed_transcripts
        self.mission.transcript_inputs.mkdir(parents=True)
        (self.mission.transcript_inputs / "PAO.csv").write_text(
            "2026-04-01T17:00:00Z|We are configured for that conference.\n"
            "2026-04-01T17:01:00Z|We will start the conference later.\n", encoding="utf-8")
        self.utterances = enrich.load_all_utterances(self.mission)

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


    def index(self, candidates=()):
        index = sources.SourceIndex(self.utterances, EmptyContext(), candidates=candidates)
        self.addCleanup(index.close)
        return index


    def candidate(self, **extra):
        return {"id": "candidate:conference", "title": "Conference underway", "status": "completed",
                "source_uid": self.utterances[0]["uid"], "t": self.utterances[0]["t"], **extra}


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

    def test_future_countdown_citation_does_not_establish_execution(self):
        self.utterances[0]["text"] = "80 seconds to burn ignition."
        index = self.index()
        result = self.finish_event()["result"]
        event = result["events"][0]
        event["claims"][0]["citations"][0]["quote"] = "80 seconds to burn ignition"
        with self.assertRaisesRegex(ValueError, "future countdown"):
            investigate.validate_event_result(result, {"candidate": self.candidate()}, index.docs, {})

    def test_observation_does_not_prove_suspected_cause(self):
        self.utterances[0]["text"] = "I cannot see lunar color. I think the tape is drowning out lunar color."
        index = self.index()
        result = self.finish_event()["result"]
        result["claims"] = [self.claim(quote="I cannot see lunar color")]
        event = result["events"][0]
        event["claims"] = [self.claim(quote="I cannot see lunar color"),
            self.claim(quote="I think the tape is drowning out lunar color", dimension="cause")]
        with self.assertRaisesRegex(ValueError, "suspected cause"):
            investigate.validate_event_result(result, {"candidate": self.candidate()}, index.docs, {})
        event["claims"].pop()
        investigate.validate_event_result(result, {"candidate": self.candidate()}, index.docs, {})


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


    def test_retrieval_keeps_radio_loops_separate(self):
        rows = [
            {**self.utterances[0], "uid": "oe1-first", "channel": "comm", "src": "first_oe1.wav"},
            {**self.utterances[0], "uid": "oe2", "channel": "comm", "src": "other_oe2.wav"},
            {**self.utterances[1], "uid": "oe1-next", "channel": "comm", "src": "next_oe1.wav"},
        ]
        index = sources.SourceIndex(rows, EmptyContext())
        self.addCleanup(index.close)
        self.assertEqual([r["uid"] for r in index.read("u:oe1-next", 5, 0)], ["oe1-first", "oe1-next"])
        self.assertEqual(index.docs["u:oe2"]["source"], "OE-2")


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
