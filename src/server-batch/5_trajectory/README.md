# 5_trajectory — Orion + Moon ephemeris pipeline

Builds the canonical, web-ready trajectory JSON consumed by the
`/trajectory-test` page.

## Inputs

| Mission    | Source                              | Location                                                           |
| ---------- | ----------------------------------- | ------------------------------------------------------------------ |
| Artemis II | NASA/JSC FOD CCSDS OEM v2.0 zips    | `$DATA_DIR/artemis-ii/raw/ephemeris/*.zip` (manually downloaded)   |
| Artemis I  | JPL Horizons VECTORS (target -1023) | Auto-fetched to `$DATA_DIR/artemis-i/raw/ephemeris/horizons_*.txt` |

Reference frame: **EME2000 (≡ ICRF, J2000)**, geocentric. Times: **UTC**.

DE440s SPK kernel is auto-downloaded to
`$DATA_DIR/_shared/kernels/de440s.bsp` (32.7 MB, shared across missions).

---

## Ephemeris coverage gaps (important)

### Artemis II — NASA OEM zips

NASA/JSC/FOD released 10 OEM v2.0 files over the course of the mission. The
latest (2026-04-10, `CREATION_DATE = 2026-04-10T03:22:19`) is the best-estimate
reconstruction and spans `2026-04-02T01:57 → 2026-04-10T23:53` (UTC).

**Pre-launch / parking orbit gap:** OEM data begins ~3h 22m after launch
(`launch_utc = 2026-04-01T22:35:12Z`). No publicly available OEM covers the
pre-TLI parking orbit. JPL Horizons returns the same gap for Orion (target
-1024) — the gap is real and not a processing artifact.

**Post-EI gap:** The latest OEM ends at `2026-04-10T23:53:16Z`; splashdown
was `2026-04-11T00:07Z`. That ~14-minute re-entry / descent segment is absent
from all available OEMs and is not in Horizons either.

**Consequence for the timeline:** `coverage_start_utc` and `coverage_end_utc`
in `trajectory.json` reflect the actual OEM extents, not launch/splashdown.
The `notes[]` array in the JSON describes both gaps so the UI can display
them. The `launch_utc` / `splashdown_utc` fields are still populated so the
HUD can show MET relative to the real launch time.

### Artemis I — JPL Horizons (target -1023)

Horizons data was used because NASA did not publicly release A1 OEMs. The
Horizons coverage is similarly gapped: it starts well after launch and ends
before splashdown. The pipeline treats A1 identically to A2; the same `notes[]`
mechanism flags the gaps.

### Why the OEM produces fewer points than expected

The raw A2 OEM archive contains 10 overlapping files covering different
mission windows. A naïve key-level merge (last-writer-wins per timestamp) gives
~18,500 points because different OEM files had slightly different timestamps
for the same physical moment — the overlap inserted interleaved samples from
multiple reconstructions and artificially inflated density.

The implemented merge strategy (`5a_orion_track.py`) is **window-based**: each
OEM owns its `[START_TIME, STOP_TIME]` interval and a newer `CREATION_DATE`
evicts all older samples within that window before inserting its own. This
collapes the 10 files to **3,264 unique points** that reflect only the single
best reconstruction at every moment.

---

## Scripts

Run with `uv run <script>.py --mission <slug>` from this folder.
Slugs: `artemis-i`, `artemis-ii`.

### 5a_orion_track.py

Produces `processed/ephemeris/orion_track.jsonl`
(`{t, x, y, z, vx, vy, vz, src}` per line).

- **OEM source**: parses every zipped OEM. Sorted ascending by
  `CREATION_DATE`; each newer OEM **owns its time window** — all older samples
  inside `[START_TIME, STOP_TIME]` are evicted before the newer OEM's samples
  are inserted. This prevents interleaved cadence from multiple
  reconstructions.
- **Horizons source**: hits `ssd.jpl.nasa.gov/api/horizons.api` once per
  mission (cached) with `REF_PLANE=FRAME`, then converts JD-TDB → UTC
  (TT−UTC = 69.184 s, constant since 2017). A1 uses Horizons because no
  public OEM was released. Horizons `-1024` (A2) was verified to agree with
  OEM positions to within frame-alignment tolerance (<0.1°).
- Flags: `--refresh-horizons` to ignore cache.

### 5b_moon_ephemeris.py

For every Orion timestamp, computes the geocentric Moon position with
Skyfield + DE440s. Output: `processed/ephemeris/moon_track.jsonl`
(`{t, x, y, z}`).

### 5c_web_trajectory.py

Joins Orion + Moon, attaches mission phases, thins, and writes
`web/ephemeris/trajectory.json` as parallel arrays
(`t, ox/oy/oz, ovx/ovy/ovz, mx/my/mz`) for compactness.

Thinning rules (preserves OEM density around burns):

- Always keep first and last point.
- Keep any point whose source gap is `< burn_gap_s` (30 s) — these sit
  inside maneuvers.
- Otherwise enforce one point every `coast_step_s` (300 s).

This yields **1,618 points** for A2 (196 KB) and **2,429 points** for A1
(295 KB). Re-entry, TLI, and lunar flyby burn segments retain their full
native OEM resolution; coast phases are thinned to one point per 5 min.

Phases are hardcoded near the top of `5c_web_trajectory.py` — sourced from
post-flight summaries on [issinfo.net/artemis](https://issinfo.net/artemis).
Adjust as official post-flight (or real-time) data becomes available.

---

## Output schema (trajectory.json)

```jsonc
{
  "mission_id": "artemis-ii",
  "mission_name": "Artemis II",
  "frame": "EME2000",
  "frame_note": "ICRF/J2000, geocentric. X–Y projection is the equatorial plane.",
  "earth_radius_km": 6378.137,
  "moon_radius_km": 1737.4,
  "launch_utc": "2026-04-01T22:35:12Z",      // real launch; may pre-date coverage_start
  "splashdown_utc": "2026-04-11T00:07:00Z",  // real splashdown; may post-date coverage_end
  "coverage_start_utc": "2026-04-02T01:57Z",  // first OEM point
  "coverage_end_utc":   "2026-04-10T23:53Z",  // last OEM point
  "phases": [{"name": "TLI", "t": "...", "color": "#ef476f"}, ...],
  "notes": [
    "Coverage starts ~3h 22m after launch — pre-TLI parking orbit not in OEM.",
    "Coverage ends ~14m before splashdown — re-entry not in OEM."
  ],
  "max_distance_km": 401234.5,
  "n_points": 1618,
  "points": {
    "t":   ["2026-...Z", ...],
    "ox":  [...], "oy": [...], "oz": [...],
    "ovx": [...], "ovy":[...], "ovz":[...],
    "mx":  [...], "my": [...], "mz": [...]
  }
}
```

---

## Adding a new mission

1. Add a `MissionConfig` entry in `../config.py` with
   `ephemeris_source` set to `"oem"` or `"horizons"`, plus
   `horizons_id`, `launch_utc`, `splashdown_utc`.
2. For OEM: drop the zips into `<DATA_DIR>/<slug>/raw/ephemeris/`.
3. Add a phase block in `_PHASES` inside `5c_web_trajectory.py`.
4. Run 5a → 5b → 5c.

For the mission event timeline, see `../6_itinerary/README.md`.
