"""PCD UTC, supplied transcript preference, playback offsets, and comm export."""
import importlib
import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from config import MissionConfig
from shared.pds import db
from shared.pds_comm import BUNDLE, enrich_pcd, import_pcd, supplied_segments, utc_time
from shared.comm_encoding import AAC_PROFILE

web = importlib.import_module("1_comm.1c_web_comm")


class PcdCommTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = patch.multiple("config", DATA_DIR=self.root, PDS_ASSETS_DIR=self.root)
        self.paths.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.paths.stop)
        self.mission = MissionConfig("PCD test", "test", "2026-04-01", "2026-04-11", "test",
                                     ia_comm_collection="test", comm_tz_offset_hours=5)
        self.audio = self.root / "art002a000005_pcd3_raw_v01.m4a"
        self.audio.write_bytes(b"audio")
        self.csv = self.root / "art002a000005_pcd3_trn-csv_v01.csv"
        self.csv.write_text('audio_time,utc,speaker,text\n00:00:03,2026-04-06 20:03:55.301,Koch,"Thank you."\n', encoding="utf-8")
        self.srt = self.root / "art002a000005_pcd3_trn-srt_v01.srt"
        self.srt.write_text("1\n00:00:03,500 --> 00:00:06,000\n[Koch] Thank you.\n", encoding="utf-8")
        self.record = {"bundle": BUNDLE, "collection": "data_audio_pcd", "nasa_id": "art002a000005",
                       "start_utc": "2026-04-06T20:03:51Z", "path": str(self.audio), "sha256": "audio1"}

    def catalogs(self, supplied=True):
        directory = self.mission.data_dir / "processed/pds"
        directory.mkdir(parents=True)
        (directory / "audio.jsonl").write_text(json.dumps(self.record) + "\n", encoding="utf-8")
        records = [dict(self.record, collection="data_transcripts_pcd", path=str(p), sha256=p.suffix)
                   for p in (self.csv, self.srt)] if supplied else []
        (directory / "transcripts.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    def test_label_utc_needs_no_io_and_no_filename_guess(self):
        lookup = unittest.mock.Mock(side_effect=AssertionError("IO should not be called"))
        enrich_pcd(self.record, self.mission, lookup)
        self.assertEqual(self.record["time_source"], "pds_label")
        self.assertEqual(self.record["channel"], "PCD")
        self.assertIsNone(utc_time("2026-04-06"))
        self.assertIsNone(utc_time("2026-04-06T20:03:51"))

    def test_io_exact_id_utc_and_cache(self):
        self.record["start_utc"] = None
        doc = {"nasa_id": "art002a000005", "vmd_start_gmt": "2026-04-06T20:03:51.877Z"}
        lookup = unittest.mock.Mock(return_value={"results": {"response": {"docs": [doc]}}})
        enrich_pcd(self.record, self.mission, lookup)
        self.assertEqual(self.record["start_utc"], utc_time(doc["vmd_start_gmt"]))
        self.assertEqual(self.record["time_source"], "imagery_online")
        self.record["start_utc"] = None
        enrich_pcd(self.record, self.mission, lookup)
        lookup.assert_called_once()

    def test_ambiguous_io_does_not_supply_utc(self):
        self.record["start_utc"] = None
        docs = [{"nasa_id": "art002a000005", "vmd_start_gmt": t} for t in
                ("2026-04-06T20:03:51Z", "2026-04-06T21:03:51Z")]
        enrich_pcd(self.record, self.mission, lambda _: {"results": {"response": {"docs": docs}}})
        self.assertIsNone(self.record["start_utc"])

    def test_csv_utc_and_srt_offsets_are_independent(self):
        segment = supplied_segments(self.csv, self.srt, 10)[0]
        self.assertEqual(segment["start"], 3.5)
        self.assertEqual(segment["end"], 6)
        self.assertEqual(segment["csv_audio_start"], 3)
        self.assertEqual(segment["utcTime"], "2026-04-06T20:03:55.301000Z")
        self.assertEqual(segment["speaker"], "Koch")

    def test_mismatched_transcript_text_is_rejected(self):
        self.srt.write_text("1\n00:00:03,000 --> 00:00:06,000\n[Koch] Different text.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            supplied_segments(self.csv, self.srt, 10)

    def test_supplied_import_and_export_are_idempotent_and_preserve_utc(self):
        self.catalogs()
        def media(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"web audio")
        with patch("shared.pds_comm._duration", return_value=10), patch("shared.pds_comm._web_audio", side_effect=media) as encode:
            import_pcd(self.mission)
            import_pcd(self.mission)
            encode.assert_called_once()
        self.assertFalse((self.mission.web_dir / "comm").exists())
        self.assertTrue((self.mission.comm_audio_cache / "pcd/art002a000005.m4a").is_file())
        web.build_web_comm(self.mission)
        row = (self.mission.web_dir / "comm.csv").read_text(encoding="utf-8").strip().split("|")
        self.assertEqual(row, ["2026-04-06T20:03:55.301000Z", "3.5", "2.5", "pcd/art002a000005.m4a", "[Koch] Thank you."])
        sources = json.loads((self.mission.web_dir / "comm_sources.json").read_text())
        self.assertEqual(sources[row[3]]["source_kind"], "crew_onboard")
        self.assertEqual(sources[row[3]]["transcript_source"], "pds_supplied")
        entry = json.loads((self.mission.web_dir / "comm.json").read_text())[0]
        self.assertEqual(entry["speaker"], "Koch")
        self.assertEqual(entry["text"], "Thank you.")
        self.assertEqual((entry["s"], entry["d"]), (3.5, 2.5))

    def test_advertised_but_unavailable_transcript_does_not_trigger_asr(self):
        self.catalogs(supplied=False)
        conn = db(self.mission)
        conn.execute("INSERT INTO remote(bundle,collection,relative,uri,status) VALUES(?,?,?,?,?)",
                     (BUNDLE, "data_transcripts_pcd", "data_transcripts_pcd/" + self.csv.name, "csv", "failed"))
        conn.commit()
        conn.close()
        with patch("shared.pds_comm._duration", side_effect=AssertionError("Must wait for supplied transcript")):
            import_pcd(self.mission)
        report = json.loads((self.mission.data_dir / "processed/pds/pcd_comm_report.json").read_text())
        self.assertEqual(report["counts"], {"awaiting_supplied_transcript": 1})

    def test_legacy_audio_upgrades_during_regular_build_without_retranscribing(self):
        self.catalogs()
        def media(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".part")
            temporary.write_bytes(b"new encoded audio")
            temporary.replace(target)
        with patch("shared.pds_comm._duration", return_value=10), patch("shared.pds_comm._web_audio", side_effect=media):
            import_pcd(self.mission)
        web.build_web_comm(self.mission)
        before_csv = (self.mission.web_dir / "comm.csv").read_bytes()
        transcript = self.mission.processed_transcripts / "comm/pcd/art002a000005.json"
        previous = json.loads(transcript.read_text())
        del previous["audio_encoding"]
        transcript.write_text(json.dumps(previous))
        with patch("shared.pds_comm._web_audio", side_effect=media) as encode, \
                patch("shared.pds_comm._duration", side_effect=AssertionError("No transcript/timing rebuild")):
            web.build_web_comm(self.mission)
            import_pcd(self.mission)
            encode.assert_called_once_with(self.audio, self.mission.comm_audio_cache / "pcd/art002a000005.m4a")
        upgraded = json.loads(transcript.read_text())
        self.assertEqual(upgraded.pop("audio_encoding"), AAC_PROFILE)
        self.assertEqual(upgraded, previous)
        self.assertEqual((self.mission.web_dir / "comm.csv").read_bytes(), before_csv)

    def test_import_also_upgrades_audio_profile_without_retranscribing(self):
        self.catalogs()
        def media(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"audio")
        with patch("shared.pds_comm._duration", return_value=10), patch("shared.pds_comm._web_audio", side_effect=media):
            import_pcd(self.mission)
        transcript = self.mission.processed_transcripts / "comm/pcd/art002a000005.json"
        data = json.loads(transcript.read_text())
        data["audio_encoding"] = {**AAC_PROFILE, "bitrate": "96k", "channels": 2}
        transcript.write_text(json.dumps(data))
        with patch("shared.pds_comm._duration", side_effect=AssertionError("No transcript/timing rebuild")), \
                patch("shared.pds_comm._web_audio", side_effect=media) as encode:
            import_pcd(self.mission)
            import_pcd(self.mission)
            encode.assert_called_once()
        self.assertEqual(json.loads(transcript.read_text())["segments"], data["segments"])

    def test_missing_transcript_catalog_cannot_imply_absent_transcripts(self):
        self.catalogs()
        (self.mission.data_dir / "processed/pds/transcripts.jsonl").unlink()
        with self.assertRaisesRegex(RuntimeError, "No PDS transcript catalog"):
            import_pcd(self.mission)

    def test_csv_without_srt_uses_next_start_and_media_duration(self):
        segment = supplied_segments(self.csv, None, 10)[0]
        self.assertEqual((segment["start"], segment["end"]), (3, 10))
        self.assertEqual(segment["end_basis"], "next_start_or_audio_end")

    def test_comm_sort_accepts_mixed_utc_precision(self):
        directory = self.mission.processed_transcripts / "comm"
        directory.mkdir(parents=True)
        for i, stamp in enumerate(("2026-04-06T20:03:51Z", "2026-04-06T20:03:51.001Z")):
            (directory / f"{i}.json").write_text(json.dumps({
                "utcTime": stamp, "tz_corrected": True,
                "segments": [{"start": 0, "end": 1, "text": str(i), "utcTime": stamp}]}))
            self.mission.comm_audio_cache.mkdir(parents=True, exist_ok=True)
            (self.mission.comm_audio_cache / f"{i}.aac").write_bytes(b"audio")
        web.build_web_comm(self.mission)
        rows = (self.mission.web_dir / "comm.csv").read_text().splitlines()
        self.assertTrue(rows[0].endswith("|0"))
        self.assertTrue(rows[1].endswith("|1"))
        entries = json.loads((self.mission.web_dir / "comm.json").read_text())
        self.assertTrue(all("speaker" not in entry for entry in entries))

    def test_missing_delivery_transcript_uses_whisper_and_absolute_utc(self):
        self.catalogs(supplied=False)
        transcriber = importlib.import_module("1_comm.1b_transcribe")
        model = unittest.mock.Mock()
        model.transcribe.return_value = {"segments": [{"start": 2, "end": 4, "text": "Observation."}]}
        def media(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"web audio")
        with patch("shared.pds_comm._duration", return_value=10), \
                patch("shared.pds_comm._web_audio", side_effect=media), \
                patch.object(transcriber, "WhisperResources", return_value=model), \
                patch.object(transcriber, "_ffmpeg_exe", return_value="ffmpeg"), \
                patch("shared.pds_comm.subprocess.run", return_value=SimpleNamespace(stdout=b"\0" * 64)):
            import_pcd(self.mission)
        output = next((self.mission.processed_transcripts / "comm/pcd").glob("*.json"))
        payload = json.loads(output.read_text())
        self.assertEqual(payload["transcript_source"], "whisperx_large-v3")
        self.assertEqual(payload["segments"][0]["utcTime"], "2026-04-06T20:03:53Z")
        model.transcribe.assert_called_once()

    def test_supplied_timing_error_preserves_text_and_utc_with_bounded_playback(self):
        self.srt.write_text("1\n00:00:09,000 --> 00:00:19,000\n[Koch] Thank you.\n", encoding="utf-8")
        self.catalogs()
        def media(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"web audio")
        with patch("shared.pds_comm._duration", return_value=10), patch("shared.pds_comm._web_audio", side_effect=media):
            import_pcd(self.mission)
        output = next((self.mission.processed_transcripts / "comm/pcd").glob("*.json"))
        payload = json.loads(output.read_text())
        segment = payload["segments"][0]
        self.assertEqual((segment["start"], segment["end"], segment["supplied_end"]), (9, 10, 19))
        self.assertEqual(segment["utcTime"], "2026-04-06T20:03:55.301000Z")
        self.assertEqual(len(payload["timing_adjustments"]), 1)


if __name__ == "__main__":
    unittest.main()
