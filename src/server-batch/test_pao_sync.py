import unittest
import json
import tempfile
from pathlib import Path
from shared.pao_sync import plan_coverage, validate_part_manifest, compact_sync_projection, part_catalog, cached_recordings, import_upload_state
from shared.pao_sync import packet_offset_evidence
from shared.pao_sync import apply_packet_evidence


class PaoSyncTests(unittest.TestCase):
    def test_packet_identity_requires_unique_consistent_sequence(self):
        chunk = [dict(data_hash=str(n), pts_time=n / 40) for n in range(30)]
        original = [dict(data_hash=str(n), pts_time=100 + n / 40) for n in range(30)]
        result = packet_offset_evidence(original, chunk)
        self.assertEqual(result["originalStartSeconds"], 100)
        self.assertEqual(result["verification"], "verified-packet-identity")
        self.assertEqual(packet_offset_evidence(original + original, chunk)["verification"], "unknown")
        original[-1]["pts_time"] += 0.1
        self.assertEqual(packet_offset_evidence(original, chunk)["verification"], "unknown")

    def test_measured_offsets_preserve_requested_cut_and_overlap(self):
        original = self.parts()
        measured = apply_packet_evidence(original, {"parts": [dict(filename="p2", verification="verified-packet-identity", originalStartSeconds=9.7)]}, "packet-report")
        self.assertEqual(measured["parts"][1]["originalStartSeconds"], 9.7)
        self.assertEqual(measured["parts"][1]["requestedSplitOffsetSeconds"], 10)
        self.assertEqual(original["parts"][1]["originalStartSeconds"], 10)

    def test_discontinuous_ia_and_yt_extensions(self):
        def r(id, source, a, b):
            return dict(id=id, source=source, feed="pao", startUtc=f"2026-04-01T00:00:{a:02d}Z", endUtc=f"2026-04-01T00:00:{b:02d}Z")
        rows = [r("yt", "youtube", 0, 50), r("ia1", "ia", 10, 20), r("ia2", "ia", 30, 40)]
        result = plan_coverage(rows, [dict(feed="pao", startUtc="2026-04-01T00:00:00Z", endUtc="2026-04-01T00:01:00Z")])
        self.assertEqual([r["source"] for r in result], ["youtube", "ia", "youtube", "ia", "youtube", "unknown"])
        self.assertTrue(all(r["verification"] != "verified" for r in result))

    def parts(self):
        return {"parts": [dict(part=1, filename="p1", originalStartSeconds=0, durationSeconds=10.2, uploadedVideoId="uploaded", uploadProvenance={"sha256": "abc"}), dict(part=2, filename="p2", originalStartSeconds=10, durationSeconds=5)]}

    def test_missing_reordered_parts_and_duplicate_upload(self):
        parts = self.parts()
        validate_part_manifest(parts)  # stream-copy overlap is retained
        parts["parts"].reverse()
        with self.assertRaises(ValueError):
            validate_part_manifest(parts)
        with self.assertRaises(ValueError):
            validate_part_manifest({"parts": [dict(part=2, filename="p", originalStartSeconds=0, durationSeconds=10)]})

    def test_verified_sync_requires_audio_and_exact_uploaded_part(self):
        piece = dict(id="s", feed="pao", startUtc="2026-04-01T00:00:00Z", endUtc="2026-04-01T00:00:05Z", partFilename="p1", recordingId="ia", recordingStartSeconds=0, videoStartSeconds=1, verification="candidate")
        self.assertEqual(compact_sync_projection({"mappings": [piece]}, self.parts())["mappings"], [])
        piece["verification"] = "verified"
        piece["evidence"] = [{"kind": "text", "reference": "match"}]
        with self.assertRaises(ValueError):
            compact_sync_projection({"mappings": [piece]}, self.parts())
        piece["evidence"] = [{"kind": "manual-audio-review", "reference": "review1"}]
        self.assertEqual(compact_sync_projection({"mappings": [piece]}, self.parts())["mappings"][0]["videoId"], "uploaded")
        piece["videoStartSeconds"] = 9
        with self.assertRaises(ValueError):
            compact_sync_projection({"mappings": [piece]}, self.parts())

    def test_playback_does_not_mutate_text_identity(self):
        parts = self.parts()
        parts["parts"] = parts["parts"][:1]
        self.assertEqual(part_catalog(parts, "2026-04-01T00:00:00Z")["parts"][0]["videoId"], "uploaded")
        self.assertNotIn("text", parts["parts"][0])

    def test_distinct_feeds_never_fill_each_others_gaps(self):
        row = dict(id="other", feed="other-feed", source="ia", startUtc="2026-04-01T00:00:00Z", endUtc="2026-04-01T00:01:00Z")
        result = plan_coverage([row], [dict(feed="pao", startUtc=row["startUtc"], endUtc=row["endUtc"])])
        self.assertEqual(result[0]["source"], "unknown")

    def test_overlapping_scope_rejected(self):
        scope = dict(feed="pao", startUtc="2026-04-01T00:00:00Z", endUtc="2026-04-01T00:01:00Z")
        with self.assertRaises(ValueError):
            plan_coverage([], [scope, scope])

    def test_cache_without_exact_parts_is_untimed_and_inputs_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ia, yt = base / "ia", base / "yt"
            ia.mkdir()
            yt.mkdir()
            original = json.dumps(dict(source="p1", part=1, duration=10, segments=[dict(text="Keep rebroadcast speech", speaker="SPEAKER_00")]))
            path = yt / "stream_part01.json"
            path.write_text(original)
            rows, untimed = cached_recordings(ia, yt)
            self.assertEqual(rows, [])
            self.assertEqual(len(untimed), 1)
            self.assertEqual(path.read_text(), original)

    def test_upload_state_import_keeps_provenance_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".yt_upload_state.json"
            path.write_text(json.dumps({"p2": "uploaded2"}))
            manifest = self.parts()
            imported = import_upload_state(manifest, path)
            self.assertNotIn("uploadedVideoId", manifest["parts"][1])
            self.assertEqual(imported["parts"][1]["uploadedVideoId"], "uploaded2")
            self.assertEqual(len(imported["parts"][1]["uploadProvenance"]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
