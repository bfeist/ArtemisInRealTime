# Photos — How They Work and Where We're Going

## Context

Artemis 2 flew April 1–10, 2026 — a crewed lunar flyby. The Artemis In Real Time
project places every artifact (audio, video, photo) on a single UTC timeline.
For photos, that means: **for every photo we surface on the site, we need to
know exactly when in UTC it was taken, what NASA ID identifies it, what
resolutions we have on disk, and that it has been cleared for public release.**

This document explains the photo problem domain — the sources, the cameras, the
concept of "export," the role of NASA IDs as the unifying key, and the quality
tiers we need to publish. It also describes the target shape we want to refactor
toward: a clean **raw → processed → web** flow keyed by NASA ID, instead of the
current pile of narrow per-source scripts.

---

## Key Concepts

### NASA ID — the unifying key

Every NASA flight photo has a `nasa_id` like `art002e168048` or `jsc2026e012345`
or `nhq202604010001`. The same photo will appear in multiple sources under the
same NASA ID (sometimes with different casing or punctuation — e.g. EOL writes
`ART002-E-168` for `art002e000168`). **Across all sources, NASA ID is the join
key.** The whole pipeline boils down to: collect everything, normalize the IDs,
and merge.

#### NASA ID prefixes seen in Artemis 2

| Prefix     | Source camera                 | Notes                                                          |
| ---------- | ----------------------------- | -------------------------------------------------------------- |
| `art002e…` | Crew onboard Orion            | EOL / crew NEF originals — true UTC in EXIF                    |
| `art002a…` | Onboard automated/aux cameras | UTC in IO `md_creation_date`                                   |
| `jsc…`     | JSC ground photographers      | Camera local time (CDT/CST) stored as UTC in IO — needs fixing |
| `nhq…`     | NASA HQ ground photographers  | Camera local time (EDT/EST) stored as UTC in IO — needs fixing |

### Export control (a.k.a. "exported" / "public")

When this codebase says a photo is **exported**, it means **NASA has released
it publicly**. Internal cataloging (IO) does not reliably mark this — IO has
both pre-release and released material side-by-side, and the `on_public_site`
flag is unreliable. **The only trustworthy signal that a photo has been
exported is its presence in a public archive.** Public archives we observe:

- Internet Archive (IA) — bulk uploads from a NASA PAO account
- EOL / Portal for Astronaut Photography — ~12,000 Artemis II photos published
- Flickr (`nasa2explore` and `NASA HQ PHOTO` accounts)
- images.nasa.gov

A photo must appear in **at least one** of those to be considered safe to
publish on the site.

### Timeline placement (UTC, second precision)

Every published photo needs an accurate UTC timestamp. Sources vary widely:

| Source                | Precision                                                                | Timezone trustworthiness                          |
| --------------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| Crew NEF/DNG (EXIF)   | second                                                                   | True UTC if `OffsetTimeOriginal` is set           |
| EOL photo metadata    | depends on table — `images` only has limited fields for ART002 right now |
| IO `md_creation_date` | second                                                                   | **Wrong for ground photographers** — local-as-UTC |
| Flickr `datetaken`    | second                                                                   | Camera local — needs offset                       |
| images.nasa.gov NHQ   | day only                                                                 | Useless for timeline placement                    |

The known timezone trap: **IO stores camera local time verbatim into a UTC
field for ground photographers.** A `21:21:15` photo taken in Houston at CDT
appears in IO as `21:21:15Z` instead of `02:21:15Z` the next day. Onboard
cameras (art002e/a) are already UTC and don't need correction.

Date priority that we ultimately want in the merged record (highest wins):

1. **`exif_offset`** — EXIF `DateTimeOriginal` + an offset tag (any of
   `OffsetTimeOriginal`, `OffsetTime`, `OffsetTimeDigitized`) from the actual
   file on disk (NEF or downloaded JPEG). Sub-second when `SubSecTimeOriginal`
   is present.
