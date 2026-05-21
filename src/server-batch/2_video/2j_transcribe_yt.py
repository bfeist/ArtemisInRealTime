"""Step 2j — Transcribe YouTube part MP4s.

Transcribes each *_part*.mp4 chunk (produced by step 2h) using the full
WhisperX pipeline: ASR → forced alignment → speaker diarization.

Saves one JSON per part to {processed_transcripts}/yt/{stem}.json with
file-relative timestamps only.  No UTC is needed here — one reason to
transcribe first is to DETERMINE the stream start UTC by comparing speech
against the comm transcript.

Once transcription is done, run step 2k (2k_filter_yt_transcript.py) to
auto-detect the stream start UTC (by matching speech against comm.json),
remove comm duplicates, and produce the combined web transcript.

─── Processing mode ──────────────────────────────────────────────────────────
Default (full-file, diarization enabled):
  Each 12-hour MP4 is loaded as a single float32 array.  With 128 GB RAM and
  24 GB VRAM this is trivial.  Chunked processing would reset speaker labels
  at each chunk boundary, making cross-chunk speaker identity impossible.

  Pipeline per part:
    1. Extract full audio → temp 16 kHz mono WAV (avoids Windows/pyenv DLL
       issues with whisperx.load_audio piped stdout).
    2. WhisperX transcription (batch_size=32 for 24 GB VRAM).
    3. WhisperX forced alignment (word-level timestamps).
    4. Pyannote speaker diarization (speaker-diarization-3.1).
    5. WhisperX speaker assignment (merges ASR + diarization).
    6. Save {stem}.json with file-relative timestamps and speaker labels.

Chunked fallback (--segment-hours N, disables diarization):
  For lower-memory machines.  Processes in N-hour audio chunks; speaker labels
  are per-chunk and NOT comparable across chunks.

─── Crash recovery ───────────────────────────────────────────────────────────
  Already-finished parts are skipped unless --force is passed.
  Chunked mode saves a .progress.json per part so partial work survives a crash.

─── One-time setup ───────────────────────────────────────────────────────────
  uv sync
  # HF_TOKEN must be set in .env for pyannote speaker-diarization-3.1 access:
  # https://huggingface.co/pyannote/speaker-diarization-3.1 (accept terms)

Usage (run from src/server-batch/):
    uv run 2_video/2j_transcribe_yt.py \\
        --mission artemis-ii \\
        --input-dir "H:/ArtemisInRealTime_yt_videos/artemis-ii"

    # Low-memory fallback — 2-hour chunks, no diarization:
    uv run 2_video/2j_transcribe_yt.py ... --segment-hours 2

    # Dry-run — list parts without transcribing:
    uv run 2_video/2j_transcribe_yt.py ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

# ── Config ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import HF_TOKEN, MISSIONS  # noqa: E402

WHISPERX_SAMPLE_RATE = 16_000

HALLUCINATIONS: frozenset[str] = frozenset({
    "Thank you.", "Bye.", "...", "Thanks for watching!",
    "Thank you for watching.", "Thank you for watching!",
    "Mmm.", "Hmm.", "Mmmmmmmm.", "MMMMMMMM",
    "Beep.", "BEEP", "Beeping.", "BEEEEEP",
    "BOOOOOM", "BOOOOOM!", "BELL RINGS",
    "This video is a derivative work of the Touhou Project",
})

INITIAL_PROMPT = (
    "NASA Artemis II live mission coverage. "
    "Crew: Commander Reid Wiseman, Pilot Victor Glover, "
    "Mission Specialist Christina Koch, Mission Specialist Jeremy Hansen (CSA). "
    "NASA Mission Control Houston. Orion spacecraft. Space Launch System SLS."
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe not found on PATH")
    return exe


def get_duration(video_path: Path) -> float:
    cmd = [_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def extract_full_audio_wav(video_path: Path, out_wav: Path) -> None:
    """Extract the entire audio track to a 16 kHz mono PCM WAV."""
    cmd = [
        _ffmpeg(),
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(video_path),
        "-ac", "1",
        "-ar", str(WHISPERX_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True)


def extract_audio_segment_wav(
    video_path: Path, start_s: float, duration_s: float, out_wav: Path
) -> None:
    """Extract a time-bounded audio segment (for chunked fallback mode)."""
    cmd = [
        _ffmpeg(),
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", str(start_s),
        "-i", str(video_path),
        "-t", str(duration_s),
        "-ac", "1",
        "-ar", str(WHISPERX_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True)


def load_wav_numpy(wav_path: Path) -> np.ndarray:
    """Load a 16 kHz mono PCM WAV to a float32 numpy array.

    Uses Python's wave module (not whisperx.load_audio) to avoid the
    torch DLL path issues on Windows + pyenv.
    """
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())

    dtype = np.int16  if sw == 2 else np.int32
    scale = 32768.0   if sw == 2 else 2_147_483_648.0
    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32) / scale

    if ch == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    elif ch > 2:
        audio = audio.reshape(-1, ch).mean(axis=1)

    if sr != WHISPERX_SAMPLE_RATE:
        new_len = int(len(audio) * WHISPERX_SAMPLE_RATE / sr)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, new_len),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    return audio


def parse_part_num(filename: str) -> int:
    m = re.search(r"_part(\d+)", filename, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _save_json(path: Path, data: object) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ── WhisperX pipeline ─────────────────────────────────────────────────────────


def run_whisperx_pipeline(
    audio: np.ndarray,
    *,
    device: str,
    compute_type: str,
    batch_size: int,
    hf_token: str,
    diarize: bool,
    max_speakers: int | None,
) -> list[dict]:
    """Run the full WhisperX pipeline: ASR → align → (diarize) → assign.

    Returns a list of segment dicts with keys: start, end, text, lang,
    speaker (empty string if diarization was skipped or produced no label).

    The initial_prompt is passed via asr_options at load_model time because
    FasterWhisperPipeline.transcribe() does not accept it as a call argument.
    """
    import gc      # noqa: PLC0415
    import torch   # noqa: PLC0415
    import whisperx  # noqa: PLC0415

    # 1. Transcription
    print("    [1/4] Transcribing …", flush=True)
    t0 = time.monotonic()
    asr_model = whisperx.load_model(
        "large-v3", device, compute_type=compute_type,
        vad_options={"vad_type": "silero"},
        asr_options={"initial_prompt": INITIAL_PROMPT},
    )
    result = asr_model.transcribe(audio, batch_size=batch_size, language="en")
    language = result.get("language", "en")
    print(f"    [1/4] Done in {time.monotonic() - t0:.0f}s — "
          f"{len(result['segments'])} raw segments, language={language}")

    del asr_model
    gc.collect()
    torch.cuda.empty_cache()

    # 2. Forced alignment (word-level timestamps — required for speaker assignment)
    print("    [2/4] Aligning …", flush=True)
    t0 = time.monotonic()
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )
    print(f"    [2/4] Done in {time.monotonic() - t0:.0f}s")

    del align_model
    gc.collect()
    torch.cuda.empty_cache()

    # 3. Diarization
    if diarize:
        if not hf_token:
            print("    [3/4] WARNING: HF_TOKEN not set — skipping diarization. "
                  "Set HF_TOKEN in .env and accept the pyannote model terms.")
            diarize = False
        else:
            print("    [3/4] Diarizing …", flush=True)
            t0 = time.monotonic()
            # pyannote 3.3.2 uses use_auth_token= (huggingface_hub ≤0.x API).
            # huggingface_hub is pinned <1.0.0 in pyproject.toml so this works.
            import pandas as pd  # noqa: PLC0415
            from pyannote.audio import Pipeline as _PyAnnoPipeline  # noqa: PLC0415
            _pa_model = _PyAnnoPipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=hf_token,
            ).to(torch.device(device) if isinstance(device, str) else device)
            audio_data = {
                "waveform": torch.from_numpy(audio[None, :]),
                "sample_rate": WHISPERX_SAMPLE_RATE,
            }
            diarize_kwargs: dict = {}
            if max_speakers is not None:
                diarize_kwargs["max_speakers"] = max_speakers
            _pa_result = _pa_model(audio_data, **diarize_kwargs)
            diarize_segments = pd.DataFrame(
                _pa_result.itertracks(yield_label=True),
                columns=["segment", "label", "speaker"],
            )
            diarize_segments["start"] = diarize_segments["segment"].apply(lambda x: x.start)
            diarize_segments["end"]   = diarize_segments["segment"].apply(lambda x: x.end)
            print(f"    [3/4] Done in {time.monotonic() - t0:.0f}s")

            # 4. Assign speakers
            print("    [4/4] Assigning speakers …", flush=True)
            result = whisperx.assign_word_speakers(diarize_segments, result)
            print("    [4/4] Done")
    else:
        print("    [3/4] Diarization skipped (--no-diarize or no HF_TOKEN)")
        print("    [4/4] (skipped)")

    # Build output segments
    segments: list[dict] = []
    for s in result.get("segments", []):
        text = s.get("text", "").strip()
        if not text or text in HALLUCINATIONS:
            continue
        segments.append({
            "start":   round(float(s.get("start", 0)), 3),
            "end":     round(float(s.get("end",   0)), 3),
            "text":    text,
            "lang":    language,
            "speaker": s.get("speaker", ""),
        })

    return segments


# ── Per-part transcription (full-file mode) ───────────────────────────────────


def transcribe_part_full(
    part_path: Path,
    part_num: int,
    output_dir: Path,
    *,
    device: str,
    compute_type: str,
    batch_size: int,
    hf_token: str,
    diarize: bool,
    max_speakers: int | None,
    force: bool,
) -> list[dict]:
    """Transcribe one MP4 part as a single audio load (full-file mode).

    Loads the full ~12-hour audio into RAM at once (2.76 GB float32 for 12 h).
    Returns segments with file-relative timestamps.
    """
    out_json = output_dir / (part_path.stem + ".json")
    if out_json.exists() and not force:
        with open(out_json, encoding="utf-8") as fh:
            data = json.load(fh)
        segs = data.get("segments", [])
        diarized = data.get("diarized", False)
        print(f"    Already done — {len(segs)} segments "
              f"({'diarized' if diarized else 'no diarization'}) — skipping.")
        return segs

    duration_s = get_duration(part_path)
    size_gb = part_path.stat().st_size / (1024 ** 3)
    audio_gb = duration_s * WHISPERX_SAMPLE_RATE * 4 / (1024 ** 3)
    print(f"    File: {size_gb:.2f} GB  Duration: {duration_s / 3600:.2f}h  "
          f"Audio array: {audio_gb:.2f} GB float32")

    with tempfile.TemporaryDirectory(prefix="yt_asr_") as tmpdir:
        tmp_wav = Path(tmpdir) / "audio.wav"
        print("    Extracting audio to temp WAV …", flush=True)
        t0 = time.monotonic()
        extract_full_audio_wav(part_path, tmp_wav)
        print(f"    Extracted in {time.monotonic() - t0:.0f}s "
              f"({tmp_wav.stat().st_size / (1024**3):.2f} GB)")

        print("    Loading WAV into RAM …", flush=True)
        t0 = time.monotonic()
        audio = load_wav_numpy(tmp_wav)
        print(f"    Loaded in {time.monotonic() - t0:.0f}s "
              f"({audio.nbytes / (1024**3):.2f} GB)")

    segments = run_whisperx_pipeline(
        audio,
        device=device,
        compute_type=compute_type,
        batch_size=batch_size,
        hf_token=hf_token,
        diarize=diarize,
        max_speakers=max_speakers,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version":  1,
        "source":   part_path.name,
        "part":     part_num,
        "duration": round(duration_s, 2),
        "language": segments[0]["lang"] if segments else "en",
        "model":    "large-v3",
        "diarized": diarize and bool(segments and segments[0].get("speaker")),
        "segments": segments,
    }
    _save_json(out_json, payload)
    print(f"    → {out_json.name}  ({len(segments)} segments)")
    return segments


# ── Per-part transcription (chunked fallback mode) ────────────────────────────


def transcribe_part_chunked(
    part_path: Path,
    part_num: int,
    output_dir: Path,
    *,
    segment_hours: float,
    device: str,
    compute_type: str,
    batch_size: int,
    force: bool,
) -> list[dict]:
    """Chunked fallback: process in N-hour segments without diarization.

    Speaker labels are NOT assigned (they would be incoherent across chunks).
    Saves a per-part .progress.json for resume granularity.
    """
    out_json = output_dir / (part_path.stem + ".json")
    if out_json.exists() and not force:
        with open(out_json, encoding="utf-8") as fh:
            data = json.load(fh)
        segs = data.get("segments", [])
        print(f"    Already done — {len(segs)} segments — skipping.")
        return segs

    import whisperx  # noqa: PLC0415

    duration_s = get_duration(part_path)
    seg_s      = segment_hours * 3600
    num_segs   = math.ceil(duration_s / seg_s)

    progress_path = output_dir / (part_path.stem + ".progress.json")
    progress: dict[str, list[dict]] = {}
    if progress_path.exists() and not force:
        with open(progress_path, encoding="utf-8") as fh:
            progress = json.load(fh)
        print(f"    Resuming — {len(progress)}/{num_segs} chunk(s) already done.")

    # Load WhisperX model once for all chunks of this part.
    # initial_prompt is baked in via asr_options so transcribe() calls need no extra args.
    asr_model = whisperx.load_model(
        "large-v3", device, compute_type=compute_type,
        vad_options={"vad_type": "silero"},
        asr_options={"initial_prompt": INITIAL_PROMPT},
    )
    all_segments: list[dict] = []
    language = "en"

    with tempfile.TemporaryDirectory(prefix="yt_asr_") as tmpdir:
        for seg_i in range(num_segs):
            key = str(seg_i)
            if key in progress:
                all_segments.extend(progress[key])
                continue

            seg_start = seg_i * seg_s
            seg_dur   = min(seg_s, duration_s - seg_start)
            h, m      = int(seg_start // 3600), int((seg_start % 3600) // 60)
            print(
                f"    Chunk {seg_i+1:2d}/{num_segs}  [{h:02d}:{m:02d} + "
                f"{segment_hours:.0f}h] …",
                end="", flush=True,
            )
            t0 = time.monotonic()

            seg_wav = Path(tmpdir) / f"seg_{seg_i:04d}.wav"
            extract_audio_segment_wav(part_path, seg_start, seg_dur, seg_wav)
            audio = load_wav_numpy(seg_wav)

            try:
                result = asr_model.transcribe(audio, batch_size=batch_size, language="en")
            except (IndexError, ValueError):
                progress[key] = []
                _save_json(progress_path, progress)
                print(f"  (no speech)  {time.monotonic()-t0:.0f}s")
                continue

            language = result.get("language", "en")
            segs: list[dict] = []
            for s in result.get("segments", []):
                text = s.get("text", "").strip()
                if not text or text in HALLUCINATIONS:
                    continue
                segs.append({
                    "start":   round(float(s["start"]) + seg_start, 3),
                    "end":     round(float(s["end"])   + seg_start, 3),
                    "text":    text,
                    "lang":    language,
                    "speaker": "",
                })

            progress[key] = segs
            _save_json(progress_path, progress)
            all_segments.extend(segs)
            print(f"  {len(segs):4d} segs  {time.monotonic()-t0:.0f}s")

    del asr_model

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version":  1,
        "source":   part_path.name,
        "part":     part_num,
        "duration": round(duration_s, 2),
        "language": language,
        "model":    "large-v3",
        "diarized": False,
        "segments": all_segments,
    }
    _save_json(out_json, payload)
    if progress_path.exists():
        progress_path.unlink()
    print(f"    → {out_json.name}  ({len(all_segments)} segments)")
    return all_segments


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe YouTube part MP4s with WhisperX (ASR + alignment + diarization)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--input-dir", required=True, type=Path,
        help="Directory containing *_part*.mp4 chunk files (from step 2h)",
    )
    parser.add_argument(
        "--chunk-hours", type=float, default=12.0,
        help="Chunk length used in step 2h (default: 12). Recorded in output JSON.",
    )
    parser.add_argument(
        "--pattern", default="*_part*.mp4",
        help="Glob for part files (default: *_part*.mp4)",
    )
    parser.add_argument(
        "--segment-hours", type=float, default=None,
        help="Chunked fallback: split each part into N-hour segments. "
             "Disables diarization. Use when RAM is insufficient for full-file load.",
    )
    parser.add_argument(
        "--no-diarize", action="store_true",
        help="Skip speaker diarization even in full-file mode.",
    )
    parser.add_argument(
        "--max-speakers", type=int, default=None,
        help="Maximum number of speakers to detect (passed to pyannote). "
             "Default: auto.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-transcribe even if output JSON already exists.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print plan (parts, sizes) without transcribing.",
    )
    args = parser.parse_args()

    mission   = MISSIONS[args.mission]
    input_dir = args.input_dir.resolve()

    if not input_dir.exists():
        print(f"ERROR: --input-dir not found: {input_dir}")
        sys.exit(1)

    output_dir = mission.processed_transcripts / "yt"
    output_dir.mkdir(parents=True, exist_ok=True)

    use_chunks = args.segment_hours is not None
    diarize    = not args.no_diarize and not use_chunks

    device       = os.environ.get("WHISPER_DEVICE",       "cuda")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
    batch_size   = int(os.environ.get("WHISPER_BATCH_SIZE", "32"))
    hf_token     = HF_TOKEN or os.environ.get("HF_TOKEN", "")

    files = sorted(input_dir.glob(args.pattern))
    if not files:
        print(f"No files matching '{args.pattern}' in {input_dir}")
        sys.exit(0)

    total_parts = len(files)

    print(f"\n=== Step 2j: Transcribe YouTube Parts ===\n")
    print(f"  Mission    : {mission.name}")
    print(f"  Input dir  : {input_dir}")
    print(f"  Parts found: {total_parts}")
    print(f"  Chunk size : {args.chunk_hours}h")
    print(f"  Mode       : "
          + ("chunked segments (no diarization)" if use_chunks
             else "full-file" + (" + diarization" if diarize else " (no diarization)")))
    if use_chunks:
        print(f"  Seg size   : {args.segment_hours}h")
    print(f"  Output dir : {output_dir}")
    if diarize:
        print(f"  HF_TOKEN   : {'set' if hf_token else 'NOT SET — diarization will be skipped'}")
    if args.dry_run:
        print(f"  (DRY RUN)")
    print()

    plan: list[tuple[Path, int]] = []
    for i, fpath in enumerate(files, 1):
        pnum = parse_part_num(fpath.name) or i
        plan.append((fpath, pnum))
        size_gb = fpath.stat().st_size / (1024 ** 3)
        print(f"  [{i:02d}/{total_parts:02d}] part{pnum:02d}  ({size_gb:.2f} GB)  {fpath.name}")

    if args.dry_run:
        print("\n  Dry-run complete — no files written.\n")
        return

    for fpath, pnum in plan:
        print(f"\n  ── Part {pnum:02d}/{total_parts}: {fpath.name} ──")
        if use_chunks:
            transcribe_part_chunked(
                fpath, pnum, output_dir,
                segment_hours=args.segment_hours,
                device=device, compute_type=compute_type, batch_size=batch_size,
                force=args.force,
            )
        else:
            transcribe_part_full(
                fpath, pnum, output_dir,
                device=device, compute_type=compute_type, batch_size=batch_size,
                hf_token=hf_token, diarize=diarize, max_speakers=args.max_speakers,
                force=args.force,
            )

    print(f"\n  Done — {total_parts} part(s) saved to {output_dir}")
    print(f"  Next: run step 2k once the stream start UTC is known.\n")


if __name__ == "__main__":
    main()
