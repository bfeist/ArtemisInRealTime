"""Step 1b — Transcribe comm audio WAVs using WhisperX.

Walks the extracted WAV files under raw/comm/, transcribes each with
WhisperX (large-v3, CUDA), and saves per-file JSON transcripts.

Input:  {data_dir}/{mission}/raw/comm/**/*.wav
Output: {data_dir}/{mission}/processed/transcripts/comm/{date}/{filename}.json
"""

import argparse
import datetime as dt
import inspect
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

# WAV filename pattern:
# 0000000000_1_OE_Comp_1_2026-04-01_12_42_51_by_ui_startdate_asc.wav
WAV_PATTERN = re.compile(
    r"^\d+_\d+_OE_Comp_\d+_"
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})_"
    r"by_ui_startdate_asc\.wav$"
)

# Known hallucination strings to filter out
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
    "Comm channel: Orion to Earth."
)

WHISPERX_SAMPLE_RATE = 16000


def load_wav_audio(wav_path: Path, target_sr: int = WHISPERX_SAMPLE_RATE) -> np.ndarray:
    """Load a WAV file and return a float32 mono array at target_sr.

    Uses Python's wave module to avoid the torch DLL path issues that
    whisperx.load_audio() hits on Windows with pyenv.
    """
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

    # Mix to mono
    if ch == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    elif ch > 2:
        audio = audio.reshape(-1, ch).mean(axis=1)

    # Resample to target_sr if needed
    if sr != target_sr:
        new_len = int(len(audio) * target_sr / sr)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, new_len),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    return audio


def parse_wav_timestamp(filename: str) -> dt.datetime | None:
    """Extract UTC timestamp from WAV filename."""
    m = WAV_PATTERN.match(filename)
    if not m:
        return None
    return dt.datetime(
        int(m.group("year")), int(m.group("month")), int(m.group("day")),
        int(m.group("hour")), int(m.group("minute")), int(m.group("second")),
        tzinfo=dt.timezone.utc,
    )


def collect_wav_files(comm_dir: Path) -> list[tuple[Path, dt.datetime]]:
    """Collect all WAV files with parsed timestamps, sorted chronologically."""
    results = []
    for dirpath, _dirs, files in os.walk(comm_dir):
        for f in files:
            if not f.lower().endswith(".wav"):
                continue
            ts = parse_wav_timestamp(f)
            if ts is None:
                continue
            results.append((Path(dirpath) / f, ts))
    results.sort(key=lambda x: x[1])
    return results


def transcribe_wav(
    wav_path: Path,
    utc_time: dt.datetime,
    output_dir: Path,
    whisper_resources,
    force: bool = False,
) -> dict | None:
    """Transcribe a single WAV file and save the result as JSON."""
    out_name = wav_path.stem + ".json"
    out_path = output_dir / out_name

    if out_path.exists() and not force:
        return None  # already done

    # Load audio using our own WAV loader (avoids torch DLL issues on Windows)
    audio = load_wav_audio(wav_path)
    duration = len(audio) / WHISPERX_SAMPLE_RATE

    # Skip very short clips (< 0.5s) — likely just noise
    if duration < 0.5:
        return None

    # Transcribe
    result = whisper_resources.transcribe(audio, initial_prompt=INITIAL_PROMPT)

    language = result.get("language", "en")
    segments = result.get("segments", [])

    # Filter hallucinations
    text = " ".join(s.get("text", "").strip() for s in segments)
    if text.strip() in HALLUCINATIONS or not text.strip():
        return None

    # Build output
    payload = {
        "version": 1,
        "source": str(wav_path.name),
        "utcTime": utc_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

    return payload


def _pipeline_supported_transcribe_params(pipeline_cls: type) -> set[str]:
    """Introspect the pipeline's transcribe() signature to find accepted params."""
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
    """Call pipeline.transcribe(), filtering kwargs to those it actually accepts."""
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
        )

    def transcribe(self, audio, **kwargs) -> dict:
        self._ensure_model()
        kwargs.setdefault("batch_size", self.batch_size)
        return transcribe_with_model(self._model, audio, **kwargs)


def transcribe_comm(
    mission: MissionConfig,
    force: bool = False,
    test: int | None = None,
) -> None:
    if not mission.ia_comm_collection:
        print(f"  No comm audio configured for {mission.name}. Skipping.")
        return

    comm_dir = mission.raw_comm
    if not comm_dir.exists():
        print(f"  Comm directory not found: {comm_dir}")
        return

    wav_files = collect_wav_files(comm_dir)
    if not wav_files:
        print(f"  No WAV files found in {comm_dir}")
        return

    transcript_dir = mission.processed_transcripts / "comm"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    # Check how many are already done
    already_done = 0
    to_process = []
    for wav_path, utc_time in wav_files:
        date_str = utc_time.strftime("%Y-%m-%d")
        out_dir = transcript_dir / date_str
        out_path = out_dir / (wav_path.stem + ".json")
        if out_path.exists() and not force:
            already_done += 1
        else:
            to_process.append((wav_path, utc_time))

    print(f"  Found {len(wav_files)} WAV files ({already_done} already transcribed, "
          f"{len(to_process)} remaining)")

    if test is not None:
        to_process = to_process[:test]
        print(f"  --test {test}: processing only {len(to_process)} file(s)")

    if not to_process:
        print("  Nothing to do.")
        return

    # Load model
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

        try:
            result = transcribe_wav(wav_path, utc_time, out_dir, resources, force=force)
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
            print(f"  [{i}/{len(to_process)}] {transcribed} transcribed, "
                  f"{skipped} skipped, {errors} errors ({rate:.1f} files/s)")

    elapsed = time.time() - start
    print(f"\n  Done in {elapsed:.1f}s: {transcribed} transcribed, "
          f"{skipped} skipped (short/hallucination), {errors} errors")


def main():
    parser = argparse.ArgumentParser(description="Transcribe comm audio with WhisperX")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument("--force", action="store_true",
                        help="Re-transcribe even if output JSON already exists")
    parser.add_argument("--test", type=int, default=None, metavar="N",
                        help="Process only N files (for quick testing)")
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 1b: Comm Transcription — {mission.name} ===\n")
    transcribe_comm(mission, force=args.force, test=args.test)


if __name__ == "__main__":
    main()
