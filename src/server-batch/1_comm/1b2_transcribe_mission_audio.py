"""Step 1b2 — Transcribe NASA Mission Audio ZIPs using WhisperX.

Processes the daily "Artemis II Mission Audio" ZIP files that contain
PAO/ground-track recordings.  These are separate from the OE Comp files
processed by step 1b but share the same output directories so that step
1c (web_comm.csv build) picks them all up together.

For each ZIP matching ``*Artemis II Mission Audio.zip`` in mission.raw_comm:
  1. Extract (if not already extracted) to a sibling folder in raw/comm/.
  2. Find all MISSION_AUDIO WAV files recursively.
  3. Parse the UTC timestamp from the filename (applying the same CDT→UTC
     offset used by step 1b).
  4. Transcribe with WhisperX large-v3.
  5. Write per-file JSON to processed/transcripts/comm/{date}/.
  6. Convert to AAC for the web player in web/comm/{date}/.

Input:  {data_dir}/{mission}/raw/comm/*Artemis II Mission Audio.zip
Output: {data_dir}/{mission}/processed/transcripts/comm/{date}/*.json
        {data_dir}/{mission}/web/comm/{date}/*.aac
"""

import argparse
import datetime as dt
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

# ---------------------------------------------------------------------------
# WAV filename pattern for Mission Audio files:
# 0000000043_MISSION_AUDIO_2026-04-01_12_00_04_by_ui_startdate_asc.wav
# ---------------------------------------------------------------------------
MISSION_AUDIO_PATTERN = re.compile(
    r"^\d+_MISSION_AUDIO_"
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})"
    r"(?:_by_\w+)?\.wav$",
    re.IGNORECASE,
)

ZIP_GLOB = "*Artemis II Mission Audio.zip"

# ---------------------------------------------------------------------------
# Hallucinations shared with step 1b
# ---------------------------------------------------------------------------
HALLUCINATIONS = {
    "Thank you.", "Bye.", "...", "Thanks for watching!",
    "Thank you for watching.", "Thank you for watching!",
    "Mmm.", "Hmm.", "Mmmmmmmm.", "MMMMMMMM",
    "Beep.", "BEEP", "Beeping.", "BEEEEEP",
    "BOOOOOM", "BOOOOOM!", "BELL RINGS",
    "This video is a derivative work of the Touhou Project",
}

INITIAL_PROMPT = (
    "Artemis II crew: Commander Reid Wiseman, Pilot Victor Glover, "
    "Mission Specialist Christina Koch, Mission Specialist Jeremy Hansen (CSA). "
    "NASA Mission Control Houston. Orion spacecraft. Space Launch System (SLS). "
    "PAO commentary and mission audio."
)

WHISPERX_SAMPLE_RATE = 16000
AAC_BITRATE = "64k"
AAC_SAMPLE_RATE = 22050


# ---------------------------------------------------------------------------
# Utility: ffmpeg
# ---------------------------------------------------------------------------

def _ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def convert_wav_to_aac(wav_path: Path, aac_path: Path) -> None:
    """Convert a WAV file to AAC (m4a) using ffmpeg."""
    aac_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_exe(),
        "-hide_banner", "-loglevel", "error",
        "-nostdin", "-y",
        "-i", str(wav_path),
        "-ac", "1",
        "-ar", str(AAC_SAMPLE_RATE),
        "-c:a", "aac",
        "-b:a", AAC_BITRATE,
        "-movflags", "+faststart",
        str(aac_path),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Utility: WAV loading (avoids torch DLL issues on Windows)
# ---------------------------------------------------------------------------

def load_wav_audio(wav_path: Path, target_sr: int = WHISPERX_SAMPLE_RATE) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())

    if sw == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    if ch == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    elif ch > 2:
        audio = audio.reshape(-1, ch).mean(axis=1)

    if sr != target_sr:
        new_len = int(len(audio) * target_sr / sr)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, new_len),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    return audio


# ---------------------------------------------------------------------------
# WAV discovery
# ---------------------------------------------------------------------------

def parse_mission_audio_timestamp(filename: str, tz_offset_hours: float = 0.0) -> dt.datetime | None:
    """Extract UTC timestamp from a MISSION_AUDIO WAV filename."""
    m = MISSION_AUDIO_PATTERN.match(filename)
    if not m:
        return None
    naive = dt.datetime(
        int(m.group("year")), int(m.group("month")), int(m.group("day")),
        int(m.group("hour")), int(m.group("minute")), int(m.group("second")),
    )
    return (naive + dt.timedelta(hours=tz_offset_hours)).replace(tzinfo=dt.timezone.utc)


def find_mission_audio_zips(comm_dir: Path) -> list[Path]:
    """Return all Mission Audio ZIPs in comm_dir, sorted by name."""
    zips = sorted(comm_dir.glob(ZIP_GLOB))
    return [z for z in zips if not z.name.endswith(".part")]


