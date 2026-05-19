"""Step 2h — Split a large MKV into YouTube-ready MP4 chunks.

Splits without re-encoding (stream copy) for maximum speed.  MP4 supports
H.264/H.265 video and AAC audio natively, so no transcode is needed for files
captured from a YouTube live stream at standard quality.

Source file (ffprobe confirmed):
    D:/NASA Artemis II Live Mission Coverage m3kR2KK8TEs.mkv
    Video : H.264 High Profile Level 4.0, 1920×1080, yuv420p, BT.709, ~30 fps
    Audio : AAC-LC, stereo, 44100 Hz
    Muxer : Lavf (yt-dlp), handler "ISO Media file produced by Google Inc."
    Size  : ~60 GB   Duration: 230 h 38 m (830 284 s)  ~29 chunks @ 8 h
    → Stream copy works perfectly; no transcode required.

If the source contains VP9 or Opus (common in high-quality yt-dlp captures),
stream copy into MP4 is unreliable.  Pass --transcode to re-encode to
H.264 + AAC using NVENC (GPU).  Transcoding a 60 GB file still takes hours
even with hardware acceleration.

Usage (run from src/server-batch/):
    # Stream copy (fast — no quality loss):
    uv run 2_video/2h_split_mkv.py --input "D:/NASA Artemis II Live Mission Coverage m3kR2KK8TEs.mkv"

    # Specify output dir and chunk size:
    uv run 2_video/2h_split_mkv.py --input "D:/..." --output-dir "D:/chunks" --chunk-hours 8

    # Force transcode via NVENC (only needed for VP9/Opus sources):
    uv run 2_video/2h_split_mkv.py --input "D:/..." --transcode

Output naming:
    {stem}_part01.mp4, {stem}_part02.mp4, …

Each chunk is independently playable with timestamps reset to 0:00:00 so
YouTube can index and seek properly.  The moov atom is moved to the front
(faststart) to speed up YouTube's ingest.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


# ── Probe helpers ─────────────────────────────────────────────────────────────


def probe_file(input_path: Path) -> dict:
    """Return ffprobe JSON for the file (format + streams)."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: ffprobe failed:\n{result.stderr}")
        sys.exit(1)
    return json.loads(result.stdout)


def describe_streams(probe: dict) -> tuple[str, str]:
    """Return (video_codec, audio_codec) from probe output, or '' if not found."""
    video_codec = ""
    audio_codec = ""
    for stream in probe.get("streams", []):
        ct = stream.get("codec_type", "")
        cn = stream.get("codec_name", "")
        if ct == "video" and not video_codec:
            video_codec = cn
        elif ct == "audio" and not audio_codec:
            audio_codec = cn
    return video_codec, audio_codec


def check_mp4_compat(video_codec: str, audio_codec: str) -> bool:
    """Return True if the codecs copy cleanly into an MP4 container."""
    MP4_VIDEO = {"h264", "hevc", "h265", "mpeg4", "mjpeg"}
    MP4_AUDIO = {"aac", "mp3", "mp2", "ac3", "eac3", "flac", "alac"}
    ok_v = video_codec.lower() in MP4_VIDEO
    ok_a = audio_codec.lower() in MP4_AUDIO
    if not ok_v:
        print(f"  WARNING: Video codec '{video_codec}' may not copy cleanly into MP4.")
        print(f"           (VP9 and AV1 are common yt-dlp choices that require --transcode.)")
    if not ok_a:
        print(f"  WARNING: Audio codec '{audio_codec}' may not copy cleanly into MP4.")
        print(f"           (Opus is common in high-quality yt-dlp captures; use --transcode.)")
    return ok_v and ok_a


# ── Splitting ─────────────────────────────────────────────────────────────────


