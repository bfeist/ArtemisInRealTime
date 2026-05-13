"""Step 4e — Emit the JSON the frontend consumes.

Reads the ledger and writes everything the website needs under
`mission.web_dir / "photos"`:

  photos.json                — slim per-photo list, one entry per published
                               hero frame (bracket members collapse onto hero)
  photos/brackets.json       — full bracket-set definitions, indexed by set_id
                               (frontend uses to navigate alternates)
  photos/exif/{nasa_id}.json — full EXIF dump per published photo, lazy-loaded
                               by the frontend when the user opens details

Filter: published = `exported == True` AND `utc` falls within the mission's
`[mission_start, mission_end]` UTC window.

The frontend mounts `web/photos/` at `/photos/` so all URLs are
`/photos/{thumb,lowres,hires,exif}/{nasa_id}.{jpg,json}`.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.photos_ledger import LedgerRecord, load_ledger

console = Console()

# Web-relative URL prefix — the published site mounts the asset tree at /photos/
TIER_URL_BASE = "/photos"

# raw_crew (NEF) is always preferred when present — it carries Nikon
# makernotes, sub-second times, and grouped EXIF/MakerNotes/XMP/Composite
# blocks. For non-raw sources the picker falls back to whichever copy has
# the most EXIF fields (see _count_exif_fields).
RAW_EXIF_SOURCE = "raw_crew"
NON_RAW_EXIF_SOURCES = ("eol", "nasa_images", "flickr", "ia_stills")


def _in_window(rec: LedgerRecord, start: date, end: date) -> bool:
    if not rec.utc:
        return False
    try:
        d = date.fromisoformat(rec.utc[:10])
    except ValueError:
        return False
    return start <= d <= end


def _record_to_web(rec: LedgerRecord, *, has_publishable_set: bool) -> dict:
    """Slim per-photo entry. The full bracket-set definition (members, EVs,
    detection source) lives in brackets.json so we don't duplicate it here."""
    out: dict = {
        "id":          rec.nasa_id,
        "title":       rec.title,
        "description": rec.description,
        "date":        rec.utc,
        "dateSource":  rec.utc_source,    # provenance: exif_offset | io_corrected | …
        "thumbUrl":    f"{TIER_URL_BASE}/thumb/{rec.nasa_id}.jpg",
        "imgUrl":      f"{TIER_URL_BASE}/lowres/{rec.nasa_id}.jpg",
        "hiResUrl":    f"{TIER_URL_BASE}/hires/{rec.nasa_id}.jpg",
        "exifUrl":     f"{TIER_URL_BASE}/exif/{rec.nasa_id}.json",
        "exportedIn":  rec.exported_in,
    }
    if rec.bracket and has_publishable_set:
        # Just a pointer — frontend looks up members + EVs in brackets.json
        out["bracketSetId"] = rec.bracket.set_id
    return out


def _count_exif_fields(exif: dict) -> int:
    """Recursively count scalar fields in an EXIF payload. Nested groups
    (EXIF, MakerNotes, XMP, Composite, GPSInfo, …) contribute their inner
    fields too. Used as a "richness" score for picking which JPEG copy to
    publish when no NEF is available.
    """
    if not exif:
        return 0
    n = 0
    for v in exif.values():
        if isinstance(v, dict):
            n += _count_exif_fields(v)
        else:
            n += 1
    return n