def extract_zip(zip_path: Path) -> Path:
    """Extract a Mission Audio ZIP to a sibling folder; return the folder."""
    extract_dir = zip_path.parent / zip_path.stem
    if extract_dir.exists():
        return extract_dir
    print(f"  Extracting {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)
    return extract_dir


def collect_mission_audio_wavs(
    extract_dir: Path,
    tz_offset_hours: float = 0.0,
) -> list[tuple[Path, dt.datetime]]:
    """Walk an extracted Mission Audio directory and return (wav_path, utc_time) pairs."""
    results = []
    for dirpath, _dirs, files in os.walk(extract_dir):
        for f in files:
            if not f.lower().endswith(".wav"):
                continue
            ts = parse_mission_audio_timestamp(f, tz_offset_hours)
            if ts is None:
                continue
            results.append((Path(dirpath) / f, ts))
    results.sort(key=lambda x: x[1])
    return results


# ---------------------------------------------------------------------------
# WhisperX wrapper (mirrors 1b_transcribe.py)
# ---------------------------------------------------------------------------

def _pipeline_supported_transcribe_params(pipeline_cls: type) -> set[str]:
    transcribe_fn = getattr(pipeline_cls, "transcribe", None)
    if not callable(transcribe_fn):
        return set()
    try:
        signature = inspect.signature(transcribe_fn)
    except (TypeError, ValueError):
        return set()
    params = set(signature.parameters.keys())
    params.discard("self")
    params.discard("args")
    params.discard("kwargs")
    return params


def transcribe_with_model(pipeline: object, audio, **kwargs):
    transcribe_fn = getattr(pipeline, "transcribe", None)
    if not callable(transcribe_fn):
        raise AttributeError(
            f"Pipeline '{type(pipeline).__name__}' does not expose a callable transcribe() method"
        )
    supported_params = _pipeline_supported_transcribe_params(type(pipeline))
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in supported_params}

    if "chunk_length" in kwargs and "chunk_length" not in supported_params:
        if "chunk_size" in supported_params and "chunk_size" not in filtered_kwargs:
            filtered_kwargs["chunk_size"] = kwargs["chunk_length"]

    return transcribe_fn(audio, **filtered_kwargs)


