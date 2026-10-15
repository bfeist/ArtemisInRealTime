# ArtemisInRealTime — server-batch

Data ingestion pipeline for Artemis mission media. Manages comms audio, video,
and photo assets from Internet Archive, YouTube, Flickr, and NASA Image & Video
Library.

## Routine updates and the readable status report

Use the staged runner for normal Artemis II upkeep. Source metadata,
downloads, web builds, and status are separate so checking for new releases
does not start an expensive transcription or multi-terabyte retrieval.

```powershell
.venv/Scripts/python.exe refresh_assets.py --mission artemis-ii --stage metadata
.venv/Scripts/python.exe refresh_assets.py --mission artemis-ii --stage photo-details
.venv/Scripts/python.exe refresh_assets.py --mission artemis-ii --stage photo-downloads
.venv/Scripts/python.exe refresh_assets.py --mission artemis-ii --stage build
.venv/Scripts/python.exe refresh_assets.py --mission artemis-ii --stage status
```

Open `F:/_repos/ArtemisInRealTime_assets/artemis-ii/processed/asset-status/index.html`.
It is a searchable local report generated from the existing ledger/manifests,
not another database. It distinguishes public-release evidence, originals held,
web files ready on F:, unneeded recordings, and pending PDS releases.
"Unconfirmed" means no public-release evidence was found, not proof of privacy.
"Ready on F" does not imply an upload to the remote production website.

Each stage saves its log and input backups on D: under
`artemis-ii/processed/refresh/`. `--only JOB ...` retries selected jobs; each
stage has a lock preventing simultaneous runs. Metadata failures retain old
IO/NASA catalogs and do not prevent independent sources from being checked.
Builds stop on failed prerequisites. `run_all.py` and individual numbered
scripts remain available for diagnostics; the legacy all-steps command also
includes costly download/transcription work and is not the routine refresh.

New Flickr/NASA/EOL originals go to D:. Processing reads originals on either
drive, keeping existing F: copies in place. Public compressed IA/PDS video,
PDS audio and supplied transcripts are prepared under F:'s `web/` with separate
indexes. Same-drive IA MP4s use hard links; cross-drive PDS copies are verified
against stored SHA-256 evidence. Unsupported codecs are reported for conversion.
IO-only records never qualify for publication by themselves. User `unneeded`
video decisions and replacement-coverage notes survive rediscovery.
Orion camera images use their camera/version identity as the ledger ID because
their numeric IDs can overlap existing IO/EOL records. Those aliases stay separate
for comparison before merging. Verified PDS browse PNG/JPEG
products fill gaps; raw scientific Bayer TIFFs are not used as display images.

Publish the mission's `web/` folder. Keep `raw/` and `processed/`, including
the internal IO catalog and this report, out of production hosting. PDS SPICE
release status is checked during discovery; supplied OEM trajectory inputs
remain a separate source while SPICE is delayed.

## Setup

