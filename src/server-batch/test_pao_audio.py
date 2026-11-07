"""Exact occurrence recovery, compact media preparation, and publication reuse."""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from config import MissionConfig
from shared.pao_audio import apply_pao_audio, prepare_pao_audio, recover_occurrences
from shared.pds import atomic_json
from shared.web_release import stage_release


class PaoRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.start = "2026-04-01T11:43:14.521Z"
        self.rows = []
        for number in (1, 2):
            atomic_json(self.root / f"coverage_part{number:02d}.json", {
                "part": number, "duration": 3, "source": f"coverage_part{number:02d}.mp4",
                "segments": [{"start": 0.5, "end": 2.5, "text": " Repeated announcement. "}]})
            self.rows.append({"t": f"2026-04-01T11:43:{15 + (number - 1) * 3:02d}.021Z",
                              "text": "Repeated announcement.", "category": "mission_background"})

    def test_repetition_uses_exact_occurrence_and_local_offsets(self):
        entries, _, failures = recover_occurrences(self.rows, self.root, self.start)
        self.assertEqual(failures, [])
        self.assertEqual([r["recordingId"] for r in entries], ["youtube-part-01", "youtube-part-02"])
        self.assertEqual([(r["s"], r["d"]) for r in entries], [(0.5, 2), (0.5, 2)])

    def test_missing_and_ambiguous_occurrences_are_not_guessed(self):
        path = self.root / "coverage_part01.json"
        data = json.loads(path.read_text())
        data["segments"] *= 2
        atomic_json(path, data)
        rows = [self.rows[0], {**self.rows[1], "text": "Changed speech."}]
        entries, _, failures = recover_occurrences(rows, self.root, self.start)
        self.assertEqual(entries, [])
        self.assertEqual([r["matches"] for r in failures], [2, 0])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_encode_resume_publish_and_reject_changed_text_or_bytes(self):
        patcher = patch("config.DATA_DIR", self.root / "assets")
        patcher.start()
        self.addCleanup(patcher.stop)
        mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test")
        transcript_dir = mission.processed_transcripts / "yt"
        transcript_dir.mkdir(parents=True)
        for path in self.root.glob("*.json"):
            shutil.copyfile(path, transcript_dir / path.name)
        for number in (1, 2):
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=44100:duration=3", "-ac", "2", "-c:a", "aac",
                str(self.root / f"coverage_part{number:02d}.mp4")], check=True, capture_output=True)
        report = prepare_pao_audio(mission, self.root, self.rows, stream_start=self.start, workers=2)
        manifest_path = Path(report["manifest"])
        manifest = json.loads(manifest_path.read_text())
        manifest_bytes = manifest_path.read_bytes()
        times = {path: path.stat().st_mtime_ns for path in mission.comm_audio_cache.rglob("*.m4a")}
        prepare_pao_audio(mission, self.root, self.rows, stream_start=self.start, workers=2)
        self.assertEqual(manifest_path.read_bytes(), manifest_bytes)
        self.assertEqual(times, {path: path.stat().st_mtime_ns for path in times})
        for recording in manifest["recordings"].values():
            stream = recording["probe"]["streams"][0]
            self.assertEqual((stream["codec_name"], stream["sample_rate"], stream["channels"]), ("aac", "22050", 1))
            path = mission.comm_audio_cache / recording["audioPath"][5:]
            content = path.read_bytes()
            self.assertLess(content.index(b"moov"), content.index(b"mdat"))
        transcripts = {"comm": [], "pao": self.rows}
        publication = importlib.import_module("1_comm.1i_web_transcripts")
        output, receipts, provenance = publication.prepared_pao(mission, transcripts)
        self.assertIn("pao_audio_manifest_sha256", provenance)
        self.assertEqual([{k: v for k, v in row.items() if k != "playback"} for row in output["pao"]], self.rows)
        release, _ = stage_release(mission, output, release_id="pao", pao_audio_receipts=receipts)
        self.assertTrue((release / output["pao"][0]["playback"]["audio"]["src"]).is_file())
        with self.assertRaisesRegex(ValueError, "does not match"):
            apply_pao_audio({"pao": [{**self.rows[0], "text": "Changed"}]}, manifest_path)
        path = next(iter(times))
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            prepare_pao_audio(mission, self.root, self.rows, stream_start=self.start, workers=2)
        with self.assertRaises(ValueError):
            stage_release(mission, output, release_id="tampered", pao_audio_receipts=receipts)


if __name__ == "__main__":
    unittest.main()
