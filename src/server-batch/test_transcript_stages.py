"""Regression checks for independent event extraction."""

import copy
import importlib
import json
import unittest


enrich = importlib.import_module("1_comm.transcript_stages")


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


    def test_missing_target_coverage_rejects_extraction(self):
        self.extraction["threads"][0]["statements"].pop()
        with self.assertRaises(ValueError):
            self.extract()

    def test_future_countdown_cannot_be_rewritten_as_executed_development(self):
        self.batch[1]["text"] = "80 seconds to burn ignition."
        self.extraction["developments"][0]["evidence"] = "80 seconds to burn ignition"
        with self.assertRaisesRegex(ValueError, "future countdown"):
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


    def test_source_context_balances_streams_and_bounds_distant_contacts(self):
        rows = [
            {"channel": "comm", "src": "old_oe1.wav", "ts": 0},
            {"channel": "comm", "src": "recent_oe1.wav", "ts": 701},
            {"channel": "comm", "src": "other_oe2.wav", "ts": 702},
            *[{"channel": "pao", "ts": t} for t in range(703, 713)],
            {"channel": "comm", "src": "target_oe1.wav", "ts": 713},
            {"channel": "pao", "ts": 714},
            {"channel": "comm", "src": "next_oe1.wav", "ts": 715},
        ]
        before, after = enrich.source_context(rows, 13, 13, 2)
        self.assertEqual(before, [rows[1]])
        self.assertEqual(after, [rows[15]])
