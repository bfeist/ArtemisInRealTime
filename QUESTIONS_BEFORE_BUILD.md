# Remaining Questions Before Building

All original questions have been answered. These are the remaining unknowns that will be resolved during implementation:

## Resolved by Your Answers

- **API keys**: Reuse from ISSiRT and IO_nasatv `.env` files
- **Python env**: Own venv via `uv` + `pyproject.toml`
- **IA video format**: Download smaller MP4 only (ISSiRT logic)
- **Video storage**: Keep `$DATA_DIR/{mission}/raw/video/ia/`
- **IA format variants**: Pick smallest MP4 per video
- **YouTube scope**: Livestreams only (`eventType=completed`), drop "Orion" from A2 search terms
- **YouTube downloads**: Store on D: drive as backups
- **Comm mission dates**: April 1–11 UTC; check for new ZIPs beyond current 5
- **Comm incremental**: Pipeline must be re-runnable to pick up new NASA uploads
- **Flickr classification**: Flight vs. other (binary), text-only, qwen3:14b
- **Transcription scope**: YouTube rips only initially, not all IA videos
- **Comm-to-YT sync**: Needed for Artemis II (port ISSiRT logic)
- **GPU**: RTX 4090 24GB available
- **Transcription timing**: Part of the pipeline, not deferred
- **Web JSON**: Per-mission files, no nasaId in output, add sourceUrl
- **Photos**: Serve from Flickr/NASA URLs, deduplicate by NASA ID across sources
- **Video filtering**: Use IO metadata to include only flight-day content
- **Build order**: IA discovery → Flickr → YouTube → Comm → Transcription
- **run_all.py**: Support `--step` flag for individual steps
- **IA preflight content**: Filter out via IO metadata timestamps

## To Resolve During Implementation

1. **Comm audio format** — Need to download a sample ZIP from `Artemis-II-ACR-Collection` and inspect contents (WAV? MP3? other?)
2. **ISSiRT MP4 size-selection logic** — Need to locate the specific ISSiRT function that picks the smaller MP4 when multiple formats exist in an IA item, and port it
3. **Comm-to-YouTube sync algorithm** — Need to study ISSiRT's transcript matching logic and determine overlap threshold / alignment approach for Artemis
4. **Artemis II IA stills** — No still imagery collection exists yet; monitor for `Artemis-II-Still-Imagery` or similar
5. **Moon photography source** — No known data source for lunar surface imagery from Artemis II
6. **Remaining comm uploads** — Only days 1–5 of 11 uploaded so far; will discover the rest as NASA adds them
