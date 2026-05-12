"""Step 2c — Download NASA TV video files from an IO collection.

Fetches all video docs from the mission's IO NASA TV collection (io_nasatv_cid),
saves a catalog, downloads each video file, and writes a web-ready JSON summary.

Catalog:   {data_dir}/{mission}/processed/io_cache/io_nasatv_catalog.jsonl
Downloads: {VIDEO_ASSETS_DIR}/{mission}/            (e.g. D:/ArtemisInRealTime_assets/videos/artemis-ii/)
Web JSON:  {data_dir}/{mission}/web/videoIO.json

Skip logic: if a file with the same nasa_id (or IO id as fallback) already
exists in the output directory it is skipped without re-downloading.

Usage:
    uv run python -m 2_video.2c_io_nasatv_download --mission artemis-ii
    uv run python -m 2_video.2c_io_nasatv_download --mission artemis-ii --catalog-only
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IO_ORIGIN_HEADER, MISSIONS, MissionConfig
from shared.io_api import load_jsonl, save_jsonl, search_io_collection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_IO_HOST = "https://io.jsc.nasa.gov"
_HEADERS = {"Origin": IO_ORIGIN_HEADER}


# ── helpers ───────────────────────────────────────────────────────────────────


def _download_io_video(url: str, dest: Path, desc: str | None = None) -> bool:
    """Download an IO video from a pre-built URL to dest. Returns True on success."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, stream=True, verify=False, headers=_HEADERS, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        label = desc or dest.name
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 17):  # 128 KiB
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = written * 100 // total
                    print(f"\r    {label}: {pct:3d}%  ({written >> 20} / {total >> 20} MiB)", end="", flush=True)
        print()
        return True
    except requests.RequestException as e:
        print(f"\n    Error downloading {url}: {e}")
        if dest.exists():
            dest.unlink()
        return False


def _video_url(doc: dict) -> str | None:
    """Build the IO video file URL from doc fields.

    URL pattern (from io-api.ts parseVideoResultMetadata):
        {IO_HOST}{doc.webpath}/video/{doc.nasa_id}.{doc.file_extension_video}
    """
    webpath = (doc.get("webpath") or "").strip()
    nasa_id = (doc.get("nasa_id") or "").strip()
    ext = (doc.get("file_extension_video") or "mp4").strip().lower()
    if not webpath or not nasa_id:
        return None
    return f"{_IO_HOST}{webpath}/video/{nasa_id}.{ext}"


def _stem_for_doc(doc: dict) -> str:
    """Return a filename stem for a video doc (nasa_id preferred, id fallback)."""
    nasa_id = doc.get("nasa_id", "").strip()
    if nasa_id:
        return nasa_id
    return f"io_id_{doc.get('id', 'unknown')}"


def _ext_for_doc(doc: dict) -> str:
    """Return the file extension for a video doc."""
    ext = (doc.get("file_extension_video") or "").strip().lower()
    return ext if ext else "mp4"


def _doc_to_web(doc: dict) -> dict:
    """Convert a raw IO catalog doc to a web-ready video record.

    Schema mirrors photos.json for consistency:
        id, title, description, start, end, duration_seconds,
        source, collections, videoUrl, dataUrl
    """
    nasa_id = (doc.get("nasa_id") or "").strip()
    webpath = (doc.get("webpath") or "").strip()
    ext = (doc.get("file_extension_video") or "mp4").strip().lower()

    # Prefer manually-verified start time; fall back to creation date
    start = doc.get("vmd_start_gmt") or doc.get("md_creation_date") or ""
    end = doc.get("vmd_end_gmt") or ""

    video_url = f"{_IO_HOST}{webpath}/video/{nasa_id}.{ext}" if webpath and nasa_id else ""
    data_url = f"{_IO_HOST}/app/info.cfm?pid={doc.get('id')}" if doc.get("id") else ""

    # Last entry in collections_string is the most-specific collection path
    col_strings: list[str] = doc.get("collections_string") or []
    collection = col_strings[-1] if col_strings else ""

    return {
        "id": nasa_id or f"io_id_{doc.get('id', 'unknown')}",
        "title": doc.get("md_title") or "",
        "description": doc.get("description") or "",
        "start": start,
        "end": end,
        "duration_seconds": doc.get("duration_seconds") or doc.get("vmd_duration_seconds") or 0,
        "source": "io_nasatv",
        "collection": collection,
        "videoUrl": video_url,
        "dataUrl": data_url,
    }


