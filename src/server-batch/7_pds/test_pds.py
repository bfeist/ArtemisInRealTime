"""Focused integration tests using temporary D: holdings and a local HTTP server."""
import hashlib
import importlib
import json
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests

from config import MissionConfig
from shared.pds import (HostLimits, atomic_json, db, destination, hashes, locked,
                        nasa_id, parse_label, safe_relative, set_meta, stream_download, verify_file)
from shared.photos_ledger import Copy, LedgerRecord, load_ledger, save_ledger
from shared.pds_photos import merge_pds_photos

pipeline = importlib.import_module("7_pds.pipeline")
PAYLOAD = b"PDS sample payload\n" * 100


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests_seen = []

    def do_GET(self):
        type(self).requests_seen.append(dict(self.headers))
        offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
        ignore = self.path == "/ignore"
        changed = self.path == "/changed"
        if ignore or (changed and self.headers.get("If-Range") != '"v2"'):
            offset = 0
        if offset >= len(PAYLOAD):
            self.send_response(416)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if offset else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", '"v2"' if changed else '"v1"')
        self.send_header("Content-Length", str(len(PAYLOAD) - offset))
        if offset:
            self.send_header("Content-Range", f"bytes {offset}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.end_headers()
        self.wfile.write(PAYLOAD[offset:])

    def log_message(self, *args):
        pass


class PDSTest(unittest.TestCase):
    def setUp(self):
        test_root = Path("D:/ArtemisInRealTime_assets/artemis-ii/staging/pds/tests")
        test_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=test_root)
        self.root = Path(self.tmp.name)
        self.mission = MissionConfig("test", "artemis-ii", "2026-04-01", "2026-04-11", "test")
        self.paths = patch.multiple("config", PDS_ASSETS_DIR=self.root, DATA_DIR=self.root / "old",
                                   BULK_ASSETS_DIR=self.root, YT_VIDEO_DIR=self.root / "yt")
        self.paths.start()
        self.c = db(self.mission)

    def tearDown(self):
        self.c.close()
        self.paths.stop()
        self.tmp.cleanup()

    def insert(self, relative="data_source/art002e023072_nkd5015_src_v01.nef", payload=PAYLOAD, uri=None):
        pipeline.remote_insert(self.c, "test", "artemis2_crew_camera", relative, uri or relative,
                               None, len(payload), hashlib.md5(payload).hexdigest())
        self.c.commit()
        set_meta(self.c, "discovery", "test")
        return self.c.execute("SELECT * FROM remote WHERE relative=?", (relative,)).fetchone()

    def test_names_paths_and_versions(self):
        self.assertEqual(nasa_id("art002e023072_nkd5015_src_v01.nef"), "art002e023072")
        self.assertEqual(nasa_id("ART002-E-168.JPG"), "art002e000168")
        self.assertEqual(nasa_id("art002a123456_pcd_v01.wav"), "art002a123456")
        self.assertEqual(nasa_id("art002m1010961540_saw3_raw_v01.mp4"), "art002m1010961540")
        self.assertEqual(pipeline.media_type({"relative": "art002a123456_pcd_v01.wav", "collection": "data_audio_pcd"}), "audio")
        self.assertEqual(pipeline.media_type({"relative": "art002a000001_pcd_raw_v01.m4a", "collection": "data_audio_pcd"}), "audio")
        for bad in ("../escape", "C:/escape", "/absolute", "a//b", "a/CON.txt", "a\\b", "a/../b", "a/name."):
            with self.assertRaises(ValueError):
                safe_relative(bad)
        self.insert()
        self.insert("data_raw/art002e023072_nkd5015_raw_v01.tif", uri="raw")
        self.assertEqual(self.c.execute("SELECT count(*) FROM remote").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            pipeline.remote_insert(self.c, "test", "artemis2_crew_camera", "DATA_RAW/art002e023072_nkd5015_raw_v01.tif", "collision", None, 1, "x")

    def test_namespace_multi_file_label_no_external_entities(self):
        p = self.root / "label.xml"
        p.write_text('''<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///C:/Windows/win.ini">]>
          <Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
          <Identification_Area><logical_identifier>urn:test:product</logical_identifier><version_id>1.0</version_id></Identification_Area>
          <Observation_Area><start_date_time>2026-04-01T23:00:00Z</start_date_time><description>&secret;</description></Observation_Area>
          <File_Area_Observational><File><file_name>a.wav</file_name><file_size>4</file_size></File></File_Area_Observational>
          <File_Area_Observational><File><file_name>b.csv</file_name></File></File_Area_Observational>
          </Product_Observational>''', encoding="utf-8")
        label = parse_label(p)
        self.assertEqual(label["lidvid"], "urn:test:product::1.0")
        self.assertEqual(len(label["file_records"]), 2)
        self.assertNotIn("[fonts]", str(label))

    def test_local_link_copy_zip_and_second_run(self):
        self.mission.pds_raw.mkdir(parents=True)
        source = self.mission.photos_bulk_crew / "art002e023072.NEF"
        source.parent.mkdir(parents=True)
        source.write_bytes(PAYLOAD)
        self.insert()
        with zipfile.ZipFile(self.mission.bulk_dir / "samples.zip", "w") as z:
            z.writestr("nested/art002e023073.NEF", b"ZIP source bytes")
        self.insert("data_source/art002e023073_nkd5015_src_v01.nef", b"ZIP source bytes")
        pipeline.inventory(self.mission)
        pipeline.reconcile(self.mission, 0)
        rows = self.c.execute("SELECT * FROM remote ORDER BY relative").fetchall()
        self.assertEqual(rows[0]["action"], "link")
        self.assertEqual(rows[1]["action"], "extract")
        for row in rows:
            self.assertTrue(pipeline.fetch_one(self.mission, row, HostLimits(), 0, 0))
        dest = destination(self.mission.pds_raw, rows[0]["bundle"] + "/" + rows[0]["relative"])
        self.assertEqual(dest.stat().st_ino, source.stat().st_ino)
        pipeline.reconcile(self.mission, 0)
        pipeline.download(self.mission, 0)
        self.assertEqual(self.c.execute("SELECT sum(network_bytes) FROM remote").fetchone()[0], 0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM attempts").fetchone()[0], 2)

    def test_checksum_mismatch_conflict_and_evidence_change(self):
        row = self.insert()
        p = self.root / "wrong.nef"
        p.write_bytes(b"x" * len(PAYLOAD))
        with self.assertRaisesRegex(ValueError, "MD5"):
            verify_file(row, p)
        old = self.mission.photos_bulk_crew / "art002e023072.NEF"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"y" * len(PAYLOAD))
        pipeline.inventory(self.mission)
        pipeline.reconcile(self.mission, 0)
        self.assertEqual(self.c.execute("SELECT action FROM remote").fetchone()[0], "network")
        self.assertTrue(self.c.execute("SELECT 1 FROM issues WHERE kind='different_bytes'").fetchone())
        pipeline.remote_insert(self.c, "new", row["bundle"], row["relative"], row["uri"], None, 1, "changed")
        self.assertEqual(self.c.execute("SELECT status FROM remote").fetchone()[0], "pending")

    def test_corrupt_zip_falls_back_to_canonical_download(self):
        archive_path = self.mission.bulk_dir / "corrupt.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("art002e023072.NEF", PAYLOAD)
        with zipfile.ZipFile(archive_path) as z:
            info = z.infolist()[0]
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        with open(archive_path, "r+b") as f:
            f.seek(offset)
            f.write(b"\xff\xff\xff")
        self.insert()
        pipeline.inventory(self.mission)
        pipeline.reconcile(self.mission, 0)
        self.assertEqual(self.c.execute("SELECT action FROM remote").fetchone()[0], "network")
        self.assertTrue(self.c.execute("SELECT 1 FROM issues WHERE kind='zip_error'").fetchone())

    def test_resume_ignored_range_and_changed_validator(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for endpoint in ("/resume", "/ignore", "/changed"):
                part, checkpoint = self.root / "transfer.part", self.root / "transfer.json"
                part.write_bytes(PAYLOAD[:100])
                r = dict(self.insert("data_source" + endpoint + ".nef", uri=endpoint))
                r["url"] = f"http://127.0.0.1:{server.server_port}{endpoint}"
                atomic_json(checkpoint, {"identity": [r["uri"], r["size"], r["md5"]], "validator": '"v1"'})
                with requests.Session() as s:
                    stream_download(s, r, part, checkpoint, HostLimits(), 0)
                self.assertEqual(part.read_bytes(), PAYLOAD)
                verify_file(r, part)
            # A completed partial receiving HTTP 416 is verified without reappending.
            r["url"] = f"http://127.0.0.1:{server.server_port}/resume"
            atomic_json(checkpoint, {"identity": [r["uri"], r["size"], r["md5"]], "validator": '"v1"'})
            with requests.Session() as s:
                stream_download(s, r, part, checkpoint, HostLimits(), 0)
            self.assertEqual(part.read_bytes(), PAYLOAD)
        finally:
            server.shutdown()
            server.server_close()

    def test_capacity_lock_and_html_error(self):
        with locked(self.mission):
            with self.assertRaises((RuntimeError, BlockingIOError)):
                with locked(self.mission):
                    pass
        self.insert()
        with patch.object(pipeline.shutil, "disk_usage", return_value=type("Disk", (), {"free": 0})()):
            with self.assertRaisesRegex(RuntimeError, "Insufficient"):
                pipeline.reconcile(self.mission, 500_000_000_000)
        p = self.root / "response.part"
        p.write_bytes(b"<html>Error</html>")
        with self.assertRaisesRegex(ValueError, "HTML"):
            verify_file({"relative": "photo.nef", "size": None, "md5": None}, p)
        verify_file({"relative": "document.html", "size": None, "md5": None}, p)

    def test_missing_inventory_and_file_reference(self):
        label = b'''<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
          <Identification_Area><logical_identifier>urn:test</logical_identifier><version_id>1.0</version_id></Identification_Area>
          <File_Area_Observational><File><file_name>missing.wav</file_name></File></File_Area_Observational></Product_Observational>'''
        row = dict(self.insert("data_source/label.xml", label))
        source = self.root / "label.xml"
        source.write_bytes(label)
        row.update(action="copy", source=str(source))
        self.assertTrue(pipeline.fetch_one(self.mission, row, HostLimits(), 0, 0))
        inventory = b"P,urn:missing::1.0\r\n"
        row = dict(self.insert("data_source/collection_inventory.csv", inventory))
        source = self.root / "inv.csv"
        source.write_bytes(inventory)
        row.update(action="copy", source=str(source))
        self.assertTrue(pipeline.fetch_one(self.mission, row, HostLimits(), 0, 0))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            pipeline.verify(self.mission)
        kinds = {r[0] for r in self.c.execute("SELECT kind FROM issues")}
        self.assertIn("missing_file_reference", kinds)
        self.assertIn("inventory_member_missing", kinds)

    def test_ledger_preserves_sources_times_and_channels(self):
        original = LedgerRecord("art002e023072", utc="2026-04-01T22:00:00Z", utc_source="io_corrected")
        original.copies["eol"] = Copy(path="old.jpg")
        records = {original.nasa_id: original}
        catalog = self.root / "photos.jsonl"
        products = [{"nasa_id": original.nasa_id, "path": f"new_{i}.tif", "start_utc": "2026-04-01T23:00:00Z"} for i in range(2)]
        catalog.write_text("\n".join(json.dumps(p) for p in products), encoding="utf-8")
        merge_pds_photos(records, catalog)
        merge_pds_photos(records, catalog)
        self.assertEqual(len(original.pds_products), 2)
        self.assertEqual(original.copies["eol"].path, "old.jpg")
        self.assertEqual(original.utc_source, "io_corrected")
        self.assertEqual(original.exported_in, ["pds"])
        ledger = self.root / "ledger.jsonl"
        save_ledger(ledger, records.values())
        self.assertEqual(load_ledger(ledger)[original.nasa_id].pds_products, products)

    def test_processed_photo_selection_excludes_bayer_raw(self):
        import io
        from PIL import Image
        tiers = importlib.import_module("3_photos.4d_generate_tiers")
        path = self.root / "processed.tif"
        Image.new("RGB", (4, 4), (100, 50, 20)).save(path)
        rec = LedgerRecord("art002e023072")
        legacy = self.root / "legacy.jpg"
        Image.new("RGB", (4, 4), (100, 50, 20)).save(legacy)
        rec.copies["eol"] = Copy(path=str(legacy))
        rec.pds_products = [{"path": str(path), "role": "data_raw", "assurance": "verified"}]
        self.assertEqual(tiers._pick_source(rec, True)[0], "eol")
        rec.pds_products[0]["role"] = "data_processed"
        self.assertEqual(tiers._pick_source(rec)[0], "eol")
        self.assertEqual(tiers._pick_source(rec, True)[0], "pds_processed")
        data = tiers._source_jpeg_bytes("pds_processed", path)
        self.assertEqual(Image.open(io.BytesIO(data)).format, "JPEG")

    def test_pagination_and_repeated_page_detection(self):
        class Response:
            status_code = 404
            content = b"Release snapshot"
            def raise_for_status(self):
                pass
            def json(self):
                return self.data
        class Session:
            repeat = False
            pages = []
            def get(self, *a, **kw):
                return Response()
            def close(self):
                pass
            def post(self, url, json, **kw):
                Session.pages.append(json)
                after = json.get("search_after", [None])[0]
                bundle = json["query"]["bool"]["filter"][0]["term"]["archive.bundle_id"]
                uri1, uri2 = [f"atlas:/" + bundle + f"/data_video/channel{i}.mp4" for i in (1,2)]
                uri = uri1 if after is None or self.repeat else (uri2 if after == uri1 else None)
                hit = {"_source": {"uri": uri, "archive": {"size": 4, "md5": "abcd"}}, "sort": [uri]}
                r = Response()
                r.data = {"hits": {"total": {"value": 2, "relation": "eq"}, "hits": [hit] if uri else []}}
                return r
        fake = Session()
        with patch.object(pipeline, "session", return_value=fake), patch.object(pipeline, "discover_directory"):
            pipeline.discover(self.mission)
        self.assertEqual(self.c.execute("SELECT count(*) FROM remote").fetchone()[0], 6)
        self.assertTrue(any("search_after" in q for q in Session.pages))
        fake.repeat = True
        with patch.object(pipeline, "session", return_value=fake), patch.object(pipeline, "discover_directory"):
            with self.assertRaisesRegex(RuntimeError, "Repeated"):
                pipeline.discover(self.mission)


if __name__ == "__main__":
    unittest.main()
