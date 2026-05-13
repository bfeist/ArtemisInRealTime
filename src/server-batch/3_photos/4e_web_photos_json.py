"""Step 4e — Emit the slim per-photo JSON the frontend consumes.

Reads the ledger; for every record that is `exported == True` AND falls
within the mission UTC date window, emits a stripped record pointing at our
own tier URLs. Bracketed sets collapse to one top-level entry per `set_id`
(the hero frame); other members are referenced via `bracket.alternates`.

Output: `mission.web_dir / "photos.json"`

Replaces the per-record output produced by the legacy `3k_web_photos.py`,
which depended on third-party CDN URLs and emitted one entry per Flickr/EOL
copy without bracket awareness.
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


def _in_window(rec: LedgerRecord, start: date, end: date) -> bool:
    if not rec.utc:
        return False
    try:
        d = date.fromisoformat(rec.utc[:10])
    except ValueError:
        return False
    return start <= d <= end


def _record_to_web(rec: LedgerRecord, alternates: list[str] | None) -> dict:
    out: dict = {
        "id":          rec.nasa_id,
        "title":       rec.title,
        "description": rec.description,
        "date":        rec.utc,
        "thumbUrl":    f"{TIER_URL_BASE}/thumb/{rec.nasa_id}.jpg",
        "imgUrl":      f"{TIER_URL_BASE}/lowres/{rec.nasa_id}.jpg",
        "hiResUrl":    f"{TIER_URL_BASE}/hires/{rec.nasa_id}.jpg",
        "exportedIn":  rec.exported_in,
    }
    if rec.bracket and alternates is not None:
        out["bracket"] = {
            "setId":      rec.bracket.set_id,
            "isHero":     True,
            "alternates": alternates,
        }
    return out


def build_web_json(mission: MissionConfig) -> None:
    ledger = load_ledger(mission.photos_ledger_path)
    if not ledger:
        console.print(
            f"[yellow]No ledger at {mission.photos_ledger_path} — run step 4c first.[/yellow]"
        )
        return

    win_start = date.fromisoformat(mission.mission_start)
    win_end = date.fromisoformat(mission.mission_end)

    # Index bracket sets so the hero entry can list its alternates.
    set_alts: dict[str, list[str]] = {}
    for rec in ledger.values():
        if rec.bracket:
            set_alts.setdefault(rec.bracket.set_id, list(rec.bracket.members))

    out: list[dict] = []
    skipped_unexported = skipped_outside_window = skipped_non_hero = 0
    for rec in ledger.values():
        if not rec.exported:
            skipped_unexported += 1
            continue
        if not _in_window(rec, win_start, win_end):
            skipped_outside_window += 1
            continue
        if rec.bracket and not rec.bracket.is_hero:
            # Non-hero frames are surfaced inside the hero's `bracket.alternates`.
            skipped_non_hero += 1
            continue
        alternates = None
        if rec.bracket:
            alternates = [m for m in set_alts.get(rec.bracket.set_id, []) if m != rec.nasa_id]
        out.append(_record_to_web(rec, alternates))

    out.sort(key=lambda p: p.get("date") or "9999")

    out_path = mission.web_dir / "photos.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    table = Table(title="Web JSON build", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Ledger records",          f"{len(ledger):,}")
    table.add_row("Published",                f"{len(out):,}")
    table.add_row("Skipped (not exported)",  f"{skipped_unexported:,}")
    table.add_row("Skipped (outside window)", f"{skipped_outside_window:,}")
    table.add_row("Skipped (non-hero bracket member)", f"{skipped_non_hero:,}")
    console.print(table)
    console.print(f"\n  [green]Wrote -> {out_path}[/green]")


def main():
    parser = argparse.ArgumentParser(description="Emit web/photos.json from the ledger")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    console.print(f"\n[bold]=== Step 4e: Web photos JSON — {mission.name} ===[/bold]\n")
    build_web_json(mission)


if __name__ == "__main__":
    main()
