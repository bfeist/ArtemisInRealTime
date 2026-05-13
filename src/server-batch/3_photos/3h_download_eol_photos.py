"""Step 3h — Download EOL large images for Artemis II to disk.

Reads eol_photos.json produced by step 3g, then downloads the large-size image
for every entry that hasn't already been saved.  Re-running the script at any
time resumes from where it left off — already-present files are skipped.

Source URL pattern:
  https://eol.jsc.nasa.gov/DatabaseImages/{large}
  e.g. https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/ART002/ART002-E-168.JPG

Output directory (from config — mission.photos_eol):
  {DATA_DIR}/{mission}/raw/photos/eol/{filename}
  e.g. F:/_repos/ArtemisInRealTime_assets/artemis-ii/raw/photos/eol/ART002-E-168.JPG

Usage:
  uv run python 3_photos/3h_download_eol_photos.py
  uv run python 3_photos/3h_download_eol_photos.py --workers 8
  uv run python 3_photos/3h_download_eol_photos.py --input /custom/path/eol_photos.json
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS
from shared.eol_naming import to_canonical_filename

console = Console()

EOL_BASE = "https://eol.jsc.nasa.gov/DatabaseImages"
DEFAULT_WORKERS = 4


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Download EOL large images for a mission. Resumable — skips existing files."
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to eol_photos.json. Defaults to the mission processed dir output from step 3g.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel download threads (default: {DEFAULT_WORKERS}).",
    )
    return parser.parse_args()


def download_file(url: str, dest: Path) -> tuple[str, bool, str | None]:
    """Download url to dest. Returns (url, success, error_msg)."""
    if dest.exists():
        return url, True, None  # already done

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        tmp.rename(dest)
        return url, True, None
    except requests.RequestException as e:
        if tmp.exists():
            tmp.unlink()
        return url, False, str(e)


def main():
    args = parse_arguments()

    mission = MISSIONS[args.mission]

    # Resolve input JSON
    input_file = args.input or mission.eol_json_path
    if not input_file.exists():
        console.print(f"[bold red]Input file not found: {input_file}[/bold red]")
        console.print("Run step 3g first: uv run python 3_photos/3g_eol_json.py")
        sys.exit(1)

    with open(input_file, encoding="utf-8") as f:
        photos: list[dict] = json.load(f)

    if not photos:
        console.print("[yellow]No photos in input JSON.[/yellow]")
        sys.exit(0)

    # Output directory
    out_dir = mission.photos_eol
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold blue]EOL photo downloader — {mission.name}[/bold blue]")
    console.print(f"Input:      {input_file}  ({len(photos):,} entries)")
    console.print(f"Output dir: {out_dir}")
    console.print(f"Workers:    {args.workers}")

    # Build work list — only entries that have a 'large' path
    work: list[tuple[str, Path]] = []
    skipped_no_large = 0
    already_done = 0

    for photo in photos:
        large_path = photo.get("large")
        if not large_path:
            skipped_no_large += 1
            continue
        url = f"{EOL_BASE}/{large_path}"
        # Save with the canonical NASA ID filename (`art002e000168.jpg`) so the
        # rest of the pipeline doesn't have to special-case the EOL form.
        on_wire_name = Path(large_path).name             # e.g. ART002-E-168.JPG
        canonical = to_canonical_filename(on_wire_name)  # e.g. art002e000168.jpg
        filename = canonical or on_wire_name             # fall back if regex misses
        dest = out_dir / filename
        if dest.exists():
            already_done += 1
        else:
            work.append((url, dest))

    console.print(
        f"\nStatus: [green]{already_done:,} already downloaded[/green], "
        f"[cyan]{len(work):,} to download[/cyan]"
        + (f", [yellow]{skipped_no_large} missing large path[/yellow]" if skipped_no_large else "")
    )

    if not work:
        console.print("\n[bold green]✓ All photos already downloaded.[/bold green]")
        sys.exit(0)

    # Download with progress
    failed: list[tuple[str, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"[cyan]Downloading {len(work):,} photos...", total=len(work)
        )

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(download_file, url, dest): (url, dest) for url, dest in work}
            try:
                for future in as_completed(futures):
                    url, success, err = future.result()
                    if not success:
                        failed.append((url, err or "unknown error"))
                    progress.advance(task)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted — cancelling pending downloads...[/yellow]")
                executor.shutdown(wait=False, cancel_futures=True)
                # Clean up any partial .part files
                for _, dest in work:
                    part = dest.with_suffix(dest.suffix + ".part")
                    if part.exists():
                        part.unlink()
                downloaded = sum(1 for _, d in work if d.exists())
                console.print(f"[yellow]Stopped after {downloaded:,} files. Re-run to resume.[/yellow]")
                sys.exit(0)

    # Summary
    downloaded = len(work) - len(failed)
    console.print(f"\n[bold green]✓ Downloaded {downloaded:,} photos → {out_dir}[/bold green]")
    if already_done:
        console.print(f"[dim]  ({already_done:,} were already present and skipped)[/dim]")
    if failed:
        console.print(f"\n[bold red]✗ {len(failed)} failed:[/bold red]")
        for url, err in failed[:20]:
            console.print(f"  [red]{Path(url).name}[/red]: {err}")
        if len(failed) > 20:
            console.print(f"  ... and {len(failed) - 20} more")


if __name__ == "__main__":
    main()
