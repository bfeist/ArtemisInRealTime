"""The common playback encoding used for mission communication clips."""

import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

AAC_BITRATE = "64k"
AAC_SAMPLE_RATE = 22050
AAC_PROFILE = {"codec": "aac", "bitrate": AAC_BITRATE, "sample_rate": AAC_SAMPLE_RATE,
               "channels": 1, "version": 1}


def encode_comm_aac(source, target):
    """Encode the common AAC profile; M4A retains its index for long-file seeking."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    target = Path(target)
    if target.suffix.lower() not in {".aac", ".m4a"}:
        raise ValueError(f"Unsupported comm audio container: {target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + "." + uuid4().hex + ".tmp" + target.suffix)
    command = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    command.extend(["-i", str(source)])
    command.extend(["-map", "0:a:0", "-ac", "1", "-ar", str(AAC_SAMPLE_RATE),
                    "-c:a", "aac", "-b:a", AAC_BITRATE])
    if target.suffix.lower() == ".m4a":
        command.extend(["-movflags", "+faststart", "-f", "mp4"])
    else:
        command.extend(["-f", "adts"])
    command.append(str(temporary))
    try:
        subprocess.run(command, check=True, capture_output=True)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"AAC encoder produced no audio: {source}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
