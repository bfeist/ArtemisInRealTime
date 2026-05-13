"""Step 3i — Rename existing EOL JPEGs to canonical NASA ID filenames.

EOL's portal serves files as `ART002-E-168.JPG`. The rest of the pipeline
keys everything off canonical NASA IDs (`art002e000168`), so an unrenamed
EOL JPEG ends up as a separate ledger entry from the matching crew NEF.

This script walks `mission.photos_eol` and renames every file in place.
Idempotent: files already in canonical form are left alone, files that
look like neither are reported and skipped.

Going forward, `3h_download_eol_photos.py` writes canonical names directly
so this rename is a one-time migration.

Usage:
  uv run python 3_photos/3i_eol_rename_canonical.py --mission artemis-ii
  uv run python 3_photos/3i_eol_rename_canonical.py --mission artemis-ii --dry-run
"""

import argparse
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.eol_naming import to_canonical_filename

console = Console()


def rename_eol(mission: MissionConfig, dry_run: bool) -> None:
    eol_dir = mission.photos_eol
    if not eol_dir.exists():
        console.print(f"[yellow]No EOL dir at {eol_dir}[/yellow]")
        return

    renamed = already = unknown = collisions = 0
    for src in eol_dir.iterdir():
        if not src.is_file():
            continue
        canonical = to_canonical_filename(src.name)
        if canonical is None:
            console.print(f"  [red]?[/red] {src.name}  (unrecognised)")
            unknown += 1
            continue
        if src.name == canonical:
            already += 1
            continue
        dest = src.with_name(canonical)
        if dest.exists():
            console.print(f"  [yellow]![/yellow] {src.name} -> {canonical}  (target exists, skipping)")
            collisions += 1
            continue
        if dry_run:
            console.print(f"  [dim](dry-run)[/dim] {src.name} -> {canonical}")
        else:
            src.rename(dest)
        renamed += 1

    console.print(
        f"\n  Renamed:    [cyan]{renamed:,}[/cyan]"
        + ("  [dim](dry run — nothing actually moved)[/dim]" if dry_run else "")
    )
    console.print(f"  Already canonical: [cyan]{already:,}[/cyan]")
    if unknown:
        console.print(f"  Unrecognised:      [red]{unknown:,}[/red]")
    if collisions:
        console.print(f"  Collisions:        [yellow]{collisions:,}[/yellow]")


def main():
    parser = argparse.ArgumentParser(
        description="Rename EOL JPEGs to canonical NASA ID filenames"
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be renamed without touching the filesystem",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    console.print(f"\n[bold]=== Step 3i: Rename EOL JPEGs to canonical — {mission.name} ===[/bold]\n")
    rename_eol(mission, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
