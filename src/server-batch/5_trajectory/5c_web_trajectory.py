"""Step 5c — Build the trajectory.json consumed by the web frontend.

Joins `orion_track.jsonl` + `moon_track.jsonl`, attaches the phase timeline,
and emits a compact JSON file.

Output: `web/ephemeris/trajectory.json`

Schema (compact, parallel arrays — keeps the wire format small):
```
{
  "mission_id": "artemis-ii",
  "mission_name": "Artemis II",
  "frame": "EME2000",
  "launch_utc": "...", "splashdown_utc": "...",
  "coverage_start_utc": "...", "coverage_end_utc": "...",
  "earth_radius_km": 6378.137,
  "moon_radius_km": 1737.4,
  "phases": [ { "name": "...", "t": "iso", "color": "#xxxxxx" }, ... ],
  "notes": [ "...", ... ],
  "points": {
    "t":           ["iso", ...],   // length N
    "ox":          [number, ...],  // Orion X (km)  — same for oy, oz
    "speed_earth": [number, ...],  // |v_orion| km/s  (velocity relative to Earth)
    "speed_moon":  [number, ...],  // |v_orion − v_moon| km/s (velocity relative to Moon)
    "mx":          [number, ...],  // Moon  X (km)  — same for my, mz
    "my":          [number, ...],
    "mz":          [number, ...]
  }
}
```

Numbers are rounded for compactness (positions to 1 m, speeds to 1 mm/s).
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig  # noqa: E402

EARTH_RADIUS_KM = 6378.137
MOON_RADIUS_KM = 1737.4


# Phase timelines. Sourced from issinfo.net/artemis missions endpoint
# (post-flight values for Artemis I & II), which was cross-referenced against
# the preflight NASA Artemis II Overview Timeline (PDF, Jan 2026). Phase start
# times are expressed as ISO-8601 UTC.
#
# Artemis II: launch 2026-04-01T22:35:12Z + start_day offsets
#   Launch & Ascent       0.000  d
#   Earth Orbit & Checkout 0.035 d ( + ~50 min)
#   Trans-Lunar Injection 1.051  d
#   Outbound Coast        1.055  d
#   Lunar Flyby           4.8061 d
#   Return Coast          5.7846 d
#   Re-entry & Splashdown 9.01175 d
# Artemis I: launch 2022-11-16T06:47:44Z + start_day offsets
#   Launch & Ascent       0.000
#   Trans-Lunar Injection 0.063
#   Outbound Coast        0.083
#   Lunar Flyby           5.250
#   Distant Retrograde    9.630
#   DRO Exit              15.630
#   Return Coast          19.410
#   Re-entry & Splashdown 25.397962962962964

_PHASES: dict[str, list[tuple[str, float, str]]] = {
    "artemis-i": [
        ("Launch & Ascent",          0.000,   "#ff6b35"),
        ("Trans-Lunar Injection",    0.063,   "#ffd166"),
        ("Outbound Coast",           0.083,   "#06d6a0"),
        ("Lunar Flyby",              5.250,   "#118ab2"),
        ("Distant Retrograde Orbit", 9.630,   "#7b2ff7"),
        ("DRO Exit",                 15.630,  "#e056a0"),
        ("Return Coast",             19.410,  "#06d6a0"),
        ("Re-entry & Splashdown",    25.398,  "#ff6b35"),
    ],
    "artemis-ii": [
        ("Launch & Ascent",          0.000,   "#ff6b35"),
        ("Earth Orbit & Checkout",   0.035,   "#ffd166"),
        ("Trans-Lunar Injection",    1.051,   "#ef476f"),
        ("Outbound Coast",           1.055,   "#06d6a0"),
        ("Lunar Flyby",              4.8061,  "#a78bfa"),
        ("Return Coast",             5.7846,  "#38bdf8"),
        ("Re-entry & Splashdown",    9.01175, "#f97316"),
    ],
}


def _add_days(iso_utc: str, days: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_phases(mission: MissionConfig) -> list[dict]:
    if not mission.launch_utc or mission.slug not in _PHASES:
        return []
    return [
        {"name": name, "t": _add_days(mission.launch_utc, off), "color": color}
        for (name, off, color) in _PHASES[mission.slug]
    ]


def _round_pos(v: float) -> float:
    # 3 decimal places (km) = 1 m resolution. Plenty for a 2-D plot.
    return round(v, 3)


def _round_vel(v: float) -> float:
    # 6 decimal places (km/s) = 1 mm/s. Used for speed/altitude HUD.
    return round(v, 6)


def _load_jsonl(p: Path) -> list[dict]:
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _downsample(orion: list[dict], moon: list[dict], coast_step_s: float = 300.0, burn_gap_s: float = 30.0) -> tuple[list[dict], list[dict]]:
    """Adaptive thinning that preserves OEM density around burns.

    Rules:
      * Always keep the first and last point.
      * Always keep a point whose *source* gap to the previous original point
        is < ``burn_gap_s`` — those are inside burns / critical events and the
        OEM is intentionally dense there.
      * Otherwise (coast), keep at most one point every ``coast_step_s``.
    """
    from datetime import datetime, timezone

    def parse(t: str) -> datetime:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    if not orion:
        return orion, moon

    kept_o, kept_m = [orion[0]], [moon[0]]
    last_kept_t = parse(orion[0]["t"])
    last_src_t = last_kept_t
    for i in range(1, len(orion) - 1):
        t = parse(orion[i]["t"])
        src_gap = (t - last_src_t).total_seconds()
        last_src_t = t
        kept_gap = (t - last_kept_t).total_seconds()
        is_burn = src_gap < burn_gap_s
        if is_burn or kept_gap >= coast_step_s:
            kept_o.append(orion[i])
            kept_m.append(moon[i])
            last_kept_t = t
    kept_o.append(orion[-1])
    kept_m.append(moon[-1])
    return kept_o, kept_m


def build_web(mission: MissionConfig, coast_step_s: float = 300.0) -> Path:
    orion = _load_jsonl(mission.processed_ephemeris / "orion_track.jsonl")
    moon = _load_jsonl(mission.processed_ephemeris / "moon_track.jsonl")
    if len(orion) != len(moon):
        raise SystemExit(f"orion/moon length mismatch: {len(orion)} vs {len(moon)}")

    n_raw = len(orion)
    orion, moon = _downsample(orion, moon, coast_step_s=coast_step_s)
    print(f"  downsampled {n_raw} → {len(orion)} points (coast step {coast_step_s:.0f}s)")

    # Sanity: timestamps must line up.
    for i, (o, m) in enumerate(zip(orion, moon)):
        if o["t"] != m["t"]:
            raise SystemExit(f"Timestamp mismatch at row {i}: {o['t']} vs {m['t']}")

    notes: list[str] = []
    if mission.launch_utc:
        coverage_start = orion[0]["t"]
        gap = _seconds(coverage_start, mission.launch_utc)
        if gap > 60:
            mins = int(round(gap / 60))
            notes.append(
                f"Ephemeris begins ~{mins} min after launch — the parking-orbit "
                "segment before Trans-Lunar Injection is not in the public OEMs."
            )
    if mission.splashdown_utc:
        coverage_end = orion[-1]["t"]
        gap = _seconds(mission.splashdown_utc, coverage_end)
        if gap > 60:
            mins = int(round(abs(gap) / 60))
            notes.append(
                f"Ephemeris ends ~{mins} min before splashdown — the atmospheric "
                "re-entry and descent segment is not covered by the public ephemeris."
            )

    payload = {
        "mission_id": mission.slug,
        "mission_name": mission.name,
        "frame": "EME2000",
        "frame_note": (
            "Earth-centered inertial, J2000 equatorial. The top-down view "
            "projects onto the X-Y plane; +Z is Earth's mean equatorial pole "
            "at J2000 (~23.4° off the ecliptic pole)."
        ),
        "earth_radius_km": EARTH_RADIUS_KM,
        "moon_radius_km": MOON_RADIUS_KM,
        "launch_utc": mission.launch_utc,
        "splashdown_utc": mission.splashdown_utc,
        "coverage_start_utc": orion[0]["t"],
        "coverage_end_utc": orion[-1]["t"],
        "phases": build_phases(mission),
        "notes": notes,
        "max_distance_km": round(
            max(math.sqrt(o["x"] ** 2 + o["y"] ** 2 + o["z"] ** 2) for o in orion), 1
        ),
        "n_points": len(orion),
        "points": {
            "t":  [o["t"] for o in orion],
            "ox": [_round_pos(o["x"])  for o in orion],
            "oy": [_round_pos(o["y"])  for o in orion],
            "oz": [_round_pos(o["z"])  for o in orion],
            "speed_earth": [
                _round_vel(math.sqrt(o["vx"]**2 + o["vy"]**2 + o["vz"]**2))
                for o in orion
            ],
            "speed_moon": [
                _round_vel(math.sqrt(
                    (o["vx"] - m["vx"])**2 +
                    (o["vy"] - m["vy"])**2 +
                    (o["vz"] - m["vz"])**2
                ))
                for o, m in zip(orion, moon)
            ],
            "mx": [_round_pos(m["x"])  for m in moon],
            "my": [_round_pos(m["y"])  for m in moon],
            "mz": [_round_pos(m["z"])  for m in moon],
        },
    }

    out_dir = mission.web_ephemeris
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trajectory.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    size_kb = out_path.stat().st_size / 1024
    print(f"  → wrote {len(orion)} points to {out_path}  ({size_kb:,.0f} KB)")
    for n in notes:
        print(f"  note: {n}")
    return out_path


def _seconds(a: str, b: str) -> float:
    from datetime import datetime, timezone
    def p(s: str) -> datetime:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return (p(b) - p(a)).total_seconds()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build web trajectory.json.")
    ap.add_argument("--mission", required=True, choices=list(MISSIONS))
    args = ap.parse_args()
    print(f"=== Building web trajectory.json for {args.mission} ===")
    build_web(MISSIONS[args.mission])


if __name__ == "__main__":
    main()
