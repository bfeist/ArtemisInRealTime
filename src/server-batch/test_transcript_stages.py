"""Regression checks for structured extraction and deterministic label assembly."""

import copy
import importlib
import json
import unittest


enrich = importlib.import_module("1_comm.1d_enrich_transcripts")


def label(index, **extra):
    return {
        "i": index, "imp": 3, "cat": "systems", "tags": ["water system"],
        "topic": "Water valve investigation and checkout", **extra,
    }


def jsonl(rows):
    return "\n".join(json.dumps(row) for row in rows)


class TranscriptStagesTest(unittest.TestCase):
    def setUp(self):
        self.batch = [
            {"channel": "comm", "text": "We have a water tank valve problem during powered flight."},
            {"channel": "comm", "text": "We saw an anomalous indication on the water isol valve between water tanks Bravo and Charlie."},
            {"channel": "comm", "text": "Based on the checkout signature, we think that valve is closed."},
            {"channel": "comm", "text": "We are going to troubleshoot that going forward."},
        ]
        self.extraction = {
            "threads": [{
                "id": "water", "subject": "Water isolation valve investigation",
                "statements": [
                    {"indices": [0, 1], "kind": "report", "summary": "Anomalous valve indication reported from powered flight", "crew": []},
                    {"indices": [2], "kind": "hypothesis", "summary": "Valve believed closed based on checkout signature", "crew": []},
                    {"indices": [3], "kind": "plan", "summary": "Further valve troubleshooting planned", "crew": []},
                ],
            }],
            "developments": [{
                "id": "valve_anomaly", "thread_id": "water", "report_indices": [0, 1],
                "anchor_i": 1, "title": "Anomalous water isolation valve indication",
                "status": "anomaly", "evidence": "We saw an anomalous indication on the water isol valve between water tanks Bravo and Charlie",
                "crew": [],
            }],
        }

    def extract(self, structure=None, event_ids=None, batch=None):
        return enrich.parse_extraction(
            json.dumps(self.extraction if structure is None else structure),
            self.batch if batch is None else batch, event_ids or set(),
        )

    def assess(self, rows=None, extraction=None, diagnostics=None, batch=None):
        return enrich.parse_staged_assessments(
            jsonl([label(i) for i in range(len(self.batch))] if rows is None else rows),
            self.batch if batch is None else batch,
            set(), self.extract() if extraction is None else extraction, diagnostics,
        )

    def test_carryover_bounds_history_and_preserves_statement_kinds_without_old_indices(self):
        targets = [{**u, "t": f"2026-04-01T18:00:0{i}Z"} for i, u in enumerate(self.batch)]
        parsed = self.assess()
        state = {}
        for _ in range(25):
            state = enrich.update_conversation_state(state, self.extract(), targets, parsed)
        self.assertEqual(len(state["recent_threads"]), 6)
        self.assertEqual(len(state["captured_developments"]), 20)
        statements = state["recent_threads"][-1]["statements"]
        self.assertEqual([s["kind"] for s in statements], ["hypothesis", "plan"])
        self.assertTrue(all("indices" not in s for s in statements))
        self.assertEqual(state["captured_developments"][-1]["report_utc"], targets[1]["t"])
        # A validated development must not become captured if its label was rejected.
        partial = enrich.update_conversation_state({}, self.extract(), targets, {0: parsed[0]})
        self.assertEqual(partial["captured_developments"], [])

    def test_one_development_survives_two_reports_at_selected_evidence_anchor(self):
        parsed = self.assess()
        self.assertEqual(set(parsed), {0, 1, 2, 3})
        self.assertEqual(parsed[0]["milestones"], [])
        self.assertEqual(len(parsed[1]["milestones"]), 1)
        milestone = parsed[1]["milestones"][0]
        self.assertEqual(milestone["evidence"], self.extraction["developments"][0]["evidence"])
        self.assertEqual(milestone["status"], "anomaly")
        self.assertEqual(parsed[2]["milestones"], [])
        self.assertEqual(parsed[3]["milestones"], [])

    def test_missing_target_coverage_rejects_extraction(self):
        self.extraction["threads"][0]["statements"].pop()
        with self.assertRaises(ValueError):
            self.extract()

    def test_unknown_development_thread_rejects_extraction(self):
        self.extraction["developments"][0]["thread_id"] = "unknown"
        with self.assertRaises(ValueError):
            self.extract()

    def test_duplicate_thread_ids_reject_extraction(self):
        self.extraction["threads"].append(copy.deepcopy(self.extraction["threads"][0]))
        with self.assertRaises(ValueError):
            self.extract()

    def test_duplicate_development_ids_reject_extraction(self):
        duplicate = copy.deepcopy(self.extraction["developments"][0])
        duplicate["evidence"] = "anomalous indication"
        self.extraction["developments"].append(duplicate)
        with self.assertRaises(ValueError):
            self.extract()

    def test_duplicate_development_with_new_id_still_rejects_extraction(self):
        duplicate = copy.deepcopy(self.extraction["developments"][0])
        duplicate["id"] = "same_anomaly_again"
        self.extraction["developments"].append(duplicate)
        with self.assertRaises(ValueError):
            self.extract()

    def test_bool_negative_and_out_of_range_indices_reject_extraction(self):
        for invalid in (True, -1, len(self.batch)):
            for location in ("statement", "report", "anchor"):
                with self.subTest(invalid=invalid, location=location):
                    structure = copy.deepcopy(self.extraction)
                    if location == "statement":
                        structure["threads"][0]["statements"][0]["indices"].append(invalid)
                    elif location == "report":
                        structure["developments"][0]["report_indices"].append(invalid)
                    else:
                        structure["developments"][0]["anchor_i"] = invalid
                    with self.assertRaises(ValueError):
                        self.extract(structure)

    def test_anchor_must_belong_to_development_reports(self):
        self.extraction["developments"][0]["report_indices"] = [0]
        with self.assertRaises(ValueError):
            self.extract()

    def test_anchor_must_belong_to_referenced_thread(self):
        self.extraction["threads"].append({
            "id": "other", "subject": "Future troubleshooting",
            "statements": [{"indices": [3], "kind": "plan", "summary": "Troubleshooting is planned", "crew": []}],
        })
        self.extraction["developments"][0]["thread_id"] = "other"
        with self.assertRaises(ValueError):
            self.extract()

    def test_empty_or_nonexact_evidence_rejects_extraction(self):
        for quote in ("", "   ", "The valve failed.", self.batch[0]["text"]):
            with self.subTest(quote=quote):
                structure = copy.deepcopy(self.extraction)
                structure["developments"][0]["evidence"] = quote
                with self.assertRaises(ValueError):
                    self.extract(structure)

    def test_hypothesis_and_plan_cannot_anchor_executed_milestones(self):
        for index in (2, 3):
            with self.subTest(index=index):
                structure = copy.deepcopy(self.extraction)
                structure["developments"][0].update(
                    anchor_i=index, report_indices=[index], evidence=self.batch[index]["text"],
                    status="completed",
                )
                with self.assertRaises(ValueError):
                    self.extract(structure)

    def test_invalid_milestone_status_rejects_extraction(self):
        self.extraction["developments"][0]["status"] = "planned"
        with self.assertRaises(ValueError):
            self.extract()

    def test_invalid_catalog_id_is_removed_without_losing_development(self):
        for event_id in ("new:water", "photo-file", "yt:clip"):
            with self.subTest(event_id=event_id):
                structure = copy.deepcopy(self.extraction)
                structure["developments"][0]["event_id"] = event_id
                parsed = self.extract(structure)
                self.assertEqual(len(parsed["developments"]), 1)
                self.assertNotIn("event_id", parsed["developments"][0])

    def test_valid_catalog_id_is_preserved(self):
        self.extraction["developments"][0]["event_id"] = "valve_event"
        parsed = self.extract(event_ids={"valve_event"})
        self.assertEqual(parsed["developments"][0]["event_id"], "valve_event")

    def test_distinct_developments_can_share_an_anchor(self):
        batch = [{"channel": "pao", "text": "Boarding complete. Hatch closed."}]
        structure = {
            "threads": [{"id": "boarding", "subject": "Crew boarding", "statements": [
                {"indices": [0], "kind": "report", "summary": "Boarding and hatch closure complete", "crew": []},
            ]}],
            "developments": [
                {"id": "boarded", "thread_id": "boarding", "report_indices": [0], "anchor_i": 0,
                 "title": "Boarding complete", "status": "completed", "evidence": "Boarding complete", "crew": []},
                {"id": "hatch", "thread_id": "boarding", "report_indices": [0], "anchor_i": 0,
                 "title": "Hatch closed", "status": "completed", "evidence": "Hatch closed", "crew": []},
            ],
        }
        parsed = self.assess([label(0)], extraction=self.extract(structure, batch=batch), batch=batch)
        self.assertEqual(len(parsed[0]["milestones"]), 2)
        self.assertEqual(parsed[0]["topic"], "Boarding complete")

    def test_overlapping_threads_keep_utterance_crew_separate_from_milestone_crew(self):
        batch = [
            {"channel": "comm", "text": "Thermal roll complete, and a question for Christina for toilet."},
            {"channel": "comm", "text": "Christina, what was the toilet status LED doing?"},
        ]
        structure = {
            "threads": [
                {"id": "roll", "subject": "Thermal roll", "statements": [
                    {"indices": [0], "kind": "report", "summary": "Thermal roll complete", "crew": []},
                ]},
                {"id": "toilet", "subject": "Toilet troubleshooting", "statements": [
                    {"indices": [0, 1], "kind": "question", "summary": "Question for Christina about the toilet LED", "crew": ["koch"]},
                ]},
            ],
            "developments": [{
                "id": "roll_complete", "thread_id": "roll", "report_indices": [0], "anchor_i": 0,
                "title": "Thermal roll complete", "status": "completed", "evidence": "Thermal roll complete", "crew": [],
            }],
        }
        parsed = self.assess([label(0), label(1)], extraction=self.extract(structure, batch=batch), batch=batch)
        self.assertIn("koch", parsed[0]["crew"])
        self.assertIn("koch", parsed[1]["crew"])
        self.assertNotIn("koch", parsed[0]["milestones"][0].get("crew", []))

    def test_generated_milestones_and_crew_cannot_change_extraction(self):
        rows = [label(i) for i in range(len(self.batch))]
        rows[0].update(crew=["wiseman"], milestones=[{
            "title": "Invented valve failure", "status": "anomaly", "evidence": self.batch[0]["text"],
        }])
        rows[1].update(crew=["hansen"], milestones=[])
        diagnostics = []
        parsed = self.assess(rows, diagnostics=diagnostics)
        self.assertEqual(parsed[0]["milestones"], [])
        self.assertEqual(parsed[0]["crew"], [])
        self.assertEqual(len(parsed[1]["milestones"]), 1)
        self.assertEqual(parsed[1]["crew"], [])
        self.assertTrue(diagnostics)

    def test_anchored_title_preserves_fact_while_unanchored_topic_comes_from_label(self):
        batch = [
            {"channel": "comm", "text": "[Glover] I am not able to see lunar color from this window. I think the Kapton tape is drowning it out."},
            {"channel": "comm", "text": "Could someone check the color from a different window?"},
        ]
        supported_title = "Glover cannot see lunar color from this window"
        structure = {
            "threads": [{"id": "color", "subject": "Lunar color visibility", "statements": [
                {"indices": [0], "kind": "report", "summary": "Glover cannot see lunar color from his window", "crew": ["glover"]},
                {"indices": [0], "kind": "hypothesis", "summary": "Kapton tape may interfere with color perception", "crew": ["glover"]},
                {"indices": [1], "kind": "question", "summary": "Question about checking color from a different window", "crew": []},
            ]}],
            "developments": [{
                "id": "visibility", "thread_id": "color", "report_indices": [0], "anchor_i": 0,
                "title": supported_title, "status": "observed",
                "evidence": "I am not able to see lunar color from this window", "crew": ["glover"],
            }],
        }
        unanchored_topic = "Question about another window's color visibility"
        rows = [
            label(0, topic="Kapton tape prevents Glover from seeing lunar colors"),
            label(1, topic=unanchored_topic),
        ]
        parsed = self.assess(rows, extraction=self.extract(structure, batch=batch), batch=batch)
        self.assertEqual(parsed[0]["topic"], supported_title)
        self.assertEqual(parsed[1]["topic"], unanchored_topic)

    def test_missing_label_stays_pending_without_discarding_other_rows(self):
        parsed = self.assess([label(0), label(2), label(3)])
        self.assertEqual(set(parsed), {0, 2, 3})

    def test_empty_tags_are_allowed_only_for_filler(self):
        diagnostics = []
        parsed = self.assess([
            label(0, cat="pao_filler", tags=[]), label(1, tags=[]),
            label(2, cat="pao_filler", tags=[""]), label(3, cat="pao_filler", tags=None),
        ], diagnostics=diagnostics)
        self.assertEqual(set(parsed), {0})
        self.assertEqual(parsed[0]["tags"], [])
        self.assertEqual([issue["index"] for issue in diagnostics], [1, 2, 3])

    def test_duplicate_label_index_stays_pending_without_discarding_other_rows(self):
        diagnostics = []
        parsed = self.assess([label(0), label(1), label(1, imp=5), label(2), label(3)], diagnostics=diagnostics)
        self.assertEqual(set(parsed), {0, 2, 3})
        self.assertTrue(diagnostics)

    def test_invalid_label_indices_do_not_overwrite_valid_rows(self):
        diagnostics = []
        rows = [label(i) for i in range(len(self.batch))]
        rows.extend([label(True, imp=5), label(-1), label(len(self.batch))])
        parsed = self.assess(rows, diagnostics=diagnostics)
        self.assertEqual(set(parsed), {0, 1, 2, 3})
        self.assertEqual(parsed[1]["importance"], 3)
        self.assertTrue(diagnostics)


if __name__ == "__main__":
    unittest.main()
