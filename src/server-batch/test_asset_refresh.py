"""Regression checks for release filtering, source joins, and user exclusions."""
import importlib
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from config import MissionConfig
from shared import io_api, ia_video_downloads
from shared.photos_ledger import LedgerRecord, save_ledger
from asset_status import photo_rows
from prepare_web_media import copy_verified

ledger = importlib.import_module("3_photos.4c_build_ledger")
web = importlib.import_module("3_photos.4e_web_photos_json")


class AssetRefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("DATA_DIR", "BULK_ASSETS_DIR", "PDS_ASSETS_DIR", "YT_VIDEO_DIR"):
            p = patch("config." + name, self.root / name)
            p.start()
            self.addCleanup(p.stop)
        self.mission = MissionConfig("Test", "test", "2026-04-01", "2026-04-12", "test", io_nasatv_cid="123")
        self.mission.ensure_dirs()

    def test_unpadded_eol_catalog_joins_canonical_copy(self):
        image = self.mission.photos_eol / "art002e000168.jpg"
        image.write_bytes(b"image")
        self.mission.eol_json_path.write_text(json.dumps([{"ID": "ART002E168", "large": "ESC/large/ART002/ART002-E-168.JPG"}]))
        rows = {"art002e000168": LedgerRecord("art002e000168")}
        ledger._add_eol(rows, self.mission.eol_json_path, self.mission.photo_source_dirs("eol"))
        self.assertEqual(list(rows), ["art002e000168"])
        self.assertTrue(rows["art002e000168"].exported)
        self.assertEqual(rows["art002e000168"].copies["eol"].path, str(image))

    def test_bulk_copies_are_visible_without_moving_legacy_files(self):
        root = self.mission.photo_download_dir("nasa_images")
        root.mkdir(parents=True)
        image = root / "art002e000168.jpg"
        image.write_bytes(b"image")
        files = ledger._scan_dir_to_nasa_id(self.mission.photo_source_dirs("nasa_images"), {".jpg"})
        self.assertEqual(files["art002e000168"], image)
        self.assertTrue(image.is_relative_to(self.mission.bulk_dir))

    def test_cross_drive_copy_promotes_only_checksum_matching_bytes(self):
        source = self.root / "source.mp4"
        target = self.root / "web" / "clip.mp4"
        source.write_bytes(b"verified source")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            copy_verified(source, target, "incorrect")
        self.assertFalse(target.exists())
        copy_verified(source, target, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_png_original_becomes_an_actual_jpeg_web_source(self):
        from PIL import Image
        source = self.root / "source.png"
        Image.new("RGB", (4, 4), "blue").save(source)
        tiers = importlib.import_module("3_photos.4d_generate_tiers")
        self.assertTrue(tiers._source_jpeg_bytes("manual", source).startswith(b"\xff\xd8"))

    def test_orion_numeric_id_does_not_release_or_retime_crew_photo(self):
        from shared.pds_photos import merge_pds_photos
        crew = LedgerRecord("art002e000168", utc="2026-04-01T23:00:00Z")
        records = {crew.nasa_id: crew}
        catalog = self.root / "orion.jsonl"
        catalog.write_text(json.dumps({"nasa_id": crew.nasa_id, "bundle": "artemis2_orion_camera",
            "path": "art002e000168_onav_raw_v01.png", "start_utc": "2026-04-02T13:13:22Z"}) + "\n")
        merge_pds_photos(records, catalog)
        self.assertFalse(crew.exported)
        self.assertEqual(crew.utc, "2026-04-01T23:00:00Z")
        self.assertTrue(records["art002e000168_onav_v01"].exported)
        self.assertEqual(records["art002e000168_onav_v01"].utc, "2026-04-02T13:13:22Z")
        catalog.write_text(json.dumps({"nasa_id": crew.nasa_id, "bundle": "artemis2_orion_camera",
            "path": "art002e000168_onav_src_v01.tif", "start_utc": "2026-04-02T13:13:22Z"}) + "\n")
        merge_pds_photos(records, catalog)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(records["art002e000168_onav_v01"].pds_products), 2)

    def test_io_incomplete_page_does_not_replace_cached_catalog(self):
        target = self.mission.io_cache / "io_photo_catalog.jsonl"
        target.write_text('{"nasa_id":"old"}\n')
        response = Mock()
        response.json.return_value = {"results": {"response": {"numfound": 501, "docs": [{}] * 500}}}
        with patch.object(io_api.requests, "get", side_effect=[response, io_api.requests.ConnectionError()]):
            with self.assertRaisesRegex(RuntimeError, "cached catalog retained"):
                io_api.search_io_collection("123")
        self.assertEqual(target.read_text(), '{"nasa_id":"old"}\n')

    def test_rediscovery_keeps_unneeded_original_and_coverage_notes(self):
        identifier = "UHD_art002m1500911758_SHARED.mxf"
        path = self.mission.data_dir / "processed" / "ia_video_originals.json"
        path.write_text(json.dumps({"mission": "test", "records": [
            {"identifier": identifier, "status": "unneeded", "recorded_at": "2026-04-01T17:58:59Z",
             "unneeded_reason": "User coverage decision", "replacement_coverage_paths": ["D:/coverage.mkv"]}]}))
        doc = {"nasa_id": "art002m1500911758", "id": 1, "md_title": "Continuous Coverage",
               "vmd_start_gmt": "2026-04-01T17:58:59Z"}
        with patch.object(ia_video_downloads, "search_io_collection", return_value=[doc]), patch.object(
                ia_video_downloads, "discover_by_subject", return_value=[{"identifier": identifier}]), patch.object(
                ia_video_downloads, "get_item_metadata") as get:
            plan = ia_video_downloads.build_plan(self.mission)
        get.assert_not_called()
        self.assertEqual(plan["records"][0]["status"], "unneeded")
        self.assertEqual(plan["records"][0]["replacement_coverage_paths"], ["D:/coverage.mkv"])

    def test_web_brackets_exclude_unconfirmed_members_and_incomplete_tiers(self):
        from shared.photos_ledger import Bracket
        hero = LedgerRecord("hero", exported=True, utc="2026-04-02T00:00:00Z")
        private = LedgerRecord("private", exported=False, utc=hero.utc)
        incomplete = LedgerRecord("incomplete", exported=True, utc=hero.utc)
        for r in [hero, private, incomplete]:
            r.bracket = Bracket("hero", ["hero", "private", "incomplete"], "metered", 0, r is hero)
            for tier in ["thumb", "lowres", "hires"]:
                if r is incomplete and tier == "thumb":
                    continue
                (self.mission.web_photos_dir / tier / (r.nasa_id + ".jpg")).write_bytes(b"tier")
        save_ledger(self.mission.photos_ledger_path, [hero, private, incomplete])
        web.build_web_json(self.mission)
        photos = json.loads((self.mission.web_dir / "photos.json").read_text())
        self.assertEqual([r["id"] for r in photos], ["hero"])
        sets = json.loads((self.mission.web_photos_dir / "brackets.json").read_text())
        self.assertNotIn("private", sets.get("hero", {}).get("members", []))
        statuses = {r["id"]: r for r in photo_rows(self.mission)}
        self.assertEqual(statuses["private"]["release"], "Unconfirmed")
        self.assertNotEqual(statuses["incomplete"]["state"], "Ready on F")


if __name__ == "__main__":
    unittest.main()
