# Pipeline 3: Photos

Refactored two-stage pipeline: **intake** scripts (`3*`) pull raw assets and
metadata from each source; **build** scripts (`4*`) merge them into a
canonical per-NASA-ID ledger and emit the published web tiers.

See [`docs/PHOTOS_EXPLAINED.md`](../docs/PHOTOS_EXPLAINED.md) for the
problem-domain background (sources, "exported" status, bracket sets, the
ledger schema, and the on-disk layout).

---

## Data flow

```
INTAKE (per-source — independent, parallelisable)
─────────────────────────────────────────────────
  3a    IA stills                  → raw/photos/ia_stills/*.jpg
  3a2   IO photo catalog           → processed/io_cache/io_photo_catalog.jsonl
  3a3   IO EXIF / TZ corrections   → processed/io_cache/photo-time-overrides.json
                                     processed/io_cache/photo-datetime-overrides.json
  3b    Flickr albums              → raw/photos/flickr/album_metadata.json
  3e    images.nasa.gov            → raw/photos/images_nasa_gov/catalog.json
  3e2   IO NHQ second-precision    → processed/io_cache/io_nhq_photos_found.jsonl
  3f    Download Flickr + NASA     → raw/photos/flickr/*, raw/photos/images_nasa_gov/*
  3g    EOL portal metadata        → processed/eol_photos.json
  3h    Download EOL JPEGs         → raw/photos/eol/jpeg_high/*.JPG
  3l    NEF ↔ EOL diff (diagnostic)→ stdout / optional .txt

  (manual)  Crew NEF + DNG drop    → raw/photos/5_Crew-Captured-Imagery/**

BUILD (depends on intake — sequential)
─────────────────────────────────────────────────
  4a    Per-copy EXIF              → processed/exif/{source}/{nasa_id}.json
                                     • raw_crew via ExifTool (NEF + DNG, full tags)
                                     • JPEG sources via PIL
  4b    Detect AEB bracket sets    → processed/io_cache/bracket_sets.jsonl
  4c    Build canonical ledger     → processed/photos_ledger.jsonl
  4d    Generate web tiers         → web/photos/{thumb,lowres,hires}/{nasa_id}.jpg
  4e    Emit web/photos.json       → web/photos.json
```

The **ledger** (`processed/photos_ledger.jsonl`) is the single source of
truth — one record per NASA ID with full per-copy EXIF, export status, the
chosen UTC timestamp + provenance, and bracket-set membership. See
[`shared/photos_ledger.py`](../shared/photos_ledger.py) for the dataclasses.

---

## Intake scripts

|       | Script                      | Pulls                        | Writes                                                |
| ----- | --------------------------- | ---------------------------- | ----------------------------------------------------- |
| `3a`  | `3a_ia_stills_download.py`  | Internet Archive item        | `raw/photos/ia_stills/*.jpg`                          |
| `3a2` | `3a2_io_photo_catalog.py`   | IO API (photo asset_type)    | `io_cache/io_photo_catalog.jsonl`                     |
| `3a3` | `3a3_io_exif_scrape.py`     | IO HTML (slow, per-page)     | `io_cache/photo-{time,datetime}-overrides.json`       |
| `3b`  | `3b_flickr_albums.py`       | Flickr API                   | `raw/photos/flickr/album_metadata.json`               |
| `3e`  | `3e_images_nasa_gov.py`     | images.nasa.gov API          | `raw/photos/images_nasa_gov/catalog.json`             |
| `3e2` | `3e2_io_nhq_lookup.py`      | IO API (per-NHQ)             | `io_cache/io_nhq_photos_{found,notfound}.jsonl`       |
| `3f`  | `3f_download_photos.py`     | Flickr `url_o`, NASA `~orig` | `raw/photos/flickr/*`, `raw/photos/images_nasa_gov/*` |
| `3g`  | `3g_eol_json.py`            | EOL Photos DB API            | `processed/eol_photos.json`                           |
| `3h`  | `3h_download_eol_photos.py` | EOL DatabaseImages           | `raw/photos/eol/jpeg_high/*.JPG`                      |
| `3l`  | `3l_flight_nef.py`          | (diagnostic — diffs disk)    | stdout report; optional `--output` text file          |

All intake scripts are **idempotent** — re-running picks up new assets
without redoing existing work.

## Build scripts

|      | Script                   | Inputs                              | Output                                          |
| ---- | ------------------------ | ----------------------------------- | ----------------------------------------------- |
| `4a` | `4a_extract_all_exif.py` | every local copy across all sources | `processed/exif/{source}/{nasa_id}.json`        |
| `4b` | `4b_detect_brackets.py`  | `processed/exif/**`                 | `io_cache/bracket_sets.jsonl`                   |
| `4c` | `4c_build_ledger.py`     | every intake output + 4a + 4b       | `processed/photos_ledger.jsonl`                 |
| `4d` | `4d_generate_tiers.py`   | the ledger + on-disk raws           | `web/photos/{thumb,lowres,hires}/{nasa_id}.jpg` |
| `4e` | `4e_web_photos_json.py`  | the ledger                          | `web/photos.json`                               |

