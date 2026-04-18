# Pipeline 2: Video (IA + IO + YouTube)

## Data Flow

```
┌────────────────────────────────────────────────────────────────────────────┐
│  TWO INDEPENDENT DISCOVERY PATHS                                          │
│                                                                            │
│  Archive.org (IA)                          IO (Imagery Online)             │
│      │                                         │                           │
│      ▼                                         ▼                           │
│  ┌─────────────────────┐              ┌─────────────────────┐              │
│  │ 2a — IA Discover    │              │ 2c2 — IO Video      │              │
│  │ Saves:              │              │ Catalog              │              │
│  │  ia_video_catalog   │              │ Saves:               │              │
│  │  .json              │              │  io_video_catalog    │              │
│  └────────┬────────────┘              │  .jsonl              │              │
│           │                           └──────────────────────┘              │
│           │                              ⚠️ OUTPUT NOT CONSUMED            │
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
│  │ 2c — IO Search      │                                                   │
│  │ Reads:              │                                                   │
│  │  ia_video_catalog   │                                                   │
│  │  .json              │                                                   │
│  │ Saves:              │                                                   │
│  │  io_found.jsonl     │                                                   │
│  │  io_notfound.jsonl  │                                                   │
│  └─────────────────────┘                                                   │
│                                                                            │
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

               │ (2a, 2c, 2d all feed into 2g)
               ▼
      ┌─────────────────────┐
      │ 2g — Web Video JSON │
      │ Reads:              │
      │  ia_video_catalog   │  (from 2a)
      │  io_found.jsonl     │  (from 2c)
      │  yt_metadata.json   │  (from 2d)
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

### 2c: IO Search (`2c_io_search.py`)

|                |                                                          |
| -------------- | -------------------------------------------------------- |
| **Input**      | `processed/ia_video_catalog.json` (from 2a)              |
| **Output**     | `processed/io_cache/io_found.jsonl`, `io_notfound.jsonl` |
| **Idempotent** | Yes — skips items already in found/notfound JSONL files  |

For each IA video item, extracts a NASA ID from the IA identifier using regex patterns (e.g. `jsc2026m000052`), then searches IO API for that keyword. Saves the IO doc (with `vmd_start_gmt` broadcast timestamp) or marks it as not found.

**Purpose**: Get accurate broadcast timestamps for IA videos. Without this, IA videos have no reliable timeline placement.

### 2c2: IO Video Catalog (`2c2_io_video_catalog.py`)

|                |                                             |
| -------------- | ------------------------------------------- |
| **Input**      | IO API (collection CID from config)         |
| **Output**     | `processed/io_cache/io_video_catalog.jsonl` |
| **Idempotent** | Overwrites output each run                  |

Fetches ALL video docs from IO's flight collections under the parent CID (e.g. 2,418 videos for Artemis II). Uses `cols=` and `as=2` (video asset type) parameters.

**Purpose**: Build a comprehensive catalog of all NASA flight video, including videos that may not be on IA.

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

|                |                                                                                                                                    |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Input**      | `processed/ia_video_catalog.json` (from 2a), `processed/io_cache/io_found.jsonl` (from 2c), `processed/yt_metadata.json` (from 2d) |
| **Output**     | `web/videoIA.json`, `web/videoYt.json`                                                                                             |
| **Idempotent** | Overwrites output each run                                                                                                         |

Merges IA video metadata with IO timestamps and produces web-ready JSON. YouTube videos get their own output file.

## Dependency Order

```
2a ─────────────┬──▶ 2b (needs ia_video_catalog.json)
                │
                ├──▶ 2c (needs ia_video_catalog.json)
                │
                └──▶ 2g (needs ia_video_catalog.json)

2c ────────────────▶ 2g (needs io_found.jsonl)

2c2 ───────────────▶ (NOTHING — output orphaned)

