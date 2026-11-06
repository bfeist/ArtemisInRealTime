"""Corrected loop authority across differing segmentation and edited audio clocks."""
import csv
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import MissionConfig
from shared.pds_comm import BUNDLE
from shared.pds_loops import apply_loop_precedence, import_loops, iso, normalize_loop, timestamp

web = importlib.import_module("1_comm.1c_web_comm")


class LoopCommTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.paths = patch.multiple("config", DATA_DIR=self.root, PDS_ASSETS_DIR=self.root)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-11", "test",
                                     ia_comm_collection="test", comm_tz_offset_hours=5)
        self.base = timestamp("2026-04-06T00:00:00Z")
        self.csv = self.root / "art002_2026-04-07_oe1_trn-csv_v01.csv"

    def write_csv(self, rows):
        with self.csv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["audio_time", "utc", "speaker", "text"])
            writer.writerows(rows)

    def row(self, start, duration, source="AI_OE_Comp_1_test.aac", text="AI text"):
        return (iso(self.base + start), "0", str(duration), source, text)

    def sources(self, windows):
        return {"loops/corrected_oe1.m4a": {
            "source_kind": "mission_voice_loop", "channel": "OE-1", "transcript_source": "pds_supplied",
            "authority_intervals": [{"start": iso(self.base + s), "end": iso(self.base + e)} for s, e in windows]}}

    def merge(self, rows, windows):
        return apply_loop_precedence(rows, self.sources(windows), self.root / "reports")

    def test_one_corrected_utterance_replaces_multiple_ai_segments(self):
        corrected = self.row(10, 20, "loops/corrected_oe1.m4a", "Corrected paragraph")
        rows = [self.row(10, 4), self.row(14, 6), self.row(20, 10), corrected]
        self.assertEqual(self.merge(rows, [(10, 30)]), [corrected])

    def test_multiple_corrected_utterances_replace_one_ai_segment(self):
        corrected = [self.row(10, 4, "loops/corrected_oe1.m4a", "First."),
                     self.row(14, 6, "loops/corrected_oe1.m4a", "Second.")]
        rows = [self.row(10, 10)] + corrected
        self.assertEqual(self.merge(rows, [(10, 20)]), corrected)

    def test_other_channels_pcd_and_uncovered_intervals_are_untouched(self):
        rows = [self.row(12, 5), self.row(12, 5, "AI_OE_Comp_2_test.aac"),
                self.row(12, 5, "pcd/art002a000005.m4a"), self.row(25, 5), self.row(5, 5), self.row(40, 5)]
        kept = self.merge(rows, [(10, 20), (30, 40)])
        self.assertEqual(kept, rows[1:])

    def test_boundary_overlap_is_whole_utterance_and_audited(self):
        original = self.row(8, 6, text="Uncut original text")
        self.assertEqual(self.merge([original], [(10, 20)]), [])
        report = json.loads((self.root / "reports/loop_comm_merge_report.json").read_text())
        audit = json.loads((self.root / "reports/superseded_comm.jsonl").read_text())
        self.assertEqual(report["boundary_overlaps"], 1)
        self.assertEqual(audit["outside_coverage_seconds"], 2)
        self.assertEqual(audit["text"], "Uncut original text")
        self.assertEqual(audit["replacement_sources"], ["loops/corrected_oe1.m4a"])

    def test_zero_duration_ai_inside_authority_is_superseded(self):
        self.assertEqual(self.merge([self.row(12, 0)], [(10, 20)]), [])

    def test_backwards_utc_cannot_create_authority(self):
        self.write_csv([("00:00:10", "2026-04-06 01:00:00.000", "", "First"),
                        ("00:00:20", "2026-04-06 00:00:20.000", "", "Second")])
        with self.assertRaisesRegex(ValueError, "UTC runs backwards"):
            normalize_loop(self.csv, 30, [])

    def test_long_silence_and_squelch_clicks_do_not_become_long_playback(self):
        self.write_csv([("00:00:10", "2026-04-06 00:00:10.000", "", "Call")])
        payload = normalize_loop(self.csv, 100, [[15, 99], [99.3, 100]])
        self.assertAlmostEqual(payload["segments"][0]["end"], 15.1)

    def test_csv_utc_not_filename_or_audio_elapsed_time_controls_replacement(self):
        self.write_csv([("00:00:10", "2026-04-06 00:00:10.250", "Koch", "First"),
                        ("00:00:20", "2026-04-06 08:00:20.500", "Hansen", "Second")])
        payload = normalize_loop(self.csv, 30, [])
        self.assertEqual(len(payload["authority_intervals"]), 2)
        self.assertEqual(payload["segments"][1]["start"], 20)
        self.assertEqual(payload["segments"][1]["utcTime"], "2026-04-06T08:00:20.500000Z")
        self.assertLess(timestamp(payload["authority_intervals"][0]["end"]), self.base + 21)

    def test_untimed_rows_preserve_text_without_extending_authority(self):
        self.write_csv([("00:00:00", "2026-04-06 00:00:00.000", "Koch", "First"),
                        ("00:00:10", "2026-04-06", "Hansen", "Unknown time"),
                        ("00:00:20", "2026-04-06 01:00:00.000", "Koch", "Last")])
        payload = normalize_loop(self.csv, 30, [])
        self.assertEqual(len(payload["segments"]), 2)
        self.assertEqual(payload["untimed_segments"][0]["text"], "Unknown time")
        self.assertEqual(payload["authority_intervals"][0]["end"], "2026-04-06T00:00:10Z")

    def test_missing_decimal_repair_requires_both_adjacent_clocks(self):
        self.write_csv([("03:10:10", "2026-04-06 03:10:10.200", "", "Before"),
                        ("03:10:20", "2026-04-06 03:10:201200", "", "Repaired"),
                        ("03:10:30", "2026-04-06 03:10:30.250", "", "After")])
        payload = normalize_loop(self.csv, 12000, [])
        self.assertEqual(payload["segments"][1]["utcTime"], "2026-04-06T03:10:20.120000Z")
        self.assertEqual(len(payload["utc_repairs"]), 1)
        self.write_csv([("00:00:00", "2026-04-06 03:10:201200", "", "Uncorroborated")])
        self.assertEqual(len(normalize_loop(self.csv, 10, [])["untimed_segments"]), 1)

    def test_reordered_csv_rows_keep_original_row_ids(self):
        self.write_csv([("00:00:20", "2026-04-06 00:00:20.200", "", "Later"),
                        ("00:00:10", "2026-04-06 00:00:10.100", "", "Earlier")])
        payload = normalize_loop(self.csv, 30, [])
        self.assertEqual([s["row"] for s in payload["segments"]], [2, 1])
        self.assertEqual(len(payload["row_order_repairs"]), 2)

    def test_silence_shortens_playback_without_shortening_authority(self):
        self.write_csv([("00:00:10", "2026-04-06 00:00:10.000", "CAPCOM", "Call")])
        payload = normalize_loop(self.csv, 100, [[15, 100]])
        self.assertAlmostEqual(payload["segments"][0]["end"], 15.1)
        self.assertEqual(payload["authority_intervals"][0]["end"], "2026-04-06T00:01:40Z")

    def test_import_export_and_rerun_do_not_restore_superseded_ai(self):
        self.write_csv([("00:00:10", "2026-04-06 00:00:10.000", "Koch", "Thank you.")])
        audio = self.root / "art002_2026-04-07_oe1_raw_v01.m4a"
        audio.write_bytes(b"original audio")
        directory = self.mission.data_dir / "processed/pds"
        directory.mkdir(parents=True)
        base = {"bundle": BUNDLE, "sha256": "hash", "start_utc": "2026-04-06T00:00:00Z"}
        (directory / "audio.jsonl").write_text(json.dumps(dict(base, collection="data_audio_loops", path=str(audio))) + "\n")
        (directory / "transcripts.jsonl").write_text("\n".join(json.dumps(dict(base, collection="data_transcripts_loops", path=str(p)))
            for p in (self.csv, self.root / "collection_data_transcripts_loops_inventory.csv")))
        comm = self.mission.processed_transcripts / "comm"
        comm.mkdir(parents=True)
        ai = comm / "AI_OE_Comp_1_test.json"
        ai.write_text(json.dumps({"utcTime": "2026-04-06T00:00:10Z", "tz_corrected": True,
                                 "segments": [{"start": 0, "end": 5, "text": "AI wrong"}]}))
        original = ai.read_bytes()
        def remux(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"web audio")
        with patch("shared.pds_loops._duration", return_value=20), \
                patch("shared.pds_loops._silences", return_value=[]), \
                patch("shared.pds_loops._remux", side_effect=remux) as media:
            import_loops(self.mission)
            import_loops(self.mission)
            media.assert_called_once()
        self.assertFalse((self.mission.web_dir / "comm").exists())
        web.build_web_comm(self.mission)
        first = (self.mission.transcript_inputs / "comm.csv").read_bytes()
        web.build_web_comm(self.mission)
        self.assertEqual(first, (self.mission.transcript_inputs / "comm.csv").read_bytes())
        self.assertEqual(ai.read_bytes(), original)
        self.assertNotIn(b"AI wrong", first)
        self.assertIn(b"[Koch] Thank you.", first)
        self.assertEqual(timestamp(first.decode().split("|", 1)[0]), self.base + 10)
        entries = json.loads((self.mission.web_dir / "comm.json").read_text())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["speaker"], "Koch")
        self.assertEqual(entries[0]["text"], "Thank you.")


if __name__ == "__main__":
    unittest.main()
