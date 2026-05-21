# Pipeline 2: Video (IA + YouTube)

## Data Flow

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Archive.org (IA)                                                          │
│      │                                                                     │
│      ▼                                                                     │
│  ┌─────────────────────┐                                                   │
│  │ 2a — IA Discover    │                                                   │
│  │ Saves:              │                                                   │
│  │  ia_video_catalog   │                                                   │
│  │  .json              │                                                   │
│  └────────┬────────────┘                                                   │
│           │                                                                │
│           ▼                                                                │
│  ┌─────────────────────┐                                                   │
│  │ 2b — IA Download    │                                                   │
│  │ Reads:              │                                                   │
│  │  ia_video_catalog   │                                                   │
│  │  .json              │                                                   │
│  │ Saves:              │                                                   │
│  │  raw/video/ia/*.mp4 │                                                   │
│  └────────┬────────────┘                                                   │
│           │                                                                │
│           ▼                                                                │
│  ┌─────────────────────┐                                                   │
│  │ 2f — IA Metadata    │                                                   │
│  │ Reads:              │                                                   │
│  │  ia_video_catalog   │                                                   │
│  │  .json              │                                                   │
│  │ Saves:              │                                                   │
│  │  ia_video_metadata  │                                                   │
│  │  .json              │                                                   │
│  └────────┬────────────┘                                                   │
│           │                                                                │
│  YouTube Data API                                                          │
│      │                                                                     │
│      ▼                                                                     │
│  ┌─────────────────────┐              ┌─────────────────────┐              │
│  │ 2d — YT Metadata    │              │ 2e — YT Download    │              │
│  │ Saves:              │──────────────▶ Reads:              │              │
│  │  yt_metadata.json   │              │  yt_metadata.json   │              │
│  └─────────────────────┘              │ Saves: YT_VIDEO_DIR │              │
│                                       └─────────────────────┘              │
└────────────────────────────────────────────────────────────────────────────┘

               │ (2f and 2d feed into 2g)
               ▼
      ┌─────────────────────┐
      │ 2g — Web Video JSON │
      │ Reads:              │
      │  ia_video_metadata  │  (from 2f — timestamps parsed from filenames)
      │  .json              │
      │  yt_metadata.json   │  (from 2d — actualStartTime from YouTube API)
      │ Saves:              │
      │  web/videoIA.json   │
      │  web/videoYt.json   │
      └─────────────────────┘

  2e output (large MKV from yt-dlp) feeds the upload sub-pipeline:

      ┌─────────────────────┐
      │ 2h — Split MKV      │  (standalone — no --mission flag)
      │ Reads:              │
      │  YT_VIDEO_DIR/*.mkv │  (from yt-dlp / 2e)
      │ Saves:              │
      │  {stem}_part01.mp4  │
      │  {stem}_part02.mp4  │
      │  …                  │
      └────────┬────────────┘
               │
       ┌───────┴────────────┐
       ▼                    ▼
  ┌──────────────────┐   ┌──────────────────────────────────┐
  │ 2i — YT Upload   │   │ 2j — Transcribe YT chunks        │
  │ (standalone)     │   │ Reads:                           │
  │ Reads:           │   │  *_part*.mp4 (from 2h)           │
  │  *_part*.mp4     │   │  comm transcript (from 1b/1c)    │
  │ Saves: YouTube   │   │ Saves:                           │
  │  video IDs in    │   │  processed/transcripts/yt/       │
  │  .yt_upload_     │   │  *.json (per chunk)              │
  │  state.json      │   └────────────────┬─────────────────┘
  └──────────────────┘                    │
                                          ▼
                               ┌──────────────────────────────────┐
                               │ 2k — Filter YT Transcript        │
                               │ Reads:                           │
                               │  processed/transcripts/yt/*.json │
                               │  web/comm.json (from 1c)         │
                               │ Saves:                           │
                               │  web/combined_transcript.json    │
                               └──────────────────────────────────┘
```

## Step Details

### 2a: IA Video Discovery (`2a_ia_video_discover.py`)

|                |                                   |
| -------------- | --------------------------------- |
| **Source**     | Archive.org Advanced Search API   |
| **Output**     | `processed/ia_video_catalog.json` |
| **Idempotent** | Overwrites output each run        |

Runs three discovery strategies and deduplicates:

1. **Subject tag search** — e.g. `subject:"Artemis II Resource Reel"`
2. **Collection search** — e.g. `collection:Artemis-II`
3. **Uploader search** — `uploader:"NASA Johnson"` filtered by mission name

Saves a list of IA item metadata (identifier, title, date, mediatype, etc).

### 2b: IA Video Download (`2b_ia_video_download.py`)

|                |                                                                      |
| -------------- | -------------------------------------------------------------------- |
| **Input**      | `processed/ia_video_catalog.json` (from 2a)                          |
| **Output**     | `raw/video/ia/*.mp4`                                                 |
| **Idempotent** | Yes — skips items where `{identifier}*` already exists in output dir |

For each IA item, fetches its file list via metadata API, picks the best MP4 (prefers `.ia.mp4` low-res derivative), and downloads it.

### 2f: IA Video Metadata (`2f_ia_video_metadata.py`)

|                |                                                                       |
| -------------- | --------------------------------------------------------------------- |
| **Input**      | `processed/ia_video_catalog.json` (from 2a), IA Metadata API          |
| **Output**     | `processed/ia_video_metadata.json`                                    |
| **Idempotent** | Yes — resumable, skips identifiers already present in the output file |

Produces a `yt_metadata.json`-equivalent for IA videos. For each catalog item:

1. **Parses a precise UTC timestamp** from the identifier using known NASA naming patterns:
   - **ART-DL resource reels**: `<Subject>_ART-DL-<CamN>_<YYYY>_<DOY>_<HHMM>_<SS><MMM>_<AssetID>` — encodes year, day-of-year, and HH:MM:SS UTC directly in the filename (e.g. `_2022_341_0755_30000` → 2022-12-07T07:55:30Z)
   - **YYMMDD suffix**: `_221128` or `_221128_AssetID` — date-only resolution
   - **KSC prefix**: `KSC-YYYYMMDD-` — date-only resolution
   - **Fallback**: IA item `date` metadata field
2. **Fetches IA item metadata** for title, description, and duration.
3. **Matches the downloaded local file** in `raw/video/ia/` by identifier glob.

Output fields per entry: `identifier`, `title`, `description`, `recorded_at`, `date_source`, `duration`, `source_url`, `filename`, and (for ART-DL items) `subject`, `camera`, `asset_id`.

### 2d: YouTube Metadata (`2d_yt_metadata.py`)

|                      |                              |
| -------------------- | ---------------------------- |
| **Input**            | YouTube Data API v3          |
| **Output**           | `processed/yt_metadata.json` |
| **Idempotent**       | Overwrites output each run   |
| **API key required** | `YOUTUBE_API_KEY`            |

Searches NASA's YouTube channel for completed livestreams matching mission terms. Fetches detailed metadata (duration, actualStartTime/EndTime). Filters by word-boundary matching to avoid cross-contamination (e.g. "Artemis I" vs "Artemis II").

### 2e: YouTube Download (`2e_yt_download.py`)

|                |                                                             |
| -------------- | ----------------------------------------------------------- |
| **Input**      | `processed/yt_metadata.json` (from 2d)                      |
| **Output**     | `YT_VIDEO_DIR/{mission}/*.mp4` (external drive)             |
| **Idempotent** | Yes — skips videos where `*{videoId}*` exists in output dir |
| **Requires**   | `yt-dlp` installed, Firefox cookies for auth                |

Downloads YouTube videos to a separate drive (H: by default). Uses Firefox cookies for authentication.

### 2g: Web Video JSON (`2g_web_video.py`)

|                |                                                                                      |
| -------------- | ------------------------------------------------------------------------------------ |
| **Input**      | `processed/ia_video_metadata.json` (from 2f), `processed/yt_metadata.json` (from 2d) |
| **Output**     | `web/videoIA.json`, `web/videoYt.json`                                               |
| **Idempotent** | Overwrites output each run                                                           |

Produces web-ready JSON from IA and YouTube metadata. IA timestamps come directly from `recorded_at` parsed in step 2f. YouTube timestamps come from `actualStartTime`/`actualEndTime` returned by the YouTube Data API.

### 2h: Split MKV → MP4 Chunks (`2h_split_mkv.py`)

|                |                                                                             |
| -------------- | --------------------------------------------------------------------------- |
| **Input**      | Large `.mkv` file (e.g. from yt-dlp or 2e)                                  |
| **Output**     | `{stem}_part01.mp4`, `{stem}_part02.mp4`, … (in same dir or `--output-dir`) |
| **Idempotent** | Yes — skips parts that already exist on disk                                |
| **Requires**   | `ffmpeg` on PATH                                                            |
| **Note**       | Standalone script — no `--mission` flag; run directly via `uv`              |

Splits a large MKV into YouTube-ready MP4 chunks using stream copy (default) or NVENC transcode (`--transcode`). Timestamps are reset to 0:00:00 per chunk and the moov atom is placed at the front (`faststart`) for fast YouTube ingest. Default chunk size is 8 hours.

Each output part JSON (written by step 2j) includes a `duration` field that 2k reads to compute exact cumulative offsets — chunk size is not assumed to be uniform.

```bash
uv run 2_video/2h_split_mkv.py --input "D:/NASA Artemis II Live Mission Coverage m3kR2KK8TEs.mkv"
uv run 2_video/2h_split_mkv.py --input "D:/..." --output-dir "D:/chunks" --chunk-hours 8
uv run 2_video/2h_split_mkv.py --input "D:/..." --transcode   # VP9/Opus sources
```

### 2i: Bulk YouTube Upload (`2i_yt_upload.py`)

|                |                                                                                    |
| -------------- | ---------------------------------------------------------------------------------- |
| **Input**      | `*_part*.mp4` files (from 2h)                                                      |
| **Output**     | Videos uploaded to YouTube; state saved in `{input_dir}/.yt_upload_state.json`     |
| **Idempotent** | Yes — skips files already recorded in `.yt_upload_state.json`                      |
| **Requires**   | OAuth 2.0 credentials at `src/server-batch/client_secrets.json` (desktop app type) |
| **Note**       | Standalone script — no `--mission` flag; run directly via `uv`                     |

Uses the YouTube Data API v3 resumable upload endpoint. Each upload costs 1,600 API quota units; the default daily quota allows ~6 uploads/day. The OAuth token is cached at `~/.config/artemis-ingest/yt_token.json` after the first browser sign-in.

```bash
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks"             # unlisted (safe default)
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks" --privacy public
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks" --dry-run  # preview without uploading
```

### 2j: Transcribe YT Chunks (`2j_transcribe_yt.py`)

|                |                                                                            |
| -------------- | -------------------------------------------------------------------------- |
| **Input**      | `*_part*.mp4` chunks (from 2h)                                             |
| **Output**     | `processed/transcripts/yt/{stem}.json` per part (file-relative timestamps) |
| **Idempotent** | Yes — skips parts whose JSON already exists                                |
| **Requires**   | WhisperX, Pyannote (`speaker-diarization-3.1`), GPU recommended            |

Runs WhisperX ASR + forced alignment + speaker diarization on each MP4 chunk. Saves one JSON per part containing file-relative segment timestamps, speaker labels, and a `duration` field (actual audio length in seconds). No UTC alignment is done here — run step 2k afterwards.

```bash
uv run run_all.py --mission artemis-ii --step 2j
# or directly:
uv run python -m 2_video.2j_transcribe_yt --mission artemis-ii
```

### 2k: Filter YT Transcript (`2k_filter_yt_transcript.py`)

|                |                                                                                  |
| -------------- | -------------------------------------------------------------------------------- |
| **Input**      | `processed/transcripts/yt/*.json` (from 2j), `web/comm.json` (from 1c)           |
| **Output**     | `web/combined_transcript.json` (web), audit files in `processed/transcripts/yt/` |
| **Idempotent** | Yes — overwrites outputs on each run                                             |
| **Requires**   | `web/comm.json` from step 1c for dedup and stream start detection                |

Determines when the YouTube stream began, removes utterances that duplicate the space-to-ground comm channel, and writes a combined timeline for the frontend.

#### Stream start UTC

The stream start UTC is the single most important calibration value — it sets the absolute UTC of every YT transcript segment. There are three ways to supply it, checked in this priority order:

1. **`--stream-start-utc` CLI flag** — explicit override, takes precedence over everything.
2. **`yt_stream_start_utc` in `config.py`** — mission-specific value set after calibration (see below). Used automatically when no CLI flag is given.
3. **Auto-detection** — fallback when neither of the above is set. Samples up to 200 YT segments, fuzzy-matches them against all comm entries, and takes the consensus median of `comm_utc − yt_relative_seconds`. Accuracy is typically ±1s but can be biased a few seconds early due to WhisperX segment-start timing. Result is saved to `_stream_start.json` for inspection.

**Artemis II calibration** (`yt_stream_start_utc = "2026-04-01T11:43:14.521Z"`):

Derived statistically from 1,801 high-confidence text-match pairs in a tight ±2s coincidence window between comm and YT transcripts. The mean comm-minus-YT offset was **7.747 ± 0.020s** (1σ), giving a stream start of `11:43:06.774Z + 7.747s = 11:43:14.521Z`. This was cross-validated against the "booster ignition and liftoff" call heard at approximately 2:51:57.5 into Part 2 (absolute YT offset ≈ 39,117.5s from stream start), compared against the known launch UTC of 22:35:12Z — yielding an independent estimate of `11:43:14.500Z`, agreeing to within 21 ms.

The WhisperX segment-start bias (~7.7s) is a known artefact of segment-level (not word-level) timestamps; the statistical method corrects for it automatically. The recording system PC clock was verified accurate to ~0.02s (no additional clock correction needed).

#### De-duplication

Two-pass process:

1. **Text + time matching** — each YT utterance is compared against comm entries within a ±90s UTC window (fallback ±300s at score ≥ 90). Matches at `fuzz.ratio ≥ 75` are marked removed.
2. **Speaker-cluster promotion** — for each diarization speaker ID, if ≥ 25% of their utterances matched comm (and they have ≥ 5 total), all remaining utterances by that speaker are also removed. Promotions with text similarity < 40 are flagged `low_confidence: true` in `_dedup_matches.json` for operator review.

#### Combined output

`web/combined_transcript.json` is a chronologically merged list of all comm entries (`"source": "comm"`) and surviving YT utterances (`"source": "yt"`). The `source` field allows the frontend to style and filter them independently.

```bash
uv run run_all.py --mission artemis-ii --step 2k
# or directly:
uv run python -m 2_video.2k_filter_yt_transcript --mission artemis-ii
# Override stream start (e.g. for re-calibration):
uv run python -m 2_video.2k_filter_yt_transcript --mission artemis-ii \
    --stream-start-utc "2026-04-01T11:43:14.521Z"
```

## Dependency Order

```
2a ─────────────┬──▶ 2b (needs ia_video_catalog.json)
                │         │
                │         └──▶ 2f (needs ia_video_catalog.json + raw/video/ia/)
                │
                └──▶ (2f and 2d can run in parallel after 2b)

2f ────────────────▶ 2g (needs ia_video_metadata.json)

2d ─────────────┬──▶ 2e (needs yt_metadata.json)
                │
                └──▶ 2g (needs yt_metadata.json)

2e ────────────────▶ 2h (split MKV → chunks)

2h ─────────────┬──▶ 2i (upload chunks to YouTube)
                │
                └──▶ 2j (transcribe; GPU required)
                           │
                           └──▶ 2k (filter comm dups; also needs 1b/1c output)
                                    └──▶ web/combined_transcript.json
```

**Minimum order**: `2a` → `2b` → `2f` + `2d` (parallel OK) → `2e` → `2g`

**Upload sub-pipeline**: `2e` → `2h` → `2i` (upload) + `2j` (transcribe) → `2k` (filter + web output)

## How to Run

Run from `src/server-batch/`:

```bash
# Run all steps in order (works for both missions)
uv run run_all.py --mission artemis-i
uv run run_all.py --mission artemis-ii

# Run individual steps
uv run run_all.py --mission artemis-ii --step 2a
uv run run_all.py --mission artemis-ii --step 2b
uv run run_all.py --mission artemis-ii --step 2f
uv run run_all.py --mission artemis-ii --step 2d
uv run run_all.py --mission artemis-ii --step 2e
uv run run_all.py --mission artemis-ii --step 2g
uv run run_all.py --mission artemis-ii --step 2j
uv run run_all.py --mission artemis-ii --step 2k

# Run steps directly via uv
uv run python -m 2_video.2a_ia_video_discover --mission artemis-ii
uv run python -m 2_video.2b_ia_video_download --mission artemis-ii
uv run python -m 2_video.2f_ia_video_metadata --mission artemis-ii
uv run python -m 2_video.2d_yt_metadata --mission artemis-ii
uv run python -m 2_video.2e_yt_download --mission artemis-ii
uv run python -m 2_video.2g_web_video --mission artemis-ii
uv run python -m 2_video.2j_transcribe_yt --mission artemis-ii
uv run python -m 2_video.2k_filter_yt_transcript --mission artemis-ii

# Standalone scripts (no --mission; run directly)
uv run 2_video/2h_split_mkv.py --input "D:/NASA Artemis II Live Mission Coverage m3kR2KK8TEs.mkv"
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks"

# Same commands with artemis-i
uv run run_all.py --mission artemis-i --step 2a
# ... etc
```

**Required env vars:** `YOUTUBE_API_KEY` (for step 2d). Step 2e requires `yt-dlp` and Firefox cookies. Step 2i requires OAuth credentials at `client_secrets.json`.

## Assets Saved (What Can Be Skipped on Re-run)

| File                                           | Produced by | Consumed by    | Re-run cost              |
| ---------------------------------------------- | ----------- | -------------- | ------------------------ |
| `processed/ia_video_catalog.json`              | 2a          | 2b, 2f         | Low (API calls)          |
| `raw/video/ia/*.mp4`                           | 2b          | 2f, (frontend) | **High** (GB downloads)  |
| `processed/ia_video_metadata.json`             | 2f          | 2g             | Low (IA metadata API)    |
| `processed/yt_metadata.json`                   | 2d          | 2e, 2g         | Low (YT API)             |
| `YT_VIDEO_DIR/{mission}/*.mp4`                 | 2e          | (frontend)     | **High** (GB downloads)  |
| `web/videoIA.json`                             | 2g          | (frontend)     | Instant                  |
| `web/videoYt.json`                             | 2g          | (frontend)     | Instant                  |
| `{stem}_part*.mp4`                             | 2h          | 2i, 2j         | **High** (ffmpeg, hours) |
| `{input_dir}/.yt_upload_state.json`            | 2i          | (resume state) | **High** (YT quota days) |
| `processed/transcripts/yt/*.json`              | 2j          | 2k             | **High** (GPU hours)     |
| `web/combined_transcript.json`                 | 2k          | (frontend)     | Fast (CPU only)          |
| `processed/transcripts/yt/_stream_start.json`  | 2k          | (audit/re-run) | Fast (CPU only)          |
| `processed/transcripts/yt/_dedup_matches.json` | 2k          | (audit)        | Fast (CPU only)          |
| `processed/transcripts/yt/_comm_speakers.json` | 2k          | (audit)        | Fast (CPU only)          |
