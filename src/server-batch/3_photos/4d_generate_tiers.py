"""Step 4d — Generate web tier JPEGs (thumb / lowres / hires).

Walks the ledger; for every record with `exported == True` and at least one
non-null copy, emits the three published tier JPEGs into:

    {data_dir}/web/photos/thumb/{nasa_id}.jpg
    {data_dir}/web/photos/lowres/{nasa_id}.jpg
    {data_dir}/web/photos/hires/{nasa_id}.jpg

Source-of-truth precedence per record:
    raw_crew (NEF/DNG) → eol → flickr → nasa_images → ia_stills

Tooling:
- raw_crew: rawpy.imread → postprocess(use_camera_wb=True, no_auto_bright=True)
            → numpy ndarray → PIL.Image.fromarray
- JPEG sources: PIL.Image.open

HDR / exposure-fusion for bracket sets is a follow-up (4d2). The per-frame
tiers stand on their own.

Idempotent: skip a NASA ID if all three target files already exist AND are
newer than the chosen source. Re-running is cheap.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig
from shared.photos_ledger import LedgerRecord, load_ledger

console = Console()

# Tier configuration — long edge in pixels and JPEG quality.
TIERS: list[tuple[str, int, int]] = [
    ("thumb",  400,  85),
    ("lowres", 1024, 88),
    ("hires",  2048, 92),
]

# Source precedence for tier generation
SOURCE_PRIORITY: tuple[str, ...] = (
    "raw_crew", "eol", "flickr", "nasa_images", "ia_stills"
)

DEFAULT_WORKERS = 4


# ── Loaders ─────────────────────────────────────────────────────────────────


def _pick_source(rec: LedgerRecord) -> tuple[str, Path] | None:
    for src in SOURCE_PRIORITY:
        copy = rec.copies.get(src)
        if copy and copy.path:
            return src, Path(copy.path)
    return None


def _tier_paths(mission: MissionConfig, nasa_id: str) -> list[tuple[str, Path]]:
    return [
        ("thumb",  mission.web_photos_thumb  / f"{nasa_id}.jpg"),
        ("lowres", mission.web_photos_lowres / f"{nasa_id}.jpg"),
        ("hires",  mission.web_photos_hires  / f"{nasa_id}.jpg"),
    ]


def _is_up_to_date(src_path: Path, dest_paths: list[Path]) -> bool:
    """All three tiers exist AND are newer than the source."""
    try:
        src_mtime = src_path.stat().st_mtime
    except OSError:
        return False
    for dest in dest_paths:
        if not dest.exists():
            return False
        try:
            if dest.stat().st_mtime <= src_mtime:
                return False
        except OSError:
            return False
    return True


# ── Decoders ────────────────────────────────────────────────────────────────


def _decode_raw_crew(path: Path):
    """NEF/DNG → PIL.Image (RGB) via rawpy + libraw.

    Camera white balance + no auto-brighten preserves the look NASA's release
    pipeline targets. This avoids the 'too punchy' default look of generic
    raw converters.
    """
    import rawpy
    from PIL import Image
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            output_bps=8,
        )
    return Image.fromarray(rgb)


def _decode_jpeg(path: Path):
    from PIL import Image
    img = Image.open(path)
    # Strip alpha / palette so JPEG save doesn't barf
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def _decode(source: str, path: Path):
    if source == "raw_crew":
        return _decode_raw_crew(path)
    return _decode_jpeg(path)


# ── Tier generation ─────────────────────────────────────────────────────────


def _resize_to_long_edge(img, long_edge: int):
    """Resize so max(width, height) == long_edge. Preserves aspect ratio."""
    from PIL import Image
    w, h = img.size
    if max(w, h) <= long_edge:
        return img
    if w >= h:
        new_w = long_edge
        new_h = round(h * long_edge / w)
    else:
        new_h = long_edge
        new_w = round(w * long_edge / h)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _generate_one(
    nasa_id: str,
    source: str,
    src_path: Path,
    dest_paths: dict[str, Path],
) -> tuple[str, bool, str | None]:
    """Decode once, write all three tiers. Returns (nasa_id, ok, err)."""
    try:
        img = _decode(source, src_path)
    except Exception as e:
        return nasa_id, False, f"decode {source}: {e}"

    try:
        for tier_name, long_edge, quality in TIERS:
            out = _resize_to_long_edge(img, long_edge)
            dest = dest_paths[tier_name]
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            out.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
            tmp.replace(dest)
    except Exception as e:
        return nasa_id, False, f"save: {e}"
    return nasa_id, True, None


# ── Orchestrator ────────────────────────────────────────────────────────────


def generate_tiers(mission: MissionConfig, workers: int) -> None:
    ledger = load_ledger(mission.photos_ledger_path)
    if not ledger:
        console.print(
            f"[yellow]No ledger at {mission.photos_ledger_path} — run step 4c first.[/yellow]"
        )
        return
    console.print(f"  Loaded ledger: [cyan]{len(ledger):,}[/cyan] records")

    # Plan work — only exported, only if we have a source, only if tiers stale.
    work: list[tuple[str, str, Path, dict[str, Path]]] = []
    not_exported = no_source = up_to_date = 0
    for nasa_id, rec in ledger.items():
        if not rec.exported:
            not_exported += 1
            continue
        pick = _pick_source(rec)
        if pick is None:
            no_source += 1
            continue
        source, src_path = pick
        dest_paths = {name: path for name, path in _tier_paths(mission, nasa_id)}
        if _is_up_to_date(src_path, list(dest_paths.values())):
            up_to_date += 1
            continue
        work.append((nasa_id, source, src_path, dest_paths))

    console.print(
        f"  [dim]Skipping: {not_exported:,} not exported, "
        f"{no_source:,} no copy on disk, {up_to_date:,} already current.[/dim]"
    )

    if not work:
        console.print("\n  [green]All exported photos already have current tiers.[/green]")
        return

    console.print(f"\n  [cyan]{len(work):,}[/cyan] photos need tier generation. "
                  f"workers={workers}\n")

    failed: list[tuple[str, str]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Generating tiers", total=len(work))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_generate_one, nid, src, sp, dp): nid
                for nid, src, sp, dp in work
            }
            try:
                for fut in as_completed(futures):
                    nid, ok, err = fut.result()
                    if not ok:
                        failed.append((nid, err or "unknown"))
                    progress.advance(task)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted — cancelling pending tasks…[/yellow]")
                executor.shutdown(wait=False, cancel_futures=True)
                # Sweep .part files
                for _, _, _, dest_paths in work:
                    for p in dest_paths.values():
                        part = p.with_suffix(p.suffix + ".part")
                        if part.exists():
                            try:
                                part.unlink()
                            except OSError:
                                pass
                console.print("[yellow]Re-run to resume — completed work was saved.[/yellow]")
                return

    ok_count = len(work) - len(failed)
    console.print(f"\n  [green]Generated tiers for {ok_count:,} photos.[/green]")
    if failed:
        console.print(f"\n  [red]Failed: {len(failed)}[/red]")
        for nid, err in failed[:10]:
            console.print(f"    [red]{nid}[/red]: {err}")
        if len(failed) > 10:
            console.print(f"    … and {len(failed) - 10} more")


def main():
    parser = argparse.ArgumentParser(description="Generate web tier JPEGs")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Worker threads (default {DEFAULT_WORKERS}). NEF decode is CPU-bound.",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    console.print(f"\n[bold]=== Step 4d: Generate web tiers — {mission.name} ===[/bold]\n")
    generate_tiers(mission, workers=args.workers)


if __name__ == "__main__":
    main()
