"""Verify real AAC playback outputs and safe replacement on encoder failures."""

import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from shared.comm_encoding import encode_comm_aac


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class CommEncodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "stereo.wav"
        rate = 48000
        with wave.open(str(self.source), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            samples = bytearray()
            for i in range(rate * 5):
                samples.extend(struct.pack("<hh", int(12000 * math.sin(i * 440 * 2 * math.pi / rate)),
                                           int(10000 * math.sin(i * 550 * 2 * math.pi / rate))))
            audio.writeframes(samples)

    def test_dated_aac_and_pcd_m4a_share_the_same_encoding(self):
        for extension in (".aac", ".m4a"):
            with self.subTest(container=extension):
                target = self.root / ("playback" + extension)
                encode_comm_aac(self.source, target)
                result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                         "-of", "json", str(target)], check=True, capture_output=True, text=True)
                info = json.loads(result.stdout)
                stream = info["streams"][0]
                self.assertEqual((stream["codec_name"], stream["sample_rate"], stream["channels"]),
                                 ("aac", "22050", 1))
                self.assertLess(int(info["format"]["bit_rate"]), 90000)
                # Raw ADTS duration is estimated by ffprobe; count decoded samples.
                decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(target),
                                          "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                                         check=True, capture_output=True)
                self.assertAlmostEqual(len(decoded.stdout) / 2 / 22050, 5, delta=0.15)
                if extension == ".m4a":
                    content = target.read_bytes()
                    self.assertLess(content.index(b"moov"), content.index(b"mdat"))
                    subprocess.run(["ffmpeg", "-v", "error", "-ss", "3", "-i", str(target),
                                    "-t", "0.3", "-f", "null", "-"], check=True, capture_output=True)

    def test_failed_conversion_preserves_existing_playback_and_removes_partial_output(self):
        target = self.root / "playback.m4a"
        target.write_bytes(b"previous playback")
        invalid = self.root / "invalid.m4a"
        invalid.write_bytes(b"broken source")
        with self.assertRaises(subprocess.CalledProcessError):
            encode_comm_aac(invalid, target)
        self.assertEqual(target.read_bytes(), b"previous playback")
        self.assertEqual(list(self.root.glob("*.tmp.m4a")), [])


if __name__ == "__main__":
    unittest.main()