2. **`io_nhq`** — second-precision date from `io_nhq_photos_found.jsonl`.
3. **`io_exif`** — IO-scraped per-photo EXIF (`io_photo_exif.jsonl`):
   `DateCreated` (sub-second), `DigitalCreationDate`+`DigitalCreationTime`,
   or `DateTimeOriginal` paired with a known offset. **Deliberately ignores
   IO's `GMT` field** — IO routinely populates that column with the camera's
   local-clock time mislabelled as GMT (the whole reason
   `photo-time-overrides.json` exists), so without an explicit offset on the
   timestamp itself we can't tell true UTC apart from mislabelled local.
   *This caveat is IO-specific — a `GMT` tag inside a real on-disk EXIF
   payload (e.g. a GoPro makernote) is unrelated.*
4. **`io_corrected`** — IO `md_creation_date` with TZ correction applied.
5. **`io_onboard`** — IO `md_creation_date` for `art002e/a` (already UTC) or
   from `photo-datetime-overrides.json`.
6. **`flickr`** — `datetaken` with offset applied.
7. **`nasa_images`** — `date_created` / `date_taken` (day-precision fallback).
8. **`eol`** — `dateTaken` from EOL JSON (last resort).

### Quality tiers we publish

For each NASA ID we want, on the website:

- **thumbnail** — gallery / scrubber preview (~200–400 px)
- **low-res** — inline timeline view (~1024 px)
- **high-res** — lightbox / detail view (~2048–4096 px)

We do **not** publish the original NEF/DNG — too large and not browser-viewable.
The high-res JPEG is the deepest tier the public site exposes.

### Bracket sets (AEB — auto exposure bracketing)

Many crew SLR shots were taken as **bracketed sets**, typically three frames
per scene (under-exposed / metered / over-exposed). On the timeline they should
appear as a single moment — not three near-duplicate thumbnails — and the
brightest/darkest frames should be discoverable as alternates from the metered
"hero" shot. We may also merge the three into an HDR composite for the
published tiers (see *Web-tier generation* below).

**How to identify a bracket set**, in priority order:

1. **Nikon makernote tags** (NEF only — ExifTool reads these from the
   MakerNotes block):
   - `BracketShotNumber` (e.g. `1 of 3`, `2 of 3`, `3 of 3`)
   - `BracketShootCount` / `AEBracketCompensationApplied`
   - When present, this is the authoritative grouping signal.
2. **Standard EXIF `ExposureBiasValue` / `ExposureCompensation`** — present
   on every NEF and every embedded/exported JPEG. Inside a bracket set the
   three frames will have distinct biases (e.g. `-1 EV`, `0 EV`, `+1 EV`).
   This tells us *which* frame is lighter / darker, even when the makernote
   tags are missing.
3. **Heuristic fallback** when the above don't disambiguate: consecutive
   frame numbers on the same roll, taken within a few seconds, same focal
   length and focus, varying `ExposureBiasValue`. This catches manually
   bracketed shots where the camera didn't tag them as AEB.

In the ledger, every bracket frame is its own NASA ID record. Each record
carries a `bracket_set` cluster pointer and its position within the set, so a
gallery view can collapse the three into one card and a detail view can offer
"see all three exposures."

---

## Sources, in detail

### 1. Crew-captured raws — NEF + DNG (`raw_crew`)