2d ─────────────┬──▶ 2e (needs yt_metadata.json)
                │
                └──▶ 2g (needs yt_metadata.json)
```

**Minimum order**: `2a` → `2b` + `2c` + `2d` (parallel OK) → `2e` → `2g`

**2c2 can run anytime** but its output is never consumed.

## Assets Saved (What Can Be Skipped on Re-run)

| File                                        | Produced by | Consumed by | Re-run cost              |
| ------------------------------------------- | ----------- | ----------- | ------------------------ |
| `processed/ia_video_catalog.json`           | 2a          | 2b, 2c, 2g  | Low (API calls)          |
| `raw/video/ia/*.mp4`                        | 2b          | (frontend)  | **High** (GB downloads)  |
| `processed/io_cache/io_found.jsonl`         | 2c          | 2g          | Medium (per-item IO API) |
| `processed/io_cache/io_notfound.jsonl`      | 2c          | (reference) | Medium                   |
| `processed/io_cache/io_video_catalog.jsonl` | 2c2         | **NOTHING** | Medium                   |
| `processed/yt_metadata.json`                | 2d          | 2e, 2g      | Low (YT API)             |
| `YT_VIDEO_DIR/{mission}/*.mp4`              | 2e          | (frontend)  | **High** (GB downloads)  |
| `web/videoIA.json`                          | 2g          | (frontend)  | Instant                  |
| `web/videoYt.json`                          | 2g          | (frontend)  | Instant                  |

## Issues Found

### 1. ⚠️ `io_video_catalog.jsonl` (2c2) is never consumed

Step 2c2 scrapes all 2,418 IO flight videos, but `2g_web_video.py` only reads:

- `ia_video_catalog.json` (from 2a) — IA items
- `io_found.jsonl` (from 2c) — per-item IO lookups
- `yt_metadata.json` (from 2d) — YouTube

The IO video catalog — the most comprehensive source of flight video — is produced but never used. This means **any video in IO that isn't also on Archive.org is invisible to the frontend**.

### 2. ⚠️ 2c and 2c2 do redundant IO queries

**2c** iterates through IA video items one by one, extracting NASA IDs and searching IO for each. **2c2** does a single bulk scrape of the same IO collection. The bulk scrape from 2c2 already contains all the data that 2c is searching for individually, plus videos that aren't on IA at all.

Running both means:

- 2c2 fetches ~2,418 docs in ~5 paginated requests
- 2c then does ~25+ individual IO API calls for the same data

### 3. 🔴 `2g_web_video.py` IO matching is fragile

The IO lookup in `build_web_video_ia()` does a substring match:

```python
for nid, doc in io_lookup.items():
    if nid in identifier.lower():
        io_doc = doc
        break
```

This iterates the entire lookup dict for every IA item and matches on substring containment, which could produce false positives (e.g. `jsc2026m000052` matching `jsc2026m0000521`). It also breaks on the `first` match arbitrarily.

## Recommended Refactoring

### Option A: Merge 2c into 2c2 (eliminate per-item IO lookups)

1. Run 2c2 first to get the full IO video catalog
2. Replace 2c with a local cross-reference script that matches IA identifiers against the IO catalog using NASA ID regex — no API calls needed
3. Have 2g read `io_video_catalog.jsonl` directly and match by nasa_id

### Option B: Make 2g consume io_video_catalog.jsonl

If you want to keep both scripts:

1. Have 2g also read `io_video_catalog.jsonl`
2. Include IO-only videos (not on IA) in the web output
3. This gives the frontend access to all 2,418 IO videos, not just the ~25 IA items

### Recommended approach: Option A

Merge 2c and 2c2 into a single step that:

1. Fetches the full IO video catalog (bulk, paginated)
2. Cross-references IA items against the catalog locally (no per-item API calls)
3. Outputs both `io_video_catalog.jsonl` (full catalog) and enriched IA metadata

This eliminates redundant API calls and produces a more comprehensive dataset.
