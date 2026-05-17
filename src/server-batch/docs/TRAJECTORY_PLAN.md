# Artemis II Trajectory Visualization — Plan

A new test page (`/trajectory-test`) that shows Orion's flight path with a draggable
timeline and a top-down (Earth north-pole) 2D view of the Earth–Moon system.

This document captures the starting position from the research I did before
writing any production code, the proposed approach, and the open questions I
need answered before implementation.

---

## 1. What we have

### 1.1 Raw NASA ephemeris (the source of truth)

`F:\_repos\ArtemisInRealTime_assets\artemis-ii\raw\ephemeris\` contains 10
zipped CCSDS **OEM (Orbit Ephemeris Message) v2.0** files released by
NASA/JSC/FOD/FDO between 2026-04-02 and 2026-04-10. Each zip holds a single
`.asc` text file. Header excerpt (latest file):

```
CCSDS_OEM_VERS = 2.0
CREATION_DATE  = 2026-04-10T03:22:19
ORIGINATOR     = NASA/JSC/FOD/FDO
OBJECT_NAME    = EM2
CENTER_NAME    = EARTH
REF_FRAME      = EME2000           ← Earth-centered inertial, J2000 equator
TIME_SYSTEM    = UTC
START_TIME     = 2026-04-02T01:57:37.084
STOP_TIME      = 2026-04-10T23:53:16.723
```

Followed by ~6,000 lines of:

```
ISO-UTC   x_km   y_km   z_km   vx_km_s   vy_km_s   vz_km_s
```

Step is non-uniform: dense near burns (10–60 s) and 4 min elsewhere.

**Coverage of available files:**

| File (release date)              | Window covered             |
| -------------------------------- | -------------------------- |
| OEM 2026-04-02 (post-USS-2)      | 2026-04-02 → EI            |
| OEM 2026-04-02 v3                | 2026-04-02 → EI            |
| OEM 2026-04-03                   | 2026-04-03 → EI            |
| OEM 2026-04-04                   | 2026-04-04 → EI            |
| OEM 2026-04-06 (pre-OTC3)        | 2026-04-06 → EI            |
| OEM 2026-04-07 (pre-Lunar-Flyby) | 2026-04-07 → EI            |
| OEM 2026-04-08 (post-ICPS-Sep)   | 2026-04-08 → EI            |
| OEM 2026-04-09 (post-ICPS-Sep)   | 2026-04-09 → EI            |
| OEM 2026-04-10 (post-ICPS-Sep)   | 2026-04-02 → 2026-04-10    |
| 2026-04-10 Post-RTC3 to EI       | small file, post-RTC3 only |

The 04-10 OEM covers the full mission window (Apr 2 → Apr 10) and is the most
recently revised reconstruction for that span, so it is the natural "ground
truth" baseline for a completed-mission replay.

**Gaps:** Even the latest OEM begins ~4 h after launch (no pre-TLI parking
orbit) and ends before re-entry (~01:00 on Apr 11 splashdown is not covered).

### 1.2 Reference implementation — issinfo.net/artemis

Single-file React-less webapp (`artemis.min.js`, 56 KB). Renderer is plain
`<canvas>` with `getContext("2d")` — no Three.js / Cesium / globe library. It
fetches three JSON endpoints from `https://api.issinfo.net`:

| Endpoint                          | What it returns                                                                         |
| --------------------------------- | --------------------------------------------------------------------------------------- |
| `/artemis/missions`               | Metadata: name, launch/splashdown UTC, crew, phases (name/start_day/color), max_dist_km |
| `/artemis/live`                   | Status + latest_completed mission block                                                 |
| `/artemis/history/{mission_id}`   | The trajectory; see below                                                               |
| `/artemis/itinerary/{mission_id}` | (403 without proper Referer; not inspected)                                             |

The history payload for `artemis-2` (890 KB) is structured exactly like what
our frontend will need:

```json
{
  "mission_id": "artemis-2",
  "step_size_minutes": 5,
  "now_index": 2550,
  "points": [
    {
      "t": "2026-04-02T02:35:12Z",
      "orion": { "x_km":…, "y_km":…, "z_km":…,
                 "distance_earth_km":…, "distance_moon_km":…, "speed_km_s":… },
      "moon":  { "x_km":…, "y_km":…, "z_km":…, "distance_earth_km":… }
    },
    …2551 points total, 5-minute cadence, 8.9 days
  ]
}
```

