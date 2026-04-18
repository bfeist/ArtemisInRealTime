"""Flickr API client — raw requests, no flickrapi library."""

import time

import requests

from config import FLICKR_API_KEY

FLICKR_API_BASE = "https://www.flickr.com/services/rest/"

# Extras string for bulk photo fetches — all available URL sizes
PHOTO_EXTRAS = (
    "description,license,date_upload,date_taken,owner_name,"
    "original_format,last_update,geo,tags,machine_tags,o_dims,views,media,"
    "path_alias,url_sq,url_t,url_s,url_m,url_o,url_l,url_c,url_z,"
    "url_h,url_k,url_3k,url_4k,url_5k,url_6k"
)


def _api_request(method: str, params: dict | None = None, max_retries: int = 3) -> dict | None:
    """Make a Flickr REST API request with retry logic."""
    if params is None:
        params = {}

    params.update({
        "api_key": FLICKR_API_KEY,
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })

    for attempt in range(max_retries):
        try:
            resp = requests.get(FLICKR_API_BASE, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if data.get("stat") == "ok":
                return data

            code = data.get("code")
            if code == 105 or resp.status_code == 429:
                wait = 2 ** attempt
                print(f"  Flickr rate limited, retrying in {wait}s...")
                time.sleep(wait)
                continue

            print(f"  Flickr API error: {data.get('message', 'unknown')}")
            return None

        except requests.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  Flickr request failed: {e}")
                return None

    return None


def get_photoset_photos(
    photoset_id: str,
    user_id: str = "29988733@N04",
) -> list[dict]:
    """Fetch all photos in a Flickr photoset/album. Paginates automatically."""
    all_photos: list[dict] = []
    page = 1

    while True:
        data = _api_request("flickr.photosets.getPhotos", {
            "photoset_id": photoset_id,
            "user_id": user_id,
            "page": page,
            "per_page": 500,
            "extras": PHOTO_EXTRAS,
        })

        if not data:
            break

        photoset = data.get("photoset", {})
        photos = photoset.get("photo", [])
        all_photos.extend(photos)

        total_pages = int(photoset.get("pages", 1))
        if page >= total_pages:
            break

        page += 1
        time.sleep(0.5)

    return all_photos


def get_photo_info(photo_id: str) -> dict | None:
    """Fetch detailed info for a single photo."""
    data = _api_request("flickr.photos.getInfo", {"photo_id": photo_id})
    if data:
        return data.get("photo")
    return None