def _published_exif_payload(rec: LedgerRecord) -> dict:
    """Pick the best per-copy EXIF block for publication.

    Always prefers raw_crew (NEF) when present — it has the Nikon makernotes
    and grouped EXIF that EOL/Flickr re-encodes strip. Otherwise picks
    whichever non-raw copy has the most fields (recursively counted), since
    a JPEG that survived a round-trip through a single re-encoder usually
    keeps more tags than one that went through several.

    Returns the EXIF dict augmented with a small `_meta` sidecar that records
    which physical copy this EXIF came from and the resolved timeline UTC.
    """
    chosen_source: str | None = None
    chosen_exif: dict = {}

    raw = rec.copies.get(RAW_EXIF_SOURCE)
    if raw and raw.exif:
        chosen_source = RAW_EXIF_SOURCE
        chosen_exif = raw.exif
    else:
        best_count = 0
        for src in NON_RAW_EXIF_SOURCES:
            copy = rec.copies.get(src)
            if not copy or not copy.exif:
                continue
            count = _count_exif_fields(copy.exif)
            if count > best_count:
                best_count = count
                chosen_source = src
                chosen_exif = copy.exif

    payload = dict(chosen_exif)  # shallow copy so we don't mutate the ledger
    payload["_meta"] = {
        "nasaId":      rec.nasa_id,
        "exifSource":  chosen_source,
        "exifFieldCount": _count_exif_fields(chosen_exif),
        "date":        rec.utc,
        "dateSource":  rec.utc_source,
        "exported":    rec.exported,
        "exportedIn":  rec.exported_in,
    }
    return payload


def _bracket_record_to_web(set_id: str, members: list[str], evs: list[float],
                           hero: str, detection_source: str) -> dict:
    """Bracket-set entry for brackets.json. Frontend uses this to render
    alternate-exposure toggles for any photo in a set."""
    return {
        "setId":           set_id,
        "hero":            hero,
        "members":         members,
        "evs":             evs,
        "detectionSource": detection_source,
    }