Same Earth-centered inertial frame (issinfo gets it from JPL Horizons; OEMs
declare EME2000; the two frames agree to better than 0.1°). Orion distance
range matches: 202 km (re-entry interface) to 406,774 km (lunar flyby), and
issinfo's `max_dist_km` for Artemis II is 406,770.34 km — within rounding of
what is in the OEM.

**Confirmation:** the OEM data we have is the same kind of data issinfo uses,
just sourced from NASA's flight dynamics team instead of JPL Horizons.

### 1.3 What issinfo's data does _not_ give us

- No Sun direction (so no day/night terminator on Earth).
- No spacecraft attitude.
- No imagery/photo overlays.
- Moon position is included in their history; OEM files are Orion-only.

---

## 2. Proposed approach

### 2.1 Pipeline (Python, in `src/server-batch/`)

New folder `src/server-batch/5_trajectory/` mirroring the existing
`1_comm/2_video/3_photos` pattern:

```
5_trajectory/
  README.md
  5a_parse_oem.py          # unzip + parse all *.asc, dedupe overlapping
                           # windows by preferring the most recently created
                           # OEM, emit a single canonical Orion track
  5b_moon_ephemeris.py     # compute geocentric Moon position at every
                           # Orion timestamp (skyfield + de440s.bsp)
  5c_web_trajectory.py     # downsample to ~5-minute cadence, project to
                           # the chosen render frame, write the web JSON
```

Output: `../ArtemisInRealTime_assets/artemis-ii/web/ephemeris/trajectory.json`

Proposed schema (mirrors issinfo so the frontend stays simple):

```jsonc
{
  "mission_id": "artemis-ii",
  "frame": "EME2000",
  "step_size_seconds": 300,
  "launch_utc": "2026-04-01T22:35:12Z",
  "splashdown_utc": "2026-04-11T00:07:00Z",
  "earth_radius_km": 6378.137,
  "moon_radius_km": 1737.4,
  "phases": [
    { "name": "Trans-Lunar Injection", "t": "2026-04-02T01:55:00Z",
      "color": "#ef476f" },
    …
  ],
  "points": [
    { "t": "2026-04-02T02:00:00Z",
      "orion": { "x": -27648.6, "y": -22283.9, "z": -1974.5,
                 "r_earth": 29194.6, "speed": 3.58 },
      "moon":  { "x": -384298.6, "y": -82933.9, "z": -19863.3 } },
    …
  ]
}
```

### 2.2 Frontend (`/trajectory-test`)

Pattern matches `PhotoTest.tsx` / `CommTest.tsx`:

