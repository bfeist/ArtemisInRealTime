import importlib
import json
from pathlib import Path
import tempfile
import unittest

from shared.pao_policy import reviewed_playback, apply_dataset_policy, validate_audio_derivatives
import hashlib


class PaoPolicyTests(unittest.TestCase):
    def setup_policy(self):
        row = dict(uid="legacy-unchanged", channel="pao", t="2026-04-01T00:00:05Z", text="Accepted wording", category="other", d=2)
        coverage = dict(schemaVersion=1, status="reviewed", review=dict(reviewer="reviewer", reference="review-1"), intervals=[dict(feed="pao-primary", startUtc="2026-04-01T00:00:00Z", endUtc="2026-04-01T00:01:00Z", source="youtube", recordingId="yt", verification="verified", reviewReference="boundary-review")])
        parts = {"parts": [dict(part=1, filename="p1", originalStartSeconds=0, durationSeconds=60, uploadedVideoId="upload", uploadProvenance={"sha256": "hash"})]}
        sync = {"mappings": [dict(id="map1", feed="pao-primary", startUtc="2026-04-01T00:00:00Z", endUtc="2026-04-01T00:01:00Z", recordingId="yt", recordingStartSeconds=0, videoStartSeconds=0, partFilename="p1", verification="verified", evidence=[dict(kind="manual-audio-review", reference="review-1")])]}
        return row, coverage, sync, parts

    def test_schema_negative_nonfinite_and_known_duration_outside_map_rejected(self):
        row, coverage, sync, parts = self.setup_policy()
        for version in (None, 2):
            coverage["schemaVersion"] = version
            with self.assertRaises(ValueError):
                reviewed_playback([row], coverage, sync, parts)
        coverage["schemaVersion"] = 1
        for duration in (-1, float("inf"), float("nan")):
            row["d"] = duration
            with self.assertRaises(ValueError):
                reviewed_playback([row], coverage, sync, parts)
        row["d"] = 2
        sync["mappings"][0]["endUtc"] = "2026-04-01T00:00:06Z"
        with self.assertRaises(ValueError):
            reviewed_playback([row], coverage, sync, parts)

    def test_audio_must_be_nonempty_and_review_hash_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "comm").mkdir()
            path = root / "comm/clip.aac"
            selections = {"uid": dict(recordingId="ia", audio=dict(src="comm/clip.aac", s=0, d=1))}
            recordings = {"ia": {}}
            path.touch()
            with self.assertRaises(ValueError):
                validate_audio_derivatives(selections, recordings, root)
            path.write_bytes(b"reviewed-media")
            recordings["ia"]["audioSha256"] = "wrong"
            with self.assertRaises(ValueError):
                validate_audio_derivatives(selections, recordings, root)
            recordings["ia"]["audioSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            receipt = validate_audio_derivatives(selections, recordings, root)
            self.assertEqual(receipt["comm/clip.aac"]["bytes"], 14)
            selections["uid"]["audio"]["src"] = "../outside.aac"
            with self.assertRaises(ValueError):
                validate_audio_derivatives(selections, recordings, root)

    def test_candidate_and_text_only_evidence_fail_closed(self):
        row, coverage, sync, parts = self.setup_policy()
        coverage["status"] = "candidate"
        with self.assertRaises(ValueError):
            reviewed_playback([row], coverage, sync, parts)
        coverage["status"] = "reviewed"
        sync["mappings"][0]["evidence"][0]["kind"] = "text"
        with self.assertRaises(ValueError):
            reviewed_playback([row], coverage, sync, parts)

    def test_unknown_retains_old_and_utterance_crossing_boundary_rejected(self):
        row, coverage, sync, parts = self.setup_policy()
        coverage["intervals"][0]["source"] = "unknown"
        selected, receipt = reviewed_playback([row], coverage, sync, parts)
        self.assertEqual(selected, {})
        self.assertEqual(receipt["unchangedPaoUids"], [row["uid"]])
        coverage["intervals"][0]["source"] = "youtube"
        coverage["intervals"][0]["endUtc"] = "2026-04-01T00:00:06Z"
        with self.assertRaises(ValueError):
            reviewed_playback([row], coverage, sync, parts)

    def test_missing_utterance_duration_caps_at_all_authority_boundaries(self):
        row, coverage, sync, parts = self.setup_policy()
        row.pop("d")
        sync["mappings"][0]["endUtc"] = "2026-04-01T00:00:20Z"
        sync["mappings"][0]["rate"] = 1.1
        selections, _ = reviewed_playback([row], coverage, sync, parts)
        self.assertAlmostEqual(selections[row["uid"]]["video"]["d"], 16.5)
        coverage["intervals"][0]["source"] = "ia"
        coverage["recordings"] = {"yt": dict(verification="verified", reviewReference="clock", startUtc="2026-04-01T00:00:00Z", durationSeconds=10, audioPath="comm/clip.aac")}
        selections, _ = reviewed_playback([row], coverage, sync, parts)
        self.assertEqual(selections[row["uid"]]["audio"]["d"], 5)
        self.assertAlmostEqual(selections[row["uid"]]["video"]["d"], 5.5)

    def test_staged_dataset_preserves_labels_text_ids_and_original_files(self):
        adapter = importlib.import_module("1_comm.transcript_dataset")
        row, coverage, sync, parts = self.setup_policy()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "processed"
            source, output = base / "source", base / "staged"
            source.mkdir(parents=True)
            artifacts = {}
            objects = dict(utterances=[row], assessments=[dict(uid=row["uid"], importance=3, category="accepted")], milestones=[], investigations=[], unresolved=[], history_report={}, published_transcripts={"pao": [dict(t=row["t"], text=row["text"], importance=3)], "comm": []}, entry_mapping=[dict(channel="pao", position=0, uid=row["uid"])])
            for name, data in objects.items():
                suffix = ".jsonl" if name in {"utterances", "assessments", "milestones", "investigations", "unresolved"} else ".json"
                path = source / (name + suffix)
                path.write_text("".join(json.dumps(r) + "\n" for r in data) if suffix == ".jsonl" else json.dumps(data))
                artifacts[name] = dict(path=path.name, sha256=adapter.checksum(path))
            adapter.write(source / "manifest.json", dict(contract=adapter.CONTRACT, report=dict(complete=True), artifacts=artifacts))
            paths = []
            for name, obj in [("coverage", coverage), ("sync", sync), ("parts", parts)]:
                path = base / (name + ".json")
                adapter.write(path, obj)
                paths.append(path)
            before = (source / "utterances.jsonl").read_bytes()
            apply_dataset_policy(source / "manifest.json", *paths, output)
            staged = adapter.read_dataset(output / "manifest.json")
            self.assertEqual(staged["utterances"][0]["uid"], row["uid"])
            self.assertEqual(staged["utterances"][0]["text"], row["text"])
            self.assertEqual(staged["published_transcripts"]["pao"][0]["playback"]["video"]["s"], 5)
            self.assertEqual((output / "assessments.jsonl").read_bytes(), (source / "assessments.jsonl").read_bytes())
            self.assertEqual((source / "utterances.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
