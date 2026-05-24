"""Step 2i — Bulk upload MP4 chunks to YouTube.

Uses YouTube Data API v3 with OAuth 2.0.  Resumable uploads handle multi-GB
files safely and can survive interruption (the API tracks progress server-side).

─── One-time setup ───────────────────────────────────────────────────────────
1. Go to https://console.cloud.google.com/
2. Enable "YouTube Data API v3" for your project.
3. Credentials → Create → OAuth 2.0 Client ID → Desktop app → Download JSON.
4. Save as  src/server-batch/client_secrets.json  (or pass --secrets <path>).
5. First run opens a browser for Google sign-in; the token is cached afterwards
   at  ~/.config/artemis-ingest/yt_token.json

─── Quota note ───────────────────────────────────────────────────────────────
Each video upload costs 1 600 API units.  Default daily quota is 10 000 units,
allowing ~6 uploads per day.  For 20 chunks you need ~4 days at default quota,
or request an increase at https://console.cloud.google.com/iam-admin/quotas
(filter by "YouTube Data API v3" → "queries per day").

This script is resumable: re-running skips files already recorded in
  {input_dir}/.yt_upload_state.json

─── Channel ──────────────────────────────────────────────────────────────────
Upload to:  ArtemisInRealTime  https://www.youtube.com/channel/UCwd_ROel7As0x6HopOquETw
Sign in as: bf@apolloinrealtime.org — then select the ArtemisInRealTime channel
            when Google prompts for channel selection during the OAuth flow.

Usage (run from src/server-batch/):
    # Upload all *_part*.mp4 files, unlisted (safe default):
    uv run 2_video/2i_yt_upload.py --input-dir "D:/"

    # Make them public:
    uv run 2_video/2i_yt_upload.py --input-dir "D:/" --privacy public

    # Dry-run — list files and titles without touching the API:
    uv run 2_video/2i_yt_upload.py --input-dir "D:/" --dry-run
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    import google.oauth2.credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
except ImportError as e:
    print(f"ERROR: Missing dependency — {e}")
    print("  Make sure you're running via uv from src/server-batch/:")
    print("    uv run 2_video/2i_yt_upload.py ...")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_CACHE = Path.home() / ".config" / "artemis-ingest" / "yt_token.json"
DEFAULT_SECRETS = Path(__file__).resolve().parent.parent / "client_secrets.json"

# 50 MB per resumable upload chunk — balances memory use and retry granularity
CHUNK_BYTES = 50 * 1024 * 1024


# ── Auth ──────────────────────────────────────────────────────────────────────


def get_youtube_client(secrets_path: Path):
    """Return an authenticated YouTube API client, refreshing / prompting as needed."""
    creds = None
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)

    if TOKEN_CACHE.exists():
        creds = google.oauth2.credentials.Credentials.from_authorized_user_file(
            str(TOKEN_CACHE), SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets_path.exists():
                print(f"\nERROR: OAuth client secrets not found at: {secrets_path}")
                print("  1. Go to https://console.cloud.google.com/apis/credentials")
                print("  2. Create an OAuth 2.0 Client ID (Desktop app)")
                print(f"  3. Download the JSON and save it to: {secrets_path}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_CACHE, "w") as f:
            f.write(creds.to_json())
        print(f"  Token cached → {TOKEN_CACHE}")

    return build("youtube", "v3", credentials=creds)


# ── Metadata ──────────────────────────────────────────────────────────────────


def _parse_part(filename: str) -> int:
    """Extract part number from filename, e.g. '_part03' → 3.  Returns 0 if not found."""
    m = re.search(r"_part(\d+)", filename, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def build_video_body(
    filename: str,
    part_num: int,
    total_parts: int,
    privacy: str,
) -> dict:
    title = f"ArtemisInRealtime.org - Artemis II - Part {part_num:02d}"
    description = (
        f"This video is part of the multimedia application at https://artemisinrealtime.org\n\n"
        f"Part {part_num} of {total_parts}."
    )
    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["Artemis II", "NASA", "Orion", "SLS", "NASA Johnson", "space"],
            "categoryId": "28",  # Science & Technology
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }


# ── Upload ────────────────────────────────────────────────────────────────────


def upload_video(youtube, video_path: Path, body: dict) -> str:
    """Upload one video file using the resumable API.  Returns the YouTube video ID."""
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=CHUNK_BYTES,
    )
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            done_mb = status.resumable_progress / (1024 ** 2)
            print(f"    {pct:3d}%  ({done_mb:.0f} MB uploaded)\r", end="", flush=True)

    print()  # end progress line
    return response["id"]


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk upload MP4 chunks to YouTube via Data API v3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir", required=True, type=Path,
        help="Directory containing the *_part*.mp4 chunk files",
    )
    parser.add_argument(
        "--pattern", default="*_part*.mp4",
        help="Glob pattern for chunk files (default: *_part*.mp4)",
    )
    parser.add_argument(
        "--secrets", type=Path, default=DEFAULT_SECRETS,
        help="Path to OAuth client_secrets.json (default: server-batch/client_secrets.json)",
    )
    parser.add_argument(
        "--privacy", choices=["public", "unlisted", "private"], default="unlisted",
        help="YouTube privacy status (default: unlisted)",
    )
    parser.add_argument(
        "--chunk-hours", type=float, default=8.0,
        help="Chunk size used during splitting, for title/description (default: 8)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List files and titles without making any API calls",
    )
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.exists():
        print(f"ERROR: Directory not found: {input_dir}")
        sys.exit(1)

    files = sorted(input_dir.glob(args.pattern))
    if not files:
        print(f"No files matching '{args.pattern}' in {input_dir}")
        sys.exit(0)

    total_parts = len(files)

    # Load existing upload state (filename → youtube video ID)
    state_path = input_dir / ".yt_upload_state.json"
    state: dict[str, str] = {}
    if state_path.exists():
        with open(state_path) as f:
            state = json.load(f)

    pending = [f for f in files if f.name not in state]

    print(f"\n=== Step 2i: YouTube Bulk Upload ===\n")
    print(f"  Input dir  : {input_dir}")
    print(f"  Files found: {total_parts}  ({total_parts - len(pending)} already uploaded, {len(pending)} pending)")
    print(f"  Privacy    : {args.privacy}")
    print(f"  Chunk size : {args.chunk_hours}h per part")
    if args.dry_run:
        print(f"  Mode       : DRY RUN — no API calls will be made")
    if pending and not args.dry_run:
        quota_days = math.ceil(len(pending) / 6)
        print(f"  Quota est. : ~{quota_days} day(s) at default quota (6 uploads/day)")
    print()

    # Authenticate up front (skip for dry-run or if nothing to do)
    youtube = None
    if not args.dry_run and pending:
        print("  Authenticating with YouTube...")
        youtube = get_youtube_client(args.secrets)
        print()

    for i, video_path in enumerate(files, 1):
        part_num = _parse_part(video_path.name) or i
        size_gb = video_path.stat().st_size / (1024 ** 3)

        if video_path.name in state:
            print(f"  [{i:02d}/{total_parts:02d}] Already uploaded → https://youtu.be/{state[video_path.name]}  (skipping)")
            continue

        body = build_video_body(
            video_path.name, part_num, total_parts, args.privacy
        )
        print(f"  [{i:02d}/{total_parts:02d}] {video_path.name}  ({size_gb:.2f} GB)")
        print(f"    Title: {body['snippet']['title']}")

        if args.dry_run:
            continue

        try:
            video_id = upload_video(youtube, video_path, body)
        except HttpError as e:
            print(f"\n  ERROR: YouTube API error uploading part {part_num}: {e}")
            if e.resp.status == 403:
                print("  Likely a quota exhaustion (403).  Wait 24 h and re-run.")
                print("  Already-uploaded parts are saved and will be skipped.")
            sys.exit(1)

        state[video_path.name] = video_id
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)

        print(f"    → https://youtu.be/{video_id}")
        print()

    if not args.dry_run:
        uploaded_count = sum(1 for f in files if f.name in state)
        print(f"\n  {uploaded_count}/{total_parts} file(s) uploaded.")
        if uploaded_count < total_parts:
            remaining = total_parts - uploaded_count
            print(f"  {remaining} remaining — re-run tomorrow to continue.")
        print(f"  State file : {state_path}")

    print()


if __name__ == "__main__":
    main()