def build_web_json(mission: MissionConfig) -> None:
    ledger = load_ledger(mission.photos_ledger_path)
    if not ledger:
        console.print(
            f"[yellow]No ledger at {mission.photos_ledger_path} — run step 4c first.[/yellow]"
        )
        return

    win_start = date.fromisoformat(mission.mission_start)
    win_end = date.fromisoformat(mission.mission_end)

    web_photos_dir = mission.web_dir / "photos"
    exif_out_dir = web_photos_dir / "exif"
    exif_out_dir.mkdir(parents=True, exist_ok=True)

    # ── Build the set of NASA IDs that have tiers on disk (i.e. that the ──
    # frontend can actually fetch). bracket.alternates and brackets.json
    # members are filtered to this set so the site never references a 404.
    hires_dir = mission.web_photos_hires
    publishable: set[str] = set()
    if hires_dir.exists():
        publishable = {f.stem for f in hires_dir.iterdir() if f.suffix.lower() == ".jpg"}

    # ── Index bracket sets by set_id (collected from any ledger record) ───
    # Members are filtered to the publishable set so non-exported bracket
    # alternates (camera shot the bracket but NASA only released the hero)
    # don't show up as broken links on the frontend.
    brackets_by_set: dict[str, dict] = {}
    for rec in ledger.values():
        if not rec.bracket:
            continue
        sid = rec.bracket.set_id
        if sid not in brackets_by_set:
            full_members = list(rec.bracket.members)
            full_evs = [_member_ev(ledger, m) for m in full_members]
            kept = [(m, ev) for m, ev in zip(full_members, full_evs) if m in publishable]
            if not kept:
                continue
            members_out, evs_out = zip(*kept)
            brackets_by_set[sid] = _bracket_record_to_web(
                sid,
                list(members_out),
                list(evs_out),
                rec.bracket.set_id,            # hero == set_id by definition
                rec.bracket.detection_source,
            )

    # ── Walk ledger; collect published heroes + write per-photo EXIF ──────
    out: list[dict] = []
    written_exif = 0
    skipped_unexported = skipped_outside_window = skipped_non_hero = 0
    skipped_no_tier = skipped_no_exif = 0

    for rec in ledger.values():
        if not rec.exported:
            skipped_unexported += 1
            continue
        if not _in_window(rec, win_start, win_end):
            skipped_outside_window += 1
            continue
        # Frontend can only render a photo if the tier files exist on disk.
        # The io_exif date tier resolves UTC for plenty of records that have
        # NO copy on disk (date came from the IO scrape alone) — those would
        # publish as broken thumbnails. Gate on tier presence.
        if rec.nasa_id not in publishable:
            skipped_no_tier += 1
            continue

        # Per-photo EXIF dump — written for every published photo (including
        # bracket alternates) so the frontend can pull details for any frame.
        exif_payload = _published_exif_payload(rec)
        if not exif_payload.get("_meta", {}).get("exifSource"):
            # No EXIF anywhere on this record — still write the _meta block
            # so the frontend has a consistent endpoint to hit.
            skipped_no_exif += 1
        exif_path = exif_out_dir / f"{rec.nasa_id}.json"
        with open(exif_path, "w", encoding="utf-8") as f:
            json.dump(exif_payload, f, indent=2, ensure_ascii=False)
        written_exif += 1

        # Photos.json gets one entry per HERO. Non-hero bracket members are
        # accessible via `bracket.alternates` on the hero.
        if rec.bracket and not rec.bracket.is_hero:
            skipped_non_hero += 1
            continue
        # Hero references its bracket set only if that set actually has at
        # least one other publishable member (otherwise the bracketSetId
        # pointer is meaningless).
        has_publishable_set = (
            rec.bracket is not None
            and rec.bracket.set_id in brackets_by_set
            and len(brackets_by_set[rec.bracket.set_id]["members"]) >= 2
        )
        out.append(_record_to_web(rec, has_publishable_set=has_publishable_set))

    out.sort(key=lambda p: p.get("date") or "9999")

    # ── Write photos.json ─────────────────────────────────────────────────
    photos_path = mission.web_dir / "photos.json"
    with open(photos_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    # ── Write brackets.json (only sets whose hero is in the published list) ─
    published_ids = {p["id"] for p in out}
    published_brackets = {
        sid: rec for sid, rec in brackets_by_set.items()
        if rec["hero"] in published_ids
    }
    brackets_path = web_photos_dir / "brackets.json"
    with open(brackets_path, "w", encoding="utf-8") as f:
        json.dump(published_brackets, f, indent=2, ensure_ascii=False)

    # ── Report ────────────────────────────────────────────────────────────
    table = Table(title="Web JSON build", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Ledger records",                    f"{len(ledger):,}")
    table.add_row("Published photos (heroes)",         f"{len(out):,}")
    table.add_row("Per-photo EXIF JSONs written",      f"{written_exif:,}")
    table.add_row("Bracket sets with published hero",  f"{len(published_brackets):,}")
    table.add_row("Skipped (not exported)",            f"{skipped_unexported:,}")
    table.add_row("Skipped (outside window)",          f"{skipped_outside_window:,}")
    table.add_row("Skipped (no tier on disk)",         f"{skipped_no_tier:,}")
    table.add_row("Skipped (non-hero bracket member)", f"{skipped_non_hero:,}")
    table.add_row("[dim]Records with no EXIF[/dim]",   f"[dim]{skipped_no_exif:,}[/dim]")
    console.print(table)
    console.print(f"\n  [green]photos.json     -> {photos_path}[/green]")
    console.print(f"  [green]brackets.json   -> {brackets_path}[/green]")
    console.print(f"  [green]exif/*.json     -> {exif_out_dir}/  ({written_exif:,} files)[/green]")


def _member_ev(ledger: dict[str, LedgerRecord], member_id: str) -> float | None:
    """Look up a bracket member's EV via its own ledger record."""
    rec = ledger.get(member_id)
    if rec and rec.bracket:
        return rec.bracket.ev
    return None


def main():
    parser = argparse.ArgumentParser(description="Emit web JSON outputs from the ledger")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    console.print(f"\n[bold]=== Step 4e: Web JSON — {mission.name} ===[/bold]\n")
    build_web_json(mission)


if __name__ == "__main__":
    main()
