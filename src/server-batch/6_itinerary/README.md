# 6_itinerary — Mission event timeline

Builds `web/itinerary.json` — the mission event timeline consumed by the
trajectory visualization UI for event tick marks and the Last/Next event
HUD display.

## Script

Run with `uv run 6a_web_itinerary.py --mission <slug>` from this folder.
Slugs: `artemis-i`, `artemis-ii`.

### 6a_web_itinerary.py

Sources:

- **Pre-flight**: NASA/JSC FOD "Artemis II Overview Timeline - FINAL" (public,
  2026-01-08). All MET values parsed from the PDF; UTC computed as
  `launch_utc + timedelta(MET)`.
- **As-flown confirmations**: NASA Artemis blog posts (April 2026). Confirmed
  times used for: RTC-2, RTC-3, CM/SM Sep, Entry Interface, Splashdown, and
  maximum Earth distance value.

As-flown EDL events are consistently ~14–15 minutes earlier than the
pre-flight plan (actual splashdown 00:07Z vs planned 00:21Z). The last OEM
point (`2026-04-10T23:53:16Z`) equals the confirmed EI time exactly.

Output: **22 events** for A2 (7.7 KB), **8 events** for A1 (2.8 KB).

Event fields: `id`, `name`, `description`, `t` (ISO UTC), `met_s`
(seconds since launch), `confidence` (`"confirmed"` / `"nominal"`),
`category`, `color` (hex).

Only events whose `t` falls within `[coverage_start_utc, coverage_end_utc]`
are shown as tick marks on the timeline slider. Events outside coverage
(launch, splashdown) still appear in the Last/Next HUD display.

---

## Output schema (itinerary.json)

```jsonc
{
  "mission_id": "artemis-ii",
  "launch_utc": "2026-04-01T22:35:00Z",
  "splashdown_utc": "2026-04-11T00:07:00Z",
  "source_preflight_pdf": "https://www.nasa.gov/wp-content/uploads/2026/01/...",
  "source_asflown": "NASA Artemis blog posts, April 2026",
  "events": [
    {
      "id": "tli",
      "name": "Trans-Lunar Injection (TLI)",
      "description": "ICPS re-ignition sends Orion onto a free-return trajectory.",
      "t": "2026-04-03T00:12:12Z",    // UTC timestamp
      "met_s": 92532,                  // seconds since launch_utc
      "confidence": "nominal",         // "confirmed" | "nominal" | "approximate"
      "category": "tli",
      "color": "#ef476f"
    },
    ...
  ]
}
```

Events are sorted ascending by `t`. `confidence = "confirmed"` means the time
was sourced from a NASA blog post or official NASA statement (not the
pre-flight plan).

---

## Adding a new mission

1. Add a `MissionConfig` entry in `../config.py` with `launch_utc` and
   `splashdown_utc` set.
2. Add an events list in `_EVENTS` inside `6a_web_itinerary.py`.
3. Run step 6a.
