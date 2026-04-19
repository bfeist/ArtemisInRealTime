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

|                |                                                                           |
| -------------- | ------------------------------------------------------------------------- |
| **Input**      | `processed/ia_video_catalog.json` (from 2a), IA Metadata API              |
| **Output**     | `processed/ia_video_metadata.json`                                        |
| **Idempotent** | Yes — resumable, skips identifiers already present in the output file     |

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

|                |                                                                                              |
| -------------- | -------------------------------------------------------------------------------------------- |
| **Input**      | `processed/ia_video_metadata.json` (from 2f), `processed/yt_metadata.json` (from 2d)        |
| **Output**     | `web/videoIA.json`, `web/videoYt.json`                                                       |
| **Idempotent** | Overwrites output each run                                                                   |

Produces web-ready JSON from IA and YouTube metadata. IA timestamps come directly from `recorded_at` parsed in step 2f. YouTube timestamps come from `actualStartTime`/`actualEndTime` returned by the YouTube Data API.

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
```

**Minimum order**: `2a` → `2b` → `2f` + `2d` (parallel OK) → `2e` → `2g`

## How to Run

Run from `src/server-batch/`:

```bash
# Run all steps in order (works for both missions)
python run_all.py --mission artemis-i
python run_all.py --mission artemis-ii

# Run individual steps
python run_all.py --mission artemis-ii --step 2a
python run_all.py --mission artemis-ii --step 2b
python run_all.py --mission artemis-ii --step 2f
python run_all.py --mission artemis-ii --step 2d
python run_all.py --mission artemis-ii --step 2e
python run_all.py --mission artemis-ii --step 2g

# Run steps directly
python -m 2_video.2a_ia_video_discover --mission artemis-ii
python -m 2_video.2b_ia_video_download --mission artemis-ii
python -m 2_video.2f_ia_video_metadata --mission artemis-ii
python -m 2_video.2d_yt_metadata --mission artemis-ii
python -m 2_video.2e_yt_download --mission artemis-ii
python -m 2_video.2g_web_video --mission artemis-ii

# Same commands with artemis-i
python run_all.py --mission artemis-i --step 2a
# ... etc
```

**Required env vars:** `YOUTUBE_API_KEY` (for step 2d). Steps 2e requires `yt-dlp` and Firefox cookies.

## Assets Saved (What Can Be Skipped on Re-run)

| File                                        | Produced by | Consumed by       | Re-run cost              |
| ------------------------------------------- | ----------- | ----------------- | ------------------------ |
| `processed/ia_video_catalog.json`           | 2a          | 2b, 2f            | Low (API calls)          |
| `raw/video/ia/*.mp4`                        | 2b          | 2f, (frontend)    | **High** (GB downloads)  |
| `processed/ia_video_metadata.json`          | 2f          | 2g                | Low (IA metadata API)    |
| `processed/yt_metadata.json`                | 2d          | 2e, 2g            | Low (YT API)             |
| `YT_VIDEO_DIR/{mission}/*.mp4`              | 2e          | (frontend)        | **High** (GB downloads)  |
| `web/videoIA.json`                          | 2g          | (frontend)        | Instant                  |
| `web/videoYt.json`                          | 2g          | (frontend)        | Instant                  |
