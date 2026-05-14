"""Step 4d — Generate web tier JPEGs (thumb / lowres / hires).

Walks the ledger; for every record with `exported == True` and at least one
non-null copy, emits the three published tier JPEGs into:

    {data_dir}/web/photos/thumb/{nasa_id}.jpg     # ~400 px long edge
    {data_dir}/web/photos/lowres/{nasa_id}.jpg    # ~1024 px long edge
    {data_dir}/web/photos/hires/{nasa_id}.jpg     # full source resolution (no downscale)

Source-of-truth precedence per record:
    raw_crew (NEF/DNG) → eol → flickr → nasa_images → ia_stills

Tooling:
- raw_crew (NEF/DNG): extract the camera-baked embedded JPEG via ExifTool
  (`-b -JpgFromRaw`). The embedded JPEG is full sensor resolution
  (5568×3712 for D5, 8256×5504 for Z9) with the camera's Picture Control
  already applied — visually identical to what NASA publishes via EOL.
  This dodges all libraw drama (Z9 HE* compression unsupported by libraw
  0.22.x) and matches NASA's published look exactly.
- JPEG sources (eol, flickr, nasa_images, ia_stills): read source bytes
  directly.

Hires is **saved verbatim** (source bytes copied byte-for-byte, no
re-encode, no downscale) — preserves the camera's quality and matches the
EOL portal's full-resolution publication. Thumb and lowres are decoded once
and downscaled with Lanczos.

Idempotent: skip a NASA ID if all three target files already exist AND are
newer than the chosen source. Re-running is cheap.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
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

# Downscale tiers — long edge in pixels and JPEG quality.
# `hires` is handled separately (verbatim copy, no downscale).
DOWNSCALE_TIERS: list[tuple[str, int, int]] = [
    ("thumb",  400,  85),
    ("lowres", 1024, 88),
]

# Source precedence for tier generation. EOL comes first because NASA's
# reprocessed EOL JPEG is encoded at noticeably higher JPEG quality than
# the camera's embedded preview baked into the NEF (~6 MB vs ~2 MB at the
# same 5568×3712 dimensions). raw_crew is the fallback for crew shots not
# yet on EOL — the embedded NEF JPEG is what the camera produced and is
# what EOL itself starts from.
SOURCE_PRIORITY: tuple[str, ...] = (
    "eol", "nasa_images", "flickr", "raw_crew", "ia_stills", "manual"
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


_EXIFTOOL_BIN: str | None = None


def _resolve_exiftool() -> str:
    """Find ExifTool on PATH, with a Windows fallback to the default winget
    install location. Cached after first lookup."""
    global _EXIFTOOL_BIN
    if _EXIFTOOL_BIN:
        return _EXIFTOOL_BIN
    import shutil
    found = shutil.which("exiftool") or shutil.which("ExifTool")
    if not found:
        # Default winget (OliverBetz.ExifTool) install
        candidate = Path.home() / "AppData/Local/Programs/ExifTool/ExifTool.exe"
        if candidate.exists():
            found = str(candidate)
    if not found:
        raise FileNotFoundError(
            "exiftool not found on PATH. Install with `winget install OliverBetz.ExifTool`."
        )
    _EXIFTOOL_BIN = found
    return found


def _source_jpeg_bytes(source: str, path: Path) -> bytes:
    """Return the source JPEG bytes for a record — these are written
    byte-for-byte to the `hires` tier (no re-encode).

    For `raw_crew` sources, runs ExifTool to extract the camera-baked
    embedded full-resolution JPEG preview from the NEF/DNG. For all other
    sources, returns the file contents directly.
    """
    if source == "raw_crew":
        import subprocess
        exiftool = _resolve_exiftool()
        res = subprocess.run(
            [exiftool, "-b", "-JpgFromRaw", str(path)],
            capture_output=True,
        )
        if res.returncode != 0 or not res.stdout:
            raise RuntimeError(
                f"ExifTool returned no embedded JPEG for {path.name}: "
                f"rc={res.returncode}, stderr={res.stderr[:200]!r}"
            )
        return res.stdout
    return path.read_bytes()


def _decode_for_downscale(jpeg_bytes: bytes):
    """Open JPEG bytes as a PIL.Image suitable for downscaling."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(jpeg_bytes))
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


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
    """Write three tiers for one NASA ID. Returns (nasa_id, ok, err).

    hires        — source JPEG bytes copied verbatim (no re-encode, no
                   downscale). Preserves the camera's full-resolution Picture
                   Control output for the lightbox view.
    lowres/thumb — decoded once, downscaled with Lanczos, re-encoded.
    """
    # 1. Get source bytes (extract embedded for raw_crew, read file otherwise)
    try:
        src_bytes = _source_jpeg_bytes(source, src_path)
    except Exception as e:
        return nasa_id, False, f"source {source}: {e}"

    # 2. hires — write source bytes verbatim
    try:
        hires_dest = dest_paths["hires"]
        hires_dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = hires_dest.with_suffix(hires_dest.suffix + ".part")
        tmp.write_bytes(src_bytes)
        tmp.replace(hires_dest)
    except Exception as e:
        return nasa_id, False, f"hires write: {e}"

    # 3. Downscale tiers — decode once, resize and re-encode for each
    try:
        img = _decode_for_downscale(src_bytes)
    except Exception as e:
        return nasa_id, False, f"decode: {e}"

    try:
        for tier_name, long_edge, quality in DOWNSCALE_TIERS:
            out = _resize_to_long_edge(img, long_edge)
            dest = dest_paths[tier_name]
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            out.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
            tmp.replace(dest)
    except Exception as e:
        return nasa_id, False, f"downscale save: {e}"

    return nasa_id, True, None


# ── Orchestrator ────────────────────────────────────────────────────────────


def _in_window(rec, win_start: date, win_end: date) -> bool:
    """Photo's UTC date falls within the mission window. Mirrors 4e's filter
    so we don't waste tier work on photos that won't be published."""
    if not rec.utc:
        return False
    try:
        d = date.fromisoformat(rec.utc[:10])
    except ValueError:
        return False
    return win_start <= d <= win_end


def generate_tiers(mission: MissionConfig, workers: int) -> None:
    ledger = load_ledger(mission.photos_ledger_path)
    if not ledger:
        console.print(
            f"[yellow]No ledger at {mission.photos_ledger_path} — run step 4c first.[/yellow]"
        )
        return
    console.print(f"  Loaded ledger: [cyan]{len(ledger):,}[/cyan] records")

    win_start = date.fromisoformat(mission.mission_start)
    win_end = date.fromisoformat(mission.mission_end)

    # Plan work — only exported AND in mission window, only if we have a
    # source, only if tiers stale.
    work: list[tuple[str, str, Path, dict[str, Path]]] = []
    not_exported = outside_window = no_source = up_to_date = 0
    for nasa_id, rec in ledger.items():
        if not rec.exported:
            not_exported += 1
            continue
        if not _in_window(rec, win_start, win_end):
            outside_window += 1
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
        f"{outside_window:,} outside window, "
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
