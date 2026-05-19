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
  │  video IDs in    │   │  processed/yt_transcript.json    │
  │  .yt_upload_     │   │  (de-duped vs comm)              │
  │  state.json      │   └──────────────────────────────────┘
  └──────────────────┘
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

Uses the YouTube Data API v3 resumable upload endpoint. Each upload costs 1 600 API quota units; the default daily quota allows ~6 uploads/day. The OAuth token is cached at `~/.config/artemis-ingest/yt_token.json` after the first browser sign-in.

```bash
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks"             # unlisted (safe default)
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks" --privacy public
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks" --dry-run  # preview without uploading
```

### 2j: Transcribe YT Chunks (`2j_transcribe_yt.py`)

|                |                                                                 |
| -------------- | --------------------------------------------------------------- |
| **Input**      | `*_part*.mp4` chunks (from 2h), comm transcript (from 1b/1c)    |
| **Output**     | `processed/yt_transcript.json` (de-duplicated against comm)     |
| **Idempotent** | Yes — skips parts already present in output                     |
| **Requires**   | WhisperX, Pyannote (`speaker-diarization-3.1`), GPU recommended |

Runs WhisperX ASR + forced alignment + speaker diarization on each MP4 chunk, then de-duplicates against the comm transcript using a two-pass text+time / speaker-cluster algorithm. Utterances also present in the comm transcript are flagged so the frontend can suppress them from the integrated timeline.

```bash
uv run run_all.py --mission artemis-ii --step 2j
# or directly:
uv run python -m 2_video.2j_transcribe_yt --mission artemis-ii
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
                └──▶ 2j (transcribe + de-dup vs comm; also needs 1b/1c)
```

**Minimum order**: `2a` → `2b` → `2f` + `2d` (parallel OK) → `2e` → `2g`

**Upload sub-pipeline**: `2e` → `2h` → `2i` (upload) + `2j` (transcribe)

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

# Run steps directly via uv
uv run python -m 2_video.2a_ia_video_discover --mission artemis-ii
uv run python -m 2_video.2b_ia_video_download --mission artemis-ii
uv run python -m 2_video.2f_ia_video_metadata --mission artemis-ii
uv run python -m 2_video.2d_yt_metadata --mission artemis-ii
uv run python -m 2_video.2e_yt_download --mission artemis-ii
uv run python -m 2_video.2g_web_video --mission artemis-ii
uv run python -m 2_video.2j_transcribe_yt --mission artemis-ii

# Standalone scripts (no --mission; run directly)
uv run 2_video/2h_split_mkv.py --input "D:/NASA Artemis II Live Mission Coverage m3kR2KK8TEs.mkv"
uv run 2_video/2i_yt_upload.py --input-dir "D:/chunks"

# Same commands with artemis-i
uv run run_all.py --mission artemis-i --step 2a
# ... etc
```

**Required env vars:** `YOUTUBE_API_KEY` (for step 2d). Step 2e requires `yt-dlp` and Firefox cookies. Step 2i requires OAuth credentials at `client_secrets.json`.

## Assets Saved (What Can Be Skipped on Re-run)

| File                                | Produced by | Consumed by    | Re-run cost              |
| ----------------------------------- | ----------- | -------------- | ------------------------ |
| `processed/ia_video_catalog.json`   | 2a          | 2b, 2f         | Low (API calls)          |
| `raw/video/ia/*.mp4`                | 2b          | 2f, (frontend) | **High** (GB downloads)  |
| `processed/ia_video_metadata.json`  | 2f          | 2g             | Low (IA metadata API)    |
| `processed/yt_metadata.json`        | 2d          | 2e, 2g         | Low (YT API)             |
| `YT_VIDEO_DIR/{mission}/*.mp4`      | 2e          | (frontend)     | **High** (GB downloads)  |
| `web/videoIA.json`                  | 2g          | (frontend)     | Instant                  |
| `web/videoYt.json`                  | 2g          | (frontend)     | Instant                  |
| `{stem}_part*.mp4`                  | 2h          | 2i, 2j         | **High** (ffmpeg, hours) |
| `{input_dir}/.yt_upload_state.json` | 2i          | (resume state) | **High** (YT quota days) |
| `processed/yt_transcript.json`      | 2j          | (frontend)     | **High** (GPU hours)     |