def _hms(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def split_file(
    input_path: Path,
    output_dir: Path,
    chunk_seconds: int,
    transcode: bool,
) -> None:
    probe = probe_file(input_path)
    duration = float(probe["format"]["duration"])
    total_s = int(duration)

    video_codec, audio_codec = describe_streams(probe)
    print(f"  Source codecs : video={video_codec}  audio={audio_codec}")

    if not transcode:
        compat = check_mp4_compat(video_codec, audio_codec)
        if not compat:
            print()
            print("  Stream copy into MP4 may produce broken output for this source.")
            print("  Re-run with --transcode to re-encode to H.264 + AAC.")
            print("  Continuing with stream copy anyway — press Ctrl+C to abort.")
            print()

    stem = input_path.stem

    # Build chunk list: (part_number, start_sec, duration_sec, output_path)
    chunks = []
    start = 0
    part = 1
    while start < total_s:
        chunk_dur = min(chunk_seconds, total_s - start)
        out = output_dir / f"{stem}_part{part:02d}.mp4"
        chunks.append((part, start, chunk_dur, out))
        start += chunk_seconds
        part += 1

    total_h = total_s // 3600
    total_m = (total_s % 3600) // 60
    total_sec = total_s % 60
    print(f"  Total duration: {total_h}h {total_m}m {total_sec}s  ({total_s} s)")
    print(f"  Chunks         : {len(chunks)} × up to {chunk_seconds // 3600}h")
    print()

    for part_num, start_s, dur_s, out_path in chunks:
        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 ** 2)
            print(f"  [Part {part_num:02d}/{len(chunks):02d}] Already exists ({size_mb:.0f} MB) — skipping: {out_path.name}")
            continue

        print(f"  [Part {part_num:02d}/{len(chunks):02d}] {_hms(start_s)} + {dur_s // 3600}h{(dur_s % 3600) // 60:02d}m → {out_path.name}")

        if transcode:
            # H.264 via NVENC (GPU), YouTube-optimal settings.
            # VBR with CQ 18 targets near-lossless quality.
            # p4 = balanced speed/quality preset; use p7 for best quality (slower).
            video_args = [
                "-c:v", "h264_nvenc",
                "-rc", "vbr",
                "-cq", "18",
                "-b:v", "0",
                "-maxrate", "8M",
                "-bufsize", "16M",
                "-preset", "p4",
                "-profile:v", "high",
                "-level", "4.0",
                "-pix_fmt", "yuv420p",
                "-spatial-aq", "1",
                "-temporal-aq", "1",
                "-rc-lookahead", "32",
            ]
            audio_args = [
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-ac", "2",
            ]
        else:
            video_args = ["-c:v", "copy"]
            audio_args = ["-c:a", "copy"]

        cmd = [
            "ffmpeg",
            # Seek before opening the input (fast for large files — keyframe-aligned)
            "-ss", str(start_s),
            "-i", str(input_path),
            "-t", str(dur_s),
            *video_args,
            *audio_args,
            "-map", "0:v:0",   # first video stream
            "-map", "0:a:0",   # first audio stream
            "-movflags", "+faststart",   # moov atom at front — faster YouTube ingest
            "-avoid_negative_ts", "make_zero",
            "-reset_timestamps", "1",
            # Overwrite guard: fail if output exists (resumability handled above)
            "-n",
            str(out_path),
        ]

        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"\n  ERROR: ffmpeg exited with code {result.returncode} on part {part_num}.")
            print(f"  Partial output (if any) left at: {out_path}")
            print(f"  Fix the error and re-run — already-completed parts will be skipped.")
            sys.exit(result.returncode)

        size_mb = out_path.stat().st_size / (1024 ** 2)
        print(f"    → {size_mb:.0f} MB  saved to {out_path}")

    print()
    print(f"  All {len(chunks)} chunk(s) complete.")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a large MKV into YouTube-ready MP4 chunks (stream copy by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the source MKV file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same directory as input file)",
    )
    parser.add_argument(
        "--chunk-hours",
        type=float,
        default=8.0,
        help="Chunk length in hours (default: 8; YouTube max is 12 h but uploads near that limit are often rejected)",
    )
    parser.add_argument(
        "--transcode",
        action="store_true",
        help="Re-encode to H.264 + AAC via NVENC instead of stream copy (needed for VP9/Opus sources)",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    output_dir = args.output_dir.resolve() if args.output_dir else input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_seconds = int(args.chunk_hours * 3600)
    mode = "transcode (h264_nvenc + AAC)" if args.transcode else "stream copy (no transcode)"

    print(f"\n=== Step 2h: Split MKV → MP4 chunks ===\n")
    print(f"  Input      : {input_path}")
    print(f"  Output dir : {output_dir}")
    print(f"  Chunk size : {args.chunk_hours}h  ({chunk_seconds} s)")
    print(f"  Mode       : {mode}")
    print()

    split_file(input_path, output_dir, chunk_seconds, args.transcode)

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
