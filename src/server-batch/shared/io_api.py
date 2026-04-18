"""Imagery Online (IO) API client — search by NASA ID and scrape collections."""

import json
import math
import time
from pathlib import Path

import requests
import urllib3

from config import IO_API_BASE, IO_KEY, IO_ORIGIN_HEADER

# IO uses a self-signed cert
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_HEADERS = {"Origin": IO_ORIGIN_HEADER}
_RPP = 500  # results per page (max)


def search_io(keyword: str, api_key: str | None = None) -> dict | None:
    """Search IO API for a keyword (NASA ID, etc.).

    Returns the full JSON response with all pages merged, or None on error.
    """
    key = api_key or IO_KEY
    url = f"{IO_API_BASE}/q={keyword}&rpp={_RPP}?key={key}&format=json"

    try:
        resp = requests.get(url, verify=False, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        total = data["results"]["response"]["numfound"]

        if total <= _RPP:
            return data

        pages = math.ceil(total / _RPP)
        for page in range(1, pages):
            page_url = f"{url}&start={page * _RPP}"
            try:
                pr = requests.get(page_url, verify=False, headers=_HEADERS, timeout=30)
                pr.raise_for_status()
                data["results"]["response"]["docs"].extend(
                    pr.json()["results"]["response"]["docs"]
                )
            except requests.RequestException as e:
                print(f"  Warning: page {page + 1} failed for '{keyword}': {e}")

        return data

    except requests.RequestException as e:
        print(f"  Error searching IO for '{keyword}': {e}")
        return None


def search_io_collection(
    collection_cid: str,
    asset_type: int | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Fetch all docs in an IO collection by CID.

    Uses the cols= param to filter by collection and as= for asset type
    (1=photos, 2=videos). Empty q= returns everything in the collection.
    Returns a flat list of doc dicts.
    """
    key = api_key or IO_KEY
    # Build path params: q= empty, cols= for collection, as= for asset type
    path_params = f"q=&rpp={_RPP}&cols={collection_cid}"
    if asset_type is not None:
        path_params += f"&as={asset_type}"
    url = f"{IO_API_BASE}/{path_params}?key={key}&format=json"

    all_docs: list[dict] = []

    try:
        resp = requests.get(url, verify=False, headers=_HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        docs = data["results"]["response"]["docs"]
        total = data["results"]["response"]["numfound"]
        all_docs.extend(docs)

        if total > _RPP:
            pages = math.ceil(total / _RPP)
            for page in range(1, pages):
                page_url = f"{url}&start={page * _RPP}"
                try:
                    pr = requests.get(page_url, verify=False, headers=_HEADERS, timeout=60)
                    pr.raise_for_status()
                    all_docs.extend(pr.json()["results"]["response"]["docs"])
                except requests.RequestException as e:
                    print(f"    Warning: page {page + 1} failed: {e}")
                time.sleep(0.3)

        print(f"    IO collection CID {collection_cid}: {total} total, {len(all_docs)} fetched")
        return all_docs

    except requests.RequestException as e:
        print(f"    Error fetching IO collection CID {collection_cid}: {e}")
        return []


def list_io_subcollections(
    parent_cid: str,
    api_key: str | None = None,
) -> list[dict]:
    """List child collections under a parent CID.

    Returns list of dicts with keys: collection_id, name, description, leaf_node.
    """
    key = api_key or IO_KEY
    url = f"https://io.jsc.nasa.gov/api/collection/{parent_cid}?key={key}&format=json"

    try:
        resp = requests.get(url, verify=False, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("child_collection", [])
    except requests.RequestException as e:
        print(f"    Error listing IO subcollections for CID {parent_cid}: {e}")
        return []


# ── JSONL helpers ─────────────────────────────────────────────────────────────


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def append_jsonl(path: Path, record: dict) -> None:
    """Append a single JSON record to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def save_jsonl(path: Path, records: list[dict]) -> None:
    """Write a list of dicts as a JSONL file (overwrites)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