def build_web_videos(mission: MissionConfig, docs: list[dict]) -> None:
    """Write web/videoIO.json from catalog docs, sorted by start time."""
    records = [_doc_to_web(d) for d in docs]
    records.sort(key=lambda r: r["start"] or "9999")

    out_path = mission.web_dir / "videoIO.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"  Web JSON: {len(records)} videos → {out_path}")


# ── main logic ────────────────────────────────────────────────────────────────


def fetch_catalog(mission: MissionConfig, refresh: bool = False) -> list[dict]:
    """Fetch video docs from the NASA TV IO collection and persist the catalog.

    If the catalog JSONL already exists on disk and refresh=False, loads from
    disk without hitting the IO API.
    """
    if not mission.io_nasatv_cid:
        print(f"  No io_nasatv_cid configured for {mission.name}. Skipping.")
        return []

    out_path = mission.io_cache / "io_nasatv_catalog.jsonl"

    if out_path.exists() and not refresh:
        docs = load_jsonl(out_path)
        print(f"  Loaded {len(docs)} video docs from cache → {out_path}")
        print(f"  (use --refresh-catalog to re-fetch from IO)")
        return docs

    print(f"  Fetching IO NASA TV collection CID {mission.io_nasatv_cid} …")
    docs = search_io_collection(mission.io_nasatv_cid, asset_type=2)
    save_jsonl(out_path, docs)
    print(f"  Saved {len(docs)} video docs → {out_path}")
    return docs


def download_videos(mission: MissionConfig, docs: list[dict]) -> None:
    """Download video files for each doc in the catalog."""
    out_dir = mission.videos_io_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build a set of stems already on disk for fast skip checks
    existing_stems: set[str] = {p.stem for p in out_dir.iterdir() if p.is_file()}

    total = len(docs)
    downloaded = skipped = failed = 0

    for i, doc in enumerate(docs, 1):
        stem = _stem_for_doc(doc)
        ext = _ext_for_doc(doc)
        filename = f"{stem}.{ext}"
        dest = out_dir / filename

        url = _video_url(doc)
        if not url:
            print(f"  [{i}/{total}] {stem} — missing webpath or nasa_id, skipping")
            failed += 1
            continue

        print(f"\n  [{i}/{total}] {stem}")

        if stem in existing_stems or dest.exists():
            print(f"    Already exists — skipping")
            skipped += 1
            continue

        ok = _download_io_video(url, dest, desc=stem)
        if ok:
            downloaded += 1
            existing_stems.add(stem)
        else:
            failed += 1

        time.sleep(0.25)

    print(f"\n  Done: {downloaded} downloaded, {skipped} skipped, {failed} failed")
    print(f"  Output: {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download NASA TV videos from an IO collection"
    )
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
        help="Mission slug (e.g. artemis-ii)",
    )
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Fetch and save the catalog without downloading files",
    )
    parser.add_argument(
        "--refresh-catalog",
        action="store_true",
        help="Re-fetch the catalog from IO even if a cached copy exists",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 2c: IO NASA TV Video Download — {mission.name} ===\n")

    docs = fetch_catalog(mission, refresh=args.refresh_catalog)
    if not docs:
        return

    build_web_videos(mission, docs)

    if args.catalog_only:
        print("  --catalog-only: skipping downloads.")
        return

    print(f"\n  Downloading {len(docs)} videos to {mission.videos_io_dir} …\n")
    download_videos(mission, docs)


if __name__ == "__main__":
    main()
