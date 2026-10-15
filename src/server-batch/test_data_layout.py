"""Storage migration checks using temporary files, never production holdings."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import MissionConfig
from migrate_data_layout import layout_moves, migrate, preflight, rebase
from shared.pds import db
from shared.pds_photos import crew_sources


class DataLayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.bulk = self.root / "bulk"
        self.bulk.mkdir()
        self.paths = patch.multiple("config", BULK_ASSETS_DIR=self.bulk,
                                   PDS_ASSETS_DIR=self.bulk, DATA_DIR=self.root / "processing",
                                   YT_VIDEO_DIR=self.root / "youtube")
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.mission = MissionConfig("Artemis II", "artemis-ii", "2026-04-01", "2026-04-11", "test")

    def write(self, relative, data=b"original bytes"):
        p = self.bulk / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_migrate_preserves_identity_rebases_saved_paths_and_is_repeatable(self):
        crew = self.write("5_Crew-Captured-Imagery/FD_01/art002e000001.NEF")
        photo = self.write("photos/artemis-ii/flickr_orig/photo.jpg")
        video = self.write("videos/artemis-ii/video.mp4")
        archive = self.write("5_Crew-Captured-Imagery.zip")
        identities = {p.name: p.stat().st_ino for p in (crew, photo, video, archive)}
        c = db(self.mission)
        with c:
            c.execute("INSERT INTO local(path,size,mtime,md5,sha256) VALUES(?,?,?,?,?)",
                      (str(crew), crew.stat().st_size, crew.stat().st_mtime_ns, "cached-md5", "cached-sha256"))
            c.execute("INSERT INTO remote(uri,source,status,network_bytes) VALUES(?,?,?,?)",
                      ("urn:test", str(crew), "verified", 42))
            c.execute("INSERT INTO members(path,member,md5) VALUES(?,?,?)", (str(archive), "nested/raw.NEF", "zip-md5"))
        c.close()
        metadata = self.mission.photos_ledger_path
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps({"copies": {"raw_crew": {"path": str(crew)}}}) + "\n", encoding="utf-8")

        migrate(self.bulk, [self.mission])
        self.assertTrue(crew.exists())
        self.assertFalse(self.mission.photos_bulk_crew.exists())
        receipt = migrate(self.bulk, [self.mission], apply=True)
        expected = [self.mission.photos_bulk_crew / "FD_01/art002e000001.NEF",
                    self.mission.bulk_dir / "raw/photos/flickr/photo.jpg",
                    self.mission.videos_io_dir / "video.mp4",
                    self.mission.bulk_dir / "raw/photos/5_Crew-Captured-Imagery.zip"]
        for p in expected:
            self.assertEqual(p.stat().st_ino, identities[p.name])
            self.assertEqual(p.read_bytes(), b"original bytes")
        self.assertFalse((self.bulk / "photos").exists())
        c = db(self.mission)
        self.assertEqual(c.execute("SELECT path,md5,sha256 FROM local").fetchone()[:],
                         (str(expected[0]), "cached-md5", "cached-sha256"))
        self.assertEqual(c.execute("SELECT source,status,network_bytes FROM remote").fetchone()[:],
                         (str(expected[0]), "verified", 42))
        self.assertEqual(c.execute("SELECT path,member,md5 FROM members").fetchone()[:],
                         (str(expected[3]), "nested/raw.NEF", "zip-md5"))
        c.close()
        self.assertEqual(json.loads(metadata.read_text())["copies"]["raw_crew"]["path"], str(expected[0]))
        self.assertTrue(Path(receipt["metadata"][0]["backup"]).exists())
        self.assertEqual(migrate(self.bulk, [self.mission], apply=True)["moves"], [])

    def test_conflicts_stop_before_any_move_and_paths_cannot_escape(self):
        crew = self.write("5_Crew-Captured-Imagery/photo.NEF")
        self.write("videos/artemis-ii/video.mp4", b"old")
        self.write("artemis-ii/raw/video/io/video.mp4", b"different")
        with self.assertRaises(FileExistsError):
            migrate(self.bulk, [self.mission], apply=True)
        self.assertTrue(crew.exists())
        with self.assertRaises(ValueError):
            preflight(self.bulk, [(crew, self.root / "outside")])

    def test_prefix_boundaries_and_separators(self):
        moves = layout_moves(self.bulk)
        old = self.bulk / "5_Crew-Captured-Imagery"
        new = self.mission.photos_bulk_crew
        self.assertEqual(rebase((old / "FD_01/file.NEF").as_posix(), moves),
                         (new / "FD_01/file.NEF").as_posix())
        self.assertEqual(rebase(str(old) + "-other/file.NEF", moves), str(old) + "-other/file.NEF")
        self.assertEqual(rebase(str(old).replace("/", "\\") + "\\file.NEF", moves),
                         str(new).replace("/", "\\") + "\\file.NEF")

    def test_mission_inventory_and_crew_sources_are_isolated(self):
        self.write("artemis-ii/raw/photos/5_Crew-Captured-Imagery/art002e000001.NEF")
        other = MissionConfig("Artemis I", "artemis-i", "2022-11-16", "2022-12-11", "test")
        self.assertEqual(crew_sources(other), {})
        self.assertEqual(set(crew_sources(self.mission)), {"art002e000001"})
        self.assertEqual(self.mission.pds_inventory_roots,
                         [self.mission.bulk_dir, self.mission.data_dir, self.mission.yt_video_dir])
        self.assertEqual(other.videos_io_dir, self.bulk / "artemis-i/raw/video/io")


if __name__ == "__main__":
    unittest.main()
