"""Step 3l — Find exported EOL photos that are NOT in the raw flight imagery.

Compares photos in the EOL exported JPEG folder against the raw crew-captured
imagery (NEF / DNG) to identify exported photos whose raw originals are missing.

Naming conventions
------------------
  EOL exported :  ART002-E-10000.JPG   (uppercase, hyphens, un-padded frame)
  Raw files    :  art002e023048.NEF     (lowercase, no hyphens, 6-digit frame)

The normalised NASA ID is: lowercase mission + roll + zero-padded frame, e.g.
  ART002-E-168.JPG  →  art002e000168

Output: prints a report and optionally writes a text file listing the missing
        NASA IDs.

Usage
-----
  python 3l_flight_nef.py --mission artemis-ii
  python 3l_flight_nef.py --mission artemis-ii --output missing_raws.txt
"""

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, CREW_RAW_SOURCE_DIR, MissionConfig
from shared.eol_naming import to_canonical_nasa_id

console = Console()

# ── Paths ─────────────────────────────────────────────────────────────────────

def _resolve_raw_dir(mission: MissionConfig) -> Path:
    """Prefer the per-mission raw_crew dir if it has files; fall back to the
    legacy CREW_RAW_SOURCE_DIR drop while the migration is in progress."""
    new_loc = mission.photos_raw_crew
    if new_loc.exists() and any(new_loc.iterdir()):
        return new_loc
    return CREW_RAW_SOURCE_DIR


def collect_raw_ids(raw_dir: Path) -> set[str]:
    """Walk the raw imagery tree and return a set of canonical NASA IDs
    (stem only, lowercase)."""
    ids: set[str] = set()
    for p in raw_dir.rglob("*"):
        if p.is_file() and p.suffix.upper() in (".NEF", ".DNG"):
            ids.add(p.stem.lower())
    return ids


def collect_eol_ids(eol_dir: Path) -> dict[str, str]:
    """Return a mapping of normalised NASA ID → original EOL filename."""
    mapping: dict[str, str] = {}
    for p in sorted(eol_dir.iterdir()):
        if not p.is_file():
            continue
        nasa_id = to_canonical_nasa_id(p.name)
        if nasa_id:
            mapping[nasa_id] = p.name
    return mapping


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Find exported EOL photos whose raw originals are missing."
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write missing IDs (one per line).",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    cfg = MISSIONS[args.mission]

    eol_dir = cfg.photos_eol
    raw_dir = _resolve_raw_dir(cfg)

    if not eol_dir.exists():
        console.print(f"[red]EOL directory not found:[/] {eol_dir}")
        sys.exit(1)
    if not raw_dir.exists():
        console.print(f"[red]Raw imagery directory not found:[/] {raw_dir}")
        sys.exit(1)

    # ── Collect IDs ───────────────────────────────────────────────────────

    console.print(f"\n[bold]Scanning EOL exports:[/]  {eol_dir}")
    eol_map = collect_eol_ids(eol_dir)
    console.print(f"  Found [cyan]{len(eol_map):,}[/] exported photos")

    console.print(f"\n[bold]Scanning raw imagery:[/]  {raw_dir}")
    raw_ids = collect_raw_ids(raw_dir)
    console.print(f"  Found [cyan]{len(raw_ids):,}[/] raw files (NEF + DNG)")

    # ── Compare ───────────────────────────────────────────────────────────

    exported_only = sorted(eol_map.keys() - raw_ids)
    raw_only = sorted(raw_ids - eol_map.keys())
    in_both = sorted(eol_map.keys() & raw_ids)

    # ── Summary table ─────────────────────────────────────────────────────

    table = Table(title="EOL ↔ Raw Comparison", show_lines=True)
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("EOL exported (total)", f"{len(eol_map):,}")
    table.add_row("Raw files (total)", f"{len(raw_ids):,}")
    table.add_row("[green]In both (exported & raw)[/]", f"[green]{len(in_both):,}[/]")
    table.add_row(
        "[yellow]Exported but NO raw file[/]", f"[yellow]{len(exported_only):,}[/]"
    )
    table.add_row(
        "[red]Raw file but NOT exported[/]", f"[red]{len(raw_only):,}[/]"
    )
    console.print()
    console.print(table)

    # ── Detail: exported but missing raw ──────────────────────────────────

    if exported_only:
        console.print(
            f"\n[yellow bold]Exported photos missing from raw imagery "
            f"({len(exported_only):,}):[/]\n"
        )
        for nasa_id in exported_only[:50]:
            eol_name = eol_map[nasa_id]
            console.print(f"  {nasa_id}  ←  {eol_name}")
        if len(exported_only) > 50:
            console.print(f"  … and {len(exported_only) - 50:,} more")

    # ── Optional file output ──────────────────────────────────────────────

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "\n".join(exported_only) + "\n", encoding="utf-8"
        )
        console.print(
            f"\n[green]Wrote {len(exported_only):,} missing IDs to {args.output}[/]"
        )

    console.print()


if __name__ == "__main__":
    main()
