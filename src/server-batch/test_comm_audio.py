"""Publication follows surviving utterances; retired derivatives remain recoverable."""

import importlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from config import MissionConfig
from shared.comm_audio import audio_relative_path
from shared.comm_encoding import AAC_PROFILE

prepare = importlib.import_module("1_comm.1c_prepare_comm")


class CommAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = patch("config.DATA_DIR", self.root)
        config.start()
        self.addCleanup(config.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-11", "test",
                                     ia_comm_collection="test")
        self.transcripts = self.mission.processed_transcripts / "comm"
        self.transcripts.mkdir(parents=True)
        self.audio = self.mission.web_dir / "comm"

    def write_audio(self, relative, cached=False, content=b"prepared audio"):
        target = (self.mission.comm_audio_cache if cached else self.audio) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def transcript(self, name, source=None, segments=None, **extra):
        payload = {"utcTime": "2026-04-06T00:00:00Z", "tz_corrected": True,
                   "segments": segments if segments is not None else [
                       {"start": 10, "end": 15, "text": "Call received."}]}
        if source is not None:
            payload["web_src"] = source
        payload.update(extra)
        path = self.transcripts / (name + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def published(self):
        return {p.relative_to(self.audio).as_posix() for p in self.audio.rglob("*") if p.is_file()}

    def test_final_rows_publish_mixed_audio_and_retire_only_unused_files(self):
        superseded = "AI_OE_Comp_1_2026-04-06_00_00_00"
        partial = "partial_OE_Comp_1_2026-04-06_00_00_00"
        self.transcript(superseded)
        self.transcript(partial, segments=[{"start": 10, "end": 15, "text": "Replaced."},
                                            {"start": 30, "end": 35, "text": "Retain this."}])
        loop = self.transcript("loop", "loops/corrected_oe1.m4a", source_kind="mission_voice_loop",
            channel="OE-1", transcript_source="pds_supplied", authority_intervals=[
                {"start": "2026-04-06T00:00:10Z", "end": "2026-04-06T00:00:20Z"}])
        self.transcript("pcd", "pcd/crew.m4a", source_kind="crew_onboard", channel="PCD",
                        audio_encoding=AAC_PROFILE)
        self.transcript("excluded_MISSION_AUDIO")
        self.transcript("hallucination", segments=[{"start": 0, "end": 1, "text": "Thank you."}])
        stale = [f"2026-04-06/{superseded}.aac", "2026-04-06/excluded_MISSION_AUDIO.aac",
                 "hallucination.aac"]
        for relative in stale:
            self.write_audio(relative)
        kept = f"2026-04-06/{partial}.aac"
        self.write_audio(kept)
        self.write_audio("loops/corrected_oe1.m4a", cached=True)
        self.write_audio("pcd/crew.m4a", cached=True)
        original_transcript = (self.transcripts / (superseded + ".json")).read_bytes()

        prepare.prepare_comm(self.mission)
        self.assertFalse((self.mission.web_dir / "comm.json").exists())
        self.assertFalse((self.mission.web_dir / "comm.csv").exists())
        self.assertEqual(self.published(), set(stale) | {kept})
        for relative in stale:
            self.assertEqual((self.audio / relative).read_bytes(), b"prepared audio")
        report = json.loads((self.mission.transcript_inputs / "comm_audio_manifest.json").read_text())
        self.assertEqual(report[kept]["utterances"], 1)
        self.assertEqual((self.transcripts / (superseded + ".json")).read_bytes(), original_transcript)
        first = (self.mission.transcript_inputs / "comm.json").read_bytes()
        prepare.prepare_comm(self.mission)
        self.assertEqual((self.mission.transcript_inputs / "comm.json").read_bytes(), first)
        self.assertFalse((self.mission.data_dir / "processed/comm_audio_reconciliations").exists())

        # Removing corrected authority restores AI directly from preserved audio.
        loop.unlink()
        prepare.prepare_comm(self.mission)
        self.assertIn(stale[0], self.published())
        self.assertNotIn("loops/corrected_oe1.m4a", self.published())
        self.assertEqual(set(json.loads((self.mission.transcript_inputs / "comm_sources.json").read_text())), {"pcd/crew.m4a"})

    def test_missing_required_audio_preserves_previous_indexes_and_stale_audio(self):
        self.transcript("missing")
        stale = self.write_audio("old.aac")
        previous = self.mission.web_dir / "comm.json"
        previous.write_text('[{"src":"old.aac"}]')
        with self.assertRaisesRegex(RuntimeError, "Missing or empty comm audio"):
            prepare.prepare_comm(self.mission)
        self.assertEqual(previous.read_text(), '[{"src":"old.aac"}]')
        self.assertTrue(stale.is_file())

    def test_empty_export_and_invalid_transcript_do_not_retire_audio(self):
        stale = self.write_audio("old.aac")
        path = self.transcript("empty", segments=[])
        with self.assertRaisesRegex(RuntimeError, "Refusing to publish empty comm"):
            prepare.prepare_comm(self.mission)
        path.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            prepare.prepare_comm(self.mission)
        self.assertTrue(stale.is_file())

    def test_failed_index_write_does_not_retire_audio(self):
        self.transcript("needed")
        self.write_audio("needed.aac", cached=True)
        stale = self.write_audio("old.aac")
        with patch.object(prepare, "atomic_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                prepare.prepare_comm(self.mission)
        self.assertTrue(stale.is_file())

    def test_cache_updates_replace_web_audio_and_retirement_keeps_older_versions(self):
        self.transcript("needed")
        self.write_audio("needed.aac", content=b"old")
        self.write_audio("needed.aac", cached=True, content=b"new")
        self.write_audio("unused.aac", content=b"older unused")
        self.write_audio("unused.aac", cached=True, content=b"newer unused")
        prepare.prepare_comm(self.mission)
        self.assertEqual((self.audio / "needed.aac").read_bytes(), b"old")
        self.assertEqual((self.mission.comm_audio_cache / "unused.aac").read_bytes(), b"newer unused")
        retired = list((self.mission.comm_audio_cache / "_retired").rglob("unused.aac"))
        self.assertEqual(len(retired), 0)
        self.assertEqual((self.audio / "unused.aac").read_bytes(), b"older unused")

    def test_legacy_path_uses_filename_date_and_new_explicit_paths_are_preserved(self):
        name = "AI_OE_Comp_1_2026-04-05_19_00_00"
        self.transcript(name)
        self.write_audio("2026-04-05/" + name + ".aac")
        self.transcript("explicit", "2026-04-06/explicit.aac")
        self.write_audio("2026-04-06/explicit.aac", cached=True)
        prepare.prepare_comm(self.mission)
        self.assertEqual(self.published(), {"2026-04-05/" + name + ".aac"})

    def test_escaping_references_and_linked_publication_paths_fail_without_moves(self):
        for invalid in ("../outside.aac", "/absolute.aac", "F:/outside.aac", "loops/../outside.aac", "loops\\outside.aac"):
            with self.subTest(source=invalid), self.assertRaises(ValueError):
                audio_relative_path(invalid)
        self.transcript("linked", "linked.aac")
        outside = self.root / "outside.aac"
        outside.write_bytes(b"outside")
        self.audio.mkdir(parents=True)
        try:
            (self.audio / "linked.aac").symlink_to(outside)
        except OSError:
            self.skipTest("Creating symbolic links requires privileges")
        with self.assertRaises(ValueError):
            prepare.prepare_comm(self.mission)
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_radio_and_excluded_mission_audio_backfill_stay_outside_web(self):
        radio = importlib.import_module("1_comm.1b_transcribe")
        mission_audio = importlib.import_module("1_comm.1b2_transcribe_mission_audio")
        start = datetime(2026, 4, 6, tzinfo=timezone.utc)
        wav = self.mission.raw_comm / "radio.wav"
        wav.parent.mkdir(parents=True)
        wav.write_bytes(b"original WAV")
        directory = self.transcripts / "2026-04-06"
        directory.mkdir()
        (directory / "radio.json").write_text(json.dumps({"web_src": "2026-04-06/radio.aac"}))
        def encode(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"cached AAC")
        with patch.object(radio, "collect_wav_files", return_value=[(wav, start)]), \
                patch.object(radio, "convert_wav_to_aac", side_effect=encode):
            radio.transcribe_comm(self.mission)
        with patch.object(mission_audio, "find_mission_audio_zips", return_value=[self.root / "audio.zip"]), \
                patch.object(mission_audio, "extract_zip", return_value=self.root), \
                patch.object(mission_audio, "collect_mission_audio_wavs", return_value=[(wav, start)]):
            mission_audio.transcribe_mission_audio(self.mission)
        self.assertFalse(self.audio.exists())
        self.assertEqual((self.mission.comm_audio_cache / "2026-04-06/radio.aac").read_bytes(), b"cached AAC")
        self.assertEqual(wav.read_bytes(), b"original WAV")


if __name__ == "__main__":
    unittest.main()