Requires [uv](https://docs.astral.sh/uv/). From this directory:

```bash
uv sync
```

This installs all dependencies (including WhisperX / PyTorch CUDA wheels) into
a local `.venv`. Only needs to be re-run when `pyproject.toml` changes.

Copy `.env.example` (or create `.env` at the repo root) with your API keys:

```
YOUTUBE_API_KEY=...
FLICKR_API_KEY=...
IO_KEY=...
HF_TOKEN=...
DATA_DIR=/path/to/ArtemisInRealTime_assets   # optional override
BULK_ASSETS_DIR=D:/ArtemisInRealTime_assets  # bulk originals; mission slug appended
```

## Storage layout

Every storage root starts with the mission slug. `DATA_DIR` keeps the existing
processing caches and web output on F:, `BULK_ASSETS_DIR` keeps bulk originals on
D:, and `YT_VIDEO_DIR` keeps YouTube downloads on D:. Nothing needs to move from
D: to F:. PDS defaults to `BULK_ASSETS_DIR`; `PDS_ASSETS_DIR` can override it.

```text
D:/ArtemisInRealTime_assets/
  artemis-ii/
    raw/
      photos/
        5_Crew-Captured-Imagery/     # FD_01/... crew NEF/DNG originals
        5_Crew-Captured-Imagery.zip
        nef_all/                    # retain the separate raw holding
        eol/jpeg_high/
        flickr/
        images_nasa_gov/
      video/io/
      audio/vehicle/1_PCD-Audio-Files/
      science/ArtemisII_Science_Data_Tracking.xlsx
      pds/                          # upstream bundle layout retained intact
    processed/pds/                  # inventory DB, manifests, logs, reports
    staging/pds/                    # resumable partials
F:/_repos/ArtemisInRealTime_assets/
  artemis-i/{raw,processed,web}/
  artemis-ii/{raw,processed,web}/
  _shared/kernels/
D:/ArtemisInRealTime_yt_videos/
  {mission}/
```

IO video downloads use `mission.videos_io_dir`; crew processing checks both
`mission.photos_raw_crew` on F: and `mission.photos_bulk_crew` on D:. PDS reuse
scans only the selected mission across its configured roots, preventing other
missions' holdings from becoming candidates. Photo copies already present on
both drives are retained.

For older holdings, stop ingestion and run from this directory:

```powershell
.venv/Scripts/python.exe migrate_data_layout.py           # preview
.venv/Scripts/python.exe migrate_data_layout.py --apply   # same-drive renames
.venv/Scripts/python.exe -m unittest test_data_layout 7_pds.test_pds -v
```

The migration verifies file identity, size and modification time; repairs
absolute paths in the PDS database and current processing metadata; and saves
a receipt plus metadata/database backups in
`artemis-ii/processed/storage-migrations/`. Cached hashes, ZIP member names and
download evidence are retained. Existing destinations cause a preflight failure
before any move. Rerunning after migration repairs paths without moving media
again. Historical reports/checkpoints and original PDS labels remain unchanged.
The removed `VIDEO_ASSETS_DIR`, `PHOTO_ASSETS_DIR`, and `CREW_RAW_SOURCE_DIR`
settings are replaced by the mission properties under `BULK_ASSETS_DIR` and
`DATA_DIR`.

## Running the full pipeline

```bash
uv run run_all.py --mission artemis-ii        # all steps
uv run run_all.py --mission artemis-i         # different mission
```

## Running a specific step or steps

```bash
uv run run_all.py --mission artemis-ii --step 2a
uv run run_all.py --mission artemis-ii --step 2a 2b 2d
```

## Incremental updates (named step groups)

Named groups expand into the right sequence of steps automatically.

```bash
# Pick up new photos from Flickr, images.nasa.gov, and EOL; rebuild web output.
# Safe to re-run at any time — all download steps skip already-present files.
uv run run_all.py --mission artemis-ii --step photos-refresh

# Re-scrape the IO photo catalog and EXIF timezone overrides, then rebuild the
# ledger and web output.  Run when IO adds new records or corrects timestamps.
uv run run_all.py --mission artemis-ii --step io-refresh
```

`--list` shows all available groups alongside individual steps.

## Running an individual script directly

Each step module has a `main()` and accepts `--mission`:

```bash
uv run python -m 2_video.2a_ia_video_discover  --mission artemis-ii
uv run python -m 2_video.2b_ia_video_download  --mission artemis-ii
uv run python -m 2_video.2d_yt_metadata        --mission artemis-ii
uv run python -m 2_video.2e_yt_download        --mission artemis-ii
uv run python -m 2_video.2g_web_video          --mission artemis-ii

# Step 2h splits a downloaded MKV into 12-hour parts; step 2j transcribes them.
# Step 2k auto-detects the stream start UTC by matching speech against comm.json,
# removes comm duplicates, and writes web/combined_transcript.json.
uv run python -m 2_video.2h_split_mkv               --mission artemis-ii
uv run python -m 2_video.2j_transcribe_yt           --mission artemis-ii \
    --input-dir "/path/to/yt_videos/artemis-ii"
uv run python -m 2_video.2k_filter_yt_transcript    --mission artemis-ii
# Override auto-detected stream start if needed:
# uv run python -m 2_video.2k_filter_yt_transcript  --mission artemis-ii \
#     --stream-start-utc "2026-03-30T12:00:00Z"

uv run python -m 1_comm.1a_download_ia_zips    --mission artemis-ii
uv run python -m 1_comm.1b_transcribe          --mission artemis-ii
uv run python -m 1_comm.1c_web_comm            --mission artemis-ii

uv run python -m 3_photos.3a_ia_stills_download  --mission artemis-ii
uv run python -m 3_photos.3a2_io_photo_catalog   --mission artemis-ii
uv run python -m 3_photos.3a3_io_exif_scrape     --mission artemis-ii
uv run python -m 3_photos.3b_flickr_albums       --mission artemis-ii
uv run python -m 3_photos.3e_images_nasa_gov     --mission artemis-ii
uv run python -m 3_photos.3e2_io_nhq_lookup      --mission artemis-ii
uv run python -m 3_photos.3f_download_photos     --mission artemis-ii
uv run python -m 3_photos.3g_eol_json            --mission artemis-ii
uv run python -m 3_photos.3h_download_eol_photos --mission artemis-ii
uv run python -m 3_photos.4a_extract_all_exif    --mission artemis-ii
uv run python -m 3_photos.4b_detect_brackets     --mission artemis-ii
uv run python -m 3_photos.4c_build_ledger        --mission artemis-ii
uv run python -m 3_photos.4d_generate_tiers      --mission artemis-ii
uv run python -m 3_photos.4e_web_photos_json     --mission artemis-ii
uv run python -m 3_photos.4f_cleanup_web         --mission artemis-ii
```

## Listing available steps

```bash
uv run run_all.py --mission artemis-ii --list
```

## Step reference

### Comms

| Step | Description                      | Missions   |
| ---- | -------------------------------- | ---------- |
| 1a   | Download comm audio ZIPs         | artemis-ii |
| 1b   | Transcribe comm audio (WhisperX) | artemis-ii |
| 1c   | Produce web-ready comm JSON      | artemis-ii |

### Video

| Step | Description                         | Missions   |
| ---- | ----------------------------------- | ---------- |
| 2a   | Discover IA video items             | all        |
| 2b   | Download IA video MP4s              | all        |
| 2d   | Fetch YouTube metadata              | all        |
| 2e   | Download YouTube videos             | all        |
| 2g   | Produce web-ready video JSON        | all        |
| 2h   | Split MKV into 12-hour MP4 parts    | artemis-ii |
| 2j   | Transcribe YouTube parts (WhisperX) | artemis-ii |
| 2k   | Align to UTC + de-duplicate vs comm | artemis-ii |

### Photos — intake (per source, independent)

| Step   | Description                                 | Missions   |
| ------ | ------------------------------------------- | ---------- |
| 3a     | Download IA stills                          | all        |
| 3a2    | Scrape IO photo collections                 | all        |
| 3a3    | Scrape IO EXIF for timezone corrections     | all        |
| 3b     | Fetch Flickr album metadata (incremental)   | all        |
| 3e     | Search images.nasa.gov                      | all        |
| 3e2    | Reverse-lookup NHQ photos in IO             | all        |
| 3f     | Download full-res photo originals           | all        |
| 3g_eol | Fetch EOL crew photo metadata (incremental) | artemis-ii |
| 3h     | Download EOL large images to disk           | artemis-ii |

### Photos — build (sequential, depend on intake)

| Step | Description                                          | Missions |
| ---- | ---------------------------------------------------- | -------- |
| 4a   | Per-copy EXIF (raw_crew via ExifTool, JPEGs via PIL) | all      |
| 4b   | Detect AEB bracket sets from EXIF                    | all      |
| 4c   | Build canonical per-NASA-ID photo ledger             | all      |
| 4d   | Generate web tier JPEGs (thumb/lowres/hires)         | all      |
| 4e   | Emit web/photos.json from the ledger                 | all      |
| 4f   | Delete stale web/ files (run after 4e)               | all      |

### Trajectory

| Step | Description                                   | Missions |
| ---- | --------------------------------------------- | -------- |
| 5a   | Build canonical Orion track from OEM/Horizons | all      |
| 5b   | Compute geocentric Moon at every Orion sample | all      |
| 5c   | Emit web/ephemeris/trajectory.json            | all      |

## Itinerary

| Step | Description             | Missions |
| ---- | ----------------------- | -------- |
| 6a   | Emit web/itinerary.json | all      |

### Step groups

| Group          | Expands to                        | Use when                               |
| -------------- | --------------------------------- | -------------------------------------- |
| photos-refresh | 3b 3e 3g_eol 3f 3h 4a 4b 4c 4d 4e | New photos on Flickr, NASA, or EOL     |
| io-refresh     | 3a2 3a3 4c 4d 4e                  | IO adds records or corrects timestamps |

## Dependency management

All dependencies live in `pyproject.toml`. To add a package:

```bash
uv add <package>
```

To upgrade all packages:

```bash
uv lock --upgrade && uv sync
```

## PDS science release

See [7_pds/README.md](7_pds/README.md) for complete, resumable Artemis II PDS
retrieval. `PDS_ASSETS_DIR` defaults to `BULK_ASSETS_DIR` independently of the
existing pipeline/web assets in `DATA_DIR`. Use the explicit `pds-plan`, `pds-fetch`,
`pds-integrate`, or `pds-refresh` runner groups. The long-running retrieval entry
point is `python -u -m 7_pds.retrieve --mission artemis-ii`.
