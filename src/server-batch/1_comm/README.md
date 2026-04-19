# Pipeline 1: Communications (Audio) — Artemis II Only

Artemis I was uncrewed — this entire pipeline is skipped for `artemis-i`.

## Data Flow

```
archive.org (Artemis-II-ACR-Collection)
    │
    ▼
┌──────────────────────────────────────┐
│  1a — Download ZIPs from IA          │
│  Reads: IA metadata API              │
│  Saves: raw/comm/*.zip               │
│         raw/comm/{date}/*.wav        │  (auto-extracted from ZIPs)
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  1b — Transcribe with WhisperX       │
│  Reads: raw/comm/**/*.wav            │
│  Saves: processed/transcripts/comm/  │
│         {date}/{filename}.json       │  (one JSON per WAV)
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  1c — Web-ready JSON                 │
│  Reads: processed/transcripts/comm/  │
│  Saves: web/comm.json                │
└──────────────────────────────────────┘
```

## Step Details

### 1a: Download IA ZIPs (`1a_download_ia_zips.py`)

|                 |                                                                |
| --------------- | -------------------------------------------------------------- |
| **Source**      | `https://archive.org/download/Artemis-II-ACR-Collection`       |
| **Input**       | IA metadata API (item file list)                               |
| **Output**      | `raw/comm/*.zip` + auto-extracted `raw/comm/{date_stem}/*.wav` |
| **Idempotent**  | Yes — skips already-downloaded ZIPs by filename                |
| **Re-runnable** | Yes — designed for it. NASA is still uploading days 6–11.      |

Downloads ZIP archives, then auto-extracts WAV files into per-date subdirectories.

### 1b: Transcribe (`1b_transcribe.py`)

|                  |                                                            |
| ---------------- | ---------------------------------------------------------- |
| **Input**        | `raw/comm/**/*.wav`                                        |
| **Output**       | `processed/transcripts/comm/{date}/{wav_stem}.json`        |
| **Idempotent**   | Yes — skips WAVs that already have a `.json` output        |
| **GPU required** | Yes — WhisperX large-v3 on CUDA                            |
| **Flags**        | `--force` re-transcribes all; `--test N` limits to N files |

Each output JSON contains:

```json
{
  "utcTime": "2026-04-01T12:42:51Z",
  "duration": 45.2,
  "language": "en",
  "segments": [{ "start": 0.0, "end": 2.5, "text": "..." }]
}
```

### 1c: Web Comm JSON (`1c_web_comm.py`)

|                |                                        |
| -------------- | -------------------------------------- |
| **Input**      | `processed/transcripts/comm/**/*.json` |
| **Output**     | `web/comm.json`                        |
| **Idempotent** | Yes — overwrites output each run       |

Aggregates per-file transcripts into a single chronologically-sorted JSON array. Filters hallucination strings. Each entry has `t` (UTC time), `d` (duration), `text`, and `segments`.

## Dependencies

Strictly linear: **1a → 1b → 1c**. No external dependencies on other pipelines.

## How to Run

> ⚠️ This pipeline is **Artemis II only**. Artemis I was uncrewed — skip all steps.

Run from `src/server-batch/`:

```bash
# Run all steps in order
python run_all.py --mission artemis-ii

# Run individual steps
python run_all.py --mission artemis-ii --step 1a
python run_all.py --mission artemis-ii --step 1b
python run_all.py --mission artemis-ii --step 1c

# Run steps directly (useful for flags like --force or --test)
python -m 1_comm.1a_download_ia_zips --mission artemis-ii
python -m 1_comm.1b_transcribe --mission artemis-ii
python -m 1_comm.1b_transcribe --mission artemis-ii --test 5   # transcribe only 5 files
python -m 1_comm.1b_transcribe --mission artemis-ii --force    # re-transcribe all
python -m 1_comm.1c_web_comm --mission artemis-ii
```

**Required env vars:** `HF_TOKEN` (Hugging Face token for WhisperX diarization), GPU with CUDA for step 1b.

## Issues

None — this pipeline is clean and straightforward.