class WhisperResources:
    """Lazy-loaded WhisperX model wrapper."""

    def __init__(self, device: str = "cuda", compute_type: str = "float16",
                 batch_size: int = 16):
        self.device = device
        self.compute_type = compute_type
        self.batch_size = batch_size
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return
        import whisperx
        print(f"  Loading WhisperX model large-v3 on {self.device} ({self.compute_type})")
        self._model = whisperx.load_model(
            "large-v3", self.device, compute_type=self.compute_type,
            vad_options={"vad_type": "silero"},
            asr_options={"initial_prompt": INITIAL_PROMPT},
        )

    def transcribe(self, audio, **kwargs) -> dict:
        self._ensure_model()
        kwargs.setdefault("batch_size", self.batch_size)
        return transcribe_with_model(self._model, audio, **kwargs)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_wav(
    wav_path: Path,
    utc_time: dt.datetime,
    output_dir: Path,
    whisper_resources: WhisperResources,
    force: bool = False,
    aac_dir: Path | None = None,
) -> dict | None:
    """Transcribe a single WAV file and save the result as JSON + AAC."""
    out_name = wav_path.stem + ".json"
    out_path = output_dir / out_name

    if out_path.exists() and not force:
        return None  # already done

    audio = load_wav_audio(wav_path)
    duration = len(audio) / WHISPERX_SAMPLE_RATE

    if duration < 0.5:
        return None

    try:
        result = whisper_resources.transcribe(audio, language="en")
    except (IndexError, ValueError):
        return None

    language = result.get("language", "en")
    segments = result.get("segments", [])

    text = " ".join(s.get("text", "").strip() for s in segments)
    if text.strip() in HALLUCINATIONS or not text.strip():
        return None

    payload = {
        "version": 1,
        "source": str(wav_path.name),
        "utcTime": utc_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tz_corrected": True,
        "duration": round(duration, 2),
        "language": language,
        "model": "large-v3",
        "segments": [
            {
                "start": round(float(s.get("start", 0)), 3),
                "end": round(float(s.get("end", 0)), 3),
                "text": s.get("text", "").strip(),
            }
            for s in segments
            if s.get("text", "").strip()
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # AAC for web player
    _aac_dir = aac_dir if aac_dir is not None else output_dir
    aac_path = _aac_dir / (wav_path.stem + ".aac")
    if not aac_path.exists():
        _aac_dir.mkdir(parents=True, exist_ok=True)
        try:
            convert_wav_to_aac(wav_path, aac_path)
        except Exception as e:
            print(f"    Warning: AAC conversion failed for {wav_path.name}: {e}")

    return payload


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def transcribe_mission_audio(
    mission: MissionConfig,
    force: bool = False,
    test: int | None = None,
) -> None:
    comm_dir = mission.raw_comm
    if not comm_dir.exists():
        print(f"  Comm directory not found: {comm_dir}")
        return

    zips = find_mission_audio_zips(comm_dir)
    if not zips:
        print(f"  No Mission Audio ZIPs found in {comm_dir}")
        return

    print(f"  Found {len(zips)} Mission Audio ZIP(s)")

    transcript_dir = mission.processed_transcripts / "comm"
    web_comm_dir = mission.web_dir / "comm"

    all_wavs: list[tuple[Path, dt.datetime]] = []
    for zip_path in zips:
        extract_dir = extract_zip(zip_path)
        wavs = collect_mission_audio_wavs(extract_dir, mission.comm_tz_offset_hours)
        print(f"    {zip_path.name}: {len(wavs)} WAVs")
        all_wavs.extend(wavs)

    all_wavs.sort(key=lambda x: x[1])
    print(f"\n  Total MISSION_AUDIO WAVs: {len(all_wavs)}")

    # Split into already done / to process
    to_process = []
    already_done = 0
    for wav_path, utc_time in all_wavs:
        date_str = utc_time.strftime("%Y-%m-%d")
        out_path = transcript_dir / date_str / (wav_path.stem + ".json")
        if out_path.exists() and not force:
            already_done += 1
        else:
            to_process.append((wav_path, utc_time))

    print(f"  {already_done} already transcribed, {len(to_process)} remaining")

    if test is not None:
        to_process = to_process[:test]
        print(f"  --test {test}: processing only {len(to_process)} file(s)")

    if not to_process:
        print("  Nothing to transcribe.")
        _backfill_aac(all_wavs, transcript_dir, web_comm_dir)
        return

    device = os.environ.get("WHISPER_DEVICE", "cuda")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
    batch_size = int(os.environ.get("WHISPER_BATCH_SIZE", "16"))
    resources = WhisperResources(device, compute_type, batch_size)

    start = time.time()
    transcribed = 0
    skipped = 0
    errors = 0

    for i, (wav_path, utc_time) in enumerate(to_process, 1):
        date_str = utc_time.strftime("%Y-%m-%d")
        out_dir = transcript_dir / date_str
        aac_dir = web_comm_dir / date_str

        try:
            result = transcribe_wav(wav_path, utc_time, out_dir, resources, force=force, aac_dir=aac_dir)
            if result is not None:
                transcribed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"    Error transcribing {wav_path.name}: {e}")
            errors += 1

        if i % 50 == 0 or i == len(to_process):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            print(
                f"  [{i}/{len(to_process)}] {transcribed} transcribed, "
                f"{skipped} skipped, {errors} errors ({rate:.1f} files/s)"
            )

    elapsed = time.time() - start
    print(
        f"\n  Done in {elapsed:.1f}s: {transcribed} transcribed, "
        f"{skipped} skipped (short/hallucination), {errors} errors"
    )

    _backfill_aac(all_wavs, transcript_dir, web_comm_dir)


def _backfill_aac(
    all_wavs: list[tuple[Path, dt.datetime]],
    transcript_dir: Path,
    web_comm_dir: Path,
) -> None:
    """Create any AAC files missing for already-transcribed WAVs."""
    needed = [
        (wav_path, utc_time)
        for wav_path, utc_time in all_wavs
        if (transcript_dir / utc_time.strftime("%Y-%m-%d") / (wav_path.stem + ".json")).exists()
        and not (web_comm_dir / utc_time.strftime("%Y-%m-%d") / (wav_path.stem + ".aac")).exists()
    ]
    if not needed:
        return
    print(f"\n  Backfilling {len(needed)} missing AAC file(s)...")
    ok = err = 0
    for wav_path, utc_time in needed:
        aac_path = web_comm_dir / utc_time.strftime("%Y-%m-%d") / (wav_path.stem + ".aac")
        aac_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            convert_wav_to_aac(wav_path, aac_path)
            ok += 1
        except Exception as e:
            print(f"    Warning: AAC conversion failed for {wav_path.name}: {e}")
            err += 1
    print(f"  AAC backfill: {ok} created, {err} error(s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe NASA Mission Audio ZIPs with WhisperX"
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--force", action="store_true",
        help="Re-transcribe even if output JSON already exists",
    )
    parser.add_argument(
        "--test", type=int, default=None, metavar="N",
        help="Process only N files (for quick testing)",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 1b2: Mission Audio Transcription — {mission.name} ===\n")
    transcribe_mission_audio(mission, force=args.force, test=args.test)


if __name__ == "__main__":
    main()
