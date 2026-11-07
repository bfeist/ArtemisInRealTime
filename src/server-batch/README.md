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

To upload prepared assets independently of application CI, run
`.venv/Scripts/python.exe publish_assets.py` (both missions), or
`refresh_assets.py --mission artemis-ii --stage publish` for one mission.
This directly runs the existing rsync workflow through WSL on Windows and
uses native rsync on Linux. `publish_assets.py --dry-run` is an optional
preview. See [asset publication](docs/asset-publication.md).

Each stage saves its log and input backups on D: under
`artemis-ii/processed/refresh/`. `--only JOB ...` retries selected jobs; each
stage has a lock preventing simultaneous runs. Metadata failures retain old
IO/NASA catalogs and do not prevent independent sources from being checked.
Builds stop on failed prerequisites. `run_all.py` and individual numbered
scripts remain available for diagnostics. With no `--step`, `run_all.py` runs
only `routine-build` (`1c`, `1i`), with no HGX, ASR, downloads or uploads.

For the preserved legacy baseline, pass `--dataset-manifest` to the build or
publication command. The current accepted import is
`F:/_repos/ArtemisInRealTime_assets/artemis-ii/processed/transcripts/datasets/legacy-20261107-v3/manifest.json`.
See the [implementation report](docs/comm-consolidation-implementation.md) for
the staged release, source differences, and measured PAO timing issues.

