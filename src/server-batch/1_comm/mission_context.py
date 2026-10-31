"""Mission reference events, pre-flight PDF watch-list, and crew attribution helpers.

Combines:
1. web/itinerary.json (primary trajectory milestones)
2. Pre-mission Overview Timeline PDF blocks & DFTOs (pdfs/artemis-ii-overview-timeline-public-final.pdf)
   — treated as AS-PLANNED watch items (not as-executed ground truth) with a wider search window
3. Preliminary Lunar Science Report & Data User Guide Table 4 (as-flown science sessions)
4. Lunar Targeting Package (LTP) Target 1..30 windows on Flight Day 6
5. web/photos.json (in-flight photo captions + crew camera photo bursts)
6. web/videoYt.json & web/videoPDS.json (broadcasts, news conferences, SAW3 clips)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import MissionConfig

LAUNCH_UTC = "2026-04-01T22:35:12Z"
EVENT_REFERENCE_KINDS = {"FLIGHT_PLAN", "PLANNED_BLOCK", "SCIENCE_REPORT", "LTP_TARGET"}

CREW_SLUGS = ("wiseman", "glover", "koch", "hansen")
CREW_INFO = {
    "wiseman": {"name": "Reid Wiseman", "role": "Commander (CDR)"},
    "glover": {"name": "Victor Glover", "role": "Pilot (PLT)"},
    "koch": {"name": "Christina Koch", "role": "Mission Specialist 1 (MS1)"},
    "hansen": {"name": "Jeremy Hansen", "role": "Mission Specialist 2 (MS2)"},
}

_CREW_PATTERNS: dict[str, re.Pattern[str]] = {
    "wiseman": re.compile(r"\b(?:reid|wiseman|commander)\b", re.IGNORECASE),
    "glover": re.compile(r"\b(?:victor|glover|ike)\b", re.IGNORECASE),
    "koch": re.compile(r"\b(?:christina|koch)\b", re.IGNORECASE),
    "hansen": re.compile(r"\b(?:jeremy|hansen)\b", re.IGNORECASE),
}
_ALL_CREW_RE = re.compile(
    r"\b(?:all four|four of them|the crew|crew walkout|crew boards|crew arrival)\b",
    re.IGNORECASE,
)


def infer_crew_members(text: str) -> list[str]:
    """Infer participating crewmember slugs from text when explicitly mentioned."""
    if not text:
        return []
    matched = [slug for slug, pat in _CREW_PATTERNS.items() if pat.search(text)]
    if matched:
        return matched
    if _ALL_CREW_RE.search(text):
        return ["all"]
    return []


def normalize_crew_list(raw: object) -> list[str]:
    """Normalize an optional crew list into valid crew slugs or ['all']."""
    if not isinstance(raw, list):
        return []
    allowed = set(CREW_SLUGS) | {"all"}
    out: list[str] = []
    for item in raw:
        val = str(item).strip().lower()
        if val in ("reid", "reid wiseman", "cdr", "commander"):
            val = "wiseman"
        elif val in ("victor", "victor glover", "plt", "pilot"):
            val = "glover"
        elif val in ("christina", "christina koch", "ms1"):
            val = "koch"
        elif val in ("jeremy", "jeremy hansen", "ms2"):
            val = "hansen"
        elif val in ("crew", "everyone", "all_crew"):
            val = "all"
        if val in allowed and val not in out:
            out.append(val)
    if "all" in out and len(out) > 1:
        out = [c for c in out if c != "all"]
    return out


def _is_event_reference(event: dict) -> bool:
    return event["kind"].startswith("ITINERARY") or event["kind"] in EVENT_REFERENCE_KINDS


def _parse_ts(utc_str: str) -> float:
    return datetime.fromisoformat(utc_str.replace("Z", "+00:00")).timestamp()


def _met_utc(days: int, hours: int, minutes: int = 0, seconds: int = 0) -> str:
    dt = datetime.fromisoformat(LAUNCH_UTC.replace("Z", "+00:00"))
    dt += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Point milestones & DFTO callouts from pdfs/artemis-ii-overview-timeline-public-final.pdf (Pre-Mission As-Planned)
OVERVIEW_TIMELINE_EVENTS = [
    # FD01
    ("cabin_depress_dfto", _met_utc(0, 0, 30), "Start Cabin Depress DFTO 3, 2"),
    ("doff_ocss", _met_utc(0, 2, 0), "Doff OCSS survival suits & DCAM setup"),
    ("prox_ops_demo", _met_utc(0, 3, 24), "Proximity Operations Demo (manual piloting near ICPS)"),
    ("dock_cam_bearing_fto", _met_utc(0, 4, 57), "Dock Cam Bearing FTO 225"),
    ("spl_deploys", _met_utc(0, 5, 27), "SPL Deploys"),
    ("fd01_pao_event", _met_utc(0, 6, 30), "FD01 PAO Event & Flight Director Conf"),
    ("dock_cam_misalign_1", _met_utc(0, 8, 40), "Dock Cam Misalign DFTO-EM2-03"),
    ("opcomm_init_act", _met_utc(0, 10, 6), "Optical Comm (OpComm) Initial Activation"),
    ("opnav_checkout", _met_utc(0, 13, 56), "OpNav Checkout & DSN pass"),
    # FD02
    ("dock_cam_asc_uninstall", _met_utc(0, 20, 30), "Dock Cam & Ascent Bracket Uninstall"),
    ("eclss_co2_dfto", _met_utc(0, 21, 0), "HEO Sys Checkouts, Exercise Test & ECLSS CO2 Monitor DFTO"),
    ("m42_natgeo_su", _met_utc(0, 23, 0), "M-42 Radiation Detector EXT & NatGeo Setup"),
    ("hera_cmd_dfto", _met_utc(1, 0, 0), "HERA Radiation Command DFTO-EM2-09 & Config Transit"),
    ("fd02_pao_post_tli", _met_utc(1, 3, 30), "Post-TLI PAO Event"),
    ("window_inspect_saw_wifi", _met_utc(1, 5, 30), "Window Inspection & SAW 1/3 Cam WiFi DFTO"),
    ("cm_sm_survey_1", _met_utc(1, 12, 0), "CM/SM Exterior Survey"),
    # FD03
    ("flywheel_day_in_life", _met_utc(1, 21, 0), "Flywheel Exercise 'Day in the Life' & OpNav"),
    ("otc1_canceled", _met_utc(2, 0, 7), "Planned OTC-1 Window (Burn Canceled due to accurate TLI)"),
    ("cpr_demo_med_kit", _met_utc(2, 3, 0), "CPR Demo & Medical Kit Drill"),
    ("lunar_cab_emer_comm", _met_utc(2, 6, 30), "Lunar Cabin Config, DSN Emergency Comm DFTO-EM2-22 & D5 Cam Window"),
    ("csa_pao_fd03", _met_utc(2, 8, 0), "CSA PAO Event"),
    ("saws_full_shadow", _met_utc(2, 11, 30), "SAWs in Full Shadow"),
    # FD04
    ("opcomm_anu_demo_1", _met_utc(2, 18, 30), "OpComm ANU Demo"),
    ("acoustic_dfto_start", _met_utc(2, 20, 30), "Start 24-Hr Acoustic DFTO 7"),
    ("pwd_ptv_exercise", _met_utc(2, 23, 0), "Potable Water Dispenser (PWD) & P/TV Exercise"),
    ("doc_cam_measure_csa_vip", _met_utc(3, 3, 0), "Dock Cam Measure, CSA VIP Call & Lunar Imaging Review"),
    ("min_exercise_dfto_fd04", _met_utc(3, 5, 0), "Min. Exercise DFTO & Lunar Img"),
    ("nav_fto_em2_223", _met_utc(3, 6, 30), "NAV FTO EM2-223"),
    ("opcomm_cfdp_demo", _met_utc(3, 12, 0), "OpComm Class 1 CFDP Demo & Stray Light DFTO-EM-04"),
    ("dock_cam_misalign_2", _met_utc(3, 15, 30), "Dock Cam Misalign DFTO & OpComm Activation"),
    # FD05
    ("ocss_suit_dfto", _met_utc(3, 21, 30), "OCSS Suit DFTO Ops & End 24-Hr Acoustic DFTO 7"),
    ("cabin_depress_10_2psi", _met_utc(4, 1, 30), "Cabin Depress Ops (Vent to 10.2 psi)"),
    ("sat_mode_test_dfto", _met_utc(4, 5, 45), "SAT Mode Test DFTO-EM2-23 & CSA PAO"),
    ("dock_cam_misalign_fd05", _met_utc(4, 12, 30), "Dock Cam Misalign DFTO-EM2-03 & OpComm ANU Demo"),
    # FD06
    ("lunar_flyby_prep", _met_utc(4, 21, 0), "Lunar Observation Config & NatGeo"),
    ("lunar_doc_xfer_pao", _met_utc(5, 3, 0), "Lunar Documentation/Transfer, PAO & Exercise Noise"),
    ("cm_sm_survey_2", _met_utc(5, 12, 45), "CM/SM Survey"),
    # FD07
    ("min_eclss_exercise_dfto", _met_utc(5, 22, 0), "P/TV Exercise & Min ECLSS Exercise (DFTO-EM2-16)"),
    ("post_lunar_debrief", _met_utc(6, 5, 30), "Post-Lunar Crew Debrief"),
    ("dock_cam_ww_dump_fto", _met_utc(6, 11, 0), "Dock Cam Wastewater (WW) Dump View FTO"),
    # FD08
    ("ccu_repress_14_7psi", _met_utc(6, 20, 0), "CCU, P/TV Exercise & Cabin Repress to 14.7 psi"),
    ("modal_survey_dfto", _met_utc(7, 0, 30), "Modal Survey & No-PCD DFTO-EM2-21"),
    ("urine_venting_dfto", _met_utc(7, 2, 30), "Urine Venting DFTO-EM2-26 & Radiation Shelter Demo"),
    ("manual_piloting_dfto", _met_utc(7, 5, 0), "Manual Piloting DFTO & Crew-to-Crew (C2C) Call"),
    ("ei_32hr_stray_light", _met_utc(7, 13, 0), "Dock Cam Misalign/Stray Light DFTO & EI-32hr"),
    ("fcs_checkouts", _met_utc(7, 17, 30), "Flight Control System (FCS) Checkouts"),
    # FD09
    ("entry_study_oig_don_1", _met_utc(7, 20, 30), "Entry Study, Entry Conf & OIG Suit Donning DFTO"),
    ("oig_don_entry_stow", _met_utc(8, 2, 30), "OIG Donning DFTO & Entry Stowage"),
    ("onwm_s_checkout", _met_utc(8, 5, 0), "ONWM S Checkout & Entry Stow"),
    # FD10
    ("fd10_cabin_entry_config", _met_utc(8, 19, 30), "FD10 Cabin Config, +X Leak Detection & Entry Checklist"),
]

# Contiguous Flight Day schedule blocks from pdfs/artemis-ii-overview-timeline-public-final.pdf
# Format: (id, fd, phase, start_utc, end_utc, category, title, description)
OVERVIEW_TIMELINE_BLOCKS = [
    # FD01
    ("fd01_prelaunch", "FD01", "Prelaunch", "2026-04-01T15:35:12Z", _met_utc(0, 0, 0), "milestone",
     "Prelaunch & Crew Ingress", "Tank loading, crew walkout, astrovan to Pad 39B, ingress, hatch closure, countdown"),
    ("fd01_ascent_leo", "FD01", "Ascent / LEO", _met_utc(0, 0, 0), _met_utc(0, 1, 48), "maneuver",
     "Ascent, LEO Insertion & Pre-ARB Checkouts", "Liftoff, ICPS PRM, Pre-ARB C/Os, T-Roll maneuvers, Cabin Depress DFTO"),
    ("fd01_arb_doff", "FD01", "High Earth Orbit", _met_utc(0, 1, 48), _met_utc(0, 3, 20), "crew_life",
     "ARB Burn, DCAM Setup & Doff OCSS Suits", "Apogee Raise Burn (ARB) to HEO, DCAM setup, crew doffs OCSS survival suits"),
    ("fd01_prox_ops", "FD01", "High Earth Orbit", _met_utc(0, 3, 20), _met_utc(0, 5, 10), "maneuver",
     "Orion/ICPS Separation & Proximity Operations Demo", "Spring separation, manual piloting Prox Ops Demo, USS & ICPS disposal"),
    ("fd01_cab_cfg_pao", "FD01", "High Earth Orbit", _met_utc(0, 5, 10), _met_utc(0, 7, 15), "systems",
     "Cabin Config, 4K Enc, N2/O2 & FD01 PAO / FD Conf", "SPL deploys, Dock Cam Bearing FTO 225, cabin config, PAO, PMC, FD Conf"),
    ("fd01_sleep_1", "FD01", "High Earth Orbit", _met_utc(0, 8, 0), _met_utc(0, 12, 0), "crew_life",
     "FD01 Crew Sleep #1 (4 hrs) & OpComm Activation", "Split sleep period #1; Dock Cam Misalign DFTO-EM2-03 & OpComm Initial Act"),
    ("fd01_prb_opnav", "FD01", "High Earth Orbit", _met_utc(0, 13, 0), _met_utc(0, 14, 30), "maneuver",
     "Orion PRB Burn, DSN Pass & OpNav Checkout", "Orion Propulsion Re-pressurization Burn (PRB) and optical navigation checkout"),
    ("fd01_sleep_2", "FD01", "High Earth Orbit", _met_utc(0, 14, 30), _met_utc(0, 19, 0), "crew_life",
     "FD01 Crew Sleep #2 (4.5 hrs)", "Second split sleep block on Flight Day 1"),
    # FD02
    ("fd02_morning_heo", "FD02", "High Earth Orbit", _met_utc(0, 19, 0), _met_utc(0, 23, 0), "systems",
     "FD02 Post-Sleep, DPC, HEO Sys C/Os & Exercise Test", "Morning DPC, Pulse Ox, Dock Cam Uninstall, HEO checkouts, ECLSS CO2 DFTO"),
    ("fd02_m42_tli", "FD02", "Trans-Lunar Injection", _met_utc(0, 23, 0), _met_utc(1, 2, 0), "maneuver",
     "M-42 EXT, NatGeo Setup & Trans-Lunar Injection (TLI)", "M-42 radiation detector, HERA DFTO-EM2-09, and Orion TLI burn"),
    ("fd02_post_tli_pao", "FD02", "Trans-Lunar", _met_utc(1, 2, 0), _met_utc(1, 4, 30), "media_event",
     "Post-TLI Meal, TLI Conf, PWD & PAO Event", "Midday meal, TLI conference, potable water dispenser, live PAO broadcast"),
    ("fd02_wnd_inspect", "FD02", "Trans-Lunar", _met_utc(1, 5, 0), _met_utc(1, 7, 30), "systems",
     "Window Inspection, SAW 1/3 Cam WiFi DFTO & FD Conf", "Crew window inspection, solar array wing WiFi DFTO, PMC, FD Conf"),
    ("fd02_sleep", "FD02", "Trans-Lunar", _met_utc(1, 8, 30), _met_utc(1, 17, 0), "crew_life",
     "FD02 Crew Sleep (8.5 hrs) & CM/SM Exterior Survey", "Overnight sleep block; automated CM/SM exterior survey at MET 01/12:00"),
    # FD03
    ("fd03_flywheel_du", "FD03", "Trans-Lunar", _met_utc(1, 19, 0), _met_utc(1, 23, 30), "crew_life",
     "FD03 Wakeup, DPC, Flywheel 'Day in the Life' & DU/ESA", "Morning DPC, Pulse Ox, PFC, OpNav, Flywheel exercise video, DU/ESA"),
    ("fd03_otc1_meal", "FD03", "Trans-Lunar", _met_utc(1, 23, 30), _met_utc(2, 2, 30), "maneuver",
     "Planned OTC-1 Window, COGN & Midday Meal", "Planned OTC-1 window (burn scrubbed), cognitive assessment, crew meal"),
    ("fd03_cpr_med_kit", "FD03", "Trans-Lunar", _met_utc(2, 2, 30), _met_utc(2, 5, 30), "crew_life",
     "PAO Event, CPR Demo, Medical Kit Drill & NatGeo", "Broadcast PAO event, in-flight CPR demonstration, medical kit drill"),
    ("fd03_lunar_cab_csa", "FD03", "Trans-Lunar", _met_utc(2, 5, 30), _met_utc(2, 8, 30), "systems",
     "Lunar Cabin Config, DSN Emergency Comm DFTO & CSA PAO", "Lunar cabin prep, DSN Emer Comm DFTO-EM2-22, D5 Cam Window, CSA PAO, PMC"),
    ("fd03_sleep", "FD03", "Trans-Lunar", _met_utc(2, 8, 30), _met_utc(2, 17, 0), "crew_life",
     "FD03 Crew Sleep (8.5 hrs) & SAWs in Full Shadow", "Overnight sleep period with Attitude DFTOs and SAWs in full shadow"),
    # FD04
    ("fd04_wakeup_pwd_ptv", "FD04", "Trans-Lunar", _met_utc(2, 18, 0), _met_utc(3, 0, 0), "crew_life",
     "FD04 OpComm ANU, DPC, 24-Hr Acoustic DFTO & P/TV Exercise", "OpComm ANU Demo, DPC, Start 24-Hr Acoustic DFTO 7, PPC, NatGeo, PWD/P/TV"),
    ("fd04_otc2_meal", "FD04", "Trans-Lunar", _met_utc(3, 0, 0), _met_utc(3, 2, 30), "maneuver",
     "OTC-2 Burn Window, COGN & Midday Meal", "Outbound Trajectory Correction 2 (OTC-2), cognitive test, midday meal"),
    ("fd04_doc_cam_csa_vip", "FD04", "Trans-Lunar", _met_utc(3, 2, 30), _met_utc(3, 7, 30), "science",
     "PAO, Dock Cam Measure, CSA VIP Call & Lunar Imaging Review", "PAO, Dock Cam Measure, CSA VIP event, Lunar Img Review, Min Exercise DFTO, NAV FTO"),
    ("fd04_sleep", "FD04", "Trans-Lunar", _met_utc(3, 8, 30), _met_utc(3, 17, 0), "crew_life",
     "FD04 Crew Sleep (8.5 hrs) & OpComm CFDP / Stray Light DFTO", "Overnight sleep; OpComm Class 1 CFDP Demo & Dock Cam Stray Light DFTO-EM-04"),
    # FD05
    ("fd05_ocss_depress", "FD05", "Trans-Lunar", _met_utc(3, 19, 0), _met_utc(4, 2, 0), "systems",
     "FD05 DPC, OCSS Suit DFTO Ops & Cabin Depress to 10.2 psi", "DPC, End 24-Hr Acoustic DFTO 7, OCSS survival suit DFTO ops, cabin vent to 10.2 psi"),
    ("fd05_otc3_sat_csa", "FD05", "Lunar Approach", _met_utc(4, 2, 0), _met_utc(4, 7, 30), "maneuver",
     "Meal, Lunar Img, OTC-3 Burn, SAT Mode DFTO & Lunar SOI Entry", "Midday meal, OTC-3 burn, SAT Mode Test DFTO-EM2-23, CSA PAO, Lunar SOI Entry"),
    ("fd05_sleep", "FD05", "Lunar Approach", _met_utc(4, 8, 30), _met_utc(4, 17, 0), "crew_life",
     "FD05 Crew Sleep (8.5 hrs)", "Overnight sleep prior to Lunar Flyby day; Dock Cam Misalign DFTO & OpComm ANU"),
    # FD06
    ("fd06_lunar_obs_flyby", "FD06", "Lunar Flyby", _met_utc(4, 19, 0), _met_utc(5, 3, 0), "science",
     "FD06 DPC, Apollo 13 Record, Lunar Flyby Observations & Close Approach", "Lunar Config, NatGeo, LTP Lunar Obs, Pericynthion, LOS/AOS, Max Earth Distance, Solar Eclipse"),
    ("fd06_doc_xfer_exer", "FD06", "Trans-Earth", _met_utc(5, 3, 0), _met_utc(5, 7, 30), "crew_life",
     "Lunar Doc & Xfer, PAO Event, PWD & Exercise Noise", "Post-flyby data transfer, live PAO broadcast, exercise noise checks, PMC, FD Conf"),
    ("fd06_sleep", "FD06", "Trans-Earth", _met_utc(5, 8, 30), _met_utc(5, 17, 0), "crew_life",
     "FD06 Crew Sleep (8.5 hrs) & CM/SM Survey", "Overnight sleep period and automated CM/SM exterior survey"),
    # FD07
    ("fd07_ptv_off_duty", "FD07", "Trans-Earth", _met_utc(5, 19, 0), _met_utc(6, 4, 0), "crew_life",
     "FD07 Lunar SOI Exit, DPC, P/TV Min ECLSS Exercise & Crew Off Duty", "Lunar SOI exit, DPC, Lunar Img, Min ECLSS Exercise DFTO-EM2-16, PFCs, Off Duty, Meal"),
    ("fd07_rtc1_debrief", "FD07", "Trans-Earth", _met_utc(6, 4, 0), _met_utc(6, 7, 30), "maneuver",
     "RTC-1 Burn Window & Post-Lunar Crew Debrief", "Return Trajectory Correction 1 (RTC-1), Off Duty, Post-Lunar Debrief, PMC, FD Conf"),
    ("fd07_sleep", "FD07", "Trans-Earth", _met_utc(6, 8, 0), _met_utc(6, 17, 0), "crew_life",
     "FD07 Crew Sleep (9 hrs Sleep Shift) & WW Dump View FTO", "Extended 9-hr sleep shift; Dock Cam Wastewater (WW) Dump View FTO"),
    # FD08
    ("fd08_ccu_repress_ptv", "FD08", "Trans-Earth", _met_utc(6, 19, 0), _met_utc(7, 1, 0), "systems",
     "FD08 CCU, DPC, RHC, P/TV Exercise, Repress to 14.7 psi & Modal Survey", "CCU, DPC, RHC checkout, P/TV exercise, cabin repress to 14.7 psi, Modal Survey & No-PCD DFTO"),
    ("fd08_urine_rad_shelter", "FD08", "Trans-Earth", _met_utc(7, 1, 0), _met_utc(7, 4, 30), "systems",
     "Meal, COGN, Urine Venting DFTO-EM2-26 & Radiation Shelter Demo", "Midday meal, COGN, Toilet/Urine venting DFTO, radiation shelter assembly demo"),
    ("fd08_man_plt_c2c", "FD08", "Trans-Earth", _met_utc(7, 4, 30), _met_utc(7, 7, 30), "maneuver",
     "Manual Piloting DFTO & Crew-to-Crew (C2C) Call", "Manual piloting handling qualities DFTO, C2C call, PMC, FD Conf"),
    ("fd08_sleep", "FD08", "Trans-Earth", _met_utc(7, 8, 30), _met_utc(7, 17, 0), "crew_life",
     "FD08 Crew Sleep (8.5 hrs) & Stray Light / EI-32hr", "Overnight sleep; Dock Cam Misalign/Stray Light DFTO and EI-32hr prep"),
    # FD09
    ("fd09_fcs_oig_don_1", "FD09", "Trans-Earth", _met_utc(7, 17, 0), _met_utc(8, 1, 0), "systems",
     "FD09 FCS Checkouts, Entry Study/Conf & OIG Suit Donning DFTO #1", "FCS checkouts, DPC, Entry Study, Entry Conf, OIG suit donning DFTO & Entry Pad"),
    ("fd09_oig_don_2_rtc2", "FD09", "Trans-Earth", _met_utc(8, 1, 0), _met_utc(8, 7, 30), "maneuver",
     "Meal, PAO, OIG Donning DFTO #2, Entry Stow, RTC-2 & ONWM S C/O", "Midday meal, PAO, second OIG suit donning block, Entry Stow, RTC-2 burn, ONWM S checkout"),
    ("fd09_sleep", "FD09", "Trans-Earth", _met_utc(8, 8, 0), _met_utc(8, 17, 0), "crew_life",
     "FD09 Final Crew Sleep (8.5 hrs)", "Final sleep period before Entry/Splashdown day"),
    # FD10
    ("fd10_rtc3_cabin_cfg", "FD10", "Trans-Earth / EDL Prep", _met_utc(8, 17, 0), _met_utc(8, 23, 30), "maneuver",
     "FD10 Wakeup, OpNav, PMC, Cabin Config, RTC-3 Burn & DPC", "Final wakeup, +X Leak Detection, Cabin Config, RTC-3 trim burn, final DPC"),
    ("fd10_edl_splashdown", "FD10", "EDL & Recovery", _met_utc(8, 23, 30), _met_utc(9, 4, 0), "milestone",
     "Entry Checklist, CM/SM Sep, Entry Interface, Splashdown & Recovery Ops", "Suit up, Entry C/L, CM/SM separation, Entry Interface blackout, chutes, Pacific splashdown & recovery"),
]

# As-flown Science Observation Sessions & Attitude/Camera Timelines from Data User Guide Table 4 & Science Report (Figs 3.1, 3.2, 3.6, Sec 5)
SCIENCE_SESSIONS = [
    ("sci_fd01_earth", "2026-04-02T11:35:16Z", "2026-04-02T12:40:35Z",
     "Scheduled Earth & Terminator Photos (Wiseman, Koch, Hansen; Nikon D5-015 Long Lens, iPhone; O/E-1 loop 12:32-12:34Z)"),
    ("sci_fd02_unsched_1", "2026-04-02T18:44:20Z", "2026-04-02T19:13:20Z",
     "Unscheduled Imaging: Earth, Cabin, Crew (Nikon D5 Long & 35mm)"),
    ("sci_fd02_earth_storms", "2026-04-02T22:40:00Z", "2026-04-02T22:45:00Z",
     "Earth Storm Systems & Saharan Dust Plume Imaging (art002e000142 at 22:42:35Z)"),
    ("sci_fd02_post_tli", "2026-04-03T00:17:24Z", "2026-04-03T00:42:25Z",
     "Unscheduled Imaging post-TLI: Unilluminated Earth Airglow Over-Exposure (art002e000192 at 00:27:39Z) & Deep Space (Nikon D5, iPhone)"),
    ("sci_fd03_earth_moon", "2026-04-03T23:28:06Z", "2026-04-03T23:49:20Z",
     "Unscheduled Imaging: Crescent Earth & Moon (D5 Long/Short, Z9 35mm)"),
    ("sci_fd03_moon", "2026-04-04T02:03:08Z", "2026-04-04T04:08:46Z",
     "Unscheduled Imaging: Moon (Nikon D5 Long Lens)"),
    ("sci_fd03_window_quality_dfto", "2026-04-04T04:28:27Z", "2026-04-04T05:39:38Z",
     "Scheduled Window Quality DFTO (Glover at Forward Windows 1-4 & Docking Window; Nikon D5 80-400mm)"),
    ("sci_fd04_opp_moon", "2026-04-05T03:55:23Z", "2026-04-05T04:13:30Z",
     "Scheduled Opportunistic Lunar Imaging & Lens Swap (Glover, Wiseman, Koch; Attitude Maneuver to Windows-to-Moon, D5 & Z9 80-400mm, O/E-1 debrief at 04:13:02Z)"),
    ("sci_fd04_ambient_audio", "2026-04-05T05:36:35Z", "2026-04-05T05:47:50Z",
     "Unscheduled Imaging & PCD3 Ambient Audio Noise Tests (Koch)"),
    ("sci_fd04_earth", "2026-04-05T06:34:45Z", "2026-04-05T06:44:05Z",
     "Unscheduled Earth Imaging (Nikon D5 Long & Short)"),
    ("sci_fd05_opp_moon", "2026-04-06T03:17:50Z", "2026-04-06T03:49:59Z",
     "Scheduled Opportunistic Lunar Imaging & Lens Swap (Glover, Wiseman; Attitude Maneuver to Windows-to-Moon, Nikon D5 & Z9 80-400mm)"),
    ("sci_fd06_oe1_readiness", "2026-04-06T16:52:00Z", "2026-04-06T17:46:00Z",
     "Pre-Flyby Science Readiness Calls on O/E-1 Voice Loop (Koch at 16:52:24Z & 17:45:06Z)"),
    ("sci_fd06_tail_to_sun_lunar_view", "2026-04-06T17:00:00Z", "2026-04-06T18:01:17Z",
     "Pre-Flyby Tail-to-Sun Lunar Viewing, Pre-Flyby Science Crew Conference (17:03-18:17Z), Start of SAW Cam 3 Video & 8-min Stills (17:30Z), Apollo 13 Distance Record (17:55:37Z)"),
    ("sci_fd06_mnvr_windows_to_moon_1", "2026-04-06T18:01:17Z", "2026-04-06T18:06:53Z",
     "Orion Attitude Maneuver: Tail-to-Sun to Windows-to-Moon for Primary Lunar Flyby"),
    ("sci_fd06_windows_to_moon_flyby", "2026-04-06T18:06:53Z", "2026-04-06T23:19:33Z",
     "Orion Attitude Windows-to-Moon: Crew Lunar Flyby Observations (Nikon D5 80-400mm & 14-24mm, Z9 35mm; SAW Cam 3 & Cab Cam 2 8-min stills & 30s timelapse)"),
    ("sci_fd06_earthset", "2026-04-06T22:43:00Z", "2026-04-06T22:46:00Z",
     "Earthset behind Lunar Farside Limb & Loss of Signal (LOS) start (22:43:20Z)"),
    ("sci_fd06_pericynthion", "2026-04-06T23:01:00Z", "2026-04-06T23:03:00Z",
     "As-Flown Lunar Pericynthion / Closest Approach (6,544 km altitude, 24.2 deg lunar disk)"),
    ("sci_fd06_earthrise", "2026-04-06T23:19:33Z", "2026-04-06T23:25:10Z",
     "Earthrise (23:19:33Z), Acquisition of Signal (AOS 23:20:00Z) & Attitude Maneuver to Windows-to-Earth"),
    ("sci_fd06_windows_to_earth", "2026-04-06T23:25:10Z", "2026-04-06T23:34:14Z",
     "Orion Attitude Windows-to-Earth (23:25-23:29Z) & Attitude Maneuver to Tail-to-Sun (23:29-23:34Z)"),
    ("sci_fd06_obs_break", "2026-04-06T23:25:00Z", "2026-04-07T00:25:00Z",
     "Lunar Flyby Observation Break & Tail-to-Sun Thermal Recovery (23:34:14Z-00:29:07Z; SAW Cam 3 8-min stills continue)"),
    ("sci_fd06_mnvr_windows_to_moon_2", "2026-04-07T00:29:07Z", "2026-04-07T00:34:44Z",
     "Orion Attitude Maneuver: Tail-to-Sun to Windows-to-Moon for Solar Eclipse Observations"),
    ("sci_fd06_solar_eclipse", "2026-04-07T00:34:44Z", "2026-04-07T01:41:31Z",
     "Solar Eclipse by the Moon (Sunset 00:35:23Z to Sunrise 01:30:26Z), Earthshine, Coronal Streamers & 5 Confirmed Lunar Impact Flashes (00:41:01Z, 00:51:15Z, art002e016252)"),
    ("sci_fd06_mnvr_tail_to_sun_post", "2026-04-07T01:41:31Z", "2026-04-07T01:47:08Z",
     "Orion Attitude Maneuver: Windows-to-Moon to Tail-to-Sun concluding Lunar Flyby Science"),
    ("sci_fd07_lunar_return_view", "2026-04-07T17:28:00Z", "2026-04-07T17:35:00Z",
     "FD07 Vehicle & Crew Return Lunar Views (17:30:05Z)"),
    ("sci_fd07_post_flyby_science_debrief", "2026-04-07T19:15:00Z", "2026-04-07T19:30:00Z",
     "Post-Lunar Flyby Science Crew Conference / Debrief on O/E-1 Voice Loop (Koch, Wiseman, Glover, Hansen with Science Officer)"),
    ("sci_fd07_opp_moon", "2026-04-07T19:41:35Z", "2026-04-07T19:48:59Z",
     "Scheduled Opportunistic Lunar Imaging (Koch; Nikon D5 Long Lens)"),
    ("sci_fd07_star_fields", "2026-04-07T21:12:18Z", "2026-04-07T21:37:21Z",
     "Unscheduled Star Fields & Deep Space Imaging (Nikon Z9 35mm)"),
    ("sci_fd08_earth_1", "2026-04-09T03:33:42Z", "2026-04-09T03:58:30Z",
     "Unscheduled Earth Imaging (Nikon D5 35mm, Z9 Long Lens)"),
    ("sci_fd08_earth_2", "2026-04-09T04:07:02Z", "2026-04-09T04:28:31Z",
     "Unscheduled Earth Imaging (Nikon D5 35mm, Z9 Long Lens)"),
]

# As-Executed Lunar Flyby Timeline scraped from Figure 4.1 (page 30) & Section 5 PCD audio citations
# in pdfs/Artemis II_Preliminary Science Report.pdf
AS_EXECUTED_LTP_TIMELINE: list[dict] = [
    {
        "id": "ltp_01_02_warmup_near_farside",
        "planned_utc": "2026-04-06T18:45:00Z",
        "start_utc": "2026-04-06T18:38:16Z",
        "end_utc": "2026-04-06T19:09:57Z",
        "label": "LTP #1 & #2: Discussions #1 & #2 — Warm Up & Near/Farside Transition",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "unaided_eye", "start_utc": "2026-04-06T18:38:16Z", "end_utc": "2026-04-06T19:09:03Z", "audio": "art002a000017..18"},
            {"crew": "hansen", "role": "camera", "start_utc": "2026-04-06T18:38:19Z", "end_utc": "2026-04-06T19:09:57Z", "audio": "art002a000028"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T18:38:16Z", "end_utc": "2026-04-06T19:09:57Z", "audio": "O/E-1 18:57:32Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T18:38:16Z", "end_utc": "2026-04-06T19:09:57Z", "audio": "O/E-1 18:48:44Z"},
        ],
    },
    {
        "id": "ltp_03_glushko_1",
        "planned_utc": "2026-04-06T19:15:00Z",
        "start_utc": "2026-04-06T19:10:06Z",
        "end_utc": "2026-04-06T19:23:53Z",
        "label": "LTP #3: Crater Duo: Glushko (1/2)",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "unaided_eye", "start_utc": "2026-04-06T19:10:06Z", "end_utc": "2026-04-06T19:18:11Z", "audio": "art002a000019"},
            {"crew": "hansen", "role": "camera", "start_utc": "2026-04-06T19:10:07Z", "end_utc": "2026-04-06T19:23:53Z", "audio": "art002a000029"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:10:06Z", "end_utc": "2026-04-06T19:23:53Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:10:06Z", "end_utc": "2026-04-06T19:23:53Z"},
        ],
    },
    {
        "id": "ltp_04_ohm_1",
        "planned_utc": "2026-04-06T19:23:00Z",
        "start_utc": "2026-04-06T19:21:57Z",
        "end_utc": "2026-04-06T19:29:59Z",
        "label": "LTP #4: Crater Duo: Ohm",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "unaided_eye", "start_utc": "2026-04-06T19:21:57Z", "end_utc": "2026-04-06T19:28:10Z", "audio": "art002a000020"},
            {"crew": "hansen", "role": "camera", "start_utc": "2026-04-06T19:24:04Z", "end_utc": "2026-04-06T19:29:59Z", "audio": "art002a000030"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:21:57Z", "end_utc": "2026-04-06T19:29:59Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:21:57Z", "end_utc": "2026-04-06T19:29:59Z"},
        ],
    },
    {
        "id": "ltp_pos_swap_1",
        "planned_utc": "2026-04-06T19:30:00Z",
        "start_utc": "2026-04-06T19:29:59Z",
        "end_utc": "2026-04-06T19:33:05Z",
        "label": "LTP Window Team Position Swap #1 (Wiseman <-> Hansen camera/eye swap)",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "window_swap", "start_utc": "2026-04-06T19:29:59Z", "end_utc": "2026-04-06T19:33:05Z"},
            {"crew": "hansen", "role": "window_swap", "start_utc": "2026-04-06T19:29:59Z", "end_utc": "2026-04-06T19:33:05Z"},
        ],
    },
    {
        "id": "ltp_05_aristarchus",
        "planned_utc": "2026-04-06T19:31:00Z",
        "start_utc": "2026-04-06T19:33:05Z",
        "end_utc": "2026-04-06T19:41:11Z",
        "label": "LTP #5: Aristarchus Plateau",
        "crew": ["hansen", "wiseman"],
        "crew_roles": [
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-06T19:33:05Z", "end_utc": "2026-04-06T19:40:05Z", "audio": "art002a000032"},
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-06T19:33:07Z", "end_utc": "2026-04-06T19:41:11Z", "audio": "art002a000021"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:33:05Z", "end_utc": "2026-04-06T19:41:11Z", "audio": "O/E-1 19:34:41Z"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:33:05Z", "end_utc": "2026-04-06T19:41:11Z"},
        ],
    },
    {
        "id": "ltp_06_reiner_gamma",
        "planned_utc": "2026-04-06T19:41:00Z",
        "start_utc": "2026-04-06T19:41:57Z",
        "end_utc": "2026-04-06T19:47:30Z",
        "label": "LTP #6: Reiner Gamma Lunar Swirl",
        "crew": ["hansen", "wiseman"],
        "crew_roles": [
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-06T19:41:57Z", "end_utc": "2026-04-06T19:45:21Z", "audio": "art002a000033"},
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-06T19:41:58Z", "end_utc": "2026-04-06T19:47:30Z", "audio": "art002a000022"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:41:57Z", "end_utc": "2026-04-06T19:47:30Z", "audio": "O/E-1 19:46:02Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T19:41:57Z", "end_utc": "2026-04-06T19:47:30Z"},
        ],
    },
    {
        "id": "ltp_crew_swap_1",
        "planned_utc": "2026-04-06T19:50:00Z",
        "start_utc": "2026-04-06T19:47:30Z",
        "end_utc": "2026-04-06T20:03:07Z",
        "label": "LTP Cabin & Window Teams Crew Swap #1 (Wiseman/Hansen <-> Glover/Koch)",
        "crew": ["wiseman", "hansen", "glover", "koch"],
        "crew_roles": [
            {"crew": "wiseman", "role": "crew_swap", "start_utc": "2026-04-06T19:47:30Z", "end_utc": "2026-04-06T20:03:07Z"},
            {"crew": "hansen", "role": "crew_swap", "start_utc": "2026-04-06T19:47:30Z", "end_utc": "2026-04-06T20:03:07Z"},
            {"crew": "glover", "role": "crew_swap", "start_utc": "2026-04-06T19:47:30Z", "end_utc": "2026-04-06T20:03:07Z"},
            {"crew": "koch", "role": "crew_swap", "start_utc": "2026-04-06T19:47:30Z", "end_utc": "2026-04-06T20:03:07Z"},
        ],
    },
    {
        "id": "ltp_07_whole_moon",
        "planned_utc": "2026-04-06T20:05:00Z",
        "start_utc": "2026-04-06T20:03:07Z",
        "end_utc": "2026-04-06T20:16:21Z",
        "label": "LTP #7: Whole Moon Observation",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-06T20:03:07Z", "end_utc": "2026-04-06T20:16:20Z", "audio": "art002a000034"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-06T20:03:08Z", "end_utc": "2026-04-06T20:16:21Z", "audio": "art002a000005"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:03:07Z", "end_utc": "2026-04-06T20:16:21Z", "audio": "O/E-1 20:16:08Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:03:07Z", "end_utc": "2026-04-06T20:16:21Z"},
        ],
    },
    {
        "id": "ltp_08_orientale_1",
        "planned_utc": "2026-04-06T20:15:00Z",
        "start_utc": "2026-04-06T20:17:03Z",
        "end_utc": "2026-04-06T20:30:48Z",
        "label": "LTP #8: Orientale Basin (1/2)",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-06T20:17:03Z", "end_utc": "2026-04-06T20:30:46Z", "audio": "art002a000035"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-06T20:17:04Z", "end_utc": "2026-04-06T20:30:48Z", "audio": "art002a000006"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:17:03Z", "end_utc": "2026-04-06T20:30:48Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:17:03Z", "end_utc": "2026-04-06T20:30:48Z"},
        ],
    },
    {
        "id": "ltp_pos_swap_2",
        "planned_utc": "2026-04-06T20:30:00Z",
        "start_utc": "2026-04-06T20:30:48Z",
        "end_utc": "2026-04-06T20:33:50Z",
        "label": "LTP Window Team Position Swap #2 (Glover <-> Koch camera/eye swap)",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "window_swap", "start_utc": "2026-04-06T20:30:48Z", "end_utc": "2026-04-06T20:33:50Z"},
            {"crew": "koch", "role": "window_swap", "start_utc": "2026-04-06T20:30:48Z", "end_utc": "2026-04-06T20:33:50Z"},
        ],
    },
    {
        "id": "ltp_09_crew_choice_1",
        "planned_utc": "2026-04-06T20:31:00Z",
        "start_utc": "2026-04-06T20:33:50Z",
        "end_utc": "2026-04-06T20:38:21Z",
        "label": "LTP #9: Crew Choice #1",
        "crew": ["koch", "glover"],
        "crew_roles": [
            {"crew": "koch", "role": "unaided_eye", "start_utc": "2026-04-06T20:33:50Z", "end_utc": "2026-04-06T20:37:29Z", "audio": "art002a000007"},
            {"crew": "glover", "role": "camera", "start_utc": "2026-04-06T20:33:52Z", "end_utc": "2026-04-06T20:38:21Z", "audio": "art002a000036"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:33:50Z", "end_utc": "2026-04-06T20:38:21Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:33:50Z", "end_utc": "2026-04-06T20:38:21Z"},
        ],
    },
    {
        "id": "ltp_10_farside_albedo",
        "planned_utc": "2026-04-06T20:39:00Z",
        "start_utc": "2026-04-06T20:38:51Z",
        "end_utc": "2026-04-06T20:56:19Z",
        "label": "LTP #10: Discussion #3 — Farside Albedo/Color",
        "crew": ["koch", "glover"],
        "crew_roles": [
            {"crew": "koch", "role": "unaided_eye", "start_utc": "2026-04-06T20:38:51Z", "end_utc": "2026-04-06T20:56:19Z", "audio": "art002a000008"},
            {"crew": "glover", "role": "camera", "start_utc": "2026-04-06T20:38:54Z", "end_utc": "2026-04-06T20:56:19Z", "audio": "art002a000037"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:38:51Z", "end_utc": "2026-04-06T20:56:19Z", "audio": "O/E-1 20:42:43Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T20:38:51Z", "end_utc": "2026-04-06T20:56:19Z"},
        ],
    },
    {
        "id": "ltp_crew_swap_2",
        "planned_utc": "2026-04-06T20:55:00Z",
        "start_utc": "2026-04-06T20:56:19Z",
        "end_utc": "2026-04-06T21:01:44Z",
        "label": "LTP Cabin & Window Teams Crew Swap #2 (Glover/Koch <-> Wiseman/Hansen)",
        "crew": ["glover", "koch", "wiseman", "hansen"],
        "crew_roles": [
            {"crew": "glover", "role": "crew_swap", "start_utc": "2026-04-06T20:56:19Z", "end_utc": "2026-04-06T21:01:44Z"},
            {"crew": "koch", "role": "crew_swap", "start_utc": "2026-04-06T20:56:19Z", "end_utc": "2026-04-06T21:01:44Z"},
            {"crew": "wiseman", "role": "crew_swap", "start_utc": "2026-04-06T20:56:19Z", "end_utc": "2026-04-06T21:01:44Z"},
            {"crew": "hansen", "role": "crew_swap", "start_utc": "2026-04-06T20:56:19Z", "end_utc": "2026-04-06T21:01:44Z"},
        ],
    },
    {
        "id": "ltp_11_glushko_2",
        "planned_utc": "2026-04-06T21:10:00Z",
        "start_utc": "2026-04-06T21:01:44Z",
        "end_utc": "2026-04-06T21:18:46Z",
        "label": "LTP #11: Glushko Crater (2/2)",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "unaided_eye", "start_utc": "2026-04-06T21:01:44Z", "end_utc": "2026-04-06T21:18:46Z", "audio": "art002a000023"},
            {"crew": "hansen", "role": "camera", "start_utc": "2026-04-06T21:01:45Z", "end_utc": "2026-04-06T21:14:48Z", "audio": "art002a000038"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:01:44Z", "end_utc": "2026-04-06T21:18:46Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:01:44Z", "end_utc": "2026-04-06T21:18:46Z"},
        ],
    },
    {
        "id": "ltp_12_orientale_2",
        "planned_utc": "2026-04-06T21:18:00Z",
        "start_utc": "2026-04-06T21:14:56Z",
        "end_utc": "2026-04-06T21:34:02Z",
        "label": "LTP #12: Orientale Basin (2/2)",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "hansen", "role": "camera", "start_utc": "2026-04-06T21:14:56Z", "end_utc": "2026-04-06T21:34:02Z", "audio": "art002a000039"},
            {"crew": "wiseman", "role": "unaided_eye", "start_utc": "2026-04-06T21:18:54Z", "end_utc": "2026-04-06T21:33:52Z", "audio": "art002a000024"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:14:56Z", "end_utc": "2026-04-06T21:34:02Z", "audio": "O/E-1 21:21:35Z, 21:27:42Z, 21:32:57Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:14:56Z", "end_utc": "2026-04-06T21:34:02Z"},
        ],
    },
    {
        "id": "ltp_13_hertzsprung",
        "planned_utc": "2026-04-06T21:34:00Z",
        "start_utc": "2026-04-06T21:33:58Z",
        "end_utc": "2026-04-06T21:40:45Z",
        "label": "LTP #13: Hertzsprung Basin",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "unaided_eye", "start_utc": "2026-04-06T21:33:58Z", "end_utc": "2026-04-06T21:39:51Z", "audio": "art002a000024"},
            {"crew": "hansen", "role": "camera", "start_utc": "2026-04-06T21:34:48Z", "end_utc": "2026-04-06T21:40:45Z", "audio": "art002a000040"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:33:58Z", "end_utc": "2026-04-06T21:40:45Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:33:58Z", "end_utc": "2026-04-06T21:40:45Z"},
        ],
    },
    {
        "id": "ltp_pos_swap_3",
        "planned_utc": "2026-04-06T21:41:00Z",
        "start_utc": "2026-04-06T21:40:45Z",
        "end_utc": "2026-04-06T21:42:43Z",
        "label": "LTP Window Team Position Swap #3 (Wiseman <-> Hansen camera/eye swap)",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "window_swap", "start_utc": "2026-04-06T21:40:45Z", "end_utc": "2026-04-06T21:42:43Z"},
            {"crew": "hansen", "role": "window_swap", "start_utc": "2026-04-06T21:40:45Z", "end_utc": "2026-04-06T21:42:43Z"},
        ],
    },
    {
        "id": "ltp_14_crew_choice_2",
        "planned_utc": "2026-04-06T21:42:00Z",
        "start_utc": "2026-04-06T21:42:43Z",
        "end_utc": "2026-04-06T21:51:50Z",
        "label": "LTP #14: Crew Choice #2",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-06T21:42:43Z", "end_utc": "2026-04-06T21:51:48Z", "audio": "art002a000025"},
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-06T21:42:44Z", "end_utc": "2026-04-06T21:51:50Z", "audio": "art002a000041"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:42:43Z", "end_utc": "2026-04-06T21:51:50Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:42:43Z", "end_utc": "2026-04-06T21:51:50Z"},
        ],
    },
    {
        "id": "ltp_15_terminator",
        "planned_utc": "2026-04-06T21:50:00Z",
        "start_utc": "2026-04-06T21:51:56Z",
        "end_utc": "2026-04-06T22:02:55Z",
        "label": "LTP #15: Discussion #4 — Terminator",
        "crew": ["wiseman", "hansen"],
        "crew_roles": [
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-06T21:51:56Z", "end_utc": "2026-04-06T22:02:53Z", "audio": "art002a000042"},
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-06T21:51:56Z", "end_utc": "2026-04-06T22:02:55Z", "audio": "art002a000026"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:51:56Z", "end_utc": "2026-04-06T22:02:55Z", "audio": "O/E-1 21:53:03Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T21:51:56Z", "end_utc": "2026-04-06T22:02:55Z"},
        ],
    },
    {
        "id": "ltp_crew_swap_3",
        "planned_utc": "2026-04-06T22:05:00Z",
        "start_utc": "2026-04-06T22:02:55Z",
        "end_utc": "2026-04-06T22:15:09Z",
        "label": "LTP Cabin & Window Teams Crew Swap #3 (Wiseman/Hansen <-> Glover/Koch)",
        "crew": ["wiseman", "hansen", "glover", "koch"],
        "crew_roles": [
            {"crew": "wiseman", "role": "crew_swap", "start_utc": "2026-04-06T22:02:55Z", "end_utc": "2026-04-06T22:15:09Z"},
            {"crew": "hansen", "role": "crew_swap", "start_utc": "2026-04-06T22:02:55Z", "end_utc": "2026-04-06T22:15:09Z"},
            {"crew": "glover", "role": "crew_swap", "start_utc": "2026-04-06T22:02:55Z", "end_utc": "2026-04-06T22:15:09Z"},
            {"crew": "koch", "role": "crew_swap", "start_utc": "2026-04-06T22:02:55Z", "end_utc": "2026-04-06T22:15:09Z"},
        ],
    },
    {
        "id": "ltp_16_ohm_2",
        "planned_utc": "2026-04-06T22:20:00Z",
        "start_utc": "2026-04-06T22:15:09Z",
        "end_utc": "2026-04-06T22:29:15Z",
        "label": "LTP #16: Ohm Crater",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-06T22:15:09Z", "end_utc": "2026-04-06T22:25:06Z", "audio": "art002a000043"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-06T22:19:06Z", "end_utc": "2026-04-06T22:29:15Z", "audio": "art002a000009"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:15:09Z", "end_utc": "2026-04-06T22:29:15Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:15:09Z", "end_utc": "2026-04-06T22:29:15Z"},
        ],
    },
    {
        "id": "ltp_17_limb",
        "planned_utc": "2026-04-06T22:28:00Z",
        "start_utc": "2026-04-06T22:26:05Z",
        "end_utc": "2026-04-06T22:33:15Z",
        "label": "LTP #17: Discussion #5 — Limb",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-06T22:26:05Z", "end_utc": "2026-04-06T22:33:15Z", "audio": "art002a000044"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-06T22:30:01Z", "end_utc": "2026-04-06T22:33:13Z", "audio": "art002a000010"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:26:05Z", "end_utc": "2026-04-06T22:33:15Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:26:05Z", "end_utc": "2026-04-06T22:33:15Z"},
        ],
    },
    {
        "id": "ltp_18_earthset",
        "planned_utc": "2026-04-06T22:38:00Z",
        "start_utc": "2026-04-06T22:33:46Z",
        "end_utc": "2026-04-06T22:47:25Z",
        "label": "LTP #18: Earthset Observation & Loss of Signal (LOS)",
        "crew": ["koch", "glover"],
        "crew_roles": [
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-06T22:33:46Z", "end_utc": "2026-04-06T22:47:25Z", "audio": "art002a000011"},
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-06T22:39:02Z", "end_utc": "2026-04-06T22:44:23Z", "audio": "art002a000045"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:33:46Z", "end_utc": "2026-04-06T22:47:25Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:33:46Z", "end_utc": "2026-04-06T22:47:25Z"},
        ],
    },
    {
        "id": "ltp_19_vavilov",
        "planned_utc": "2026-04-06T22:46:00Z",
        "start_utc": "2026-04-06T22:44:54Z",
        "end_utc": "2026-04-06T22:57:29Z",
        "label": "LTP #19: Vavilov Crater",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-06T22:44:54Z", "end_utc": "2026-04-06T22:57:28Z", "audio": "art002a000046"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-06T22:50:58Z", "end_utc": "2026-04-06T22:57:29Z", "audio": "art002a000012"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:44:54Z", "end_utc": "2026-04-06T22:57:29Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:44:54Z", "end_utc": "2026-04-06T22:57:29Z"},
        ],
    },
    {
        "id": "ltp_pos_swap_4",
        "planned_utc": "2026-04-06T22:54:00Z",
        "start_utc": "2026-04-06T22:57:29Z",
        "end_utc": "2026-04-06T22:59:07Z",
        "label": "LTP Window Team Position Swap #4 (Glover <-> Koch camera/eye swap)",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "window_swap", "start_utc": "2026-04-06T22:57:29Z", "end_utc": "2026-04-06T22:59:07Z"},
            {"crew": "koch", "role": "window_swap", "start_utc": "2026-04-06T22:57:29Z", "end_utc": "2026-04-06T22:59:07Z"},
        ],
    },
    {
        "id": "ltp_20_crew_choice_3",
        "planned_utc": "2026-04-06T22:54:00Z",
        "start_utc": "2026-04-06T22:59:07Z",
        "end_utc": "2026-04-06T23:06:28Z",
        "label": "LTP #20: Crew Choice #3 (Spanning Lunar Pericynthion at 23:01Z)",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "camera", "start_utc": "2026-04-06T22:59:07Z", "end_utc": "2026-04-06T23:06:28Z", "audio": "art002a000047"},
            {"crew": "koch", "role": "unaided_eye", "start_utc": "2026-04-06T23:00:17Z", "end_utc": "2026-04-06T23:05:18Z", "audio": "art002a000013"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:59:07Z", "end_utc": "2026-04-06T23:06:28Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T22:59:07Z", "end_utc": "2026-04-06T23:06:28Z"},
        ],
    },
    {
        "id": "ltp_21_impact_flashes",
        "planned_utc": "2026-04-06T23:02:00Z",
        "start_utc": "2026-04-06T23:06:07Z",
        "end_utc": "2026-04-06T23:11:08Z",
        "label": "LTP #21: Impact Flashes Survey (Farside Pass)",
        "crew": ["koch", "glover"],
        "crew_roles": [
            {"crew": "koch", "role": "unaided_eye", "start_utc": "2026-04-06T23:06:07Z", "end_utc": "2026-04-06T23:10:02Z", "audio": "art002a000014"},
            {"crew": "glover", "role": "camera", "start_utc": "2026-04-06T23:07:11Z", "end_utc": "2026-04-06T23:11:08Z", "audio": "art002a000048 (terminator mosaic art002e014040..60 at 23:09:15-23:09:34Z)"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T23:06:07Z", "end_utc": "2026-04-06T23:11:08Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T23:06:07Z", "end_utc": "2026-04-06T23:11:08Z"},
        ],
    },
    {
        "id": "ltp_22_lofted_dust",
        "planned_utc": "2026-04-06T23:10:00Z",
        "start_utc": "2026-04-06T23:11:55Z",
        "end_utc": "2026-04-06T23:20:40Z",
        "label": "LTP #22: Lofted Lunar Dust Horizon Glow Survey",
        "crew": ["koch", "glover"],
        "crew_roles": [
            {"crew": "koch", "role": "unaided_eye", "start_utc": "2026-04-06T23:11:55Z", "end_utc": "2026-04-06T23:20:40Z", "audio": "art002a000015"},
            {"crew": "glover", "role": "camera", "start_utc": "2026-04-06T23:11:55Z", "end_utc": "2026-04-06T23:14:09Z", "audio": "art002a000048"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T23:11:55Z", "end_utc": "2026-04-06T23:20:40Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T23:11:55Z", "end_utc": "2026-04-06T23:20:40Z"},
        ],
    },
    {
        "id": "ltp_23_earthrise",
        "planned_utc": "2026-04-06T23:18:00Z",
        "start_utc": "2026-04-06T23:15:51Z",
        "end_utc": "2026-04-06T23:26:30Z",
        "label": "LTP #23: Earthrise Observation & Acquisition of Signal (AOS)",
        "crew": ["glover", "koch"],
        "crew_roles": [
            {"crew": "glover", "role": "camera", "start_utc": "2026-04-06T23:15:51Z", "end_utc": "2026-04-06T23:26:30Z", "audio": "art002a000049 (art002e009280)"},
            {"crew": "koch", "role": "unaided_eye", "start_utc": "2026-04-06T23:21:09Z", "end_utc": "2026-04-06T23:23:57Z", "audio": "art002a000016"},
            {"crew": "wiseman", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T23:15:51Z", "end_utc": "2026-04-06T23:26:30Z"},
            {"crew": "hansen", "role": "cabin_support_sitrep", "start_utc": "2026-04-06T23:15:51Z", "end_utc": "2026-04-06T23:26:30Z"},
        ],
    },
    {
        "id": "ltp_24_sunset",
        "planned_utc": "2026-04-07T00:36:00Z",
        "start_utc": "2026-04-07T00:27:44Z",
        "end_utc": "2026-04-07T00:41:14Z",
        "label": "LTP #24: Solar Eclipse Sunset & First Confirmed Impact Flash (00:41:01Z)",
        "crew": ["hansen", "wiseman"],
        "crew_roles": [
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-07T00:27:44Z", "end_utc": "2026-04-07T00:41:14Z", "audio": "art002a000052 (earthshine 1/8 Moon at 00:38:57Z)"},
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-07T00:27:44Z", "end_utc": "2026-04-07T00:41:14Z", "audio": "art002a000052 (Impact Flash #1 at 00:41:01Z)"},
            {"crew": "glover", "role": "cabin_support_sitrep", "start_utc": "2026-04-07T00:27:44Z", "end_utc": "2026-04-07T00:41:14Z"},
            {"crew": "koch", "role": "cabin_support_sitrep", "start_utc": "2026-04-07T00:27:44Z", "end_utc": "2026-04-07T00:41:14Z"},
        ],
    },
    {
        "id": "ltp_25_29_eclipse_deep_space",
        "planned_utc": "2026-04-07T00:44:00Z",
        "start_utc": "2026-04-07T00:41:16Z",
        "end_utc": "2026-04-07T01:25:55Z",
        "label": "LTP #25–#29: Discussion #6 — Forward Look, Eclipsed Moon, Earthshine, Impact Flashes & Deep Space",
        "crew": ["hansen", "wiseman", "koch", "glover"],
        "crew_roles": [
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-07T00:41:17Z", "end_utc": "2026-04-07T01:25:55Z", "audio": "art002a000052 (coincident impact flash 00:51:15Z, earthshine 00:48:45Z & 01:03:27Z)"},
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-07T00:41:16Z", "end_utc": "2026-04-07T01:25:55Z", "audio": "art002a000052..53 (coincident impact flash 00:51:15Z, earthshine 01:10:50Z)"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-07T00:41:16Z", "end_utc": "2026-04-07T01:25:55Z", "audio": "Nikon Z9 earthshine art002e015921 at 00:52:19Z"},
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-07T01:08:44Z", "end_utc": "2026-04-07T01:25:54Z", "audio": "O/E-1 00:50:18Z, 01:14:07Z, 01:15:52Z, 01:17:18Z"},
        ],
    },
    {
        "id": "ltp_30_sunrise",
        "planned_utc": "2026-04-07T01:23:00Z",
        "start_utc": "2026-04-07T01:25:56Z",
        "end_utc": "2026-04-07T01:31:00Z",
        "label": "LTP #30: Solar Eclipse Sunrise (All 4 Crew Observing)",
        "crew": ["hansen", "wiseman", "koch", "glover"],
        "crew_roles": [
            {"crew": "hansen", "role": "unaided_eye", "start_utc": "2026-04-07T01:25:57Z", "end_utc": "2026-04-07T01:31:00Z", "audio": "art002a000053"},
            {"crew": "glover", "role": "unaided_eye", "start_utc": "2026-04-07T01:25:56Z", "end_utc": "2026-04-07T01:30:59Z", "audio": "O/E-1 01:40:08Z"},
            {"crew": "wiseman", "role": "camera", "start_utc": "2026-04-07T01:25:57Z", "end_utc": "2026-04-07T01:31:00Z", "audio": "art002a000053"},
            {"crew": "koch", "role": "camera", "start_utc": "2026-04-07T01:25:57Z", "end_utc": "2026-04-07T01:31:00Z"},
        ],
    },
]

# Lunar Targeting Package (LTP) schedule during FD06 Flyby (2026-04-06 / 2026-04-07)
LTP_TARGETS = [
    ("ltp_01_warmup", "2026-04-06T18:45:00Z", "LTP #1: Discussion #1 Warm up"),
    ("ltp_02_near_farside", "2026-04-06T18:55:00Z", "LTP #2: Discussion #2 Near/Farside Transition"),
    ("ltp_03_glushko_1", "2026-04-06T19:15:00Z", "LTP #3: Crater Duo: Glushko (1/2)"),
    ("ltp_04_ohm_1", "2026-04-06T19:23:00Z", "LTP #4: Crater Duo: Ohm"),
    ("ltp_05_aristarchus", "2026-04-06T19:31:00Z", "LTP #5: Aristarchus Plateau"),
    ("ltp_06_reiner_gamma", "2026-04-06T19:41:00Z", "LTP #6: Reiner Gamma Lunar Swirl"),
    ("ltp_07_whole_moon", "2026-04-06T20:05:00Z", "LTP #7: Whole Moon Observation"),
    ("ltp_08_orientale_1", "2026-04-06T20:15:00Z", "LTP #8: Orientale Basin (1/2)"),
    ("ltp_09_crew_choice_1", "2026-04-06T20:31:00Z", "LTP #9: Crew Choice #1"),
    ("ltp_10_farside_albedo", "2026-04-06T20:39:00Z", "LTP #10: Discussion #3 Farside Albedo/Color"),
    ("ltp_11_glushko_2", "2026-04-06T21:10:00Z", "LTP #11: Glushko Crater (2/2)"),
    ("ltp_12_orientale_2", "2026-04-06T21:18:00Z", "LTP #12: Orientale Basin (2/2)"),
    ("ltp_13_hertzsprung", "2026-04-06T21:34:00Z", "LTP #13: Hertzsprung Basin"),
    ("ltp_14_crew_choice_2", "2026-04-06T21:42:00Z", "LTP #14: Crew Choice #2"),
    ("ltp_15_terminator", "2026-04-06T21:50:00Z", "LTP #15: Discussion #4 Terminator"),
    ("ltp_16_ohm_2", "2026-04-06T22:20:00Z", "LTP #16: Ohm Crater"),
    ("ltp_17_limb", "2026-04-06T22:28:00Z", "LTP #17: Discussion #5 Limb"),
    ("ltp_18_earthset", "2026-04-06T22:38:00Z", "LTP #18: Earthset Observation"),
    ("ltp_19_vavilov", "2026-04-06T22:46:00Z", "LTP #19: Vavilov Crater"),
    ("ltp_20_crew_choice_3", "2026-04-06T22:54:00Z", "LTP #20: Crew Choice #3"),
    ("ltp_21_impact_flashes", "2026-04-06T23:02:00Z", "LTP #21: Impact Flashes Survey"),
    ("ltp_22_lofted_dust", "2026-04-06T23:10:00Z", "LTP #22: Lofted Lunar Dust Horizon Glow Survey"),
    ("ltp_23_earthrise", "2026-04-06T23:18:00Z", "LTP #23: Earthrise Observation"),
    ("ltp_24_sunset", "2026-04-07T00:36:00Z", "LTP #24: Solar Eclipse Sunset"),
    ("ltp_25_forward_look", "2026-04-07T00:44:00Z", "LTP #25: Discussion #6 Forward Look"),
    ("ltp_26_eclipsed_moon_1", "2026-04-07T00:54:00Z", "LTP #26/#27: Eclipsed Moon & Deep Space (1/2)"),
    ("ltp_28_eclipsed_moon_2", "2026-04-07T01:04:00Z", "LTP #28/#29: Eclipsed Moon & Deep Space (2/2)"),
    ("ltp_30_sunrise", "2026-04-07T01:23:00Z", "LTP #30: Solar Eclipse Sunrise"),
]


class MissionContextIndex:
    """Loads and queries all external mission reference sources for a time window."""

    def __init__(self, mission: MissionConfig) -> None:
        self.events: list[dict] = []
        self.planned_blocks: list[dict] = []
        self.crew_photo_ts: list[float] = []
        self.crew_photos: list[dict] = []
        self.photo_captions: list[dict] = []
        self._load_all(mission)

    def _load_all(self, mission: MissionConfig) -> None:
        # 1. web/itinerary.json
        it_path = mission.web_dir / "itinerary.json"
        if it_path.exists():
            data = json.loads(it_path.read_text(encoding="utf-8"))
            for e in data.get("events", []):
                if e.get("t"):
                    ts = _parse_ts(e["t"])
                    self.events.append({
                        "id": e["id"],
                        "t": e["t"],
                        "start_ts": ts,
                        "end_ts": ts,
                        "kind": f"ITINERARY ({e.get('confidence', 'nominal')})",
                        "label": f"{e['name']} — {e.get('description', '')}",
                        "planned_only": e.get("confidence", "nominal") != "confirmed",
                    })

        # 2. Pre-mission Overview Timeline PDF point events (as-planned watch items)
        for eid, t_str, desc in OVERVIEW_TIMELINE_EVENTS:
            ts = _parse_ts(t_str)
            self.events.append({
                "id": eid,
                "t": t_str,
                "start_ts": ts,
                "end_ts": ts + 1800,
                "kind": "FLIGHT_PLAN",
                "label": f"{desc} (pre-mission planned time — watch for actual execution)",
                "planned_only": True,
            })

        # 2b. Pre-mission Overview Timeline PDF contiguous blocks
        for bid, fd, phase, st_str, et_str, cat, title, desc in OVERVIEW_TIMELINE_BLOCKS:
            self.planned_blocks.append({
                "id": bid,
                "flight_day": fd,
                "phase": phase,
                "start_utc": st_str,
                "end_utc": et_str,
                "start_ts": _parse_ts(st_str),
                "end_ts": _parse_ts(et_str),
                "category": cat,
                "title": title,
                "description": desc,
            })

        # 3. Science Report / Data User Guide Table 4 sessions (as-flown)
        for eid, start_str, end_str, desc in SCIENCE_SESSIONS:
            self.events.append({
                "id": eid,
                "t": f"{start_str}..{end_str[11:]}",
                "start_ts": _parse_ts(start_str),
                "end_ts": _parse_ts(end_str),
                "kind": "SCIENCE_REPORT",
                "label": desc,
                "crew": infer_crew_members(desc),
                "as_executed": True,
            })

        # 4. As-Executed LTP Targets & Crew Swaps from Preliminary Science Report Figure 4.1
        for item in AS_EXECUTED_LTP_TIMELINE:
            st_str = item["start_utc"]
            et_str = item["end_utc"]
            role_summary = ", ".join(
                f"{r['crew']}={r['role']}"
                for r in item.get("crew_roles", [])
                if r.get("role") in ("unaided_eye", "camera")
            )
            label = f"{item['label']} ({role_summary})" if role_summary else item["label"]
            self.events.append({
                "id": item["id"],
                "t": f"{st_str}..{et_str[11:]}",
                "planned_utc": item.get("planned_utc"),
                "start_ts": _parse_ts(st_str),
                "end_ts": _parse_ts(et_str),
                "kind": "LTP_TARGET",
                "label": label,
                "crew": item.get("crew", []),
                "crew_roles": item.get("crew_roles", []),
                "as_executed": True,
            })

        # 5. web/videoYt.json & web/videoPDS.json
        vyt_path = mission.web_dir / "videoYt.json"
        if vyt_path.exists():
            for v in json.loads(vyt_path.read_text(encoding="utf-8")):
                st = v.get("startTime")
                et = v.get("endTime") or st
                title = v.get("title", "")
                if st and "Official Broadcast" not in title:
                    self.events.append({
                        "id": f"yt:{v.get('id', '')}",
                        "t": st,
                        "start_ts": _parse_ts(st),
                        "end_ts": _parse_ts(et),
                        "kind": "BROADCAST_EVENT",
                        "label": title,
                    })

        vpds_path = mission.web_dir / "videoPDS.json"
        if vpds_path.exists():
            for v in json.loads(vpds_path.read_text(encoding="utf-8")):
                st = v.get("startTime")
                et = v.get("endTime") or st
                if st:
                    self.events.append({
                        "id": f"saw3:{v.get('nasaId', '')}",
                        "t": st,
                        "start_ts": _parse_ts(st),
                        "end_ts": _parse_ts(et),
                        "kind": "ORION_SAW3_VIDEO",
                        "label": f"Orion external SAW3 camera recording ({v.get('nasaId', '')})",
                    })

        self.events.sort(key=lambda x: x["start_ts"])

        # 6. web/photos.json (crew photo timestamps + rich captions)
        photos_path = mission.web_dir / "photos.json"
        if photos_path.exists():
            seen_captions: set[str] = set()
            for p in json.loads(photos_path.read_text(encoding="utf-8")):
                dt_str = p.get("date")
                if not dt_str or dt_str < "2026-04-01T00:00:00Z" or dt_str.endswith("T00:00:00Z"):
                    continue
                try:
                    ts = _parse_ts(dt_str)
                except ValueError:
                    continue
                pid = p.get("id", "")
                desc = (p.get("description") or "").strip()
                title = (p.get("title") or "").strip()
                if pid.startswith("art002e"):
                    self.crew_photo_ts.append(ts)
                    self.crew_photos.append({
                        "id": pid,
                        "t": dt_str,
                        "ts": ts,
                        "camera": p.get("camera") or "",
                        "lens": p.get("lens") or "",
                        "crew": infer_crew_members(f"{title} {desc}"),
                    })
                if desc and not desc.startswith("FD0") and desc not in seen_captions:
                    seen_captions.add(desc)
                    short_desc = desc[:160] + ("..." if len(desc) > 160 else "")
                    self.photo_captions.append({
                        "ts": ts,
                        "t": dt_str,
                        "id": pid,
                        "text": f"{title}: {short_desc}" if title else short_desc,
                        "crew": infer_crew_members(f"{title} {desc}"),
                    })
            self.crew_photo_ts.sort()
            self.crew_photos.sort(key=lambda x: x["ts"])
            self.photo_captions.sort(key=lambda x: x["ts"])

    def _nearby_events(self, start_ts: float, end_ts: float) -> list[dict]:
        """Return nearby events; planned PDF items get a wider ±90 min watch margin."""
        out: list[dict] = []
        for ev in self.events:
            margin = 90 * 60 if ev.get("kind") == "FLIGHT_PLAN" else 30 * 60
            if ev["end_ts"] >= start_ts - margin and ev["start_ts"] <= end_ts + margin:
                out.append(ev)
        return out

    def query_event_ids(self, start_ts: float, end_ts: float) -> set[str]:
        """Catalog links available in this window; media IDs are never event IDs."""
        return {
            ev["id"] for ev in self._nearby_events(start_ts, end_ts)
            if _is_event_reference(ev) and ev.get("kind") != "PLANNED_BLOCK"
        }

    def query_window(self, start_ts: float, end_ts: float) -> list[str]:
        """Return formatted context lines for a batch window [start_ts, end_ts]."""
        lines: list[str] = []

        # Active pre-flight planned schedule blocks from Overview Timeline PDF
        for blk in self.planned_blocks:
            if blk["end_ts"] >= start_ts - 1800 and blk["start_ts"] <= end_ts + 1800:
                lines.append(
                    f"- [PLANNED_SCHEDULE_BLOCK ({blk['flight_day']} {blk['phase']}, pre-flight PDF as-planned)] "
                    f"@ {blk['start_utc']}..{blk['end_utc'][11:]}: {blk['title']} — {blk['description']}"
                )

        # Nearby events/milestones
        for ev in self._nearby_events(start_ts, end_ts):
            id_kind = "event_id" if _is_event_reference(ev) else "media_id"
            lines.append(f"- [{ev['kind']}] {id_kind}=\"{ev['id']}\" @ {ev['t']}: {ev['label']}")

        # Crew handheld camera activity inside exact window
        in_win_photos = sum(1 for ts in self.crew_photo_ts if start_ts <= ts <= end_ts)
        if in_win_photos > 0:
            lines.append(
                f"- [CREW CAMERA ACTIVITY] Crew captured {in_win_photos} handheld photo(s) (art002e*) during this exact window."
            )

        # Nearby captioned photos (within ±15 min, max 5)
        cap_margin = 15 * 60
        caps = [
            c for c in self.photo_captions
            if (start_ts - cap_margin) <= c["ts"] <= (end_ts + cap_margin)
        ][:5]
        for c in caps:
            lines.append(f"- [PHOTO CAPTION] media_id=\"{c['id']}\" @ {c['t']}: {c['text']}")

        return lines
