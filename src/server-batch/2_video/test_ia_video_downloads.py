"""Checks for mission selection and interrupted MP4 transfers."""

import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.ia_video_downloads import NASA_ID, coverage_exclusion, fetch_media, fetch_mp4, plan_lock, run_transfers, select_mp4
originals = importlib.import_module("2_video.2l_ia_originals")


class Response:
    def __init__(self, data, status=200, headers=None, fail=False):
        self.data, self.status_code, self.headers, self.fail = data, status, headers or {}, fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.data
        if self.fail:
            raise requests.ConnectionError("interrupted")


class SelectionTests(unittest.TestCase):
    def test_preserves_distinct_nasa_ids(self):
        self.assertEqual(NASA_ID.search("UHD_art000m1501010058_SHARED.mxf")[1], "art000m1501010058")
        self.assertEqual(NASA_ID.search("art002m1501002359B_SHARED.mxf")[1], "art002m1501002359B")

    def test_excludes_recaps_and_preflight_but_keeps_continuous_recording(self):
        mission = SimpleNamespace(mission_start="2026-03-30", mission_end="2026-04-12")
        doc = {"vmd_start_gmt": "2026-04-05T13:58:59Z", "md_title": "Flight Day Four Highlights"}
        self.assertEqual(coverage_exclusion(doc, mission), "standalone_highlights_or_recap")
        doc["md_title"] += " / Continuous Coverage Part 90"
        self.assertIsNone(coverage_exclusion(doc, mission))
        doc["vmd_start_gmt"] = "2025-08-01T12:00:00Z"
        self.assertEqual(coverage_exclusion(doc, mission), "outside_mission_dates")
        self.assertEqual(coverage_exclusion(None, mission), "no_exact_io_mission_match")

    def test_mp4_only_and_complete_derivative_preferred(self):
        files = [{"name": "a.mxf", "size": "9000", "length": "3660"},
                 {"name": "a.mp4", "size": "200", "length": "2638"},
                 {"name": "a.ia.mp4", "size": "100", "length": "3660"}]
        self.assertEqual(select_mp4(files)["name"], "a.ia.mp4")
        self.assertIsNone(select_mp4(files[:1]))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name) / "clip.mp4"
        self.part = self.dest.with_suffix(".mp4.part")
        self.record = {"local_path": str(self.dest), "download_url": "https://archive.org/download/item/clip.mp4",
                       "bytes": 6, "md5": hashlib.md5(b"abcdef").hexdigest()}

    def test_resumes_range_and_validates_checksum(self):
        self.part.write_bytes(b"abc")
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(
            b"def", 206, {"Content-Range": "bytes 3-5/6"}
        )) as get:
            self.assertEqual(fetch_mp4(self.record)["status"], "complete")
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=3-5")
        self.assertEqual(self.dest.read_bytes(), b"abcdef")
        self.assertFalse(self.part.exists())

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        self.part.write_bytes(b"abc")
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(b"abcdef")):
            self.assertEqual(fetch_mp4(self.record)["status"], "complete")
        self.assertEqual(self.dest.read_bytes(), b"abcdef")

    def test_interrupted_download_remains_partial(self):
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(b"abc", fail=True)):
            self.assertEqual(fetch_mp4(self.record, attempts=1)["status"], "failed")
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.part.read_bytes(), b"abc")

    def test_reuses_only_verified_existing_file(self):
        self.dest.write_bytes(b"abcdef")
        with patch("shared.ia_video_downloads.requests.get") as get:
            self.assertTrue(fetch_mp4(self.record)["reused"])
            get.assert_not_called()
        self.dest.write_bytes(b"broken")
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(b"abcdef")):
            self.assertEqual(fetch_mp4(self.record)["status"], "complete")
        self.assertEqual(self.dest.read_bytes(), b"abcdef")

    def test_wrong_range_does_not_corrupt_partial(self):
        self.part.write_bytes(b"abc")
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(
            b"def", 206, {"Content-Range": "bytes 0-2/6"}
        )):
            self.assertEqual(fetch_mp4(self.record, attempts=1)["status"], "failed")
        self.assertEqual(self.part.read_bytes(), b"abc")
        self.assertFalse(self.dest.exists())

    def test_multiple_ranges_assemble_one_verified_mp4(self):
        responses = [Response(b"abc", 206, {"Content-Range": "bytes 0-2/6"}),
                     Response(b"def", 206, {"Content-Range": "bytes 3-5/6"})]
        with patch("shared.ia_video_downloads.RANGE_BYTES", 3), patch(
            "shared.ia_video_downloads.requests.get", side_effect=responses
        ) as get:
            self.assertEqual(fetch_mp4(self.record)["status"], "complete")
        self.assertEqual([c.kwargs["headers"]["Range"] for c in get.call_args_list],
                         ["bytes=0-2", "bytes=3-5"])
        self.assertEqual(self.dest.read_bytes(), b"abcdef")

    def test_successful_ranges_reset_failure_budget(self):
        responses = [requests.ConnectionError("temporary"),
                     Response(b"abc", 206, {"Content-Range": "bytes 0-2/6"}),
                     requests.ReadTimeout("temporary"),
                     Response(b"def", 206, {"Content-Range": "bytes 3-5/6"})]
        with patch("shared.ia_video_downloads.RANGE_BYTES", 3), patch(
            "shared.ia_video_downloads.requests.get", side_effect=responses
        ), patch("shared.ia_video_downloads.time.sleep"):
            self.assertEqual(fetch_mp4(self.record, attempts=2)["status"], "complete")
        self.assertEqual(self.dest.read_bytes(), b"abcdef")

    def test_progress_before_disconnect_resets_failure_budget(self):
        responses = [Response(b"ab", 206, {"Content-Range": "bytes 0-5/6"}, fail=True),
                     Response(b"cd", 206, {"Content-Range": "bytes 2-5/6"}, fail=True),
                     Response(b"ef", 206, {"Content-Range": "bytes 4-5/6"})]
        with patch("shared.ia_video_downloads.requests.get", side_effect=responses) as get, patch(
            "shared.ia_video_downloads.time.sleep"
        ):
            self.assertEqual(fetch_mp4(self.record, attempts=2)["status"], "complete")
        self.assertEqual([c.kwargs["headers"]["Range"] for c in get.call_args_list],
                         ["bytes=0-5", "bytes=2-5", "bytes=4-5"])
        self.assertEqual(self.dest.read_bytes(), b"abcdef")

    def test_no_progress_timeouts_are_bounded_and_retryable(self):
        self.part.write_bytes(b"abc")
        with patch("shared.ia_video_downloads.requests.get", side_effect=requests.ReadTimeout) as get, patch(
            "shared.ia_video_downloads.time.sleep"
        ):
            result = fetch_mp4(self.record, attempts=2)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.part.read_bytes(), b"abc")

    def test_checksum_failure_does_not_retry_forever(self):
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(b"badbad")) as get:
            result = fetch_mp4(self.record)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertEqual(get.call_count, 1)
        self.assertFalse(self.dest.exists())

    def test_permanent_http_error_preserves_partial_without_retry(self):
        self.part.write_bytes(b"abc")
        response = requests.Response()
        response.status_code = 404
        with patch("shared.ia_video_downloads.requests.get", side_effect=requests.HTTPError(response=response)) as get:
            result = fetch_mp4(self.record)
        self.assertFalse(result["retryable"])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(self.part.read_bytes(), b"abc")

    def test_retry_round_resumes_after_network_failure(self):
        path = Path(self.temp.name) / "plan.json"
        record = dict(self.record, filename=self.dest.name, status="pending")
        path.write_text(json.dumps({"records": [record]}))
        responses = [Response(b"abc", fail=True),
                     Response(b"def", 206, {"Content-Range": "bytes 3-5/6"})]
        with patch("shared.ia_video_downloads.requests.get", side_effect=responses) as get, patch(
            "shared.ia_video_downloads.time.sleep"
        ) as sleep:
            success = run_transfers(path, lambda r: fetch_mp4(r, attempts=1), [0],
                                    workers=1, retry_rounds=1, retry_delay=10)
        self.assertTrue(success)
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=3-5")
        sleep.assert_called_once_with(10)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["summary"], {"complete": 1})
        self.assertEqual(saved["records"][0]["downloaded_bytes"], 6)
        self.assertNotIn("error", saved["records"][0])

    def test_retry_limit_leaves_partial_and_incomplete_manifest(self):
        path = Path(self.temp.name) / "plan.json"
        self.part.write_bytes(b"abc")
        record = dict(self.record, filename=self.dest.name, status="pending")
        path.write_text(json.dumps({"records": [record]}))
        with patch("shared.ia_video_downloads.requests.get", side_effect=requests.ReadTimeout) as get, patch(
            "shared.ia_video_downloads.time.sleep"
        ):
            success = run_transfers(path, lambda r: fetch_mp4(r, attempts=1), [0],
                                    workers=1, retry_rounds=1)
        self.assertFalse(success)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.part.read_bytes(), b"abc")
        self.assertEqual(json.loads(path.read_text())["status"], "incomplete")

    def test_duplicate_plan_lock_is_rejected(self):
        path = Path(self.temp.name) / "plan.json"
        with plan_lock(path):
            with self.assertRaises(RuntimeError):
                with plan_lock(path):
                    self.fail("Second downloader acquired the same plan")
        with plan_lock(path):
            pass

    def test_original_download_is_explicit_and_preserves_format(self):
        self.dest = self.dest.with_suffix(".mxf")
        record = dict(self.record, local_path=str(self.dest))
        with self.assertRaises(ValueError):
            fetch_mp4(record)
        with patch("shared.ia_video_downloads.requests.get", return_value=Response(b"abcdef")):
            self.assertEqual(fetch_media(record, allowed_extensions={".mxf"})["status"], "complete")
        self.assertEqual(self.dest.read_bytes(), b"abcdef")

    def test_original_plan_selects_only_missing_mp4_recordings(self):
        root = Path(self.temp.name)
        source = root / "mp4.json"
        identifier = "UHD_art000m1500911659_SHARED.mxf"
        source.write_text(json.dumps({"mission": "artemis-ii", "records": [
            {"identifier": identifier, "status": "no_mp4_available", "recorded_at": "2026-04-01T16:59:50Z"},
            {"identifier": "already_downloaded", "status": "complete"}
        ], "excluded": [{"identifier": "recap", "reason": "standalone_highlights_or_recap"}]}))
        meta = {"files": [{"name": identifier, "source": "original", "size": "6", "md5": self.record["md5"]}]}
        with patch.object(originals, "get_item_metadata", return_value=meta) as get:
            result = originals.build_original_plan(source, SimpleNamespace(slug="artemis-ii"), root)
        get.assert_called_once_with(identifier)
        self.assertEqual(len(result["records"]), 1)
        self.assertTrue(result["records"][0]["needs_conversion"])
        self.assertEqual(result["records"][0]["bytes"], 6)

    def test_original_batch_limits_leave_remaining_records_queued(self):
        path = Path(self.temp.name) / "originals.json"
        records = [dict(self.record, filename=f"clip{i}.mxf", local_path=str(Path(self.temp.name) / f"clip{i}.mxf"), status="pending") for i in range(2)]
        path.write_text(json.dumps({"records": records}))
        with patch.object(originals, "fetch_media", side_effect=lambda r, **kw: dict(r, status="complete")):
            originals.run_original_plan(path, max_files=1)
        result = json.loads(path.read_text())
        self.assertEqual(result["summary"], {"complete": 1, "pending": 1})
        self.assertEqual(result["status"], "incomplete")

    def test_originals_defer_when_storage_is_insufficient(self):
        path = Path(self.temp.name) / "originals.json"
        record = dict(self.record, filename="clip.mxf", local_path=str(Path(self.temp.name) / "clip.mxf"), status="pending")
        path.write_text(json.dumps({"records": [record]}))
        with patch.object(originals.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), patch.object(originals, "fetch_media") as fetch:
            originals.run_original_plan(path)
        fetch.assert_not_called()
        self.assertEqual(json.loads(path.read_text())["summary"], {"waiting_for_space": 1})

    def test_unneeded_originals_never_resume_retained_partials(self):
        path = Path(self.temp.name) / "originals.json"
        dest = Path(self.temp.name) / "clip.mxf"
        part = Path(str(dest) + ".part")
        part.write_bytes(b"abc")
        record = dict(self.record, filename=dest.name, local_path=str(dest),
                      status="unneeded", unneeded_reason="Covered by YouTube")
        path.write_text(json.dumps({"records": [record]}))
        with patch.object(originals, "fetch_media") as fetch:
            self.assertTrue(originals.run_original_plan(path))
        fetch.assert_not_called()
        self.assertEqual(part.read_bytes(), b"abc")
        saved = json.loads(path.read_text())
        self.assertEqual(saved["summary"], {"unneeded": 1})
        self.assertEqual(saved["records"][0]["unneeded_reason"], "Covered by YouTube")


if __name__ == "__main__":
    unittest.main()
