"""Direct rsync command contract; no production uploads."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import publish_assets as publish


class PublishTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("rsync"), "Native rsync required for local transfer fixture")
    def test_actual_rsync_replaces_selected_web_and_leaves_other_missions_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            def write(base, name, content):
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                return path
            write(source, "artemis-ii/web/index.json", "new index")
            write(source, "artemis-ii/web/releases/.staging-test/unfinished.json", "incomplete")
            write(source, "artemis-ii/raw/private.txt", "private")
            current = write(target, "artemis-ii/web/index.json", "old")
            stale = write(target, "artemis-ii/web/stale.json", "stale")
            other = write(target, "artemis-i/web/index.json", "other mission")
            args = publish.command(source, "artemis-ii", destination=str(target) + "/", transport="native", dry_run=True)
            subprocess.run(args, check=True, capture_output=True)
            self.assertEqual(current.read_text(), "old")
            self.assertTrue(stale.exists())
            args.remove("--dry-run")
            subprocess.run(args, check=True, capture_output=True)
            self.assertEqual(current.read_text(), "new index")
            self.assertFalse(stale.exists())
            self.assertEqual(other.read_text(), "other mission")
            self.assertFalse((target / "artemis-ii/raw/private.txt").exists())
            self.assertFalse((target / "artemis-ii/web/releases/.staging-test").exists())

    def test_native_command_preserves_manual_sync_behavior(self):
        command = publish.command(Path(tempfile.gettempdir()) / "assets with spaces", transport="native")
        self.assertEqual(command[:2], ["rsync", "-rt"])
        for option in ("--delete", "--partial", "--prune-empty-dirs", "--include=/*/web/***", "--exclude=*"):
            self.assertIn(option, command)
        self.assertNotIn("--dry-run", command)
        self.assertEqual(command[-1], publish.DEFAULT_DESTINATION)
        self.assertTrue(command[-2].endswith("assets with spaces/"))

    def test_single_mission_preview_excludes_unfinished_staging(self):
        command = publish.command("assets", "artemis-ii", dry_run=True, transport="native")
        self.assertIn("--dry-run", command)
        self.assertIn("--include=/artemis-ii/web/***", command)
        exclusion = "--exclude=**/web/releases/.staging-*/***"
        self.assertLess(command.index(exclusion), command.index("--include=/artemis-ii/web/***"))
        self.assertNotIn("--delete-excluded", command)

    def test_windows_calls_wsl_and_converts_local_path(self):
        with patch.object(publish.subprocess, "check_output", return_value="/mnt/f/assets with spaces\n") as convert:
            command = publish.command("assets", transport="wsl", distribution="Ubuntu")
        self.assertEqual(command[:5], ["wsl.exe", "--distribution", "Ubuntu", "--exec", "rsync"])
        self.assertIn("wslpath", convert.call_args.args[0])
        self.assertEqual(command[-2], "/mnt/f/assets with spaces/")


if __name__ == "__main__":
    unittest.main()
