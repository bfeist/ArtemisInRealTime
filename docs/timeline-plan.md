# Timeline Test Page — Implementation Plan

## Goal

A standalone `/timeline-test` page that demonstrates a two-tier interactive
timeline for Artemis II, using real mission data already served by the same
CDN that feeds TrajectoryTest2 and the other test pages. The design concepts
(zoom window, drag-to-scrub, detail strip) come from the colleague's
`TimelinePanel.tsx` demo, but the implementation is written fresh for our
architecture and data sources, and is structured so it can later be extracted
into a reusable component.

---

## Data Sources

| Track                          | Source                                                  | How fetched                     |
| ------------------------------ | ------------------------------------------------------- | ------------------------------- |
| Mission events (point markers) | `{ASSETS_BASE}/artemis-ii/web/ephemeris/itinerary.json` | already used in TrajectoryTest2 |
| Mission phases (color band)    | same `itinerary.json` → `phases[]` array                | already used                    |
| Photos (availability marks)    | `{ASSETS_BASE}/artemis-ii/web/photos.json`              | used in PhotoTest               |
| Comm / audio segments          | `{ASSETS_BASE}/artemis-ii/web/comm.csv`                 | used in CommTest                |

All four are fetched in parallel on mount. Photos whose `dateSource` gives
only day-level precision (midnight UTC, no time component) are omitted from
the timeline because their position would be meaningless at fine zoom levels.

---

## Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  Heading + MET/UTC clocks                                           │
├─────────────────────────────────────────────────────────────────────┤
│  OVERVIEW BAR  (full mission, click/drag → sets playhead)           │
│  · flight-day segments (FD01…FD10 + T-2h pre-launch)               │
│  · calendar date labels                                             │
│  · mission-event tick marks (short vertical lines)                 │
│  · phase color band (thin strip at bottom)                          │
│  · zoom-window highlight rectangle + bezier connectors to detail   │
│  · playhead needle + MET badge                                      │
├─────────────────────────────────────────────────────────────────────┤
│  DETAIL STRIP  (zoomed window, drag left/right to pan)              │
│  · time axis (adaptive tick spacing depending on zoom level)        │
│  · mission events (diamond markers + labels)                        │
│  · Photos row (green tick per photo)                                │
│  · Comm/audio row (colored segment per utterance)                   │
│  · center-line playhead                                             │
│  · zoom +/- buttons                                                 │
└─────────────────────────────────────────────────────────────────────┘
  Play │ Pause    ×1  ×24  ×60  ×600  ×3600
```

---

## State & Interactions

### Scrub time (`scrubMs` + `scrubMsRef`)

- Owned by the page component as `useState<number>`.
- `scrubMsRef` is kept in sync so animation frames don't need re-subscriptions.
- Range: `coverageStartMs` … `coverageEndMs` (from `itinerary.json`).

### Zoom window (`windowDurationMs`)

- Starts at 1 day.
- Stepped through a fixed set: total, 12 h, 6 h, 3 h, 1 h, 30 m, 10 m, 1 m.
- `windowStartMs = scrubMs - windowDurationMs / 2`, clamped to coverage range.

### Playback

- `playing` + `speed` (×1, ×24, ×60, ×600, ×3600).
- `requestAnimationFrame` loop advances `scrubMs` by `dt * speed`.
- For real-app use the ×1 speed would be the default and the others removed.

### Drag modes

- **Overview bar** — pointer-down + move → seeks to absolute position.
- **Detail strip** — pointer-down + move → pans (shifts `scrubMs` so the
  pointed-to time stays under the cursor, like a camera pan).
- Both use `window` pointer-move / pointer-up listeners during drag so the
  mouse can leave the element without releasing.

---

## Component Structure (file layout)

```
src/pages/
  TimelineTest.tsx          ← page component (this PR)
  TimelineTest.module.css   ← styles
```

Internal sub-components (all in the same file for now):

- `OverviewBar` — the top ruler + zoom-window SVG + playhead.
- `DetailStrip` — the zoomed, pannable track area.
- `TimelineTest` (default export) — fetches data, owns all state, renders both.

The data-fetching logic and state shape are intentionally designed so that in a
future refactor the two strip components can accept props and be used inside
any layout panel (Dockview, etc.).

---

## Key Concepts from Colleague's Code (adapted)

| Concept                   | Colleague's code                                       | Our adaptation                                   |
| ------------------------- | ------------------------------------------------------ | ------------------------------------------------ |
| Two-tier layout           | `timeline-shell` + `crew-timeline-shell`               | `OverviewBar` + `DetailStrip`                    |
| SVG bezier connectors     | `leftConnectorPath` / `rightConnectorPath`             | Same math, drawn as `<svg>` inside `OverviewBar` |
| Zoom window highlight     | `timeline-zoom-window` div + `zoomFillCutoutPath` mask | `<rect>` in SVG overlay                          |
| Drag to seek              | `updateFromClientX`                                    | `handleOverviewPointerMove`                      |
| Drag to pan               | `updateCrewFromClientX` (delta-based)                  | `handleDetailPointerMove`                        |
| Adaptive tick labels      | `crewHourMarkers` useMemo                              | `detailTicks` useMemo                            |
| Asset availability tracks | `zoomAssetTracks` useMemo                              | `photoMarkers` + `commSegments` useMemo          |

---

## Styling Approach

Reuse the dark-panel design language from `TrajectoryTest.module.css`:

- `#050810` page background, `Roboto Mono` font.
- `#0e1726` card/bar backgrounds, `#1f3052` borders.
- `#6e9aff` accent / highlights.

The two bars are full-width (`calc(100% + 3rem)` with negative left margin)
matching the canvas treatment in TrajectoryTest2.

---

## File Dependencies

- No new npm packages required — everything available already.
- `src/types/photos.ts` — `Photo` type (re-used from PhotoTest).
- CSS modules only (no global CSS additions).
- Route added to `src/index.tsx`: `<Route path="timeline-test" element={<TimelineTest />} />`.
