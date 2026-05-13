"""Step 3f — Download full-resolution photo originals.

Sources:
  1. Flickr — reads album_metadata.json (from step 3b), downloads via url_o.
  2. images.nasa.gov — reads catalog.json (from step 3e), resolves ~orig asset.

Produces:
  {data_dir}/{mission}/raw/photos/flickr/{photo_id}.{ext}
  {data_dir}/{mission}/raw/photos/nasa_images/{nasa_id}~orig.{ext}
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig

NASA_ASSETS_BASE = "https://images-assets.nasa.gov/image"


# ── helpers ───────────────────────────────────────────────────────────────────


def _download(url: str, dest: Path, desc: str | None = None) -> bool:
    """Download url → dest with a progress bar. Skip if dest already exists."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        with open(dest, "wb") as f, tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            desc=desc or dest.name,
            leave=False,
        ) as bar:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                bar.update(len(chunk))
        return True
    except requests.RequestException as e:
        print(f"    Error downloading {url}: {e}")
        if dest.exists():
            dest.unlink()
        return False


def _nasa_orig_url(nasa_id: str) -> str | None:
    """Fetch the images-assets.nasa.gov collection.json and return the ~orig URL."""
    collection_url = f"{NASA_ASSETS_BASE}/{nasa_id}/collection.json"
    try:
        resp = requests.get(collection_url, timeout=30)
        resp.raise_for_status()
        assets: list[str] = resp.json()
        # Prefer ~orig. files; fall back to largest available
        orig = next((a for a in assets if "~orig." in a), None)
        if orig:
            return orig
        # Fallback priority: ~large, ~medium, ~small
        for suffix in ("~large.", "~medium.", "~small."):
            match = next((a for a in assets if suffix in a), None)
            if match:
                return match
        return assets[0] if assets else None
    except (requests.RequestException, ValueError, IndexError) as e:
        print(f"    Could not resolve assets for {nasa_id}: {e}")
        return None


# ── Flickr downloader ─────────────────────────────────────────────────────────


def download_flickr_originals(mission: MissionConfig) -> None:
    metadata_path = mission.raw_photos_flickr / "album_metadata.json"
    if not metadata_path.exists():
        print("  album_metadata.json not found — run step 3b first. Skipping Flickr.")
        return

    with open(metadata_path, "r", encoding="utf-8") as f:
        photos: list[dict] = json.load(f)

    out_dir = mission.photos_flickr_orig
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(photos)
    print(f"  {total} photos in Flickr metadata")

    # Pre-populate with filenames already on disk (from previous runs).
    # Anything written during *this* run is tracked in written_this_run so
    # collisions (two photos resolving to the same NASA ID) get a disambiguating
    # suffix instead of being silently skipped.
    preexisting: set[str] = {p.name for p in out_dir.iterdir()} if out_dir.exists() else set()
    written_this_run: set[str] = set()

    downloaded = skipped = missing_url = collisions = 0
    for i, photo in enumerate(photos, 1):
        url_o = photo.get("url_o", "")
        if not url_o:
            missing_url += 1
            continue

        photo_id = photo.get("id", "")
        ext = photo.get("originalformat") or Path(urlparse(url_o).path).suffix.lstrip(".") or "jpg"

        # Try to extract NASA ID from title + description (same logic as 3g_web_photos).
        # Falls back to sanitized title, then numeric Flickr ID.
        raw_title = photo.get("title", "").strip()
        description_content = photo.get("description", {})
        if isinstance(description_content, dict):
            description_content = description_content.get("_content", "")
        search_text = (raw_title + " " + description_content).lower()
        nasa_id_match = re.search(r"(art\d+[me]\d+|jsc\d+[me]\d+|nhq\d+)", search_text)
        if nasa_id_match:
            stem = nasa_id_match.group(1)
        elif raw_title:
            stem = re.sub(r"[^\w\-.]", "_", raw_title)
        else:
            stem = photo_id
        filename = f"{stem}.{ext}"
        # If this filename was already on disk before this run → truly skip.
        if filename in preexisting:
            skipped += 1
            continue

        # If another photo in *this* run already wrote the same filename,
        # it's a real duplicate (same NASA ID, different Flickr record).
        # Append the Flickr photo_id to disambiguate.
        if filename in written_this_run:
            filename = f"{stem}_{photo_id}.{ext}"
            collisions += 1

        dest = out_dir / filename
        ok = _download(url_o, dest, desc=f"flickr {photo_id}")
        if ok:
            downloaded += 1
            written_this_run.add(filename)

        if i % 50 == 0:
            print(f"    [{i}/{total}] downloaded={downloaded} skipped={skipped} collisions={collisions}")
        time.sleep(0.1)

    print(
        f"\n  Flickr done: {downloaded} downloaded, {skipped} already present"
        + (f", {collisions} duplicate NASA IDs disambiguated" if collisions else "")
        + (f", {missing_url} missing url_o" if missing_url else "")
    )
    print(f"  Output: {out_dir}")


# ── NASA images downloader ────────────────────────────────────────────────────


def download_nasa_originals(mission: MissionConfig) -> None:
    catalog_path = mission.raw_photos_nasa / "catalog.json"
    if not catalog_path.exists():
        print("  catalog.json not found — run step 3e first. Skipping NASA images.")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        items: list[dict] = json.load(f)

    out_dir = mission.photos_nasa_orig
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(items)
    print(f"  {total} items in NASA images catalog")

    downloaded = skipped = failed = 0
    for i, item in enumerate(items, 1):
        nasa_id = item.get("nasa_id", "")
        if not nasa_id:
            failed += 1
            continue

        # Resolve original asset URL
        orig_url = _nasa_orig_url(nasa_id)
        if not orig_url:
            failed += 1
            continue

        ext = Path(urlparse(orig_url).path).suffix  # e.g. ".jpg"
        dest = out_dir / f"{nasa_id}{ext}"

        if dest.exists():
            skipped += 1
            time.sleep(0.05)
            continue

        ok = _download(orig_url, dest, desc=nasa_id)
        if ok:
            downloaded += 1
        else:
            failed += 1

        if i % 25 == 0:
            print(f"    [{i}/{total}] downloaded={downloaded} skipped={skipped} failed={failed}")
        time.sleep(0.3)

    print(
        f"\n  NASA done: {downloaded} downloaded, {skipped} already present"
        + (f", {failed} failed/skipped" if failed else "")
    )
    print(f"  Output: {out_dir}")


# ── entry point ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Download full-res photo originals")
    parser.add_argument("--mission", required=True, choices=list(MISSIONS.keys()))
    parser.add_argument(
        "--source",
        choices=["flickr", "nasa", "all"],
        default="all",
        help="Which source to download (default: all)",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3f: Download Photo Originals — {mission.name} ===\n")

    if args.source in ("flickr", "all"):
        print("--- Flickr originals ---")
        download_flickr_originals(mission)

    if args.source in ("nasa", "all"):
        print("\n--- images.nasa.gov originals ---")
        download_nasa_originals(mission)


if __name__ == "__main__":
    main()
