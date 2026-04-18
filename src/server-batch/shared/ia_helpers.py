"""Archive.org (Internet Archive) discovery and download helpers."""

import json
import math
import os
import time
from pathlib import Path

import requests
from tqdm import tqdm

IA_SEARCH_URL = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata"
IA_DOWNLOAD_URL = "https://archive.org/download"


# ── Discovery ─────────────────────────────────────────────────────────────────


def search_ia(
    query: str,
    fields: list[str] | None = None,
    rows: int = 500,
    media_type: str | None = None,
) -> list[dict]:
    """Run an advanced search on archive.org.  Returns all matching rows (paginated)."""
    if fields is None:
        fields = ["identifier", "title", "date", "mediatype", "collection", "subject"]

    params = {
        "q": query,
        "fl[]": fields,
        "rows": rows,
        "page": 1,
        "output": "json",
    }
    if media_type:
        params["q"] += f" AND mediatype:{media_type}"

    all_items: list[dict] = []

    while True:
        resp = requests.get(IA_SEARCH_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        docs = data.get("response", {}).get("docs", [])
        total = data.get("response", {}).get("numFound", 0)
        all_items.extend(docs)

        if len(all_items) >= total:
            break
        params["page"] += 1
        time.sleep(0.5)

    return all_items


def discover_by_subject(subject_tag: str, media_type: str | None = None) -> list[dict]:
    """Strategy 1: search by subject tag (e.g. 'Artemis II Resource Reel')."""
    return search_ia(f'subject:"{subject_tag}"', media_type=media_type)


def discover_by_collection(collection_id: str, media_type: str | None = None) -> list[dict]:
    """Strategy 2: search by collection membership."""
    return search_ia(f"collection:{collection_id}", media_type=media_type)


def discover_by_uploader(uploader: str, title_filter: str | None = None) -> list[dict]:
    """Strategy 3: search by uploader (e.g. 'NASA Johnson')."""
    q = f'uploader:"{uploader}"'
    if title_filter:
        q += f' AND title:"{title_filter}"'
    return search_ia(q)


# ── Metadata ──────────────────────────────────────────────────────────────────


def get_item_metadata(identifier: str) -> dict | None:
    """Fetch full metadata for a single IA item."""
    try:
        resp = requests.get(f"{IA_METADATA_URL}/{identifier}", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  Error fetching metadata for {identifier}: {e}")
        return None


def get_item_files(identifier: str) -> list[dict]:
    """Get the files list for an IA item."""
    meta = get_item_metadata(identifier)
    if meta is None:
        return []
    return meta.get("files", [])


# ── MP4 selection (prefer low-res .ia.mp4) ────────────────────────────────────


def find_best_mp4(files: list[dict], identifier: str) -> tuple[str, str] | None:
    """Pick the best MP4 to download from an IA item's file list.

    Prefers .ia.mp4 (IA's auto-generated lower-res derivative) over original.
    Returns (download_url, filename) or None.
    """
    lowres: list[dict] = []
    fullres: list[dict] = []

    for f in files:
        name = f.get("name", "")
        if name.lower().endswith(".ia.mp4"):
            lowres.append(f)
        elif name.lower().endswith(".mp4"):
            fullres.append(f)

    # Prefer low-res (smaller, faster to download)
    candidates = lowres or fullres
    if not candidates:
        return None

    # If multiple, pick the largest (most complete) among the preferred tier
    best = max(candidates, key=lambda f: int(f.get("size", 0)))
    url = f"{IA_DOWNLOAD_URL}/{identifier}/{best['name']}"
    return url, best["name"]


# ── Download ──────────────────────────────────────────────────────────────────


def download_file(url: str, dest: Path, desc: str | None = None) -> bool:
    """Download a file with progress bar.  Skips if dest already exists."""
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
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                bar.update(len(chunk))
        return True
    except requests.RequestException as e:
        print(f"  Error downloading {url}: {e}")
        if dest.exists():
            dest.unlink()
        return False


# ── Deduplication ─────────────────────────────────────────────────────────────


def deduplicate_items(items: list[dict]) -> list[dict]:
    """Deduplicate IA items by identifier."""
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        ident = item.get("identifier", "")
        if ident and ident not in seen:
            seen.add(ident)
            result.append(item)
    return result
