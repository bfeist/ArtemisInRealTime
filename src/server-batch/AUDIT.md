# Server-Batch Pipeline Audit

Full audit of the data ingestion pipeline as of April 2026.

See per-pipeline docs:

- [1_comm/README.md](1_comm/README.md) — Communications audio (Artemis II only)
- [2_video/README.md](2_video/README.md) — Video (IA + IO + YouTube)
- [3_photos/README.md](3_photos/README.md) — Photos (IO + IA + Flickr + images.nasa.gov)
- [shared/README.md](shared/README.md) — Shared API clients

## Full Execution Order

```
python run_all.py --mission artemis-ii
```

Runs these steps sequentially:

| Step | Script                     | Description                                | Dependencies              |
| ---- | -------------------------- | ------------------------------------------ | ------------------------- |
| 1a   | `1a_download_ia_zips.py`   | Download comm audio ZIPs                   | —                         |
| 1b   | `1b_transcribe.py`         | Transcribe comm audio (GPU)                | 1a                        |
| 1c   | `1c_web_comm.py`           | Produce `web/comm.json`                    | 1b                        |
| 2a   | `2a_ia_video_discover.py`  | Discover IA video items                    | —                         |
| 2b   | `2b_ia_video_download.py`  | Download IA video MP4s                     | 2a                        |
| 2c   | `2c_io_search.py`          | Per-item IO lookups for IA videos          | 2a                        |
| 2c2  | `2c2_io_video_catalog.py`  | Bulk scrape IO video collection            | —                         |
| 2d   | `2d_yt_metadata.py`        | Fetch YouTube metadata                     | —                         |
| 2e   | `2e_yt_download.py`        | Download YouTube videos                    | 2d                        |
| 2g   | `2g_web_video.py`          | Produce `web/videoIA.json`, `videoYt.json` | 2a, 2c, 2c2, 2d           |
| 3a   | `3a_ia_stills_download.py` | Download IA stills                         | —                         |
| 3a2  | `3a2_io_photo_catalog.py`  | Bulk scrape IO photo collection            | —                         |
| 3a3  | `3a3_io_exif_scrape.py`    | Scrape EXIF for timezone corrections       | —                         |
| 3b   | `3b_flickr_albums.py`      | Fetch Flickr album metadata                | —                         |
| 3e   | `3e_images_nasa_gov.py`    | Search images.nasa.gov                     | —                         |
| 3e2  | `3e2_io_nhq_lookup.py`     | Reverse-lookup NHQ photos in IO            | 3e                        |
| 3f   | `3f_web_photos.py`         | Produce `web/photos.json`                  | 3a, 3a2, 3a3, 3b, 3e, 3e2 |

## Cross-Pipeline Dependency Map

```
Pipeline 1 (Comm)      Pipeline 2 (Video)         Pipeline 3 (Photos)
─────────────────      ──────────────────         ───────────────────
1a → 1b → 1c          2a → 2b                    3a ──────────┐
                       2a → 2c ──┐                3a2 ─────────┤
                       2c2 ──────┤                3a3 ─────────┤
                       2d → 2e   ├─▶ 2g           3b ──────────┤
                       2d ───────┘                3e → 3e2 ────┤
                                                               └─▶ 3f
```

All three pipelines are independent of each other and could run in parallel.

## Changes Made During Audit

### Fixes Applied

1. **Registered 3a3 and 3e2 in `run_all.py`** — These steps existed but were missing from the pipeline runner. Without them, `web/photos.json` would have wrong timestamps for ground photographer photos and day-precision-only dates for NHQ photos.

2. **Fixed Flickr dates in `3f_web_photos.py`** — Flickr-only photo entries were getting `"date": ""` despite the API returning `datetaken`. Now uses `photo.get("datetaken", "")`.

3. **Made `2g_web_video.py` consume `io_video_catalog.jsonl`** — The IO video catalog from step 2c2 was being produced but never read. Now `build_io_lookup()` merges both `io_video_catalog.jsonl` (bulk catalog) and `io_found.jsonl` (per-item lookups) into a single lookup. This gives 2g access to all IO video metadata, not just items that matched IA identifiers.

4. **Fixed fragile IO matching in `2g_web_video.py`** — Replaced the O(n²) substring scan (`if nid in identifier.lower()`) with proper NASA ID extraction via regex patterns from the mission config. This eliminates false positives and is faster.

## Remaining Issues to Consider

### 2c may be redundant with 2c2

Step 2c does individual IO API lookups for each IA video item (~25 API calls). Step 2c2 does a single bulk scrape of the entire IO video collection (~5 paginated requests). After the fix above, 2g now reads both sources. In practice, 2c2's bulk data should contain everything 2c finds. You could skip 2c entirely if 2c2 covers all the NASA IDs you need. Consider testing by running only 2c2 and checking how many IA items get IO matches in 2g.

### 3a3 uses a hardcoded collection CID

`3a3_io_exif_scrape.py` hardcodes `ARTEMIS_MISSIONS_CID = "2346894"` for its IO date-range query, while the mission config has `io_parent_cid = "2380537"` for Artemis II. These are different collections. Consider updating 3a3 to use `mission.io_parent_cid` for consistency, or verify that `2346894` is intentionally a different (broader?) collection.

### Missing planned steps

| Planned                       | Status          | Notes                                   |
| ----------------------------- | --------------- | --------------------------------------- |
| 2f — Video transcription      | Not implemented | WhisperX on YouTube audio               |
| 2f2 — Comm-to-YouTube sync    | Not implemented | Cross-reference comm ↔ video timelines  |
| 3c — Flickr photo details     | Not needed      | 3b fetches all extras inline            |
| 3d — Flickr AI classification | Not implemented | All Flickr photos included unclassified |