`4a` and `4d` are CPU-bound — both use a `ThreadPoolExecutor` with a small
worker count (4 by default) so the NEF decode doesn't thrash the disk.

---

## How to run

From `src/server-batch/` with the project venv activated:

```bash
# Full pipeline for a mission
uv run run_all.py --mission artemis-ii

# Individual steps
uv run run_all.py --mission artemis-ii --step 3a 3a2 3b 3e
uv run run_all.py --mission artemis-ii --step 3g 3h        # EOL — Artemis II only
uv run run_all.py --mission artemis-ii --step 4a           # per-copy EXIF
uv run run_all.py --mission artemis-ii --step 4b           # bracket detection
uv run run_all.py --mission artemis-ii --step 4c           # build ledger
uv run run_all.py --mission artemis-ii --step 4d           # web tier JPEGs
uv run run_all.py --mission artemis-ii --step 4e           # web/photos.json
```

After the intake scripts run once, you can re-run just `4a 4b 4c 4d 4e` to
rebuild the ledger and republish — typically completes in seconds when
nothing has changed (each step skips up-to-date work).

### Required env vars

| Var                   | Used by    | Notes                                             |
| --------------------- | ---------- | ------------------------------------------------- |
| `FLICKR_API_KEY`      | `3b`       |                                                   |
| `IO_KEY`              | IO steps   | `3a2`, `3a3`, `3e2`                               |
| `NASA_EOL_API_KEY`    | `3g`       |                                                   |
| `CREW_RAW_SOURCE_DIR` | `3l`, `4a` | Fallback for crew raws while migrating to F: tree |

### Required tools

| Tool       | Used by | Notes                                                                                       |
| ---------- | ------- | ------------------------------------------------------------------------------------------- |
| `exiftool` | `4a`    | Reads NEF/DNG including Nikon makernote bracket tags. `winget install OliverBetz.ExifTool`. |
| `rawpy`    | `4d`    | libraw bindings — decode NEF + DNG. Installed via `uv sync`.                                |

---

## Bracket-set detection (4b)

The crew shot AEB sets (typically 3 frames per scene). Detection cascades
in priority order:

1. **`MakerNotes:ShootingMode` contains "Bracketing"** — strongest signal
   (Nikon D5/D6). Group consecutive frames on the same roll within 5 s
   where EV varies. Hero = the EV-zero frame.
2. **`BracketShotNumber` / `BracketShootCount`** — older Canon-style cameras
   tag every frame with "2 of 3"; walk forward from "1 of N".
3. **EV-only heuristic** — fallback for JPEG re-encodes that lost
   makernotes; require ≥3 consecutive frames on the same roll within 5 s
   with varying EV and stable focal length.

In the ledger every member of a set carries the same `bracket.set_id` (the
NASA ID of the metered/0-EV frame). The web JSON (`4e`) collapses each set
to one top-level entry — the hero — with `bracket.alternates` listing the
other members.

## Date-priority chain (4c)

Highest priority wins:

1. `exif_offset` — DateTimeOriginal + OffsetTimeOriginal from any local copy
2. `io_nhq` — second-precision date from `io_nhq_photos_found.jsonl`
3. `io_corrected` — IO `md_creation_date` + TZ override from `photo-time-overrides.json`
4. `io_onboard` — onboard-camera UTC (already correct in IO for `art\d+e/a` prefixes)
5. `flickr` — Flickr `datetaken` (TZ-corrected if we have the photographer's offset)
6. `nasa_images` — `date_taken` / `date_created` from images.nasa.gov
7. `eol` — `dateTaken` from EOL JSON

Both `utc` and `utc_source` are recorded on each ledger row for traceability.
The summary table at the end of `4c` shows the distribution.

---

## Diagnostic / one-off scripts

- **`3l_flight_nef.py`** — compares EOL exports against local crew raws.
  Prints what's exported but missing a raw, and what we have a raw for but
  isn't yet exported. Useful for tracking which crew raws are queued to
  light up on the next EOL release pass.

---

## See also

- [`docs/PHOTOS_EXPLAINED.md`](../docs/PHOTOS_EXPLAINED.md) — problem domain,
  source taxonomy, ledger schema, decisions
- [`docs/IO_DATA_EXPLAINED.md`](../docs/IO_DATA_EXPLAINED.md) — IO catalog
  details, collection hierarchy
- [`docs/PLANNING_DATA_INGESTION.md`](../docs/PLANNING_DATA_INGESTION.md) —
  whole-project ingestion plan (photos section is now superseded by this
  README + PHOTOS_EXPLAINED.md)