The crew/imagery team handed over the original digital negatives. The
collection is mixed — Nikon SLR NEFs (`art002e023048.NEF`) plus DNGs from
iPhones and other cameras the crew used. Currently on a D: drive at
`D:/ArtemisInRealTime_assets/5_Crew-Captured-Imagery/`, moving to
`F:\_repos\ArtemisInRealTime_assets\artemis-ii\raw\photos\raw_crew\` once
disk space is freed. They are the **highest-fidelity source for onboard
photos** and contain accurate EXIF UTC times.

- Not all of these have been exported. Many have (≈12,000 are in EOL), but
  some are still embargoed.
- These are **input**, not output. We never publish the raw — we use it to
  generate quality tiers when we have export clearance.

### 2. EOL / Portal for Astronaut Photography (`eol.jsc.nasa.gov`)

Public NASA portal for crew photography. The EOL JSON API (step 3g) lists the
~12,000 ART002 photos that have been released. EOL also serves large JPEGs
under `https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/ART002/ART002-E-168.JPG`
which we can pull down with step 3h.

- **Authoritative export-status signal for crew photos.** If a photo is in
  EOL's listing, it has been cleared.
- EOL's image is a downsampled JPEG, not a NEF. If we have the NEF, we should
  prefer to render tiers from the NEF rather than re-encode EOL's JPEG.

### 3. Imagery Online (`io.jsc.nasa.gov`) — internal NASA catalog

Comprehensive metadata catalog. For Artemis II, IO has ~43,000 photos across
flight collections (`MISSION IMAGERY`, `Artemis-02 FCR`, `Launch`, etc.). See
[IO_DATA_EXPLAINED.md](IO_DATA_EXPLAINED.md) for the full IO schema.

- **Best metadata source** — second-precision timestamps, titles,
  descriptions, collection hierarchy, NASA IDs.
- **Export status is not reliable** — `on_public_site` is set inconsistently.
  Don't trust it; use cross-reference with public archives instead.
- **Timezone trap** for ground photographers (jsc/nhq prefixes) — see above.

### 4. Internet Archive — `Artemis-II` and `Artemis-I-Still-Imagery`

NASA PAO (john.l.stoll@nasa.gov) uploads bulk JPEG sets to IA. For Artemis I,
this gave us 62 still images. For Artemis II, an analogous still imagery item
hasn't appeared yet. Photos here are public by definition.

### 5. Flickr — `nasa2explore` and `NASA HQ PHOTO`

Public-relations photo distribution. Two albums for Artemis II are in the
config. Flickr serves multiple sizes (thumb/medium/large/original) we can use
directly. Photos here are public by definition.

### 6. images.nasa.gov

NASA's public image search. Often duplicates Flickr but sometimes has unique
items, especially NHQ press shots. Returns multiple sizes (~thumb / ~small /
~medium / ~large). Photos here are public by definition.

### 7. Manual additions (`manual`)

Photos obtained outside the automated pipeline — press handouts, social media
grabs, personal event photos, etc. Drop JPEG/PNG/TIFF files named by NASA ID
into `raw/photos/manual/`. File presence implies exported (the user asserts
clearance by placing the file). EXIF is extracted via PIL; the date-priority
chain derives UTC from EXIF if an offset tag is present.

---

## Where We Are Today (the incremental mess)

The `3_photos/` folder accumulated organically as each source was added. The
scripts mostly work but step on each other and don't share a clear pipeline
shape:

| Script                      | What it does                                                         |
| --------------------------- | -------------------------------------------------------------------- |
| `3a_ia_stills_download.py`  | Pull JPEGs from an IA still-imagery item                             |
| `3a2_io_photo_catalog.py`   | Bulk scrape IO photo metadata for the mission                        |
| `3a3_io_exif_scrape.py`     | Slow per-photo IO HTML scrape to fix the ground-photographer TZ bug  |
| `3b_flickr_albums.py`       | Pull Flickr album metadata                                           |
| `3e_images_nasa_gov.py`     | Search and catalog images.nasa.gov                                   |
| `3e2_io_nhq_lookup.py`      | Reverse-lookup NHQ photos in IO for second-precision dates           |
| `3f_download_photos.py`     | Download originals from Flickr + images.nasa.gov                     |
| `3f2_extract_photo_exif.py` | Read EXIF from those downloaded originals to get true UTC            |
| `3g_eol_json.py`            | Pull EOL listing for ART002 (~12,000 photos)                         |
| `3h_download_eol_photos.py` | Download EOL large JPEGs                                             |
| `3k_web_photos.py`          | Merge everything → `web/photos.json`                                 |
| `3l_flight_nef.py`          | Diff EOL exports against local NEF holdings (which raws do we have?) |

Issues this layout has caused:

1. **No single "raw photos" location.** EOL JPEGs go to one disk path, Flickr
   originals to another, IA stills to a third, NEFs to a fourth. There's no
   single root we can point a thumb/lowres/highres generator at.
2. **No quality-tier generation step at all.** We download originals and that's
   it. The web JSON points at remote URLs (Flickr, images.nasa.gov, EOL) for
   thumbs and large views, which means the site depends on third-party CDNs
   and gives us no control over sizing.
3. **Source-driven rather than ID-driven.** Each script outputs a per-source
   file; only `3k` joins them. There's no canonical per-NASA-ID record built
   up incrementally.
4. **Export status is implicit, never modeled.** Whether a photo is safe to
   publish is computed inside `3k`'s merge logic by checking which dictionaries
   it landed in. Nothing names the concept.
5. **Step ordering is fragile.** Some steps are `[planned]`, some are slow,
   some are independent. The numbering (3a, 3a2, 3a3, 3e, 3e2, 3f, 3f2, 3g…)
   no longer reflects actual order or dependencies.

---

## Where We Want to Go

The refactor is built. The shape below describes what's actually on disk:

### A. Single raw photos root

One directory per mission, under `F:\_repos\ArtemisInRealTime_assets\` (the
canonical asset root for both raw and web). Organized by source so we can
always trace where a JPEG/NEF came from:

```
F:\_repos\ArtemisInRealTime_assets\{mission}\raw\photos\
  5_Crew-Captured-Imagery\   # crew raws — NEF (Nikon SLR) + DNG (iPhone/other);
                             # walked recursively (FD_01/, FD_02/, … subfolders)
  eol\jpeg_high\             # large JPEGs from EOL
  ia_stills\                 # JPEGs from IA bulk uploads
  flickr\                    # originals from Flickr (+ album_metadata.json)
  images_nasa_gov\           # originals from images.nasa.gov (+ catalog.json)
  manual\                    # manually added photos from outside the pipeline
