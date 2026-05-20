"""Step 3g — Fetch EOL metadata for all Artemis II crew photography.

Queries the NASA Gateway to Astronaut Photography (EOL) Photos Database API
for all photos taken during the Artemis II mission (mission code ART002) and
generates a single JSON manifest.

NOTE: As of the mission timeline, Artemis II photos reside only in the `images`
table — the `nadir`, `frames`, `mlcoord`, and `camera` tables have no records yet
for ART002. When those tables gain data in the future, re-run with --overwrite to
pick up coordinates, timestamps, and other enrichment.

API:    https://eol.jsc.nasa.gov/SearchPhotos/PhotosDatabaseAPI/
Output: {data_dir}/processed/eol_photos.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS

console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

API_ENDPOINT = "https://eol.jsc.nasa.gov/SearchPhotos/PhotosDatabaseAPI/PhotosDatabaseAPI.pl"
BASE_URL = "https://eol.jsc.nasa.gov/DatabaseImages"

# Mapping from mission slug → EOL mission code
EOL_MISSION_CODES: dict[str, str] = {
    "artemis-ii": "ART002",
}

api_key = os.getenv("NASA_EOL_API_KEY")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Fetch EOL metadata for a mission's crew photography and write eol_photos.json."
    )
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output file if it already exists.",
    )
    return parser.parse_args()


def api_get(query: str, return_fields: str, description: str) -> list | None:
    """Make a single API request; return list of records or None on failure."""
    params = {"query": query, "return": return_fields, "key": api_key}
    try:
        r = requests.get(API_ENDPOINT, params=params, timeout=120)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        # API returns {"result": "SQL found no records..."} when empty
        return None
    except (requests.RequestException, json.JSONDecodeError) as e:
        console.print(f"[red]API error ({description}): {e}[/red]")
        return None


def fetch_images_data(mission_code: str) -> list | None:
    """Fetch all image file records for the mission from the images table."""
    return api_get(
        query=f"images|mission|eq|{mission_code}",
        return_fields=(
            "images|mission|images|roll|images|frame"
            "|images|directory|images|filename"
            "|images|filesize|images|width|images|height"
        ),
        description="Images baseline",
    )


def fetch_enrichment_data(mission_code: str, progress, task_id) -> dict:
    """Attempt to fetch enrichment data from nadir/frames/mlcoord/camera tables.

    These tables may be empty for newly uploaded missions. Results are returned
    as a dict keyed by (mission, roll, frame) → merged record.
    """
    query_nadir = f"nadir|mission|eq|{mission_code}"
    query_frames = f"frames|mission|eq|{mission_code}"

    enrichment_queries = [
        # nadir table
        {
            "id": 1, "query": query_nadir,
            "return_fields": "nadir|mission|nadir|roll|nadir|frame|nadir|pdate|nadir|ptime",
            "description": "Nadir datetime",
        },
        {
            "id": 2, "query": query_nadir,
            "return_fields": (
                "nadir|mission|nadir|roll|nadir|frame"
                "|mlcoord|lat|mlcoord|lon"
                "|mlcoord|ul_lat|mlcoord|ul_lon|mlcoord|ur_lat|mlcoord|ur_lon"
                "|mlcoord|ll_lat|mlcoord|ll_lon|mlcoord|lr_lat|mlcoord|lr_lon"
            ),
            "description": "Nadir MLCoord",
        },
        {
            "id": 3, "query": query_nadir,
            "return_fields": "nadir|mission|nadir|roll|nadir|frame|mlfeat|feat",
            "description": "Nadir ML features",
        },
        {
            "id": 4, "query": query_nadir,
            "return_fields": "nadir|mission|nadir|roll|nadir|frame|captions|caption",
            "description": "Nadir captions",
        },
        {
            "id": 5, "query": query_nadir,
            "return_fields": "nadir|mission|nadir|roll|nadir|frame|camera|fclt|camera|camera",
            "description": "Nadir camera",
        },
        # frames table
        {
            "id": 6, "query": query_frames,
            "return_fields": "frames|mission|frames|roll|frames|frame|frames|pdate|frames|ptime|frames|fclt|frames|camera",
            "description": "Frames baseline",
        },
        {
            "id": 7, "query": query_frames,
            "return_fields": (
                "frames|mission|frames|roll|frames|frame"
                "|mlcoord|lat|mlcoord|lon"
                "|mlcoord|ul_lat|mlcoord|ul_lon|mlcoord|ur_lat|mlcoord|ur_lon"
                "|mlcoord|ll_lat|mlcoord|ll_lon|mlcoord|lr_lat|mlcoord|lr_lon"
            ),
            "description": "Frames MLCoord",
        },
        {
            "id": 8, "query": query_frames,
            "return_fields": "frames|mission|frames|roll|frames|frame|frames|feat",
            "description": "Frames features",
        },
        {
            "id": 9, "query": query_frames,
            "return_fields": "frames|mission|frames|roll|frames|frame|captions|caption",
            "description": "Frames captions",
        },
        {
            "id": 10, "query": query_frames,
            "return_fields": "frames|mission|frames|roll|frames|frame|publicfeatures|features",
            "description": "Frames public features",
        },
    ]

    query_status = {q["id"]: "[yellow]·[/yellow]" for q in enrichment_queries}

    def update_display():
        parts = [f"{i:2d}:{query_status[i]}" for i in range(1, 11)]
        return f"[cyan]Enrichment queries: {' '.join(parts)}"

    enrichment: dict = defaultdict(dict)

    def run_query(q):
        result = api_get(q["query"], q["return_fields"], q["description"])
        return q["id"], result

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(run_query, q): q for q in enrichment_queries}
        for future in as_completed(futures):
            qid, result = future.result()
            if result:
                query_status[qid] = "[green]✓[/green]"
                prefix = "nadir" if qid <= 5 else "frames"
                for rec in result:
                    key = (
                        rec.get(f"{prefix}.mission"),
                        rec.get(f"{prefix}.roll"),
                        rec.get(f"{prefix}.frame"),
                    )
                    if all(key):
                        enrichment[key].update(rec)
            else:
                query_status[qid] = "[dim]0[/dim]"

            if progress and task_id is not None:
                done = sum(1 for s in query_status.values() if s != "[yellow]·[/yellow]")
                progress.update(task_id, completed=done, description=update_display())

    return dict(enrichment)


def build_manifest(images_data: list, enrichment: dict) -> list:
    """Group images by (mission, roll, frame), attach enrichment, return manifest."""
    grouped: dict = defaultdict(dict)

    for rec in images_data:
        mission = rec.get("images.mission")
        roll = rec.get("images.roll")
        frame = rec.get("images.frame")
        directory = rec.get("images.directory")
        filename = rec.get("images.filename")

        if not all([mission, roll, frame, directory, filename]):
            continue

        key = (mission, roll, frame)

        if "/large/" in directory:
            grouped[key]["large"] = f"{directory}/{filename}"
            grouped[key]["width"] = rec.get("images.width")
            grouped[key]["height"] = rec.get("images.height")
            grouped[key]["filesize"] = rec.get("images.filesize")

        grouped[key].setdefault("mission", mission)
        grouped[key].setdefault("roll", roll)
        grouped[key].setdefault("frame", frame)
        grouped[key].setdefault("ID", f"{mission}{roll}{frame}")

    manifest = []
    for key, value in grouped.items():
        mission, roll, frame = key
        enrich = enrichment.get(key, {})

        # Determine timestamp — prefer frames, then nadir
        pdate = enrich.get("frames.pdate") or enrich.get("nadir.pdate")
        ptime = enrich.get("frames.ptime") or enrich.get("nadir.ptime")
        try:
            dt = datetime.strptime(f"{pdate}{ptime}", "%Y%m%d%H%M%S")
            date_taken = dt.isoformat() + "Z"
        except (ValueError, TypeError):
            date_taken = None

        entry: dict = {"ID": value["ID"]}
        if date_taken:
            entry["dateTaken"] = date_taken

        if value.get("large"):
            entry["large"] = value["large"]
        if value.get("width"):
            entry["width"] = int(value["width"])
        if value.get("height"):
            entry["height"] = int(value["height"])

        # ML-derived coordinates
        lat = enrich.get("mlcoord.lat")
        lon = enrich.get("mlcoord.lon")
        if lat and lon:
            entry["lat"] = float(lat)
            entry["lon"] = float(lon)
            corner_keys = [
                "mlcoord.ul_lat", "mlcoord.ul_lon",
                "mlcoord.ur_lat", "mlcoord.ur_lon",
                "mlcoord.ll_lat", "mlcoord.ll_lon",
                "mlcoord.lr_lat", "mlcoord.lr_lon",
            ]
            if all(enrich.get(k) for k in corner_keys):
                entry["corners"] = {
                    "ul": {"lat": float(enrich["mlcoord.ul_lat"]), "lon": float(enrich["mlcoord.ul_lon"])},
                    "ur": {"lat": float(enrich["mlcoord.ur_lat"]), "lon": float(enrich["mlcoord.ur_lon"])},
                    "ll": {"lat": float(enrich["mlcoord.ll_lat"]), "lon": float(enrich["mlcoord.ll_lon"])},
                    "lr": {"lat": float(enrich["mlcoord.lr_lat"]), "lon": float(enrich["mlcoord.lr_lon"])},
                }

        ml_feat = enrich.get("mlfeat.feat")
        if ml_feat and ml_feat != "PAN-":
            entry["mlFeat"] = ml_feat

        if enrich.get("frames.feat"):
            entry["feat"] = enrich["frames.feat"]

        if enrich.get("captions.caption"):
            entry["caption"] = enrich["captions.caption"]

        if enrich.get("publicfeatures.features"):
            entry["publicFeatures"] = enrich["publicfeatures.features"]

        focal = enrich.get("frames.fclt") or enrich.get("camera.fclt")
        if focal:
            entry["focalLength"] = int(focal)

        camera = enrich.get("frames.camera") or enrich.get("camera.camera")
        if camera:
            entry["camera"] = camera

        manifest.append(entry)

    # Sort by frame number (integer) since we may not have dateTaken
    manifest.sort(key=lambda e: int("".join(filter(str.isdigit, e["ID"].split("E")[-1])) or "0"))
    return manifest


def main():
    args = parse_arguments()

    if not api_key:
        console.print("[bold red]Error: NASA_EOL_API_KEY not set in .env[/bold red]")
        sys.exit(1)

    mission = MISSIONS[args.mission]
    mission_code = EOL_MISSION_CODES.get(args.mission)
    if not mission_code:
        console.print(f"[bold red]No EOL mission code configured for '{args.mission}'.[/bold red]")
        sys.exit(1)

    output_file = mission.eol_json_path
    output_dir = output_file.parent

    console.print(f"[bold blue]Fetching EOL data for mission {mission_code}[/bold blue]")
    console.print(f"Output: {output_file}")

    if output_file.exists() and not args.overwrite:
        # Load prior manifest so we can merge rather than just skip.
        with open(output_file, "r", encoding="utf-8") as f:
            prior_manifest: list = json.load(f)
        console.print(
            f"[yellow]Output file exists ({len(prior_manifest)} photos). "
            "Fetching fresh data to check for new entries "
            "(use --overwrite to also refresh existing records).[/yellow]"
        )
    else:
        prior_manifest = []

    # Step 1: Fetch all image file records
    console.print("[cyan]Fetching images table...[/cyan]")
    images_data = fetch_images_data(mission_code)
    if not images_data:
        console.print(f"[bold red]No image records found for mission {mission_code}.[/bold red]")
        sys.exit(1)
    console.print(f"[green]  → {len(images_data)} image file records[/green]")

    # Step 2: Attempt to enrich from nadir/frames/mlcoord tables
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        enrich_task = progress.add_task("[cyan]Querying enrichment tables...", total=10)
        enrichment = fetch_enrichment_data(mission_code, progress, enrich_task)
        progress.update(enrich_task, completed=10)

    console.print(f"[green]  → {len(enrichment)} photos with enrichment data[/green]")

    # Step 3: Build manifest
    manifest = build_manifest(images_data, enrichment)
    if not manifest:
        console.print("[bold red]No manifest entries built.[/bold red]")
        sys.exit(1)

    # Merge with prior data when not doing a full overwrite.
    # Key is the EOL composite ID (e.g. "ART002E001").
    if prior_manifest and not args.overwrite:
        prior_by_id = {e["ID"]: e for e in prior_manifest if e.get("ID")}
        fresh_by_id = {e["ID"]: e for e in manifest if e.get("ID")}
        new_ids = set(fresh_by_id) - set(prior_by_id)
        # Prior entries for IDs the API no longer returns are kept intact.
        merged_by_id = {**prior_by_id, **fresh_by_id}
        manifest = sorted(
            merged_by_id.values(),
            key=lambda e: int("".join(filter(str.isdigit, e["ID"].split("E")[-1])) or "0"),
        )
        if new_ids:
            console.print(f"[green]  → {len(new_ids)} new EOL photo(s) added[/green]")
        else:
            console.print("[dim]  → No new EOL photos found since last run.[/dim]")
    elif prior_manifest and args.overwrite:
        pass  # fresh manifest already complete; prior discarded intentionally

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    has_date = sum(1 for e in manifest if "dateTaken" in e)
    has_coords = sum(1 for e in manifest if "lat" in e)
    has_corners = sum(1 for e in manifest if "corners" in e)
    has_camera = sum(1 for e in manifest if "camera" in e)

    summary = Table(title="[bold]Summary[/bold]", show_header=True, header_style="bold magenta")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Count", justify="right", style="green")
    summary.add_row("Total Photos", str(len(manifest)))
    summary.add_row("Prior count", str(len(prior_manifest)) if prior_manifest else "—")
    summary.add_row("With dateTaken", str(has_date))
    summary.add_row("With Coordinates", str(has_coords))
    summary.add_row("With Corner Footprint", str(has_corners))
    summary.add_row("With Camera Info", str(has_camera))
    console.print(summary)

    console.print(f"\n[bold green]✓ Saved {len(manifest)} photos → {output_file}[/bold green]")


if __name__ == "__main__":
    main()
