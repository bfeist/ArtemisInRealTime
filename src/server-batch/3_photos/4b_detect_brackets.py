"""Step 4b — Detect AEB bracket sets.

Reads the per-copy EXIF JSONs written by step 4a (preferring `raw_crew` —
NEF/DNG — when available, falling back to EOL JPEGs) and groups frames into
auto-exposure-bracket sets.

Algorithm, in priority order:
  1. **ShootingMode flag.** If the camera records
     `MakerNotes.ShootingMode` containing "Bracketing" (Nikon's signal for
     active AEB), group consecutive frames on the same roll within a 5-second
     window with varying `ExposureBiasValue`. This is the strongest signal
     for the D5 / D6 cameras the crew used. Hero = the EV-zero frame.
  2. **BracketShotNumber.** Older Canon-style cameras tag each frame with
     "2 of 3" — group by matching `BracketShootCount`.
  3. **EV-only heuristic.** Fallback for cameras / formats that don't surface
     either above (e.g. JPEG re-encodes that dropped makernotes). Group
     consecutive frames on the same roll within a 5-second window with
     varying `ExposureBiasValue` and stable `FocalLength`. (FNumber is
     intentionally NOT required — Nikon AEB in manual mode varies aperture
     to achieve EV change.)
  4. **Singletons.** Anything not matched is dropped (no record written).

Output: `mission.bracket_sets_path` (`io_cache/bracket_sets.jsonl`), one
record per set:

    {"set_id": "art002e000168",
     "members": ["art002e000167", "art002e000168", "art002e000169"],
     "evs":     [-1.0, 0.0, 1.0],
     "hero":    "art002e000168",
     "detection_source": "makernote"}

The ledger build (4c) joins this in.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.io_api import save_jsonl

console = Console()

# ── Helpers ──────────────────────────────────────────────────────────────────


def _frame_index(nasa_id: str) -> int | None:
    """Trailing digits of a NASA ID — used to detect 'consecutive' frames."""
    m = re.search(r"(\d+)$", nasa_id)
    return int(m.group(1)) if m else None


def _roll_key(nasa_id: str) -> str:
    """Everything before the trailing digit run.
    'art002e000168' → 'art002e'    'jsc2026e012345' → 'jsc2026e'
    """
    return re.sub(r"\d+$", "", nasa_id)


def _parse_dt(payload: dict) -> datetime | None:
    """Best-effort timestamp from an EXIF payload (top-level or nested EXIF)."""
    s = (
        payload.get("DateTimeOriginalUTC")
        or payload.get("DateTimeOriginal")
        or payload.get("EXIF", {}).get("DateTimeOriginal")
    )
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",   # sub-second UTC (4a's new format)
        "%Y-%m-%dT%H:%M:%SZ",      # second-precision UTC
        "%Y:%m:%d %H:%M:%S.%f",    # camera local with sub-second
        "%Y:%m:%d %H:%M:%S",       # camera local, second-precision
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _shooting_mode(payload: dict) -> str:
    """MakerNotes:ShootingMode (Nikon) → empty string when absent."""
    return str(payload.get("MakerNotes", {}).get("ShootingMode", "") or "")


def _ev(payload: dict) -> float | None:
    """Best EV value from any of the source-specific tags."""
    for v in (
        payload.get("ExposureBiasValue"),
        payload.get("EXIF", {}).get("ExposureCompensation"),
        payload.get("MakerNotes", {}).get("ExposureBracketValue"),
    ):
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _focal_length(payload: dict):
    return payload.get("FocalLength") or payload.get("EXIF", {}).get("FocalLength")


def _fnumber(payload: dict):
    return payload.get("FNumber") or payload.get("EXIF", {}).get("FNumber")


def _bracket_shot_payload(payload: dict):
    """`BracketShotNumber` lives at top level for Canons, MakerNotes for some Nikons."""
    return (
        payload.get("BracketShotNumber")
        or payload.get("MakerNotes", {}).get("BracketShotNumber")
    )


def _bracket_count_payload(payload: dict):
    return (
        payload.get("BracketShootCount")
        or payload.get("MakerNotes", {}).get("BracketShootCount")
    )


def _parse_bracket_shot(value) -> tuple[int, int] | None:
    """ '2 of 3' or (2, 3) or '2/3' → (current, total). Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    s = str(value).strip()
    m = re.match(r"^(\d+)\s*(?:of|/)\s*(\d+)$", s, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _ev_to_position(ev: float, all_evs: list[float]) -> str:
    """Return 'darker' | 'metered' | 'lighter' based on EV vs the set."""
    if ev == 0.0:
        return "metered"
    if ev < 0.0:
        return "darker"
    return "lighter"


# ── Frame loading ────────────────────────────────────────────────────────────


def _load_frames(exif_dir: Path) -> dict[str, dict]:
    """Build {nasa_id: payload} preferring raw_crew, falling back to eol.

    Other JPEG sources rarely carry useful bracket info (re-encoded JPEGs
    drop makernotes), but they're consulted as a last resort so we still
    catch e.g. a flickr-only release of a bracketed shot.
    """
    out: dict[str, dict] = {}
    # Preference order: raw_crew > eol > flickr > nasa_images > ia_stills
    for source in ("raw_crew", "eol", "flickr", "nasa_images", "ia_stills"):
        src_dir = exif_dir / source
        if not src_dir.exists():
            continue
        for jf in src_dir.iterdir():
            if jf.suffix != ".json":
                continue
            nasa_id = jf.stem
            if nasa_id in out:
                continue   # already have a higher-priority copy
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    out[nasa_id] = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return out


# ── Detection ────────────────────────────────────────────────────────────────


def _group_by_roll(frames: dict[str, dict]) -> dict[str, list[str]]:
    """Bucket NASA IDs by roll prefix, sorted by frame index within each roll."""
    rolls: dict[str, list[str]] = {}
    for nid in frames:
        rolls.setdefault(_roll_key(nid), []).append(nid)
    for ids in rolls.values():
        ids.sort(key=lambda n: (_frame_index(n) or 0, n))
    return rolls


def _emit_set(members: list[str], evs: list[float],
              detection_source: str) -> dict | None:
    """Build a set record from accumulated members. Returns None if the
    sequence isn't a valid bracket (need ≥2 frames and varying EV)."""
    if len(members) < 2 or len(set(evs)) < 2:
        return None
    try:
        hero_idx = next(k for k, ev in enumerate(evs) if ev == 0.0)
    except StopIteration:
        hero_idx = len(members) // 2
    return {
        "set_id": members[hero_idx],
        "members": members,
        "evs": evs,
        "hero": members[hero_idx],
        "detection_source": detection_source,
    }


def _detect_shooting_mode_sets(
    rolls: dict[str, list[str]],
    frames: dict[str, dict],
    *,
    time_window_s: float = 8.0,
    inter_frame_gap_s: float = 3.0,
    ev_match_tolerance: float = 0.3,
) -> tuple[list[dict], set[str]]:
    """Strongest signal: camera tagged the frames as bracketed in MakerNotes.

    Walks consecutive frames on the same roll where ShootingMode contains
    "Bracketing" and EV varies. A new bracket set is started whenever:
      - The candidate's EV equals the current set's first EV AND we've seen
        ≥2 distinct EVs already (i.e. one bracket cycle is complete and
        the camera is repeating it). This catches the common AEB pattern
        of `0,-,+` repeated multiple times in a continuous burst.
      - The gap between consecutive frames exceeds `inter_frame_gap_s`,
        OR the total span exceeds `time_window_s` (camera was paused).

    Hero = the EV-0 frame in each emitted set.
    """
    sets: list[dict] = []
    consumed: set[str] = set()

    for ids in rolls.values():
        i = 0
        while i < len(ids):
            nid = ids[i]
            if nid in consumed:
                i += 1
                continue
            payload = frames[nid]
            if "Bracketing" not in _shooting_mode(payload):
                i += 1
                continue
            anchor_dt = _parse_dt(payload)
            anchor_ev = _ev(payload)
            if anchor_dt is None or anchor_ev is None:
                i += 1
                continue

            members = [nid]
            evs = [anchor_ev]
            prev_dt = anchor_dt
            j = i + 1
            while j < len(ids):
                cand = ids[j]
                if cand in consumed:
                    break
                cand_payload = frames[cand]
                if "Bracketing" not in _shooting_mode(cand_payload):
                    break
                cand_dt = _parse_dt(cand_payload)
                cand_ev = _ev(cand_payload)
                if cand_dt is None or cand_ev is None:
                    break
                # Time-based break: gap between consecutive shots, or total span
                if (cand_dt - prev_dt).total_seconds() > inter_frame_gap_s:
                    break
                if (cand_dt - anchor_dt).total_seconds() > time_window_s:
                    break
                # EV-cycle break: bracket repeats the starting EV after at
                # least 2 distinct EVs were seen → the next cycle is starting,
                # finish the current set here so the burst splits cleanly.
                # Tolerance handles AWB / metering drift (Hugin uses 0.5 EV
                # for clustering; we use 0.3 EV to preserve burst boundaries
                # without lumping legitimately-distinct exposures together).
                if (abs(cand_ev - anchor_ev) < ev_match_tolerance
                        and len(set(evs)) >= 2):
                    break
                members.append(cand)
                evs.append(cand_ev)
                prev_dt = cand_dt
                j += 1

            rec = _emit_set(members, evs, "shooting_mode")
            if rec is not None:
                sets.append(rec)
                consumed.update(members)
                i = j      # next iteration starts AT j (the EV-cycle break frame)
            else:
                i += 1

    return sets, consumed


def _detect_makernote_sets(
    rolls: dict[str, list[str]],
    frames: dict[str, dict],
    consumed: set[str],
) -> tuple[list[dict], set[str]]:
    """Older Canon-style cameras tag each frame with `BracketShotNumber`
    ("2 of 3") and `BracketShootCount`. Group by walking forward from a
    "1 of N" frame."""
    sets: list[dict] = []

    for ids in rolls.values():
        i = 0
        while i < len(ids):
            nid = ids[i]
            if nid in consumed:
                i += 1
                continue
            payload = frames[nid]
            shot = _parse_bracket_shot(_bracket_shot_payload(payload))
            count = _bracket_count_payload(payload)
            try:
                count = int(count) if count is not None else None
            except (TypeError, ValueError):
                count = None
            if not shot or not count or count < 2 or shot[1] != count:
                i += 1
                continue

            members = [nid]
            j = i + 1
            while j < len(ids) and len(members) < count:
                next_nid = ids[j]
                if next_nid in consumed:
                    break
                next_payload = frames[next_nid]
                next_shot = _parse_bracket_shot(_bracket_shot_payload(next_payload))
                next_count = _bracket_count_payload(next_payload)
                try:
                    next_count = int(next_count) if next_count is not None else None
                except (TypeError, ValueError):
                    next_count = None
                if (next_shot and next_count == count
                        and next_shot[1] == count
                        and next_shot[0] == len(members) + 1):
                    members.append(next_nid)
                    j += 1
                else:
                    break

            if len(members) == count:
                evs = [_ev(frames[m]) or 0.0 for m in members]
                try:
                    hero_idx = next(k for k, ev in enumerate(evs) if ev == 0.0)
                except StopIteration:
                    hero_idx = len(members) // 2
                sets.append({
                    "set_id": members[hero_idx],
                    "members": members,
                    "evs": evs,
                    "hero": members[hero_idx],
                    "detection_source": "makernote_shotnumber",
                })
                consumed.update(members)
                i = j
            else:
                i += 1

    return sets, consumed


def _detect_ev_heuristic_sets(
    rolls: dict[str, list[str]],
    frames: dict[str, dict],
    consumed: set[str],
    *,
    time_window_s: float = 5.0,
) -> list[dict]:
    """EV-difference fallback for cameras / formats with no MakerNotes
    bracket signal — typically JPEG re-encodes that dropped makernotes.

    Requires ≥3 consecutive frames on the same roll with varying EV inside
    a short time window and stable focal length. FNumber is intentionally
    NOT required to match (Nikon AEB in manual mode varies aperture).
    """
    sets: list[dict] = []
    for ids in rolls.values():
        i = 0
        while i < len(ids):
            nid = ids[i]
            if nid in consumed:
                i += 1
                continue
            payload = frames[nid]
            ev = _ev(payload)
            anchor_dt = _parse_dt(payload)
            if ev is None or anchor_dt is None:
                i += 1
                continue
            anchor_focal = _focal_length(payload)

            members = [nid]
            evs = [ev]
            j = i + 1
            while j < len(ids):
                cand = ids[j]
                if cand in consumed:
                    break
                cand_payload = frames[cand]
                cand_ev = _ev(cand_payload)
                cand_dt = _parse_dt(cand_payload)
                if cand_ev is None or cand_dt is None:
                    break
                if _focal_length(cand_payload) != anchor_focal:
                    break
                if abs((cand_dt - anchor_dt).total_seconds()) > time_window_s:
                    break
                members.append(cand)
                evs.append(cand_ev)
                j += 1

            if len(members) >= 3 and len(set(evs)) >= 3:
                try:
                    hero_idx = next(k for k, ev in enumerate(evs) if ev == 0.0)
                except StopIteration:
                    hero_idx = len(members) // 2
                sets.append({
                    "set_id": members[hero_idx],
                    "members": members,
                    "evs": evs,
                    "hero": members[hero_idx],
                    "detection_source": "ev_heuristic",
                })
                consumed.update(members)
                i = j
            else:
                i += 1

    return sets


# ── main ────────────────────────────────────────────────────────────────────


def detect_brackets(mission: MissionConfig) -> None:
    exif_dir = mission.exif_cache_dir
    if not exif_dir.exists():
        console.print(f"[yellow]No EXIF cache at {exif_dir} — run step 4a first.[/yellow]")
        return

    frames = _load_frames(exif_dir)
    console.print(f"  Loaded EXIF for [cyan]{len(frames):,}[/cyan] unique NASA IDs")
    if not frames:
        return

    rolls = _group_by_roll(frames)
    console.print(f"  Grouped into [cyan]{len(rolls):,}[/cyan] rolls")

    sm_sets, consumed = _detect_shooting_mode_sets(rolls, frames)
    console.print(f"  ShootingMode sets:  [green]{len(sm_sets):,}[/green]")

    mn_sets, consumed = _detect_makernote_sets(rolls, frames, consumed)
    console.print(f"  ShotNumber sets:    [green]{len(mn_sets):,}[/green]")

    ev_sets = _detect_ev_heuristic_sets(rolls, frames, consumed)
    console.print(f"  EV-heuristic sets:  [green]{len(ev_sets):,}[/green]")

    all_sets = sm_sets + mn_sets + ev_sets
    save_jsonl(mission.bracket_sets_path, all_sets)
    console.print(
        f"\n  Wrote [cyan]{len(all_sets):,}[/cyan] sets covering "
        f"[cyan]{sum(len(s['members']) for s in all_sets):,}[/cyan] frames "
        f"-> {mission.bracket_sets_path}"
    )


def main():
    parser = argparse.ArgumentParser(description="Detect AEB bracket sets")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    console.print(f"\n[bold]=== Step 4b: Bracket detection — {mission.name} ===[/bold]\n")
    detect_brackets(mission)


if __name__ == "__main__":
    main()