```

In code these are addressed via `mission.photos_raw_crew`,
`mission.photos_eol`, `mission.photos_ia_stills`, `mission.photos_flickr_orig`,
`mission.photos_nasa_orig`, `mission.photos_manual`. The internal source-key
names used in the ledger and EXIF cache (`raw_crew`, `eol`, `ia_stills`,
`flickr`, `nasa_images`, `manual`) are short identifiers and don't need to
match the on-disk folder names.

### B. A canonical per-NASA-ID ledger

One JSONL file (or SQLite table) where each row is a NASA ID and lists
**every place we found it**, the best-known UTC timestamp, IO metadata, which
raw files we have on disk, **per-source EXIF** for each copy, and bracket-set
membership:

```jsonc
{
  "nasa_id": "art002e000168",
  "title": "...",
  "description": "...",

  // Best available UTC, with provenance
  "utc": "2026-04-03T14:22:18Z",
  "utc_source": "exif_offset",            // exif_offset | io_nhq | io_corrected | io_onboard | flickr | nasa_images

  // Export status — true iff seen in at least one public archive.
  // Multiple sources are kept for provenance, not just "is it public yes/no."
  "exported": true,
  "exported_in": ["eol", "flickr"],       // [] when not yet exported — record is still kept for internal tracking

  // Per-copy file paths AND per-copy EXIF, keyed by source.
  // Each copy has its own EXIF block because the JPEG re-encode may have
  // dropped or rewritten tags compared with the raw original.
  // We capture *every* EXIF tag the camera/source wrote — top-level fields
  // are aliases populated from whichever group carries them, so downstream
  // code can read a uniform schema. The DateTimeOriginalUTC alias carries
  // millisecond precision when the source has SubSecTimeOriginal — important
  // for ordering bracket-burst frames that share the same wall-clock second.
  "copies": {
    "raw_crew": {
      "path": "F:/_repos/ArtemisInRealTime_assets/artemis-ii/raw/photos/5_Crew-Captured-Imagery/FD_02/D5_15/art002e000168.NEF",
      "exif": {
        // Top-level cross-source aliases (always present when derivable):
        "DateTimeOriginal":     "2026:04:02 20:55:15",
        "OffsetTimeOriginal":   "+00:00",
        "ExposureBiasValue":     0.0,
        "DateTimeOriginalUTC":  "2026-04-02T20:55:15.150Z",   // sub-second!
        // Full grouped EXIF straight from ExifTool:
        "EXIF":       { "ExposureTime": 0.0025, "FNumber": 16.0, "ISO": 400, "FocalLength": 80.0, "Make": "NIKON CORPORATION", "Model": "NIKON D5", "SubSecTimeOriginal": "15", ... },
        "MakerNotes": { "ShootingMode": "Continuous, Exposure Bracketing", "AutoBracketOrder": "0,-,+", "ExposureBracketValue": 0, "TimeZone": "+00:00", ... },
        "XMP":        { ... },
        "Composite":  { "Aperture": 16.0, "ShutterSpeed": 0.0025, "LightValue": 14.6, "SubSecDateTimeOriginal": "2026:04:02 20:55:15.15", ... }
      }
    },
    "eol": {
      "path": "F:/_repos/.../raw/photos/eol/jpeg_high/ART002-E-168.JPG",
      "exif": { "DateTimeOriginal": "...", "ExposureBiasValue": 0.0, "GPSInfo": {...}, ... }
    },
    "flickr":      null,
    "nasa_images": null,
    "ia_stills":   null
  },

  // Bracket-set membership.  null when the photo is a single (non-bracketed)
  // shot.  When it IS part of a set, every member of the set carries the
  // same `set_id` (we use the metered/0-EV frame's NASA ID as the canonical
  // set id), and `position` says where this frame sits within it.
  "bracket": {
    "set_id":   "art002e000168",                   // canonical id of the set (the 0-EV "hero" frame)
    "members":  ["art002e000167", "art002e000168", "art002e000169"],
    "position": "metered",                         // "darker" | "metered" | "lighter"  (derived from ExposureBiasValue)
    "ev":       0.0,
    "is_hero":  true                               // true on the metered frame; gallery views collapse the set onto this entry
  },

  "io": { /* full IO record verbatim, when IO has it */ }
}
```

This file is **the** thing the website-build step consumes. It is rebuilt
deterministically from raw + cached-source files, so re-running is cheap and
idempotent. Records are kept even when `exported: false` — they're useful for
internal tracking ("what NEFs are we sitting on that haven't been cleared
yet?") and let us light them up automatically as soon as they appear in a
public archive on a future re-run.

### C. Web outputs (the only thing that ships to the internet)

Everything under `mission.web_dir` is what the website serves; nothing else
is published.

```
F:\_repos\ArtemisInRealTime_assets\{mission}\web\
  photos.json                       # slim per-photo list (heroes only) — frontend index
  photos\
    brackets.json                   # all bracket sets, indexed by setId
    exif\{nasa_id}.json             # full EXIF dump per photo (lazy-loaded by frontend)
    thumb\{nasa_id}.jpg             # ~400 px long edge — gallery thumbnails
    lowres\{nasa_id}.jpg            # ~1024 px long edge — inline timeline view
    hires\{nasa_id}.jpg             # FULL source resolution — lightbox / detail
