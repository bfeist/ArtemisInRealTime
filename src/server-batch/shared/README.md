# Shared Utilities

Reusable API clients and helpers used across all three pipelines.

## Modules

### `io_api.py` — Imagery Online (IO) API Client

NASA's internal imagery database at `io.jsc.nasa.gov`. Contains the authoritative catalog of all NASA flight imagery and video with precise timestamps.

**Key functions:**

| Function                                | Purpose                                                                               | Used by   |
| --------------------------------------- | ------------------------------------------------------------------------------------- | --------- |
| `search_io(keyword)`                    | Search IO by keyword (NASA ID). Paginates automatically.                              | 2c, 3e2   |
| `search_io_collection(cid, asset_type)` | Bulk-fetch all docs in an IO collection by CID. `as=1` for photos, `as=2` for videos. | 2c2, 3a2  |
| `list_io_subcollections(parent_cid)`    | List child collections under a parent CID.                                            | (utility) |
| `load_jsonl(path)`                      | Load a JSONL file into a list of dicts.                                               | 2g, 3f    |
| `append_jsonl(path, record)`            | Append a single record to JSONL (incremental writes).                                 | 2c, 3e2   |
| `save_jsonl(path, records)`             | Write a full list of records as JSONL (overwrites).                                   | 2c2, 3a2  |

**API quirks:**

- URL format splits params across path and query string: `{base}/q={kw}&rpp=500?key={key}&format=json`
- Self-signed SSL cert — `verify=False` required
- Requires `Origin: coda.fit.nasa.gov` header
- Response wrapped in extra layer: `data["results"]["response"]["docs"]`

### `ia_helpers.py` — Archive.org (Internet Archive) Helpers

Discovery and download functions for IA items and collections.

**Key functions:**

| Function                     | Purpose                                                 | Used by                  |
| ---------------------------- | ------------------------------------------------------- | ------------------------ |
| `search_ia(query)`           | Advanced search API. Paginates automatically.           | (via discover\_\* below) |
| `discover_by_subject(tag)`   | Search by subject tag.                                  | 2a                       |
| `discover_by_collection(id)` | Search by collection membership.                        | 2a, 3a                   |
| `discover_by_uploader(name)` | Search by uploader.                                     | 2a                       |
| `get_item_metadata(id)`      | Fetch full metadata for one IA item.                    | (via get_item_files)     |
| `get_item_files(id)`         | Get file list for an IA item.                           | 1a, 2b, 3a               |
| `find_best_mp4(files, id)`   | Pick best MP4 — prefers `.ia.mp4` (low-res derivative). | 2b                       |
| `download_file(url, dest)`   | Stream download with tqdm progress bar.                 | 1a, 2b, 3a               |
| `deduplicate_items(items)`   | Deduplicate IA items by identifier.                     | 2a                       |

### `flickr_api.py` — Flickr API Client

Raw REST API client (no flickrapi library dependency).

**Key functions:**

| Function                        | Purpose                                                                                                             | Used by                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `get_photoset_photos(album_id)` | Fetch all photos in a Flickr album with full metadata. Paginates automatically. Returns all URL sizes, dates, tags. | 3b                     |
| `get_photo_info(photo_id)`      | Detailed info for a single photo.                                                                                   | (available but unused) |

**Note:** `get_photoset_photos` fetches extensive extras including `date_taken`, `url_o`, `url_l`, etc. in a single call, which is why there's no separate `3c` step for photo details.
