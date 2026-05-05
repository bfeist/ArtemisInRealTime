# ArtemisInRealTime — server-batch

Data ingestion pipeline for Artemis mission media. Manages comms audio, video,
and photo assets from Internet Archive, YouTube, Flickr, and NASA Image & Video
Library.

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
```

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

## Running an individual script directly

Each step module has a `main()` and accepts `--mission`:

```bash
uv run python -m 2_video.2a_ia_video_discover  --mission artemis-ii
uv run python -m 2_video.2b_ia_video_download  --mission artemis-ii
uv run python -m 2_video.2d_yt_metadata        --mission artemis-ii
uv run python -m 2_video.2e_yt_download        --mission artemis-ii
uv run python -m 2_video.2g_web_video          --mission artemis-ii

uv run python -m 1_comm.1a_download_ia_zips    --mission artemis-ii
uv run python -m 1_comm.1b_transcribe          --mission artemis-ii
uv run python -m 1_comm.1c_web_comm            --mission artemis-ii

uv run python -m 3_photos.3a_ia_stills_download  --mission artemis-ii
uv run python -m 3_photos.3a2_io_photo_catalog   --mission artemis-ii
uv run python -m 3_photos.3a3_io_exif_scrape     --mission artemis-ii
uv run python -m 3_photos.3b_flickr_albums       --mission artemis-ii
uv run python -m 3_photos.3e_images_nasa_gov     --mission artemis-ii
uv run python -m 3_photos.3e2_io_nhq_lookup      --mission artemis-ii
uv run python -m 3_photos.3g_eol_json            --mission artemis-ii
uv run python -m 3_photos.3h_download_eol_photos --mission artemis-ii
uv run python -m 3_photos.3k_web_photos          --mission artemis-ii
```

## Listing available steps

```bash
uv run run_all.py --mission artemis-ii --list
```

## Step reference

| Step   | Description                             | Missions   |
| ------ | --------------------------------------- | ---------- |
| 1a     | Download comm audio ZIPs                | artemis-ii |
| 1b     | Transcribe comm audio (WhisperX)        | artemis-ii |
| 1c     | Produce web-ready comm JSON             | artemis-ii |
| 2a     | Discover IA video items                 | all        |
| 2b     | Download IA video MP4s                  | all        |
| 2d     | Fetch YouTube metadata                  | all        |
| 2e     | Download YouTube videos                 | all        |
| 2g     | Produce web-ready video JSON            | all        |
| 3a     | Download IA stills                      | all        |
| 3a2    | Scrape IO photo collections             | all        |
| 3a3    | Scrape IO EXIF for timezone corrections | all        |
| 3b     | Fetch Flickr album metadata             | all        |
| 3e     | Search images.nasa.gov                  | all        |
| 3e2    | Reverse-lookup NHQ photos in IO         | all        |
| 3f     | Produce web-ready photos JSON           | all        |
| 3g_eol | Fetch EOL crew photo metadata           | artemis-ii |
| 3h     | Download EOL large images to disk       | artemis-ii |

## Dependency management

All dependencies live in `pyproject.toml`. To add a package:

```bash
uv add <package>
```

To upgrade all packages:

```bash
uv lock --upgrade && uv sync
```