New Flickr/NASA/EOL originals go to D:. Processing reads originals on either
drive, keeping existing F: copies in place. Public compressed IA/PDS video
is prepared under F:'s `web/` with separate indexes. PDS audio and supplied
transcript originals stay in D:'s `raw/pds`; comm imports cache playback under
`processed/comm_audio`, with normalized transcripts under `processed/transcripts`.
Step `1c` prepares private `inputs/comm.csv`, `comm.json`, source metadata and
an audio manifest. It can upgrade cached PCD encodes, but does not publish,
replace or retire web audio. `1i` stages a new immutable release; activation is
separate. No retained release or shared video is deleted by this workflow.
Optional stage `2r` prepares compact YouTube PAO audio from existing video parts
and cached transcripts. Once prepared, `1i` reuses its exact utterance mappings
and includes the referenced audio in web releases without rerunning ASR or
assessments. See [PAO audio preparation](2_video/README.md#compact-youtube-pao-audio-2r).
The generic media build does not create `web/audio`,
`web/transcripts`, `audioPDS.json`, or `transcriptsPDS.json`. Same-drive IA MP4s
use hard links; cross-drive PDS copies are verified
against stored SHA-256 evidence. Unsupported codecs are reported for conversion.
IO-only records never qualify for publication by themselves. User `unneeded`
video decisions and replacement-coverage notes survive rediscovery.
Orion camera images use their camera/version identity as the ledger ID because
their numeric IDs can overlap existing IO/EOL records. Those aliases stay separate
for comparison before merging. Verified PDS browse PNG/JPEG
products fill gaps; raw scientific Bayer TIFFs are not used as display images.

The direct rsync publisher mirrors the current mission `web` trees, including
finalized releases and any local active pointer. Keep root photo/trajectory
assets required by other views there too. Release manifests describe selected
dependencies; rsync itself copies the web tree rather than enforcing those
allowlists. Keep `raw/` and `processed/`, including
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

## Model-free default build

```bash
uv run run_all.py --mission artemis-ii        # private inputs + staged release
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

# Classify, discover, verify, build and publish transcript history.
uv run run_all.py --mission artemis-ii --step comm-history --work-plan reviewed-plan.json --history-limit 10 --hide-reasoning
```

`--list` shows all available groups alongside individual steps.

Communications history runs **1d (classification) -> 1e (discovery) ->
1f (source verification) -> 1g (hierarchy) -> 1h (timeline) -> 1i (transcripts)**.
The `comm-history` group includes every stage and resumes validated work by
default. Classification uses HGX, `LLM-Alpha-512K` (GLM-5.3-Flash), low reasoning
and a constrained JSON schema. Event discovery and verification consume these
labels without routinely classifying the utterances again.

Create the impact plan from explicit current source rows before selecting model
work (`prepare_dataset_inputs.py` emits `current-source-rows.jsonl` containing
loader utterance records, not model output):

```powershell
uv run python -m 1_comm.assessment_reuse --dataset-manifest <dataset>/manifest.json --current-jsonl current-source-rows.jsonl --output reviewed-plan.json --max-requests 10 --stages 1d 1e 1f
```

Model stages require an explicit reviewed `--work-plan` tied to the current
semantic revision and a positive `--history-limit`. Direct stages use `--limit`.
The request cap applies **per model-stage invocation**, including repair/tool
rounds and retries within that run;
it is not a durable cumulative budget across restarts or stages. A multi-stage
runner can spend the cap separately in each selected stage. Repeating a
command authorizes another invocation. `--retry-unresolved` remains explicit.
`2j` fallback ASR requires explicit selection plus `--input-dir`; `2k` is a
legacy fallback operation, excluded from routine builds.

### Preserve and publish an imported baseline

Run these commands from `src/server-batch`, replacing the paths and IDs. Every
import/snapshot output must be new. Review their reports before publication.

```powershell
uv run python -m 1_comm.transcript_dataset --legacy-root <processed-transcripts> --web-root <mission-web> --output <new-freeze-receipt.json> --inventory --hashes
uv run python -m 1_comm.transcript_dataset --legacy-root <processed-transcripts> --web-root <mission-web> --output <new-dataset-dir>
uv run python -m 1_comm.prepare_dataset_inputs --mission artemis-ii --dataset-manifest <new-dataset-dir>/manifest.json --output <new-input-snapshot>
uv run python -m 1_comm.1i_web_transcripts --mission artemis-ii --dataset-manifest <new-dataset-dir>/manifest.json --release-id baseline-20261107
uv run python -c "from pathlib import Path; from shared.web_release import verify_release; print(verify_release(Path('<mission-web>/releases/baseline-20261107')))"
# Explicit first cutover: the baseline must preserve current root indexes.
uv run python -m 1_comm.1i_web_transcripts --mission artemis-ii --activate-release baseline-20261107 --legacy-baseline baseline-20261107
# Later activation, and rollback to a retained release:
uv run python -m 1_comm.1i_web_transcripts --mission artemis-ii --activate-release next-release
uv run python -m 1_comm.1i_web_transcripts --mission artemis-ii --activate-release baseline-20261107
```

Staging never changes `active-release.json`; activation verifies assets and commits
that pointer last under a lock. The client pins one release per page session;
reload after cutover. A missing pointer uses legacy root assets. Keep prior
release directories and every referenced shared public video. `--include-exports`
adds optional CSV downloads. `--sync-path` accepts private verified mappings with
audio evidence, projected against the measured local upload-part manifest.

Release JSON has deterministic `.json.gz` sidecars in the allowlist. The host
must negotiate `Accept-Encoding: gzip`, serve JSON with `Content-Encoding: gzip`
and `Vary: Accept-Encoding`, and retain uncompressed fallback. Sidecars alone do
not enable HTTP negotiation. Production host/CDN configuration and actual CORS,
Range/206, caching and compression checks remain required before remote cutover.
The publisher never uploads or configures a production host.

For already verified local PDS material, `--step comm-prepare` runs
`7f -> 1b3 -> 1b4 -> 1c`; `pds-integrate` includes the photo rebuild too.
Neither fetches the archive. `pds-plan` inventories/plans, while `pds-fetch` and
`pds-refresh` explicitly select acquisition. `refresh_assets.py --stage build`
also routes through both supplied-transcript importers before `1c` and stages
`1i`; pass `--dataset-manifest` to publish the preserved imported projection.

See the [communications pipeline](1_comm/README.md#transcript-classification-and-verified-history)
for stage responsibilities, manifests, semantic validation and fixture tests.

## Running an individual script directly

Each step module has a `main()` and accepts `--mission`:

```bash
uv run python -m 2_video.2a_ia_video_discover  --mission artemis-ii
uv run python -m 2_video.2b_ia_video_download  --mission artemis-ii
uv run python -m 2_video.2d_yt_metadata        --mission artemis-ii
uv run python -m 2_video.2e_yt_download        --mission artemis-ii
uv run python -m 2_video.2g_web_video          --mission artemis-ii

# Step 2h splits a downloaded MKV into 12-hour parts; step 2j transcribes them.
# Step 2k auto-detects the stream start UTC by matching speech against the internal comm.csv,
# legacy duplicate filtering writes private inputs/PAO.csv (explicit fallback only).
uv run python -m 2_video.2h_split_mkv               --mission artemis-ii
uv run python -m 2_video.2j_transcribe_yt           --mission artemis-ii \
    --input-dir "/path/to/yt_videos/artemis-ii"
uv run python -m 2_video.2k_filter_yt_transcript    --mission artemis-ii
# Override auto-detected stream start if needed:
# uv run python -m 2_video.2k_filter_yt_transcript  --mission artemis-ii \
#     --stream-start-utc "2026-03-30T12:00:00Z"

uv run python -m 1_comm.1a_download_ia_zips    --mission artemis-ii
uv run python -m 1_comm.1b_transcribe          --mission artemis-ii
uv run python -m 1_comm.1c_prepare_comm            --mission artemis-ii

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
| 1b3  | Import verified PDS PCD audio, supplied text and speakers | artemis-ii |
| 1b4  | Import corrected PDS voice-loop text and clocks | artemis-ii |
| 1c   | Prepare private COMM inputs and audio cache      | artemis-ii |
| 1d   | Classify transcripts with constrained HGX schema | artemis-ii |
| 1e   | Discover candidate events without changing labels | artemis-ii |
| 1f   | Verify candidate events against original sources | artemis-ii |
| 1g   | Build mission hierarchy from verified history | artemis-ii |
| 1h   | Publish compact mission timeline after 1g | artemis-ii |
| 1i   | Stage immutable comm/PAO release without model requests | artemis-ii |

### Video

| Step | Description                         | Missions   |
| ---- | ----------------------------------- | ---------- |
| 2a   | Discover IA video items             | all        |
| 2b   | Download IA video MP4s              | all        |
| 2d   | Fetch YouTube metadata              | all        |
| 2e   | Download YouTube videos             | all        |
| 2g   | Produce web-ready video JSON        | all        |
| 2h   | Split MKV into 8-hour MP4 parts by default | artemis-ii |
| 2j   | Transcribe YouTube parts (WhisperX) | artemis-ii |
| 2k   | Align to UTC + de-duplicate vs comm | artemis-ii |

`2j` and `2k` are explicit historical/fallback operations, not routine build
steps. The replacement evidence and policy commands run directly, with their
own required input/output paths; use `--help` on each script:

| Script | Purpose |
| --- | --- |
| `2_video/2m_pao_sync.py` | Candidate source coverage and verified sync projection |
| `2_video/2n_import_parts.py` | Import existing split/upload identities |
| `2_video/2o_check_split_packets.py` | Measure original-to-split packet offsets |
| `2_video/2p_pao_alignment.py` | Cached text comparison and bounded acoustic checks |
| `2_video/2q_apply_pao_policy.py` | Apply reviewed playback policy to a new dataset |

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
| routine-build  | 1c 1i | Prepare private inputs and stage publication, without models |
| comm-prepare   | 7f 1b3 1b4 1c | Import verified local PDS products into COMM inputs |
| comm-publish   | 1i | Stage an accepted dataset or complete current assessments |
| comm-history   | 1d 1e 1f 1g 1h 1i | Explicit bounded new research with a matching impact plan |

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
