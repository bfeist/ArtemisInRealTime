# Pipeline 3: Photos (IO + IA Stills + Flickr + images.nasa.gov)

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FIVE INDEPENDENT DATA SOURCES                                                  │
│                                                                                 │
│  IO (Imagery Online)        IA (Archive.org)        images.nasa.gov             │
│      │                           │                       │                      │
│      ▼                           ▼                       ▼                      │
│  ┌──────────────┐         ┌──────────────┐        ┌──────────────┐              │
│  │ 3a2 — IO     │         │ 3a — IA      │        │ 3e — NASA    │              │
│  │ Photo Catalog│         │ Stills DL    │        │ Images API   │              │
│  │ Saves:       │         │ Saves:       │        │ Saves:       │              │
│  │  io_photo_   │         │  raw/photos/ │        │  images_nasa │              │
│  │  catalog     │         │  ia_stills/  │        │  _gov/       │              │
│  │  .jsonl      │         │  *.jpg       │        │  catalog.json│              │
│  └──────┬───────┘         └──────────────┘        └──────┬───────┘              │
│         │                                                │                      │
│         ▼                                                ▼                      │
│  ┌──────────────┐                                 ┌──────────────┐              │
│  │ 3a3 — IO     │                                 │ 3e2 — IO NHQ │              │
│  │ EXIF Scrape  │                                 │ Lookup       │              │
│  │ Reads: IO API│                                 │ Reads:       │              │
│  │ Saves:       │                                 │  catalog.json│              │
│  │  photo-exif  │                                 │ Saves:       │              │
│  │  -metadata   │                                 │  io_nhq_*    │              │
│  │  .json       │                                 │  .jsonl      │              │
│  │  photo-time  │                                 └──────────────┘              │
│  │  -overrides  │                                                               │
│  │  .json       │         Flickr API                                            │
│  └──────────────┘              │                                                │
│                                ▼                                                │
│                         ┌──────────────┐                                        │
│                         │ 3b — Flickr  │                                        │
│                         │ Albums       │                                        │
│                         │ Saves:       │                                        │
│                         │  album_      │                                        │
│                         │  metadata    │                                        │
│                         │  .json       │                                        │
│                         └──────────────┘                                        │
└─────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────┐
  │  ALL SOURCES                                             │
  │                                                          │
  │  3f — Download Photo Originals                          │
  │  Saves: PHOTO_ASSETS_DIR/{mission}/nasa_orig/*.jpg       │
  │         PHOTO_ASSETS_DIR/{mission}/flickr_orig/*.jpg     │
  └──────────────────────┬───────────────────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────────────────┐
  │  3f2 — Extract Photo EXIF Datetimes                     │
  │  Reads: nasa_orig/ + flickr_orig/ (DateTimeOriginal +   │
  │         OffsetTimeOriginal EXIF tags)                    │
  │  Saves: processed/io_cache/photo-exif-datetimes.json    │
  └──────────────────────┬───────────────────────────────────┘
                         │
                         ▼
               ┌──────────────────────┐
               │ 3g — Web Photos JSON │
               │ Reads: ALL of above  │
               │ Saves: web/photos.json│
               └──────────────────────┘
```

## Step Details

### 3a: IA Stills Download (`3a_ia_stills_download.py`)

|                |                                                             |
| -------------- | ----------------------------------------------------------- |
| **Source**     | Archive.org item (e.g. `Artemis-I-Still-Imagery`, 62 JPEGs) |
| **Output**     | `raw/photos/ia_stills/*.jpg`                                |
| **Idempotent** | Yes — skips existing files                                  |
| **Artemis I**  | 62 JPEGs from `Artemis-I-Still-Imagery`                     |
| **Artemis II** | No stills collection configured (skipped)                   |

Downloads JPEG originals from an IA item. Tries the item as a direct download first, falls back to treating it as a collection of items.

### 3a2: IO Photo Catalog (`3a2_io_photo_catalog.py`)

|                |                                                    |
| -------------- | -------------------------------------------------- |
| **Source**     | IO API — bulk collection scrape                    |
| **Output**     | `processed/io_cache/io_photo_catalog.jsonl`        |
| **Idempotent** | Overwrites output each run                         |
| **Artemis II** | ~23,959 photos across 7 flight collections         |
| **Artemis I**  | Requires `io_parent_cid` (configured as `2355140`) |

Fetches all photo docs from IO flight collections under the parent CID using `cols=` and `as=1` (photo asset type). This is the **authoritative catalog** — IO has every NASA flight photo with precise timestamps.

### 3a3: IO EXIF Scrape (`3a3_io_exif_scrape.py`)

|                          |                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| **Source**               | IO API (date-range query) + IO info pages (HTML scrape)                                       |
| **Input**                | IO API (independent — does its own date-range query, not io_photo_catalog)                    |
| **Output**               | `processed/io_cache/photo-exif-metadata.json`, `processed/io_cache/photo-time-overrides.json` |
| **Idempotent**           | Yes — resume support via existing metadata file                                               |
| **⚠️ Not in run_all.py** | Must be run manually                                                                          |

Solves the timezone problem: IO stores camera local time as UTC in `md_creation_date`. Ground photographer cameras (JSC, NHQ) are in CDT/EDT, so timestamps are wrong by 4–6 hours. This script:

1. Fetches mission-day ground photos from IO (its own date-range query, independent of 3a2)
2. Filters to `jsc*` and `nhq*` prefixed photos (onboard `art002e/a` cameras are already UTC)
3. Scrapes each photo's IO info page for EXIF metadata (DigitalCreationTime with timezone)
4. Builds a timezone correction map using three priority levels:
   - EXIF tz_offset from scraped page
   - Camera serial → known timezone mapping
   - Prefix default (jsc → CDT, nhq → EDT)

**This is slow** — scrapes individual HTML pages sequentially (with CONCURRENCY=10 batch size but no async HTTP).

### 3b: Flickr Albums (`3b_flickr_albums.py`)

|                      |                                                        |
| -------------------- | ------------------------------------------------------ |
| **Source**           | Flickr API                                             |
| **Output**           | `raw/photos/flickr/album_metadata.json`                |
| **Idempotent**       | Yes — skips if output file exists (delete to re-fetch) |
| **API key required** | `FLICKR_API_KEY`                                       |

Fetches all photos in a known Flickr album (album ID from config). Returns full photo metadata including URLs at all sizes, dates, tags, description, etc.

### 3e: images.nasa.gov (`3e_images_nasa_gov.py`)

|                |                                                    |
| -------------- | -------------------------------------------------- |
| **Source**     | NASA Image and Video Library API (public, no auth) |
| **Output**     | `raw/photos/images_nasa_gov/catalog.json`          |
| **Idempotent** | Overwrites output each run                         |

Searches `images-api.nasa.gov` for mission-related photos. Paginates through all results. Extracts NASA IDs, titles, dates, keywords, and thumbnail URLs.

### 3e2: IO NHQ Lookup (`3e2_io_nhq_lookup.py`)

|                          |                                                                                |
| ------------------------ | ------------------------------------------------------------------------------ |
| **Input**                | `raw/photos/images_nasa_gov/catalog.json` (from 3e)                            |
| **Output**               | `processed/io_cache/io_nhq_photos_found.jsonl`, `io_nhq_photos_notfound.jsonl` |
| **Idempotent**           | Yes — skips already-processed NASA IDs                                         |
| **⚠️ Not in run_all.py** | Must be run manually                                                           |

images.nasa.gov only stores day-precision dates for NHQ (NASA HQ) photos. This script looks up each NHQ photo in IO to get second-precision `md_creation_date`. Only processes photos with `NHQ` prefix.

### 3f: Download Photo Originals (`3f_download_photos.py`)

|                |                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------ |
| **Input**      | `raw/photos/flickr/album_metadata.json` (3b), `raw/photos/images_nasa_gov/catalog.json` (3e)     |
| **Output**     | `PHOTO_ASSETS_DIR/{mission}/flickr_orig/*.{ext}`, `PHOTO_ASSETS_DIR/{mission}/nasa_orig/*.{ext}` |
| **Idempotent** | Yes — skips files already on disk                                                                |

Downloads full-resolution originals from Flickr (`url_o`) and images.nasa.gov (`~orig` asset).

### 3f2: Extract Photo EXIF Datetimes (`3f2_extract_photo_exif.py`)

|                |                                                                   |
| -------------- | ----------------------------------------------------------------- |
| **Input**      | `PHOTO_ASSETS_DIR/{mission}/nasa_orig/`, `flickr_orig/` (from 3f) |
| **Validation** | `processed/io_cache/io_photo_catalog.jsonl` (from 3a2)            |
| **Output**     | `processed/io_cache/photo-exif-datetimes.json`                    |
| **Idempotent** | Overwrites output each run                                        |

Reads `DateTimeOriginal` and `OffsetTimeOriginal` EXIF tags from every downloaded photo, converts to true UTC, and writes the result as `{ nasa_id: "YYYY-MM-DDTHH:MM:SSZ" }`. Only photos that have an offset tag are written — photos without an offset fall back to the existing `photo-time-overrides.json` TZ-correction logic.

**Validates against IO catalog:** confirms that IO stores ground-photographer local time verbatim as UTC. For Artemis II, 796/835 overlapping photos (95%) show this discrepancy — e.g. a photo taken at `21:21:15 -05:00` local appears as `21:21:15Z` in IO instead of the correct `02:21:15Z`.

### 3g: Web Photos JSON (`3g_web_photos.py`)

|                |                            |
| -------------- | -------------------------- |
| **Input**      | ALL of the above outputs   |
| **Output**     | `web/photos.json`          |
| **Idempotent** | Overwrites output each run |

Merges all data sources with deduplication by nasa_id. Date priority (highest wins):

1. **IO photo catalog** (from 3a2) — authoritative base; `md_creation_date` is local time stored as UTC for ground photographers
2. **IA stills** (from 3a) — only adds photos not already in IO
3. **images.nasa.gov** (from 3e) — adds public URLs, creates entries for photos not in IO
4. **IO NHQ date enrichment** (from 3e2) — replaces day-precision dates with second-precision
5. **Flickr** (from 3b) — tries to match by NASA ID in title, otherwise adds as flickr-only entries
6. **Timezone corrections** (from 3a3) — applies tz offset to correct ground photographer timestamps from IO
7. **EXIF UTC datetimes** (from 3f2) — highest priority; true UTC from `DateTimeOriginal + OffsetTimeOriginal` in the actual downloaded file; overrides everything above

## Dependency Graph

```
3a  ──────────────────────────────────────────────────────▶ 3g
3a2 ──────────────────────────────────────────────────────▶ 3g
3a3 ──────────────────────────────────────────────────────▶ 3g
3b  ─────────────┬────────────────────────────────────────▶ 3g
                 └──▶ 3f ──▶ 3f2 ──────────────────────▶ 3g
3e  ─────────────┬────────────────────────────────────────▶ 3g
                 ├──▶ 3e2 ─────────────────────────────▶ 3g
                 └──▶ 3f ──▶ 3f2 ──────────────────────▶ 3g
```

**3a, 3a2, 3a3, 3b, 3e** can all run in parallel — they have no interdependencies.

**3e2 depends on 3e** (needs the images.nasa.gov catalog to know which NHQ IDs to look up).

**3f depends on 3b and 3e** (needs their catalogs to know what to download).

**3f2 depends on 3f** (reads the downloaded files).

**3g depends on all of them** — it's the final merge step.

### Minimum execution order:

```
Parallel: 3a, 3a2, 3a3, 3b, 3e
Then:     3e2 (after 3e), 3f (after 3b + 3e)
Then:     3f2 (after 3f)
Finally:  3g  (after everything)
```

## Assets Saved

| File                                               | Produced by | Consumed by        | Re-run cost                            |
| -------------------------------------------------- | ----------- | ------------------ | -------------------------------------- |
| `raw/photos/ia_stills/*.jpg`                       | 3a          | 3g                 | Medium (downloads)                     |
| `processed/io_cache/io_photo_catalog.jsonl`        | 3a2         | 3g, 3f2 (validate) | Medium (IO API, 24K+ docs)             |
| `processed/io_cache/io_photo_exif.jsonl`           | 3a3         | 3a3 (cache)        | **High** (scrapes 1000s of HTML pages) |
| `processed/io_cache/photo-time-overrides.json`     | 3a3         | 3g                 | Derived from above                     |
| `processed/io_cache/photo-datetime-overrides.json` | 3a3         | 3g                 | Onboard camera absolute datetimes      |
| `raw/photos/flickr/album_metadata.json`            | 3b          | 3f, 3g             | Low (single API call)                  |
| `raw/photos/images_nasa_gov/catalog.json`          | 3e          | 3e2, 3f, 3g        | Low (public API)                       |
| `processed/io_cache/io_nhq_photos_found.jsonl`     | 3e2         | 3g                 | Medium (per-item IO API)               |
| `processed/io_cache/io_nhq_photos_notfound.jsonl`  | 3e2         | (reference)        | Medium                                 |
| `PHOTO_ASSETS_DIR/{mission}/nasa_orig/`            | 3f          | 3f2                | High (large file downloads)            |
| `PHOTO_ASSETS_DIR/{mission}/flickr_orig/`          | 3f          | 3f2                | High (large file downloads)            |
| `processed/io_cache/photo-exif-datetimes.json`     | 3f2         | 3g                 | Fast (reads local files)               |
| `web/photos.json`                                  | 3g          | (frontend)         | Instant                                |

## Issues Found

### 1. ✅ Steps 3a3 and 3e2 are registered in `run_all.py`

Both scripts are included in the STEPS list in `run_all.py` and run as part of the normal pipeline. Note that 3a3 is slow (scrapes HTML pages) and 3e2 depends on 3e.

### 2. ⚠️ 3a3 does its own IO query independent of 3a2

Step 3a3 fetches mission-day photos from IO using a date-range query with `ARTEMIS_MISSIONS_CID = "2346894"` (hardcoded), while 3a2 uses the mission config's `io_parent_cid` (`2380537` for Artemis II). These are **different collection CIDs**, so they may return different photo sets. If 3a3 used the same catalog as 3a2, we could avoid a redundant IO API call and ensure consistency.

### 3. ⚠️ Flickr dates are discarded

The Flickr API returns `datetaken` and `dateupload` fields (they're included via `PHOTO_EXTRAS` in `flickr_api.py`), but `3f_web_photos.py` creates Flickr entries with `"date": ""`. This means Flickr-only photos (not matched to IO by NASA ID) have no timeline placement. Fix:

```python
photos[entry_id] = {
    ...
    "date": photo.get("datetaken", ""),  # Use Flickr's date
    ...
}
```

### 4. ⚠️ Flickr NASA ID extraction is limited

The regex in 3f only looks for `art\d+[me]\d+` and `jsc\d+[me]\d+` in Flickr photo titles. But many Flickr photos have NASA IDs in their description, tags, or in formats like `NHQ202604010001`. The NHQ prefix photos are never matched, so they end up as duplicate entries (`flickr_{id}` alongside `nhq...` from images.nasa.gov).

### 5. Minor: No `3c` (Flickr photo details) or `3d` (Flickr classification)

## How to Run

Run from `src/server-batch/`:

```bash
# Run all registered steps in order (works for both missions)
python run_all.py --mission artemis-i
python run_all.py --mission artemis-ii

# Run individual steps via run_all.py
python run_all.py --mission artemis-ii --step 3a
python run_all.py --mission artemis-ii --step 3a2
python run_all.py --mission artemis-ii --step 3a3   # slow: scrapes HTML pages
python run_all.py --mission artemis-ii --step 3b
python run_all.py --mission artemis-ii --step 3e
python run_all.py --mission artemis-ii --step 3e2   # run after 3e
python run_all.py --mission artemis-ii --step 3f    # download originals (after 3b + 3e)
python run_all.py --mission artemis-ii --step 3f2   # extract EXIF datetimes (after 3f)

# Then run 3g to merge everything
python run_all.py --mission artemis-ii --step 3g

# Same commands with artemis-i
python run_all.py --mission artemis-i --step 3a2
# ... etc
```

**Required env vars:** `FLICKR_API_KEY` (for step 3b), `IO_KEY` (for IO API steps).

The PLANNING doc describes these steps but they don't exist as scripts. Currently 3b fetches album metadata with all photo extras in a single call, which may be sufficient. The AI classification step (3d) is also absent — all Flickr photos are included unclassified.

## Missing Steps from Planning Doc

| Planned Step                  | Status                       | Impact                                 |
| ----------------------------- | ---------------------------- | -------------------------------------- |
| 3c — Flickr photo details     | Not implemented              | Not needed — 3b fetches extras inline  |
| 3d — Flickr AI classification | Not implemented              | All Flickr photos included unfiltered  |
| 3e2 — IO NHQ lookup           | Implemented, in `run_all.py` | Run after step 3e                      |
| 3a3 — IO EXIF scrape          | Implemented, in `run_all.py` | Slow — scrapes HTML pages              |
| 3f2 — EXIF datetime extract   | Implemented, in `run_all.py` | Run after step 3f (download originals) |
