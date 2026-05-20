"""6a_web_itinerary.py — Build web/itinerary.json

Sources
-------
Pre-flight:
  NASA/JSC FOD "Artemis II Overview Timeline - FINAL" (public, 2026-01-08)
  https://www.nasa.gov/wp-content/uploads/2026/01/artemis-ii-overview-timeline-public-final.pdf

As-flown confirmations from NASA Artemis blog posts (April 2026):
  https://www.nasa.gov/blogs/artemis/

Usage
-----
    uv run 6_itinerary/6a_web_itinerary.py --mission artemis-ii
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import MISSIONS

# ── MET → UTC helper ─────────────────────────────────────────────────────────

def met(launch_utc: str, days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0) -> str:
    """Convert MET (mission elapsed time) to UTC ISO string."""
    t = datetime.fromisoformat(launch_utc.replace("Z", "+00:00"))
    t += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Per-mission event definitions ─────────────────────────────────────────────

_EVENTS: dict[str, list[dict]] = {}

# ── Artemis II ────────────────────────────────────────────────────────────────
# Pre-flight times from the NASA/JSC FOD public timeline PDF (2026-01-08).
# MET format: D/HH:MM[:SS] — day-offset / hours:minutes[:seconds] after launch.
# As-flown confirmations (tagged "confirmed") sourced from NASA blog posts.

_A2_LAUNCH = "2026-04-01T22:35:00Z"

_A2_RAW: list[dict] = [
    # ── Ascent (pre-OEM coverage) ─────────────────────────────────────────────
    dict(id="launch",       name="Launch",
         met_d=0, met_h=0,  met_m=0,  met_s=0,
         confidence="confirmed",  # 6:35 PM EDT = 22:35 UTC
         category="ascent",  color="#ff6b35",
         description="SLS/Orion liftoff from LC-39B, Kennedy Space Center, FL."),
    dict(id="icps_prm",     name="ICPS Perigee Raise Maneuver",
         met_d=0, met_h=0,  met_m=50, met_s=0,
         confidence="nominal",
         category="ascent",  color="#ff6b35",
         description="ICPS performs the perigee raise burn to set up HEO insertion."),
    dict(id="arb_tig",      name="ARB Ignition (ICPS-2)",
         met_d=0, met_h=1,  met_m=48, met_s=0,
         confidence="nominal",
         category="ascent",  color="#ff6b35",
         description="Second ICPS burn — apogee raise. Orion enters high Earth orbit (HEO)."),
    dict(id="orion_icps_sep", name="Orion/ICPS Separation",
         met_d=0, met_h=3,  met_m=24, met_s=0,
         confidence="nominal",
         category="ascent",  color="#ffd166",
         description="Orion spring-separates from ICPS upper stage."),
    dict(id="orion_uss",    name="Orion Umbilical Sep (USS)",
         met_d=0, met_h=4,  met_m=52, met_s=0,
         confidence="nominal",
         category="heo",    color="#ffd166",
         description="Orion umbilical separator fires; spacecraft is fully independent."),
    dict(id="icps_disposal", name="ICPS Disposal Burn",
         met_d=0, met_h=5,  met_m=2,  met_s=0,
         confidence="nominal",
         category="heo",    color="#666",
         description="ICPS performs disposal burn to heliocentric orbit."),
    # ── High Earth Orbit ───────────────────────────────────────────────────────
    dict(id="orion_prb",    name="Orion Propulsion Re-pressurization",
         met_d=0, met_h=13, met_m=45, met_s=0,
         confidence="nominal",
         category="heo",    color="#ffd166",
         description="Orion propulsion system re-pressurization burn (PRB)."),
    # ── Trans-Lunar Injection ─────────────────────────────────────────────────
    dict(id="tli",          name="Trans-Lunar Injection (TLI)",
         met_d=1, met_h=1,  met_m=37, met_s=0,
         confidence="nominal",  # as-flown: "April 2 EDT" = April 3 UTC, consistent with nominal
         category="tli",    color="#ef476f",
         description="ICPS re-ignition sends Orion onto a free-return trajectory to the Moon."),
    # ── Outbound coast ─────────────────────────────────────────────────────────
    dict(id="otc1",         name="Outbound Trajectory Correction 1 (OTC-1)",
         met_d=2, met_h=0,  met_m=7,  met_s=0,
         confidence="nominal",
         category="trans_lunar", color="#06d6a0",
         description="First small thruster firing to refine the lunar approach trajectory."),
    dict(id="otc2",         name="Outbound Trajectory Correction 2 (OTC-2)",
         met_d=3, met_h=0,  met_m=12, met_s=0,
         confidence="nominal",
         category="trans_lunar", color="#06d6a0",
         description="Second trajectory correction burn during outbound coast."),
    dict(id="otc3",         name="Outbound Trajectory Correction 3 (OTC-3)",
         met_d=4, met_h=5,  met_m=23, met_s=0,
         confidence="nominal",
         category="lunar_approach", color="#06d6a0",
         description="Final outbound correction burn prior to lunar sphere of influence entry."),
    # ── Lunar approach ─────────────────────────────────────────────────────────
    dict(id="lunar_soi_entry", name="Lunar Sphere of Influence Entry",
         met_d=4, met_h=6,  met_m=59, met_s=0,
         confidence="nominal",
         category="lunar_approach", color="#a78bfa",
         description="Orion enters the region where the Moon's gravity dominates (~66,000 km from Moon)."),
    dict(id="apollo13_record", name="Apollo 13 Distance Record Exceeded",
         met_d=4, met_h=21, met_m=2,  met_s=0,
         confidence="nominal",
         category="lunar_flyby", color="#ffd166",
         description="Orion surpasses 400,171 km — the previous human spaceflight distance record set by Apollo 13 in 1970."),
    # ── Lunar flyby ────────────────────────────────────────────────────────────
    dict(id="perilune",     name="Lunar Close Approach (Perilune)",
         met_d=5, met_h=1,  met_m=23, met_s=20,
         confidence="nominal",
         category="lunar_flyby", color="#a78bfa",
         description="Closest approach to the Moon — approximately 7,600 km above the surface."),
    dict(id="max_earth_dist", name="Maximum Earth Distance",
         met_d=5, met_h=1,  met_m=26, met_s=57,
         confidence="confirmed",  # as-flown: 252,756 mi = 406,726 km
         category="lunar_flyby", color="#ffd166",
         description="Farthest point from Earth: 252,756 miles (406,726 km). New human distance record."),
    # ── Return coast ───────────────────────────────────────────────────────────
    dict(id="lunar_soi_exit", name="Lunar Sphere of Influence Exit",
         met_d=5, met_h=19, met_m=47, met_s=0,
         confidence="nominal",
         category="trans_earth", color="#38bdf8",
         description="Orion exits the Moon's gravitational sphere of influence, now Earth-bound."),
    dict(id="rtc1",         name="Return Trajectory Correction 1 (RTC-1)",
         met_d=6, met_h=4,  met_m=23, met_s=0,
         confidence="nominal",
         category="trans_earth", color="#38bdf8",
         description="First return burn to refine the re-entry corridor."),
    # ── EDL ────────────────────────────────────────────────────────────────────
    # RTC-2 and RTC-3 are confirmed from NASA blog post times (as-flown).
    dict(id="rtc2",         name="Return Trajectory Correction 2 (RTC-2)",
         t_override="2026-04-10T02:53:00Z",  # confirmed: 10:53 PM EDT Apr 9
         confidence="confirmed",
         category="trans_earth", color="#38bdf8",
         description="Second return burn (9 s, +5.3 ft/s). As-flown 10:53 PM EDT April 9."),
    dict(id="rtc3",         name="Return Trajectory Correction 3 (RTC-3)",
         t_override="2026-04-10T18:53:00Z",  # confirmed: 2:53 PM EDT Apr 10
         confidence="confirmed",
         category="edl",    color="#f97316",
         description="Final return burn (8 s, +4.2 ft/s). As-flown 2:53 PM EDT April 10."),
    dict(id="cm_sm_sep",    name="CM/SM Separation",
         t_override="2026-04-10T23:33:00Z",  # confirmed: 7:33 PM EDT Apr 10
         confidence="confirmed",
         category="edl",    color="#f97316",
         description="Crew Module separates from the Service Module over the Pacific Ocean."),
    dict(id="entry_interface", name="Entry Interface (EI)",
         t_override="2026-04-10T23:53:00Z",  # confirmed: 7:53 PM EDT Apr 10 (= last OEM point)
         confidence="confirmed",
         category="edl",    color="#f97316",
         description="Orion reaches 400,000 ft above Earth traveling at Mach 35. Communications blackout begins."),
    dict(id="splashdown",   name="Splashdown",
         t_override="2026-04-11T00:07:00Z",  # confirmed: 8:07 PM EDT Apr 10
         confidence="confirmed",
         category="edl",    color="#ff6b35",
         description="Orion splashes down in the Pacific Ocean off San Diego. Mission duration: 9d 1h 32m."),
]

_EVENTS["artemis-ii"] = _A2_RAW


# ── Artemis I ────────────────────────────────────────────────────────────────
# Artemis I had no crew. Key milestones from NASA/Wikipedia.
_A1_LAUNCH = "2022-11-16T06:47:44Z"

_EVENTS["artemis-i"] = [
    dict(id="launch", name="Launch",
         met_d=0, met_h=0, met_m=0, met_s=0,
         confidence="confirmed",
         category="ascent", color="#ff6b35",
         description="SLS/Orion (uncrewed) liftoff from LC-39B, Kennedy Space Center, FL."),
    dict(id="tli", name="Trans-Lunar Injection (TLI)",
         met_d=0, met_h=1, met_m=28, met_s=0,
         confidence="nominal",
         category="tli", color="#ef476f",
         description="ICPS sends Orion onto the outbound lunar trajectory."),
    dict(id="lunar_flyby_close", name="Lunar Close Approach",
         met_d=5, met_h=23, met_m=0, met_s=0,
         confidence="nominal",
         category="lunar_flyby", color="#a78bfa",
         description="Closest approach to the Moon — approximately 130 km above the surface."),
    dict(id="dro_insertion", name="DRO Insertion Burn",
         met_d=6, met_h=2, met_m=0, met_s=0,
         confidence="nominal",
         category="lunar_orbit", color="#a78bfa",
         description="Orion enters a distant retrograde orbit (DRO) around the Moon."),
    dict(id="max_earth_dist", name="Maximum Earth Distance",
         met_d=13, met_h=0, met_m=0, met_s=0,
         confidence="nominal",
         category="lunar_orbit", color="#ffd166",
         description="Farthest point from Earth: approximately 432,210 km."),
    dict(id="dro_exit", name="DRO Departure Burn",
         met_d=19, met_h=0, met_m=0, met_s=0,
         confidence="nominal",
         category="trans_earth", color="#38bdf8",
         description="Orion departs the distant retrograde orbit and heads home."),
    dict(id="cm_sm_sep", name="CM/SM Separation",
         met_d=25, met_h=11, met_m=0, met_s=0,
         confidence="nominal",
         category="edl", color="#f97316",
         description="Crew Module separates from Service Module prior to re-entry."),
    dict(id="splashdown", name="Splashdown",
         met_d=25, met_h=11, met_m=53, met_s=0,
         confidence="nominal",
         category="edl", color="#ff6b35",
         description="Orion splashes down in the Pacific Ocean off San Diego. Mission duration: 25d 10h 53m."),
]


# ── Build ─────────────────────────────────────────────────────────────────────

def build_itinerary(mission_id: str) -> dict:
    if mission_id not in MISSIONS:
        raise SystemExit(f"Unknown mission: {mission_id}")
    mission = MISSIONS[mission_id]
    raw = _EVENTS.get(mission_id, [])
    if not raw:
        raise SystemExit(f"No itinerary events defined for {mission_id}")

    launch_utc: str = mission.launch_utc or ""
    if not launch_utc:
        raise SystemExit(f"Mission {mission_id} has no launch_utc set in config.py")

    events: list[dict] = []
    for ev in raw:
        row = dict(ev)
        # Resolve timestamp
        if "t_override" in row:
            t = row.pop("t_override")
        else:
            t = met(
                launch_utc,
                days=row.pop("met_d", 0),
                hours=row.pop("met_h", 0),
                minutes=row.pop("met_m", 0),
                seconds=row.pop("met_s", 0),
            )
        row["t"] = t
        # Compute MET in whole seconds for convenience
        launch_dt = datetime.fromisoformat(launch_utc.replace("Z", "+00:00"))
        event_dt  = datetime.fromisoformat(t.replace("Z", "+00:00"))
        row["met_s"] = int((event_dt - launch_dt).total_seconds())
        events.append(row)

    events.sort(key=lambda e: e["t"])

    return {
        "mission_id": mission_id,
        "mission_name": mission.name,
        "launch_utc": launch_utc,
        "splashdown_utc": mission.splashdown_utc,
        "total_distance_km": 694_481 * 1.60934 if mission_id == "artemis-ii" else None,
        "source_preflight_pdf": (
            "https://www.nasa.gov/wp-content/uploads/2026/01/"
            "artemis-ii-overview-timeline-public-final.pdf"
        ) if mission_id == "artemis-ii" else None,
        "source_asflown": (
            "NASA Artemis blog posts, April 2026 "
            "(https://www.nasa.gov/blogs/artemis/)"
        ) if mission_id == "artemis-ii" else "NASA mission pages / Wikipedia",
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build web/itinerary.json")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    data = build_itinerary(args.mission)

    out_path = mission.web_dir / "itinerary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {len(data['events'])} events → {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