- `TrajectoryTest.tsx` + `TrajectoryTest.module.css`
- Fetch `${ASSETS_BASE}/artemis-ii/web/ephemeris/trajectory.json` once.
- Single `<canvas>` rendering top-down (project EME2000 x,y; ignore z, which is
  the Earth-equator-pole axis — true geographic north pole is offset by Earth's
  ~23.4° axial tilt from EME2000 z, close enough for "top-down north-pole
  view"; we will note this in the page caption).
- Earth disk at origin (scaled), Moon trail + current position, Orion full
  trail + current position.
- Auto-fit zoom so the whole trajectory fits the canvas.
- Timeline slider beneath the canvas, plus play/pause and speed (×12 / ×24 /
  ×60 / ×600).
- HUD: MET, UTC, distance to Earth, distance to Moon, speed, current phase.
- Add route `<Route path="trajectory-test" element={<TrajectoryTest />} />` in
  `src/index.tsx`.

No Three.js dependency. Pure 2D canvas like issinfo.

---

## 3. Open questions (need answers before I write production code)

1. **Frame for the top-down view.** EME2000 z-axis is Earth's mean equatorial
   pole at J2000, _not_ the geographic north pole and _not_ the ecliptic pole.
   Three options:
   - (a) Project EME2000 x,y as-is. Simple, matches issinfo, but the Moon
     appears tilted ~5° out of plane.
   - (b) Rotate into the ecliptic frame so the Moon lies nearly flat. Most
     intuitive "looking down on the solar system" view.
   - (c) Rotate into Earth-fixed (ITRF) so the Earth doesn't spin. Wrong for
     a translunar trajectory.

   **Recommendation: (a)** to mirror issinfo and keep the pipeline trivial,
   unless you specifically want (b).
   a since the difference is small and we can call it out in the caption. (c) is a non-starter

2. **Moon position source.** OEMs don't include the Moon. Options:
   - (a) Compute via `skyfield` + `de440s.bsp` (~30 MB JPL kernel, vendored
     into the batch script repo, public domain).
   - (b) Reuse issinfo's `/artemis/history/artemis-2` JSON as the Moon
     reference (free, already verified to agree with the mission timeline).
   - (c) Pull from JPL Horizons directly (`Major Body 301`, observer = 399).

   **Recommendation: (a)** so we have no runtime dependency on a third-party
   site. Acceptable to start with (b) and swap later.
   a - let's do this and include the kernel in the raw data folder, and the py to process it in the batch folder.

3. **Multiple-OEM merge policy.** Each newer OEM is a reconstruction of the
   most recent past + a propagation forward. For a _completed-mission replay_
   we only need the latest. Do you also want a "what NASA thought the
   trajectory was on day N" replay (planned-vs-actual)? If yes I should store
   _all_ OEMs and let the UI pick which release to render.

   **Recommendation (simpler):** ship only the merged best-estimate (latest
   OEM wins on overlap) for v1.
   Only the best estimate for v1 since it'll be difficult to show subtle differences in the UI, but we can keep the others in the repo for future use if we want to do a "compare different OEM releases" feature later.

4. **Pre-TLI and post-EI coverage.** The OEMs miss the parking-orbit segment
   before TLI (~Apr 1 22:35 → Apr 2 01:55) and the re-entry/splashdown segment
   (~Apr 10 23:53 → Apr 11 00:07). For visual continuity, do you want me to:
   - (a) Drop them (start the timeline at TLI, end at the last OEM point).
   - (b) Linearly extrapolate from velocity at the endpoints.
   - (c) Hand-author placeholder points (LEO circle for parking orbit,
     surface re-entry point).
   - (d) Scrape issinfo to fill the gap.

   **Recommendation: (a)** for v1; add a label on the slider for "Launch" and
   "Splashdown" so it's clear we're showing the coast phase only.
   d and if it's not there, try JPL horizons. Reentry coverage needs to be accurate because we'll be showing velocity and altitude at that point, so if we can't get it from a reliable source, it's better to just end the timeline at the last OEM point with a clear label. Note that we might want the resolution of ephemeris data overall to be better than every 5 minutes because we'll be showing rapidly changing velocity and distance during the re-entry phase, so we might want to consider keeping more of the original OEM points (which are denser around burns and critical events) rather than downsampling to a uniform 5-minute cadence. We can discuss this further once we have the OEM data parsed and can see how it looks on the timeline.

5. **Mission phase boundaries.** issinfo publishes a known-good list of phase
   start_days (TLI, Outbound Coast, Lunar Flyby, Return Coast, Re-entry). Are
   you happy to seed our config with those values, or do you have a NASA
   timeline you want me to drive off instead?
   There's this one https://www.nasa.gov/wp-content/uploads/2026/01/artemis-ii-overview-timeline-public-final.pdf but that was published preflight. See if you can find a post-flight one to verify if there were any changes. Whatever you find, digest it into something to put in the web folder for the website to consume.

6. **Asset/output path naming.** The batch scripts use
   `../ArtemisInRealTime_assets/artemis-ii/web/...` — confirm
   `web/ephemeris/trajectory.json` is the right home (consistent with
   `web/photos.json`, `web/comm/...`).

ok

7. **Page route name.** I propose `/trajectory-test`. OK?

ok

8. **Visual style.** Match issinfo (dark background, white Earth glow, dashed
   Moon path, solid Orion path with progress in white and remainder dimmed)
   or design our own from scratch?

Match the style for now, but keep it flexible because we'll be styling this whole app ourselves later.

9. **Future-proofing.** Should the same pipeline auto-handle Artemis III/IV
   when their OEMs arrive (i.e., a `--mission` flag like `1c_web_comm.py`
   uses), or hard-code Artemis II for now?

keep --mission for now. Actually, that's a good point, have a look around for artemis 1 ephemeris data and see if we can use the same pipeline to process that as well, so we can have a trajectory replay for Artemis 1 on the site too. That way we can verify the pipeline works end-to-end before Artemis 2 data starts coming in, and we'll have more content on the site at launch.

---

## 4. Implementation notes (post-build findings)

### 4.1 Ephemeris coverage gaps — confirmed unfixable

Both NASA OEMs and JPL Horizons have the same gaps:

**Artemis II**

- **Pre-TLI gap:** OEM/Horizons data begins at `2026-04-02T01:57:37Z` —
  approximately 3 h 22 m after launch (`2026-04-01T22:35:12Z`). The parking
  orbit before TLI is simply not present in any publicly available source.
  JPL Horizons (target -1024) has the identical gap, confirming this is not a
  processing issue.
- **Post-EI gap:** Latest OEM ends at `2026-04-10T23:53:16Z`; splashdown was
  `2026-04-11T00:07:00Z`. The ~14-minute re-entry / parachute descent is
  absent from all OEMs and from Horizons.

**Artemis I** (Horizons target -1023): Similar pre-launch and post-splashdown
gaps. NASA never publicly released A1 OEMs.

**Decision (per Q4 answer + implementation):** Timeline runs from first OEM
sample to last. `launch_utc` / `splashdown_utc` are stored in the JSON so the
HUD can show real MET, but the slider extent is bounded by `coverage_start_utc`
/ `coverage_end_utc`. The `notes[]` array in `trajectory.json` describes both
gaps; the UI displays them beneath the canvas.

### 4.2 OEM merge — window-based, not key-based

A naïve merge (last-writer-wins per timestamp) inflated the A2 point count to
~18,500 because 10 overlapping OEM files had slightly different timestamp
cadences for the same physical moment, producing interleaved samples from
multiple reconstructions.

The implemented strategy: **newer `CREATION_DATE` owns its `[START_TIME,
STOP_TIME]` window** and evicts all earlier samples within that interval before
inserting its own. This produces a clean, single-reconstruction track:

| Stage                            | Point count |
| -------------------------------- | ----------- |
| Raw merge (key-based, naïve)     | 18,514      |
| Window-based merge (implemented) | 3,264       |
| After burn-aware thinning (5c)   | 1,618       |
| Final `trajectory.json` (A2)     | 196 KB      |
| Final `trajectory.json` (A1)     | 295 KB      |

### 4.3 EME2000 vs ICRF / JPL Horizons

Horizons returns data in `REF_PLANE=FRAME` which is ICRF. OEMs declare
`REF_FRAME = EME2000`. The two frames agree to <0.1° (the difference is
sub-pixel at any useful canvas scale), so they are treated as identical. The
`frame_note` field in `trajectory.json` documents this.

### 4.4 JD-TDB → UTC conversion (Horizons)

Horizons timestamps are Julian Date, Barycentric Dynamical Time (JD TDB).
Conversion: `TT − UTC = 69.184 s` (constant 2017+), and `TT − TDB < 0.002 s`
for dates of interest, so `UTC ≈ TDB − 69.184 s`. This was validated by
comparing Horizons "10:00:00.000 TDB" with the expected UTC offset for 2022.

### 4.5 Burn-aware thinning

Coast segments are thinned to one sample per 300 s (5 min). Any sample whose
OEM-source gap to its predecessor is <30 s is treated as a burn sample and
kept verbatim. This preserves the full native OEM resolution for TLI, trajectory
correction manoeuvres, lunar flyby close-approach, and re-entry — exactly where
rapid velocity and altitude changes make the timeline most interesting.

### 4.6 Temporary scratch files

`.scratch/inspect.py`, `.scratch/history.json`, and `.scratch/itin.json`
(issinfo API responses used for schema verification) were deleted after
implementation.

### 4.7 Files produced

```
src/server-batch/5_trajectory/
  __init__.py
  5a_orion_track.py
  5b_moon_ephemeris.py
  5c_web_trajectory.py
  README.md

src/pages/
  TrajectoryTest.tsx
  TrajectoryTest.module.css

src/index.tsx                         ← added /trajectory-test route
src/server-batch/run_all.py           ← added steps 5a / 5b / 5c
```

Output assets:

```
$DATA_DIR/artemis-ii/web/ephemeris/trajectory.json   (196 KB, 1,618 points)
$DATA_DIR/artemis-i/web/ephemeris/trajectory.json    (295 KB, 2,429 points)
```
