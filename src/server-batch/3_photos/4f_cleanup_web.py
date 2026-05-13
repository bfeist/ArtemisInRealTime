"""Step 4f — Remove stale files under web/photos/.

Walks `web/photos/{thumb,lowres,hires,exif}/` and deletes any file whose
NASA ID is no longer in the publishable set. The publishable set is:

  - every `id` in photos.json
  - every `members[i]` of every bracket set in photos/brackets.json

Files outside that set are leftovers from prior wider-window or pre-filter
runs (e.g. tier JPEGs for photos that fell outside the mission date window
when we tightened it).

Idempotent. Use `--dry-run` to preview.
"""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

console = Console()


def _publishable_nasa_ids(mission: MissionConfig) -> set[str]:
    """Read the current photos.json + brackets.json and return every NASA
    ID the frontend may reference."""
    out: set[str] = set()
    photos_path = mission.web_dir / "photos.json"
    if not photos_path.exists():
        console.print(f"[red]No photos.json at {photos_path} — run 4e first.[/red]")
        return out
    with open(photos_path, "r", encoding="utf-8") as f:
        for p in json.load(f):
            out.add(p["id"])
    brackets_path = mission.web_dir / "photos" / "brackets.json"
    if brackets_path.exists():
        with open(brackets_path, "r", encoding="utf-8") as f:
            for sid, b in json.load(f).items():
                out.update(b.get("members", []))
    return out


def cleanup(mission: MissionConfig, *, dry_run: bool) -> None:
    publishable = _publishable_nasa_ids(mission)
    if not publishable:
        return
    console.print(f"  Publishable NASA IDs: [cyan]{len(publishable):,}[/cyan]")

    web_photos = mission.web_dir / "photos"
    targets = [
        ("thumb",  web_photos / "thumb",  ".jpg"),
        ("lowres", web_photos / "lowres", ".jpg"),
        ("hires",  web_photos / "hires",  ".jpg"),
        ("exif",   web_photos / "exif",   ".json"),
    ]

    table = Table(title="Stale-file cleanup", show_lines=False)
    table.add_column("Folder", style="bold")
    table.add_column("On disk",   justify="right")
    table.add_column("Stale",     justify="right")
    table.add_column("Action",    justify="left")

    grand_stale = 0
    grand_freed = 0
    for label, dir_, ext in targets:
        if not dir_.exists():
            table.add_row(label, "0", "0", "[dim](dir missing)[/dim]")
            continue
        on_disk = [f for f in dir_.iterdir() if f.suffix.lower() == ext]
        stale = [f for f in on_disk if f.stem not in publishable]
        freed = sum(f.stat().st_size for f in stale)
        grand_stale += len(stale)
        grand_freed += freed
        action = (
            f"[dim]would delete[/dim]" if dry_run
            else f"[red]deleting[/red]"
        )
        if not stale:
            action = "[green]nothing to do[/green]"
        table.add_row(label, f"{len(on_disk):,}", f"{len(stale):,}", action)

        if not dry_run:
            for f in stale:
                try:
                    f.unlink()
                except OSError as e:
                    console.print(f"[yellow]  failed to delete {f}: {e}[/yellow]")

    console.print(table)
    mb = grand_freed / (1024 * 1024)
    console.print(
        f"\n  Total stale: [cyan]{grand_stale:,}[/cyan]  "
        f"Disk reclaimed: [cyan]{mb:.1f} MB[/cyan]"
        + ("  [dim](dry run — nothing actually deleted)[/dim]" if dry_run else "")
    )


def main():
    parser = argparse.ArgumentParser(
        description="Remove stale files from web/photos/ that the current "
                    "photos.json/brackets.json no longer references."
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted, take no action")
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    console.print(f"\n[bold]=== Step 4f: web/ cleanup — {mission.name} ===[/bold]\n")
    cleanup(mission, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