```

**`photos.json`** entries (one per published *hero* — bracket alternates
collapse onto their hero):

```jsonc
{
  "id":          "art002e000188",
  "title":       "...",
  "description": "...",
  "date":        "2026-04-02T20:55:15.150Z",   // best-known UTC, sub-second when possible
  "dateSource":  "exif_offset",                // provenance: exif_offset | io_nhq | io_corrected | io_onboard | flickr | nasa_images
  "thumbUrl":    "/photos/thumb/art002e000188.jpg",
  "imgUrl":      "/photos/lowres/art002e000188.jpg",
  "hiResUrl":    "/photos/hires/art002e000188.jpg",
  "exifUrl":     "/photos/exif/art002e000188.json",   // lazy-loaded full EXIF
  "exportedIn":  ["eol"],
  "bracket":     {                              // present iff this hero is part of an AEB set
    "setId":      "art002e000188",
    "isHero":     true,
    "alternates": ["art002e000189", "art002e000190"]   // other set members
  }
}
```

**`brackets.json`** is a `{setId: bracketRecord}` map for the frontend's
"see darker / lighter" toggle:

```jsonc
{
  "art002e000188": {
    "setId":           "art002e000188",
    "hero":            "art002e000188",
    "members":         ["art002e000188", "art002e000189", "art002e000190"],
    "evs":             [0.0, -1.0, 1.0],
    "detectionSource": "shooting_mode"          // makernote-confirmed AEB
  }
}
```

**`exif/{nasa_id}.json`** holds the full grouped EXIF (all groups: EXIF,
MakerNotes, XMP, Composite, etc.) plus a `_meta` block:

```jsonc
{
  // Grouped EXIF straight from the highest-priority copy (raw_crew preferred
  // — it has the Nikon makernotes that EOL's re-encode strips).
  "DateTimeOriginal":    "2026:04:02 20:55:15",
  "DateTimeOriginalUTC": "2026-04-02T20:55:15.150Z",
  "EXIF":       { ... },
  "MakerNotes": { ... },
  "XMP":        { ... },
  "Composite":  { ... },

  // Derived metadata for the frontend
  "_meta": {
    "nasaId":     "art002e000188",
    "exifSource": "raw_crew",              // which copy this EXIF came from
    "date":       "2026-04-02T20:55:15.150Z",
    "dateSource": "exif_offset",
    "exported":   true,
    "exportedIn": ["eol"]
  }
}
```

#### Tier generation details (step 4d)

For every NASA ID that is `exported` and has at least one local copy, the
tier writer:

- Pulls the **source JPEG bytes** once — embedded preview from the NEF via
  ExifTool for `raw_crew`, or the source file directly for JPEG sources.
- Writes those bytes **verbatim** to `hires/{nasa_id}.jpg` (no re-encode,
  no downscale — same byte size as the source). Full sensor resolution:
  5568×3712 for D5, 8256×5504 for Z9.
- Decodes once and Lanczos-downscales for `lowres/{nasa_id}.jpg` (~1024 px)
  and `thumb/{nasa_id}.jpg` (~400 px), re-encoding at JPEG q=88/q=85.

Source-of-truth precedence (best JPEG quality wins):

1. **EOL JPEG** — NASA's reprocessed, higher-quality JPEG (~6 MB at full
   res). When present, this is the definitive published version.
2. **`nasa_images` original** — `~orig` asset from images.nasa.gov.
3. **`flickr` original** — `url_o` from the Flickr API.
4. **NEF embedded JPEG via ExifTool** — what NASA itself starts from when
   building EOL. Lower JPEG quality (~2 MB at full res) than EOL but works
   for crew photos not yet on EOL, including Z9 HE\*-compressed NEFs that
   libraw 0.22.x can't decode.
5. **IA stills JPEG** (last resort fallback).

(The NEFs are valuable mainly for their **EXIF makernotes**, not as a tier
source — EOL strips Nikon's `ShootingMode`, `ExposureBracketValue`,
`AutoBracketOrder`, and `MakerNotes:TimeZone` during its re-encode, but we
need those for high-confidence bracket detection (4b) and D5 ground-photographer
TZ correction (4c). raw_crew is now an EXIF-enrichment layer rather than a
tier source.)

Skip any NASA ID that is not exported. Idempotent — skip if all three tiers
already exist on disk and are newer than the source.

**HDR composite generation** was prototyped (cv2 Mertens exposure fusion)
and shelved. The crew brackets are misaligned (zero-G camera drift between
frames) and AlignMTB couldn't compensate; full feature-matching alignment
would be required, which combined with the marginal visual benefit on
most scenes wasn't worth the engineering cost. The frontend exposes
bracket alternates via `bracket.alternates` so users can flip between the
darker/metered/lighter frames manually instead.

### D. The pipeline as built

```
RAW INTAKE                 (independent, parallel)
  ├─ 3a   ia_stills_download         → raw/photos/ia_stills/
  ├─ 3b   flickr_albums               → raw/photos/flickr/album_metadata.json
  ├─ 3e   images_nasa_gov             → raw/photos/images_nasa_gov/catalog.json
  ├─ 3f   download_photos             → raw/photos/flickr/ + raw/photos/images_nasa_gov/
  ├─ 3g   eol_json                     → processed/eol_photos.json (EOL metadata)
  ├─ 3h   download_eol_photos          → raw/photos/eol/jpeg_high/
  ├─ 3i   eol_rename_canonical         → renames EOL JPEGs to canonical NASA IDs
  └─ (manual) crew raws drop            → raw/photos/5_Crew-Captured-Imagery/**

METADATA INTAKE            (independent, parallel)
  ├─ 3a2  io_photo_catalog            → io_cache/io_photo_catalog.jsonl
  ├─ 3a3  io_exif_scrape              → io_cache/photo-{time,datetime}-overrides.json
  ├─ 3e2  io_nhq_lookup                → io_cache/io_nhq_photos_*.jsonl
  └─ 4a   extract_all_exif             → processed/exif/{source}/{nasa_id}.json
                                                                  ↑ per-copy EXIF, full
                                                                    schema incl. makernotes

LEDGER + BRACKETS          (depends on intake + 4a)
  ├─ 4b   detect_brackets              → io_cache/bracket_sets.jsonl
  └─ 4c   build_ledger                 → processed/photos_ledger.jsonl
                                          (canonical per-NASA-ID merge)

WEB BUILD                  (depends on ledger)
  ├─ 4d   generate_tiers               → web/photos/{thumb,lowres,hires}/{nasa_id}.jpg
  └─ 4e   web_photos_json              → web/photos.json
                                          web/photos/brackets.json
                                          web/photos/exif/{nasa_id}.json
```

`raw/`, `processed/`, and `io_cache/` stay local — only `web/` ships to the
internet. Tier files are bytes-on-disk, JSON files are bytes-on-disk, and
the frontend mounts `web/photos/` at `/photos/` so all URLs are
`/photos/{thumb,lowres,hires,exif}/{nasa_id}.{jpg,json}`.

Bracket detection runs **after** per-copy EXIF extraction (it needs
`MakerNotes:ShootingMode`, `ExposureBracketValue`, and `SubSecTimeOriginal`
for sub-second ordering) and **before** ledger finalization, so the
bracket-cluster fields land in the ledger record.

---

## Decisions Carrying Into the Refactor

These resolve the open questions raised during the design discussion:

1. **Tier rendering quality.** The NEFs are unedited digital negatives — color
   rendering matters. Use `rawpy` (libraw) for the decode with camera white
   balance; fall back to `darktable-cli` if the rawpy defaults look off.
   Generate HDR/exposure-fusion composites for bracket sets where we have all
   three NEFs (`enfuse` from the Hugin tools is the planned tool). HDR can
   land in v2 if it slows v1 down — the per-frame tiers stand on their own.

2. **Asset root.** Everything (raw + web) lives under
   `F:\_repos\ArtemisInRealTime_assets\` in a `{mission}\raw\photos\…` and
   `{mission}\web\photos\…` hierarchy. NEFs will move into this tree once
   disk space is freed; the pipeline should already be pointed at the new
   location.

3. **Always pull "as close to original as we can."** Even for press / NHQ
   photos that arrive only via Flickr or images.nasa.gov, fetch the largest
   available size so the tier generator has a good basis to downscale from.
   No trusting third-party CDNs in the published page.

4. **Multiple `exported_in` sources are kept, not collapsed.** A photo that
   appears in EOL *and* Flickr *and* images.nasa.gov stores all three in
   `exported_in`. Provenance matters as much as the boolean — it tells us
   which downstream URL to credit and gives us a fallback if one CDN goes
   away.

5. **Re-runs are first-class.** The pipeline re-runs to pick up (a) new
   exports as NASA releases more photos to EOL/Flickr/etc., and (b) new raw
   assets as they arrive locally, regardless of whether they are exported
   yet. Ledger build and tier generation must both be incremental — only
   touch what changed.

6. **Unexported photos stay in the ledger.** NEFs we have but that NASA
   hasn't released are recorded with `exported: false` and `exported_in: []`.
   The web build skips them, but they're visible internally so we know
   what's queued to light up on the next clearance pass.

---

## Related Docs

- [IO_DATA_EXPLAINED.md](IO_DATA_EXPLAINED.md) — IO schema, collection
  hierarchy, the three IO data products
- [PLANNING_DATA_INGESTION.md](PLANNING_DATA_INGESTION.md) — the original
  whole-project ingestion plan; photo section is now superseded by this doc
- [src/server-batch/3_photos/README.md](../3_photos/README.md) — current
  per-script reference for what's actually on disk today
