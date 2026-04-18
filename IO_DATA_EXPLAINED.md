# Imagery Online (IO) Data — What We Gathered

## What Is IO?

**Imagery Online** (IO) is NASA JSC's internal media asset management system at `io.jsc.nasa.gov`. It catalogs every piece of flight imagery and video that NASA produces — photos taken by crew, mission video recordings, NASA TV broadcasts, etc. Each item has a unique `nasa_id`, precise GMT timestamps (`vmd_start_gmt`), detailed metadata (duration, resolution, descriptions), and collection hierarchy information.

IO is the **authoritative source** for "when did this happen?" — its `vmd_start_gmt` timestamps tell us exactly when a video was broadcast or a photo was taken, which is critical for placing media on the mission timeline.

---

## Three Different IO Data Products

We gather three separate things from IO. They serve different purposes:

### 1. IO Cross-Reference Search (`io_found.jsonl` / `io_notfound.jsonl`) — Step 2c

**Purpose**: Look up IA video items in IO to get accurate broadcast timestamps.

**How it works**: Step 2a discovers video items on Internet Archive (e.g., `art001m1503252244_Artemis_I_Outbound_Powered_Flyby_Briefing`). We extract the NASA ID from the filename (`art001m1503252244`) and search IO's API for that exact ID. If IO has it, we get back the full IO metadata record — most importantly `vmd_start_gmt` (the exact GMT time the video was broadcast/recorded).

**What it produces**:

- `io_found.jsonl` — IA videos that were found in IO, with full IO metadata attached
- `io_notfound.jsonl` — IA videos with no IO match (NASA ID not in IO, or no extractable NASA ID)

**Why it matters**: IA items don't have reliable timestamps. IO gives us the exact broadcast time so we can place each video on the mission timeline.

| Mission    | Found in IO | Not Found | Total IA Items |
| ---------- | ----------- | --------- | -------------- |
| Artemis I  | 14          | 73        | 87             |
| Artemis II | 10          | 8         | 18             |

The "not found" items are typically IA uploads that used non-standard identifiers, composite/highlight reels that don't map to a single IO record, or items uploaded before IO cataloged them.

---

### 2. IO Video Catalog (`io_video_catalog.jsonl`) — Step 2c2

**Purpose**: Get the **complete catalog** of all flight video in IO's collection — not just the ones that match IA uploads.

**How it works**: Each mission has a parent collection in IO (a CID). We scrape all video assets (`asset_type=2`) under that parent collection. This fetches everything — NASA TV broadcasts, crew recordings, event coverage — whether or not it was ever uploaded to Internet Archive.

**What it produces**: One JSONL file with every video document in the IO collection.

| Mission    | IO Parent CID | Videos in IO | Videos on IA |
| ---------- | ------------- | ------------ | ------------ |
| Artemis I  | 2355140       | 2,108        | 87           |
| Artemis II | 2380537       | 2,233        | 18           |

**Why it matters**: Internet Archive only has a fraction of the video that IO catalogs. The IO video catalog is the comprehensive inventory — it tells us every video that exists for the mission, with accurate timestamps, durations, and descriptions. This is used for:

- Building the complete video timeline (not just IA uploads)
- Cross-referencing with YouTube livestreams
- Identifying what video content exists but hasn't been made publicly available yet

---

### 3. IO Photo Catalog (`io_photo_catalog.jsonl`) — Step 3a2

**Purpose**: Get the **complete catalog** of all flight photography in IO's collection.

**How it works**: Same as the video catalog, but filtering for photo assets (`asset_type=1`). Scrapes all photos under the mission's parent collection.

**What it produces**: One JSONL file with every photo document in the IO collection.

| Mission    | IO Parent CID | Photos in IO | Other Photo Sources                         |
| ---------- | ------------- | ------------ | ------------------------------------------- |
| Artemis I  | 2355140       | 16,329       | 63 IA stills, 283 Flickr, 4,660 NASA images |
| Artemis II | 2380537       | 43,307       | 391 Flickr, 5,421 NASA images               |

**Why it matters**: IO is the master photo catalog. The same photos appear across multiple public sources (Flickr, images.nasa.gov) but IO has them all, with NASA IDs that let us deduplicate across sources. IO photos also have timestamps for timeline placement, collection hierarchy (which flight day, which event), and the `on_public_site` flag indicating whether the photo is publicly accessible.

---

## How They Relate

```
Internet Archive           IO (Imagery Online)              Public Sources
  87 IA videos  ──────┐
                       │    ┌─────────────────────────┐
  Step 2c: search IO   ├───>│  IO Video Catalog       │     YouTube (274 videos)
  for each IA item's   │    │  2,108 videos (A1)      │
  NASA ID              │    │  2,233 videos (A2)      │
                       │    └─────────────────────────┘
  io_found.jsonl ◄─────┘
  (14 matches)               ┌─────────────────────────┐
                             │  IO Photo Catalog       │    Flickr (283 photos)
                             │  16,329 photos (A1)     │    images.nasa.gov (4,660)
                             │  43,307 photos (A2)     │    IA Stills (63 photos)
                             └─────────────────────────┘
```

- **io_found** = "which of our IA videos did we find in IO?" (timestamp enrichment for IA items)
- **io_video_catalog** = "what is every video IO knows about for this mission?" (comprehensive inventory)
- **io_photo_catalog** = "what is every photo IO knows about for this mission?" (comprehensive inventory)

The cross-reference search (step 2c) is a targeted lookup. The catalogs (steps 2c2, 3a2) are bulk scrapes of the entire collection. They answer different questions.

---

## IO Document Structure

Each IO document (video or photo) contains fields like:

| Field                | Example                                         | Purpose                                                       |
| -------------------- | ----------------------------------------------- | ------------------------------------------------------------- |
| `nasa_id`            | `art001m1503252244`                             | Unique identifier, cross-refs with other sources              |
| `md_title`           | `"Artemis I Post OPF Briefing"`                 | Human-readable title                                          |
| `description`        | Full text description                           | What the content shows                                        |
| `vmd_start_gmt`      | `2022-11-21T22:44:41Z`                          | **Exact broadcast/capture time** (critical for timeline)      |
| `vmd_end_gmt`        | `2022-11-21T23:48:29Z`                          | End time                                                      |
| `duration_seconds`   | `3827.77`                                       | Duration (video only)                                         |
| `collections_string` | `["P2355140/Artemis-01\|VIDEO\|NASA TV\|FD06"]` | Collection hierarchy (mission > type > category > flight day) |
| `webpath`            | `/photos/vrps/13490`                            | Internal path                                                 |
| `on_public_site`     | `0` or `1`                                      | Whether publicly accessible                                   |
| `asset_type`         | `1` (photo) or `2` (video)                      | Media type                                                    |
| `md_creation_date`   | `2022-11-21T22:44:41Z`                          | Creation date                                                 |

---

## Collection Hierarchy

IO organizes content in nested collections. For Artemis I (CID 2355140):

```
Artemis - Missions
  └── Artemis-01 (CID 2355140)
        ├── VIDEO
        │     ├── NASA TV
        │     │     ├── FD01
        │     │     ├── FD02
        │     │     └── ...
        │     └── Other Video
        ├── MISSION IMAGERY
        ├── FCR (Flight Control Room)
        ├── Launch
        ├── Splashdown & Recovery
        └── Events
```

The `collections_string` field in each document shows exactly where it sits in this hierarchy, which is useful for categorizing content by flight day and event type.
