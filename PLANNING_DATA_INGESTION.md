# Artemis In Real Time — Data Ingestion Plan

## Context

This document plans the data ingestion backend for ArtemisInRealTime, covering **both Artemis I and Artemis II** missions. Modeled after [ISSiRT's `server-batch`](../ISSiRT/src/server-batch) architecture and the [`IO_nasatv`](../data_processing/IO_nasatv) pipeline.

Artemis I is complete. Artemis II flew April 1–11, 2026 (UTC) and the mission is complete, but **NASA is still uploading comm audio** to archive.org — so the comm pipeline needs to be re-runnable to pick up new uploads. All other pipelines are run-once batch jobs that can be re-run idempotently. All scripts accept a `--mission` argument (`artemis-i` or `artemis-ii`) so the same codebase handles both missions with mission-specific config. Uses `uv` for package management with `pyproject.toml`.

### Mission Differences

| Aspect           | Artemis I                                        | Artemis II                                       |
| ---------------- | ------------------------------------------------ | ------------------------------------------------ |
| Type             | Uncrewed test flight                             | Crewed lunar flyby                               |
| Dates            | Nov 16 – Dec 11, 2022                            | Apr 1 – Apr 11, 2026 (UTC)                       |
| Comm audio       | **None** (uncrewed)                              | Yes — Orion-to-Earth ACR audio                   |
| IA video         | 91 items (subject: `Artemis I Resource Reel`)    | 25+ items (`Artemis-II` collection)              |
| IA still imagery | `Artemis-I-Still-Imagery` (62 JPEGs)             | TBD                                              |
| YouTube          | Livestreams of launch, flyby, splashdown         | Livestreams of crewed mission                    |
| Flickr           | Album exists — needs flight photo classification | Album exists — needs flight photo classification |

---

## Architecture Overview

```
src/
├── config.py                  # Paths, API keys, per-mission constants
├── run_all.py                 # Sequential runner (--mission flag, --step for individual steps)
│
├── 1_comm/                    # Audio communications (Artemis II only)
│   ├── 1a_download_ia_zips.py       # Download ZIPs from archive.org
│   ├── 1b_transcribe.py             # WhisperX transcription
│   └── 1c_web_comm.py               # Produce web-ready JSON
│
├── 2_video/                   # YouTube livestreams + IA video
│   ├── 2a_ia_video_discover.py      # Discover IA items by subject tag + uploader search
│   ├── 2b_ia_video_download.py      # Download MP4s from discovered IA items
│   ├── 2c_io_search.py              # Search IO API for nasa_ids & broadcast timestamps
│   ├── 2c2_io_video_catalog.py      # Scrape IO flight video collections
│   ├── 2d_yt_metadata.py            # YouTube Data API: fetch livestream metadata
│   ├── 2e_yt_download.py            # yt-dlp: download YouTube videos
│   ├── 2f_transcribe.py             # WhisperX transcription of video audio
│   └── 2g_web_video.py              # Produce web-ready JSON
│
├── 3_photos/                  # IA still imagery + Flickr + images.nasa.gov
│   ├── 3a_ia_stills_download.py     # Download still imagery from IA collections
│   ├── 3a2_io_photo_catalog.py      # Scrape IO flight photo collections
│   ├── 3b_flickr_albums.py          # Discover & fetch Flickr album metadata
│   ├── 3c_flickr_photos.py          # Fetch photo metadata + URLs
│   ├── 3d_flickr_classify.py        # AI classification: flight vs preflight/portrait/etc.
│   ├── 3e_images_nasa_gov.py        # Scrape images.nasa.gov for Artemis photos
│   └── 3f_web_photos.py             # Produce web-ready JSON
│
└── shared/                    # Shared utilities
    ├── io_api.py                     # Imagery Online API client
    ├── ia_helpers.py                 # Archive.org discovery + download helpers
    ├── flickr_api.py                 # Flickr API client
    └── yt_helpers.py                 # YouTube API + yt-dlp wrappers
```

### Data directory layout

Assets live outside the repo in a sibling directory (`../ArtemisInRealTime_assets/`) to keep large binary files (video, audio, images) out of version control. `DATA_DIR` in `.env` points to this location.

```
../ArtemisInRealTime_assets/
├── artemis-i/
│   ├── raw/
│   │   ├── video/
│   │   │   ├── ia/                # IA video downloads (91 items)
│   │   │   └── yt/                # YouTube livestream downloads
│   │   └── photos/
│   │       ├── ia_stills/         # Artemis-I-Still-Imagery (62 JPEGs)
│   │       ├── flickr/            # Flickr metadata JSON
│   │       └── images_nasa_gov/   # images.nasa.gov metadata
│   ├── processed/
│   │   ├── transcripts/           # WhisperX output (video only, no comm)
│   │   └── io_cache/              # IO API response cache
│   │       ├── io_found.jsonl     # IA videos matched in IO
│   │       ├── io_notfound.jsonl  # IA videos not in IO
│   │       ├── io_video_catalog.jsonl  # Full IO video catalog
│   │       └── io_photo_catalog.jsonl  # Full IO photo catalog
│   └── web/                       # Web-ready JSON
│       ├── videoIA.json
│       ├── videoYt.json
│       └── photos.json
│
├── artemis-ii/
│   ├── raw/
│   │   ├── comm/                  # Downloaded audio ZIPs + extracted WAVs
│   │   ├── video/
│   │   │   ├── ia/                # IA video downloads
│   │   │   └── yt/                # YouTube livestream downloads
│   │   └── photos/
│   │       ├── ia_stills/         # IA still imagery (when available)
│   │       ├── flickr/            # Flickr metadata JSON
│   │       └── images_nasa_gov/   # images.nasa.gov metadata
│   ├── processed/
│   │   ├── transcripts/           # WhisperX output (comm + video)
│   │   └── io_cache/              # IO API response cache
│   │       ├── io_found.jsonl     # IA videos matched in IO
│   │       ├── io_notfound.jsonl  # IA videos not in IO
│   │       ├── io_video_catalog.jsonl  # Full IO video catalog
│   │       └── io_photo_catalog.jsonl  # Full IO photo catalog
│   └── web/                       # Web-ready JSON
│       ├── comm.json
│       ├── videoIA.json
│       ├── videoYt.json
│       └── photos.json
│
└── shared/
    └── flickr_classify_cache/     # AI classification results (reusable)
```

---

## Pipeline 1: Communications (Audio) — Artemis II Only

Artemis I was uncrewed — no space-to-ground or crew communications exist.

### Source

- **Archive.org collection**: `https://archive.org/download/Artemis-II-ACR-Collection`
- Files observed so far: `04-01-26_Orion-to-Earth audio.zip` through `04-05-26_Orion-to-Earth audio.zip` (46–63 MB each)
- Mission ran April 1–11, 2026 UTC — **NASA has not finished uploading all comm audio**. ZIPs for days 6–11 are expected to appear over time.
- XML manifest at: `https://archive.org/download/Artemis-II-ACR-Collection/Artemis-II-ACR-Collection_files.xml`
- **Audio format inside ZIPs**: TBD — need to download one ZIP and inspect contents. Likely WAV or MP3 based on ISSiRT comm patterns.

### Step 1a: Download ZIPs from Archive.org

**Reference**: `ISSiRT/src/server-batch/1_comm/1a_download_collection_IA_zips.py`

- Fetch `_files.xml` manifest, parse with `lxml`
- Filter for `.zip` files only
- Download each ZIP with `requests` streaming + `tqdm` progress
- Skip already-downloaded files (check by filename in output dir)
- Normalize filenames (pad single-digit dates: `4-1-26` → `04-01-26`)
- Extract audio files (WAV/MP3) from ZIPs into date-organized folders

**Differences from ISSiRT**:

- Single collection vs. 48+ collections — no collection iteration needed
- No state-tracking text files needed — just check filesystem
- Filename pattern is `MM-DD-YY_Orion-to-Earth audio.zip` (not space-to-ground)
- **Re-runnable**: NASA is still uploading comm ZIPs. Re-running this step picks up any new ZIPs that appeared since the last run (skip already-downloaded files).

### Step 1b: Transcription

**Reference**: `ISSiRT/src/server-batch/1_comm/6_transcribe_using_corpus.py`

- Load WhisperX model (`large-v3`, CUDA)
- For each extracted audio file:
  - Voice activity detection (webrtcvad)
  - Transcribe with WhisperX
  - Speaker diarization
  - Save transcript as JSON per audio file
- Optional: simple Artemis II crew roster + terminology list as WhisperX initial_prompt
- Store results in `$DATA_DIR/artemis-ii/processed/transcripts/comm/`

### Step 1c: Web-ready JSON

- Aggregate per-file transcripts into a single `comm.json`
- Format: array of segments with `{ timestamp, speaker, text, audioFile }`

---

## Pipeline 2: Video (IA + YouTube + IO)

This pipeline handles both missions. IA video is the primary source; YouTube livestreams supplement with live broadcast recordings.

### Sources: Archive.org

#### Artemis I — Known IA Items (91 total)

Discovered via `subject:"Artemis I Resource Reel"`. All uploaded by `john.l.stoll@nasa.gov`, in `jsc-pao-video-collection`. Key items include:

| IA Identifier                                               | Content                                   | Size   |
| ----------------------------------------------------------- | ----------------------------------------- | ------ |
| `Artemis_I_Return_Powered_Flyby`                            | 2 videos: Return Powered Flyby coverage   | 116.5G |
| `Artemis_I_Outbound_Powered_Flyby_Coverage`                 | Outbound flyby coverage                   | Large  |
| `art001m1503352130-Artemis_I_Orion_Prepares_to_Leave_DRO_*` | DRO departure                             | —      |
| `jsc2022m000281_ParachutesBringingOrionHome-*`              | Splashdown/recovery                       | —      |
| `View-of-Earth_ART-DL-2_*`                                  | Earth views from Orion                    | —      |
| `Artemis-I-Still-Imagery`                                   | 62 still photos (handled in Pipeline 3)   | 30.7M  |
| ... + ~85 more items                                        | Resource reels, event coverage, downlinks | —      |

**Filename/ID patterns observed**:

- `art001m\d+` — Artemis program NASA IDs (e.g., `art001m1503352130`)
- `jsc\d+m\d+` — JSC PAO videos (e.g., `jsc2022m000281`)
- `ART-DL-\d+_\d{4}_\d{3}_\d{4}` — Artemis downlink pattern (e.g., `ART-DL-2_2022_345_1510`)

#### Artemis II — Known IA Items

**Collection**: `Artemis-II` (identifier: `Artemis-II`, 83.3G, uploaded by john.l.stoll@nasa.gov)

Contents include:

- Flight Day highlights (Day 1, 2, 3, 4, 5 — NoLowerThirds versions)
- Training resource reels (`jsc2025m000144`, `jsc2026m000052`)
- News conferences (Mission Overview, Science & Tech, Crew)
- Crew training videos (Lunar Flyby Photography, TLI Simulation)
- Crew profile AVAILs (Meet NASA Astronaut series — 4K, MXF, SOCIAL versions)
- Mission status briefings

**Filename/ID patterns**: `jsc2025m\d+`, `jsc2026m\d+`

**Additional IA collection for future video**: `Artemis-II-ACR-Collection` may receive video alongside the comm audio.

### Source: YouTube

- **NASA YouTube channel**: `UCLA_DiR1FfKNvjuUpBHmylQ`
- **Artemis I keywords**: `"Artemis I"`, `"Artemis 1"`
- **Artemis II keywords**: `"Artemis II"`, `"Artemis 2"`
- Filter: `eventType=completed` (finished live broadcasts only)

### Source: Imagery Online API

**IO is a primary data source** — not just a timestamp lookup. IO contains the complete catalog of NASA flight imagery and video.

- **Endpoint**: `https://io.jsc.nasa.gov/api/search`
- **URL format** (unusual — query params split across path and query string):
  ```
  https://io.jsc.nasa.gov/api/search/q={keyword}&rpp=500?key={api_key}&format=json
  ```
  Note: `q=` and `rpp=` are in the path segment; `key=` and `format=` are in the query string.
- **Auth**: API key via `IO_KEY` env var, `Origin: coda.fit.nasa.gov` header
- **SSL**: IO uses a self-signed cert — `verify=False` required, suppress InsecureRequestWarning
- **Response format** (Solr-style wrapped in extra layer):
  ```json
  { "results": { "response": { "numfound": N, "docs": [...] } } }
  ```
- **Pagination**: `rpp=500` per page, `&start={offset}` for subsequent pages
- **Key fields per doc**: `nasa_id`, `vmd_start_gmt`, `on_public_site`, `collections_string`, `webpath`, `md_creation_date`

#### Artemis II IO Flight Collections (parent cid=2380537)

From `https://io.jsc.nasa.gov/app/collections.cfm?cid=2380537`:

| Collection                           | Photos | Videos | Notes                       |
| ------------------------------------ | ------ | ------ | --------------------------- |
| **MISSION IMAGERY**                  | 21,659 | 8      | Primary flight photo source |
| **VIDEO**                            | 0      | 2,418  | Primary flight video source |
| **Artemis-02 FCR**                   | 1,313  | 4      | Flight Control Room imagery |
| **Artemis-02 FCR Teams**             | 13     | 0      | FCR team photos             |
| **Artemis-02 Launch**                | 88     | 0      | Launch day imagery          |
| **Artemis-02 Splashdown & Recovery** | 11     | 0      | Recovery imagery            |
| **Artemis-02 Events**                | 875    | 5      | Mission events              |
| _Excluded: Preflight_                | 6,601  | 45     | Training, pre-mission       |
| _Excluded: Simulations_              | 2,659  | 2      | Sim runs                    |
| _Excluded: Press Briefings_          | 557    | 3      | Media events                |
| _Excluded: Crew Announcement_        | 1,806  | 37     | Pre-mission crew events     |
| _Excluded: Portraits/Patches_        | 59+    | 0      | Official portraits          |

**Total flight imagery to ingest**: ~23,959 photos, ~2,435 videos

### Step 2a: IA Video Discovery

**Reference**: `IO_nasatv/scripts/IA_videos/1_scan_ia_metadata.py`

Material is **not stored in a single collection** — it is spread across many individual IA items, standalone identifiers, and partially across larger collections. Discovery requires running all strategies below and deduplicating by identifier.

#### Discovery Strategy 1: Subject Tag Search (primary)

Query IA Advanced Search for each known subject tag. Each result is its own standalone IA item.

| Mission    | Subject tag query                    | Known count |
| ---------- | ------------------------------------ | ----------- |
| Artemis I  | `subject:"Artemis I Resource Reel"`  | 91 items    |
| Artemis II | `subject:"Artemis II Resource Reel"` | growing     |

Example items found via this search:

- `art001m1503352130-Artemis_I_Orion_Prepares_to_Leave_DRO_221201_1732085`
- `Artemis_I_Outbound_Powered_Flyby_Coverage`
- `jsc2022m000281_ParachutesBringingOrionHome-221206`
- `View-of-Earth_ART-DL-2_2022_345_1510_00000_1739069`
- `Artemis_I_Return_Powered_Flyby` (116.5G, 2 videos)

#### Discovery Strategy 2: Known Standalone Identifiers

Items that may not carry the subject tag but are known to exist. Scan their `_files.xml` directly.

```python
IA_KNOWN_IDENTIFIERS = {
    "artemis-i": [
        "Artemis_I_Return_Powered_Flyby",
        "Artemis_I_Outbound_Powered_Flyby_Coverage",
        "Artemis-I-Still-Imagery",
    ],
    "artemis-ii": [
        "Artemis-II",
        "Artemis-II-ACR-Collection",
    ],
}
```

#### Discovery Strategy 3: Uploader / Creator Search

Query IA by the NASA PAO uploader accounts, then filter results for Artemis-related titles/subjects.

```python
# IA Advanced Search queries to run per uploader
for email in IA_UPLOADERS:
    query = f'uploader:"{email}" AND (subject:"Artemis" OR title:"Artemis")'
```

Also query by creator name fields used in some items:

- `creator:"John Stoll"`
- `creator:"nasa"`

#### Discovery Strategy 4: Collection Membership

Scan the `jsc-pao-video-collection` collection membership for Artemis items. This catches anything in the PAO collection that wasn't tagged with a subject. Filter by title containing `Artemis`.

#### Deduplication & Manifest Fetch

After collecting all discovered identifiers across all strategies:

1. Deduplicate by IA identifier
2. For each unique identifier, fetch `_files.xml`:
   `https://archive.org/download/{identifier}/{identifier}_files.xml`
3. Extract file metadata: filename, format (H.264, MPEG2, MPEG4), size, duration
4. Filter for video files only (not thumbnails, metadata, torrents)
5. When multiple MP4s exist for the same content, pick the **smaller** MP4 (use ISSiRT's format-selection logic). Skip MPEG2 originals entirely.
6. Extract NASA IDs from filenames for IO API lookup

Output: `$DATA_DIR/{mission}/raw/video/ia/ia_video_metadata.jsonl`

Update `config.py` as new items are discovered — add them to `IA_KNOWN_IDENTIFIERS` so re-runs pick them up without relying solely on search.

### Step 2b: IA Video Download

**Reference**: `IO_nasatv/scripts/IA_videos/4_download_ia_mp4s.py`

- Download the smaller MP4 for each discovered item (use ISSiRT logic: when an item has multiple MP4 variants like 4K/MXF/SOCIAL, pick the smallest MP4)
- Streaming download with `tqdm` progress
- Skip existing files
- Concurrent downloads (configurable, default 3)
- Rename with IO timestamp when available: `{vmd_start_gmt}_{filename}.mp4`

Output: `$DATA_DIR/{mission}/raw/video/ia/`

### Step 2c: IO API Search & Flight-Day Filtering

**Reference**: `IO_nasatv/scripts/IA_videos/3_search_io_api.py`

- Extract NASA IDs from IA filenames:
  - Pattern `art\d+m\d+` (Artemis program IDs)
  - Pattern `jsc\d+m\d+` (JSC PAO IDs)
- Also try downlink patterns: `ART-DL-\d+_\d{4}_\d{3}_\d{4}_\d+_\d+`
- Query IO API (note the weird URL format):
  ```
  https://io.jsc.nasa.gov/api/search/q={nasa_id}&rpp=500?key={IO_KEY}&format=json
  ```
  Headers: `{"Origin": "coda.fit.nasa.gov"}`, `verify=False`
- Response: `data["results"]["response"]["docs"]` — Solr-style with extra wrapper
- Paginate if `numfound > 500`: append `&start={offset}`
- Rate limit: 0.5–1s between requests
- Capture `vmd_start_gmt` for accurate broadcast timestamps
- **Flight-day filtering**: Use IO metadata (`vmd_start_gmt`) to determine which videos were broadcast during the mission date range. Mark preflight content (training reels, news conferences, crew profiles) so it can be excluded from the timeline. Only videos with timestamps within `mission_start` to `mission_end` are included in web output.
- Output: `$DATA_DIR/{mission}/processed/io_cache/io_found.jsonl`, `io_notfound.jsonl`

### Step 2c2: IO Video Collection Scrape

Scrape IO’s flight video collections directly (not just IA filename lookups). For Artemis II this is the **VIDEO** collection (2,418 videos) plus FCR videos.

- Query IO API by collection: search within each `io_flight_collections` entry
- For each video doc, extract: `nasa_id`, `vmd_start_gmt`, `webpath`, `collections_string`
- Cross-reference with IA video metadata from Step 2a — many will overlap
- Deduplicate by `nasa_id`
- This gives us the full catalog of IO flight video with accurate broadcast timestamps
- Output: `$DATA_DIR/{mission}/processed/io_cache/io_video_catalog.jsonl`

### Step 2d: YouTube Metadata

**Reference**: `ISSiRT/src/server-batch/5_video/3_web_yt_api_live_recordings_json.py`

- YouTube Data API v3:
  - `/search` endpoint: `channelId`, `q={search_term}`, `eventType=completed`, `type=video`
  - `/videos` endpoint: fetch `contentDetails` (duration), `liveStreamingDetails` (actualStartTime), `snippet`
- Per-mission search terms (see config)
- Extract: `videoId`, `title`, `actualStartTime`, `duration`
- Support manual additions via `youtube_manual.csv` for any missed streams
- Output: `$DATA_DIR/{mission}/raw/video/yt/yt_metadata.json`

### Step 2e: YouTube Download

**Reference**: `ISSiRT/src/server-batch/5_video/4_download_yt_videos.py`

- Use `yt-dlp` to download best quality: `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best`
- Filename template: `{actualStartTime}_{videoId}_{title}.mp4`
- Skip already-downloaded (check by videoId in filename)
- **Download to D: drive** (backup copies, like ISSiRT): `D:/ArtemisInRealTime_yt_videos/{mission}/`
- Output metadata stays in: `$DATA_DIR/{mission}/raw/video/yt/`

### Step 2f: Video Transcription

- WhisperX on extracted audio from **YouTube livestream rips only** (not all IA videos — IA resource reels/AVAILs are B-roll without meaningful speech)
- Same WhisperX config as comm transcription (large-v3, CUDA)
- Output: `$DATA_DIR/{mission}/processed/transcripts/video/`

### Step 2f2: Comm-to-YouTube Sync (Artemis II only)

**Reference**: ISSiRT comm-to-YouTube matching logic

- Cross-reference comm transcripts (from Step 1b) with YouTube video transcripts
- Use transcript text overlap and timestamps to determine the time offset between comm audio and YouTube livestream video
- This enables syncing the comm audio playback with the YouTube video timeline in the frontend
- Output: sync mapping in `$DATA_DIR/artemis-ii/processed/comm_yt_sync.json`

### Step 2g: Web-ready JSON

- Merge YouTube + IA video metadata
- Enrich with IO timestamps where available
- **Only include flight-day videos** (filtered by IO metadata date range in Step 2c)
- Video fields: `{ id, title, source, startTime, duration, thumbnailUrl, sourceUrl }`
  - `sourceUrl` links back to original IA item or YouTube video for download
  - No `nasaId` in output (internal use only)
- Output: `$DATA_DIR/{mission}/web/videoYt.json`, `$DATA_DIR/{mission}/web/videoIA.json`

---

## Pipeline 3: Photos (IO + IA Stills + Flickr + images.nasa.gov)

### Source: Imagery Online (Primary)

IO contains the **complete catalog** of NASA flight imagery. Flight photo collections for Artemis II:

- **MISSION IMAGERY** — 21,659 photos (the primary flight photo collection)
- **Artemis-02 FCR** — 1,313 photos (Flight Control Room during mission)
- **Artemis-02 FCR Teams** — 13 photos
- **Artemis-02 Launch** — 88 photos
- **Artemis-02 Splashdown & Recovery** — 11 photos
- **Artemis-02 Events** — 875 photos

IO photos have NASA IDs that cross-reference with Flickr and images.nasa.gov.

### Source: IA Still Imagery

#### Artemis I

- **Collection**: `Artemis-I-Still-Imagery` (62 JPEGs, 30.7M)
- NASA IDs in filenames: `art001e000273.jpg` etc.
- Uploaded by john.l.stoll@nasa.gov, in `jsc-pao-video-collection`

#### Artemis II

- No still imagery collection identified yet — expected to appear in similar format

### Source: Flickr

- **Account**: `nasa2explore` (NASA JSC)
- Mission-specific album(s) for each Artemis mission (album IDs TBD — discover via API)
- **Requires AI classification**: Albums may contain mixed content (flight photos, preflight training, portraits, press events). Need to classify which photos are actual flight photos vs. other content.

### Source: images.nasa.gov

- **API**: `https://images-api.nasa.gov/search` (public, no auth)
- Search for Artemis I and Artemis II imagery
- Verify availability via HEAD request to `https://images-assets.nasa.gov/image/{nasa_id}/{nasa_id}~small.jpg`

### Source: Moon Photography

- No source identified yet — placeholder for future lunar surface imagery from Artemis II

### Step 3a: IA Still Imagery Download

- Fetch `_files.xml` for still imagery collections
- Filter for image files (JPEG, PNG)
- Download all images
- Extract NASA IDs from filenames (`art\d+e\d+` pattern for stills)
- Output: `$DATA_DIR/{mission}/raw/photos/ia_stills/`

### Step 3a2: IO Photo Collection Scrape

Scrape IO’s flight photo collections to build a complete catalog of mission imagery.

- Query IO API for each collection in `io_flight_collections`:
  ```
  https://io.jsc.nasa.gov/api/search/q=collections_string:"{collection}"&rpp=500?key={IO_KEY}&format=json
  ```
  (Or use `cols=` parameter if API supports it — test during implementation)
- Paginate through all results (`numfound` may be 21,000+)
- For each photo doc, extract: `nasa_id`, `vmd_start_gmt` or `md_creation_date`, `webpath`, `on_public_site`, `collections_string`
- Rate limit: 0.5s between page fetches
- Deduplicate by `nasa_id` across collections (a photo may appear in multiple)
- Note `on_public_site` flag — photos with this flag are likely also on images.nasa.gov
- Output: `$DATA_DIR/{mission}/processed/io_cache/io_photo_catalog.jsonl`

### Step 3b: Flickr Album Discovery

**Reference**: `ISSiRT/src/server-batch/4_photos/9e_get_jsc_flickr_albums.py`

- Flickr API: `flickr.people.findByUsername` → get `nasa2explore` NSID
- `flickr.photosets.getList` → scan all albums for Artemis-related titles
- Match by keywords: `"Artemis I"`, `"Artemis II"`, `"Artemis 1"`, `"Artemis 2"`
- `flickr.photosets.getPhotos` → get all photo IDs per matched album
- Auth: `FLICKR_API_KEY` env var
- Output: `$DATA_DIR/{mission}/raw/photos/flickr/album_metadata.json`

### Step 3c: Flickr Photo Details

**Reference**: `ISSiRT/src/server-batch/4_photos/9f_get_flickr_photos_metadata.py`

- For each photo: `flickr.photos.getInfo` → title, description, dates, tags
- `flickr.photos.getSizes` → available image URLs (all sizes)
- Rate limit between requests
- Output: `$DATA_DIR/{mission}/raw/photos/flickr/photos_metadata.json`

### Step 3d: Flickr Photo Classification (AI)

**Reference**: `ISSiRT/src/server-batch/4_photos/9h_make_flickr_flight_photos_list_using_ai.py`

Classify Flickr photos as **flight** vs. **other**. Simpler than ISSiRT's multi-expedition classification.

- Load photo metadata (title, description, tags, dates)
- Use Ollama (`qwen3:14b`, text-only) to classify each photo:
  - **Flight**: Taken during the mission (in-space, launch, landing, recovery, mission ops)
  - **Other**: Preflight, training, portraits, hardware, press events, graphics
- Prompt template includes mission dates (and crew names for Artemis II) for context
- For Artemis I (uncrewed), all vehicle imagery during the mission window counts as flight
- Note: we may end up using all photos for Artemis I since there are fewer flight images — classification still useful for sorting/prioritization
- Batch processing with rate limiting for Ollama
- Cache classification results to avoid re-processing
- Output: `$DATA_DIR/{mission}/raw/photos/flickr/photos_classified.json`
  - Format: `{ flickr_id, title, classification, is_flight_photo }`

### Step 3e: images.nasa.gov

**Reference**: `IO_nasatv/scripts/images_nasa_gov/`

- Query `https://images-api.nasa.gov/search?q=artemis+{I|II}&media_type=image`
- Filter: exclude preflight/portraits/press briefings based on keywords
- Verify image availability via HEAD request to `images-assets.nasa.gov`
- Cross-reference with IO API for NASA IDs where possible
- Output: `$DATA_DIR/{mission}/raw/photos/images_nasa_gov/images_metadata.json`

### Step 3f: Web-ready JSON

- Merge IO photo catalog + IA stills + classified Flickr flight photos + images.nasa.gov
- **Deduplicate by NASA ID** — the same photo often appears across IO, Flickr, and images.nasa.gov
- IO catalog is the authoritative source for NASA IDs and timestamps
- For public-facing URLs: prefer images.nasa.gov or Flickr URLs (IO is NASA-internal)
- Photos served from original source URLs (Flickr/NASA) — no local image downloads
- Include both flight-classified and other photos (tagged), since Artemis I has fewer flight images
- Output: `$DATA_DIR/{mission}/web/photos.json`

---

## Shared Utilities

### `shared/io_api.py` — Imagery Online Client

Ported from `IO_nasatv/scripts/IA_videos/3_search_io_api.py`:

```python
def search_io(keyword: str, api_key: str) -> dict:
    """Search IO API by NASA ID or keyword.

    URL format (unusual — params split across path and query string):
        https://io.jsc.nasa.gov/api/search/q={keyword}&rpp=500?key={api_key}&format=json

    Headers: {"Origin": "coda.fit.nasa.gov"}
    SSL: verify=False (self-signed cert)
    Response: data["results"]["response"]["docs"] (Solr wrapped in extra layer)
    Paginates automatically if numfound > 500.
    """

def search_io_collection(collection_name: str, api_key: str, media_type: str = None) -> list[dict]:
    """Search IO for all items in a named collection.
    Handles pagination for large collections (21,000+ items).
    Optional media_type filter: 'image' or 'video'.
    """

def extract_nasa_id(filename: str) -> str | None:
    """Extract NASA ID from filename. Handles multiple patterns:
    - art\d+m\d+   (Artemis video IDs)
    - art\d+e\d+   (Artemis still image IDs)
    - jsc\d+m\d+   (JSC PAO video IDs)
    """

def extract_downlink_id(filename: str) -> str | None:
    """Extract ART-DL-N_YYYY_DDD_HHMM pattern from filename."""
```

### `shared/ia_helpers.py` — Archive.org Helpers

Ported from `ISSiRT/src/server-batch/1_comm/1a_download_collection_IA_zips.py` and `IO_nasatv/scripts/IA_videos/1_scan_ia_metadata.py`:

```python
def search_ia(query: str) -> list[dict]:
    """IA Advanced Search API. Returns list of item identifiers + metadata."""

def fetch_collection_manifest(identifier: str) -> list[dict]:
    """Fetch and parse _files.xml for an IA item. Returns file entries."""

def download_file(url: str, dest: Path, skip_existing: bool = True):
    """Stream-download a file with progress bar."""

def normalize_date_filename(filename: str) -> str:
    """Pad single-digit dates in filenames."""

def filter_video_files(files: list[dict]) -> list[dict]:
    """Filter manifest for video files, preferring H.264 > MPEG4 > MPEG2."""

def filter_image_files(files: list[dict]) -> list[dict]:
    """Filter manifest for image files (JPEG, PNG)."""
```

### `shared/flickr_api.py` — Flickr Client

Ported from `ISSiRT/src/server-batch/4_photos/9e_*.py` through `9i_*.py`:

```python
def find_user_nsid(username: str, api_key: str) -> str:
    """Look up Flickr user NSID by username."""

def get_user_albums(nsid: str, api_key: str) -> list[dict]:
    """Get all public albums for a user."""

def get_album_photos(album_id: str, nsid: str, api_key: str) -> list[dict]:
    """Get all photos in a Flickr album with metadata."""

def get_photo_info(photo_id: str, api_key: str) -> dict:
    """Get detailed info for a single photo."""

def get_photo_sizes(photo_id: str, api_key: str) -> list[dict]:
    """Get available image URLs/sizes for a photo."""
```

### `shared/yt_helpers.py` — YouTube Wrappers

Ported from `ISSiRT/src/server-batch/5_video/3_web_yt_api_live_recordings_json.py`:

```python
def search_completed_broadcasts(channel_id: str, query: str, api_key: str) -> list[dict]:
    """Search for completed live broadcasts on a YouTube channel."""

def get_video_details(video_ids: list[str], api_key: str) -> list[dict]:
    """Batch-fetch video details (duration, liveStreamingDetails)."""

def download_video(video_id: str, output_dir: Path):
    """Download a YouTube video using yt-dlp."""
```

---

## Configuration

### Environment Variables (`.env`)

```env
# API Keys (copy from ISSiRT and IO_nasatv .env files)
YOUTUBE_API_KEY=
FLICKR_API_KEY=
IO_KEY=

# Paths
DATA_DIR=../ArtemisInRealTime_assets
YT_DOWNLOAD_DIR=D:/ArtemisInRealTime_yt_videos

# GPU/ML (for transcription)
WHISPER_MODEL_TYPE=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16

# AI Classification (for Flickr photo classification)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3:14b
```

### `config.py` — Per-Mission Constants

```python
from dataclasses import dataclass

@dataclass
class MissionConfig:
    name: str
    mission_start: str               # ISO date, UTC (e.g., "2022-11-16")
    mission_end: str                 # ISO date, UTC
    ia_subject_tag: str              # IA subject search
    ia_collections: list[str]        # Known IA collection identifiers
    ia_comm_collection: str | None   # Comm audio collection (None for uncrewed)
    ia_stills_collection: str | None # Still imagery collection
    io_parent_cid: str | None        # IO parent collection ID
    io_flight_collections: list[str] # IO collection names to scrape for flight imagery
    yt_search_terms: list[str]       # YouTube search keywords
    flickr_album_id: str | None       # Known Flickr album ID (None = discover via API)
    flickr_album_keywords: list[str] # Fallback keywords to match Flickr album titles
    nasa_id_patterns: list[str]      # Regex patterns for NASA IDs in filenames

MISSIONS = {
    "artemis-i": MissionConfig(
        name="Artemis I",
        mission_start="2022-11-16",
        mission_end="2022-12-11",
        ia_subject_tag="Artemis I Resource Reel",
        ia_collections=["Artemis-I-Still-Imagery"],
        ia_comm_collection=None,  # Uncrewed — no comm
        ia_stills_collection="Artemis-I-Still-Imagery",
        io_parent_cid=None,  # TBD — need to find Artemis I parent collection
        io_flight_collections=[],  # TBD
        yt_search_terms=["Artemis I", "Artemis 1"],
        flickr_album_id="72177720303788800",
        flickr_album_keywords=["Artemis I", "Artemis 1"],
        nasa_id_patterns=[r"art001[me]\d+", r"jsc2022m\d+"],
    ),
    "artemis-ii": MissionConfig(
        name="Artemis II",
        mission_start="2026-04-01",
        mission_end="2026-04-11",
        ia_subject_tag="Artemis II Resource Reel",
        ia_collections=["Artemis-II", "Artemis-II-ACR-Collection"],
        ia_comm_collection="Artemis-II-ACR-Collection",
        ia_stills_collection=None,  # TBD
        io_parent_cid="2380537",
        io_flight_collections=[
            "MISSION IMAGERY",
            "VIDEO",
            "Artemis-02 FCR",
            "Artemis-02 FCR Teams",
            "Artemis-02 Launch",
            "Artemis-02 Splashdown & Recovery",
            "Artemis-02 Events",
        ],
        yt_search_terms=["Artemis II", "Artemis 2"],
        flickr_album_id="72177720307234654",
        flickr_album_keywords=["Artemis II", "Artemis 2"],
        nasa_id_patterns=[r"art\d+m\d+", r"jsc202[56]m\d+"],
    ),
}

# Shared constants
IA_ROOT = "https://archive.org/download/"
YT_CHANNEL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"  # NASA
YT_DOWNLOAD_DIR = "D:/ArtemisInRealTime_yt_videos"  # YT backups on separate drive

IO_API_ENDPOINT = "https://io.jsc.nasa.gov/api/search"
IO_ORIGIN_HEADER = "coda.fit.nasa.gov"

IA_UPLOADERS = [
    "john.l.stoll@nasa.gov",
    "elizabeth.k.weissinger@nasa.gov",
    "dexter.herbert-1@nasa.gov",
    "edmond.a.toma@nasa.gov",
    "e.toma@nasa.gov",
]

FLICKR_USERNAME = "nasa2explore"

# Known standalone IA identifiers to always scan (supplement subject tag search)
IA_KNOWN_IDENTIFIERS = {
    "artemis-i": [
        "Artemis_I_Return_Powered_Flyby",
        "Artemis_I_Outbound_Powered_Flyby_Coverage",
        "Artemis-I-Still-Imagery",
        # Add more as discovered
    ],
    "artemis-ii": [
        "Artemis-II",
        "Artemis-II-ACR-Collection",
        # Add more as discovered
    ],
}
```

---

## Dependencies

Managed via `uv` with `pyproject.toml`:

```toml
[project]
requires-python = ">=3.11"
dependencies = [
    # Core
    "requests",
    "beautifulsoup4",
    "lxml",
    "python-dotenv",
    "tqdm",
    # YouTube
    "yt-dlp",
    "google-api-python-client",
    # Audio/Transcription
    "whisperx",
    "pydub",
    "webrtcvad",
    "torch",
    "torchaudio",
    # AI Classification
    "ollama",
    # Data
    "python-dateutil",
]

---

## Execution Order

Scripts accept `--mission artemis-i` or `--mission artemis-ii`. The `run_all.py` runner also supports `--step 2a` to run individual steps for development/debugging. Pipeline 1 is skipped for Artemis I (uncrewed).

### Artemis I

```

1. 2a_ia_video_discover.py # Discover 91 IA items via subject tag search
2. 2b_ia_video_download.py # Download MP4s (large — 116G+ for some items)
3. 2c_io_search.py # Search IO for nasa_ids & broadcast timestamps
4. 2c2_io_video_catalog.py # Scrape IO flight video collections
5. 2d_yt_metadata.py # Fetch YouTube Artemis I livestream metadata
6. 2e_yt_download.py # Download YouTube videos
7. 2f_transcribe.py # Transcribe YouTube video audio (GPU)
8. 2g_web_video.py # Generate video JSON

9. 3a_ia_stills_download.py # Download 62 JPEGs from Artemis-I-Still-Imagery
10. 3a2_io_photo_catalog.py # Scrape IO flight photo collections
11. 3b_flickr_albums.py # Discover Artemis I Flickr album(s)
12. 3c_flickr_photos.py # Fetch photo metadata
13. 3d_flickr_classify.py # AI classify flight vs preflight photos
14. 3e_images_nasa_gov.py # Fetch images.nasa.gov
15. 3f_web_photos.py # Generate photos.json

```

### Artemis II

```

1.  1a_download_ia_zips.py # Download comm audio from archive.org
2.  1b_transcribe.py # Transcribe comm audio (GPU)
3.  1c_web_comm.py # Generate comm.json

4.  2a_ia_video_discover.py # Discover IA items (Artemis-II collection + subject search)
5.  2b_ia_video_download.py # Download MP4s
6.  2c_io_search.py # Search IO for nasa_ids
7.  2c2_io_video_catalog.py # Scrape IO flight video collections (2,418 videos)
8.  2d_yt_metadata.py # Fetch YouTube metadata
9.  2e_yt_download.py # Download YouTube videos (to D: drive)
10. 2f_transcribe.py # Transcribe YouTube video audio (GPU)
11. 2f2_comm_yt_sync.py # Sync comm transcripts with YouTube timelines
12. 2g_web_video.py # Generate video JSON

13. 3a2_io_photo_catalog.py # Scrape IO flight photo collections (23,000+ photos)
14. 3b_flickr_albums.py # Discover Artemis II Flickr album(s)
15. 3c_flickr_photos.py # Fetch photo metadata
16. 3d_flickr_classify.py # AI classify flight vs other photos
17. 3e_images_nasa_gov.py # Fetch images.nasa.gov
18. 3f_web_photos.py # Generate photos.json

```

Pipelines 1–3 are independent and could be run in parallel at the pipeline level. Within each pipeline, steps are sequential.

---

## Key Differences from ISSiRT

| Aspect           | ISSiRT                                                     | ArtemisInRealTime                                             |
| ---------------- | ---------------------------------------------------------- | ------------------------------------------------------------- |
| Scope            | 25+ years of ISS ops                                       | 2 missions (uncrewed + crewed)                                |
| Incremental      | Yes — date watermarks, SQLite state DB, retry with backoff | Mostly run-once batch; comm pipeline re-runnable for new uploads |
| Orchestrator     | Full async orchestrator with GPU coordination, dashboard   | `run_all.py` with `--mission` and `--step` flags               |
| IA video         | Scanned by uploader email                                  | Discovered by subject tag + known collection identifiers      |
| Comm source      | 48+ IA collections, space-to-ground                        | 1 IA collection, Orion-to-Earth (Artemis II only)             |
| Comm naming      | `MM-DD-YY SG#.zip`                                         | `MM-DD-YY_Orion-to-Earth audio.zip`                           |
| Photo classify   | AI classifies by ISS expedition (multi-expedition albums)  | AI classifies flight vs. other (text-only, qwen3:14b)          |
| AI corpus        | Daily prompt-context generation via Ollama                 | Optional — small crew/terminology file sufficient             |
| IO integration   | Lookup only (timestamp by NASA ID)                         | Primary source — scrape full flight collections (photos+video) |
| Moon photos      | N/A                                                        | TBD — future source                                           |
| NASA ID patterns | `jsc\d+m\d+`, `iss\d+m\d+`                                 | `art\d+m\d+`, `art\d+e\d+`, `jsc\d+m\d+`                      |
| GPU coordination | ResourceManager (WhisperX ↔ Ollama switching)              | Sequential — run transcription scripts one at a time          |
| State tracking   | SQLite DB + text files + JSONL                             | Filesystem presence checks only                               |
| Pkg management   | pip + requirements.txt                                     | uv + pyproject.toml                                           |

---

## Open Questions

1. ~~**Flickr album IDs**~~ — **Resolved.** Artemis I: `72177720303788800`, Artemis II: `72177720307234654`
2. ~~**IA download size**~~ — **Resolved.** Download the smaller MP4 only. Use ISSiRT's format-selection logic.
3. ~~**YouTube search scope**~~ — **Resolved.** Strictly `"Artemis I"` / `"Artemis 1"` and `"Artemis II"` / `"Artemis 2"`. Livestreams only (`eventType=completed`). No `"Orion"` term.
4. ~~**Photo classification prompt**~~ — **Resolved.** Flight vs. other (binary). Text-only via qwen3:14b.
5. ~~**IO role**~~ — **Resolved.** IO is a primary data source. Scrape flight collections for all mission imagery + video.
6. **Artemis I IO collections** — Need to find the Artemis I parent collection ID on IO (similar to `cid=2380537` for A2) and identify which sub-collections contain flight imagery.
7. **IO API collection search syntax** — Need to test whether `cols=` parameter works in API or if we search by `collections_string` field in query. Will determine during implementation.
8. **Artemis II IA stills** — No still imagery collection on IA yet. IO's MISSION IMAGERY collection (21,659 photos) may be the primary source instead.
9. **Moon photography source** — No known API or data source yet for lunar surface imagery from Artemis II.
10. **Comm audio format** — Need to download a sample ZIP from the ACR collection and inspect contents to determine audio format (WAV/MP3/other).
11. **Remaining comm uploads** — NASA has uploaded days 1–5 so far (of 11 mission days). Pipeline must be re-runnable to catch new ZIPs as they appear.
12. **Comm-to-YouTube sync algorithm** — Need to study ISSiRT's matching logic and adapt for Artemis.
13. **ISSiRT MP4 size-selection logic** — Need to locate and port the specific function that picks the smaller MP4 when multiple formats exist.
```
