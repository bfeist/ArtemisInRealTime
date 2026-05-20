"""Step 3b — Discover & fetch Flickr album metadata.

Uses known album IDs from config (supports multiple albums per mission).

Produces:
  {data_dir}/{mission}/raw/photos/flickr/album_metadata.json
  {data_dir}/{mission}/raw/photos/flickr/album_metadata_sources.json  (audit)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, FlickrAlbum, MissionConfig
from shared.flickr_api import get_photoset_photos


def _effective_albums(mission: MissionConfig) -> list[FlickrAlbum]:
    """Return the list of albums to fetch for this mission.

    Prefers the new ``flickr_albums`` list; falls back to the legacy
    ``flickr_album_id`` field (using the NASA Johnson default user_id).
    """
    if mission.flickr_albums:
        return mission.flickr_albums
    if mission.flickr_album_id:
        return [FlickrAlbum(
            photoset_id=mission.flickr_album_id,
            user_id="29988733@N04",
            owner_label="NASA Johnson",
        )]
    return []


def fetch_albums(mission: MissionConfig) -> None:
    albums = _effective_albums(mission)
    if not albums:
        print(f"  No Flickr albums configured for {mission.name}. Skipping.")
        return

    out_path = mission.raw_photos_flickr / "album_metadata.json"

    # Load previously fetched photos so we can merge rather than replace.
    # This allows incremental runs to pick up photos added to albums after
    # the initial fetch without re-downloading everything.
    prior: dict[str, dict] = {}
    if out_path.exists():
        with open(out_path, "r", encoding="utf-8") as f:
            prior = {p["id"]: p for p in json.load(f) if p.get("id")}
        print(f"  Loaded {len(prior)} previously fetched photos.")

    # merged dict: flickr photo id → photo record (first album wins on conflict)
    merged: dict[str, dict] = {}
    audit_albums: list[dict] = []
    total_fetched = 0

    for album in albums:
        label = f"{album.owner_label} ({album.user_id})" if album.owner_label else album.user_id
        print(f"  Fetching album {album.photoset_id}  [{label}]...")
        photos = get_photoset_photos(album.photoset_id, album.user_id)
        fetched = len(photos)
        print(f"    → {fetched} photos")
        total_fetched += fetched

        duplicates_this_album = 0
        for photo in photos:
            fid = photo.get("id", "")
            if not fid:
                continue
            if fid in merged:
                # Photo already seen in an earlier album — append album reference
                existing_rec = merged[fid]
                if "source_album_ids" not in existing_rec:
                    existing_rec["source_album_ids"] = [existing_rec.get("album_id", "")]
                existing_rec["source_album_ids"].append(album.photoset_id)
                duplicates_this_album += 1
            else:
                photo["album_id"] = album.photoset_id
                photo["album_user_id"] = album.user_id
                photo["album_owner_label"] = album.owner_label
                merged[fid] = photo

        audit_albums.append({
            "photoset_id": album.photoset_id,
            "user_id": album.user_id,
            "owner_label": album.owner_label,
            "fetched": fetched,
            "duplicates_with_prior_albums": duplicates_this_album,
        })

    # Merge freshly-fetched records with anything from a prior run.
    # prior entries not seen in the latest API response are kept (they
    # may have been removed from the album temporarily but we still hold
    # the original download — better to keep than silently drop).
    # New entries (not in prior) are the incremental additions.
    new_ids = set(merged) - set(prior)
    combined = {**prior, **merged}   # API response wins on key collision
    result = list(combined.values())
    unique_count = len(result)
    duplicate_count = total_fetched - len(merged)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n  Merged output: {unique_count} unique photos → {out_path}")
    if new_ids:
        print(f"  New photos since last run: {len(new_ids)}")
    if duplicate_count:
        print(f"  Duplicates removed: {duplicate_count}")

    # Write audit file
    audit = {
        "albums": audit_albums,
        "total_fetched": total_fetched,
        "unique_count": unique_count,
        "new_since_last_run": len(new_ids),
        "duplicate_count": duplicate_count,
    }
    audit_path = mission.raw_photos_flickr / "album_metadata_sources.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)
    print(f"  Audit written → {audit_path}")


def main():
    parser = argparse.ArgumentParser(description="Fetch Flickr album metadata")
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]
    mission.ensure_dirs()

    print(f"\n=== Step 3b: Flickr Albums — {mission.name} ===\n")
    fetch_albums(mission)


if __name__ == "__main__":
    main()
