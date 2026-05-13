"""ArtemisInRealTime data ingestion — configuration and per-mission constants."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# server-batch/config.py → parent.parent.parent = repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(REPO_ROOT / ".env")

# ── API keys ──────────────────────────────────────────────────────────────────

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
FLICKR_API_KEY = os.environ.get("FLICKR_API_KEY", "")
IO_KEY = os.environ.get("IO_KEY", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# ── Directories ───────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_ROOT.parent / "ArtemisInRealTime_assets"))
YT_VIDEO_DIR = Path(os.environ.get("YT_VIDEO_DIR", "H:/ArtemisInRealTime_yt_videos"))
VIDEO_ASSETS_DIR = Path(os.environ.get("VIDEO_ASSETS_DIR", "D:/ArtemisInRealTime_assets/videos"))

# Crew raw imagery (NEF + DNG) lives outside the per-mission tree until disk
# space is freed and they're moved into mission.photos_raw_crew. The legacy D:
# location is the default; override with CREW_RAW_SOURCE_DIR.
CREW_RAW_SOURCE_DIR = Path(
    os.environ.get(
        "CREW_RAW_SOURCE_DIR",
        "D:/ArtemisInRealTime_assets/5_Crew-Captured-Imagery",
    )
)

# DEPRECATED: PHOTO_ASSETS_DIR is being collapsed into mission.data_dir.
# Kept as an alias for one cycle in case any code still imports it.
PHOTO_ASSETS_DIR = Path(os.environ.get("PHOTO_ASSETS_DIR", DATA_DIR))

# ── IO API ────────────────────────────────────────────────────────────────────

IO_API_BASE = "https://io.jsc.nasa.gov/api/search"
IO_ORIGIN_HEADER = "coda.fit.nasa.gov"

# ── Mission configuration ────────────────────────────────────────────────────


@dataclass
class FlickrAlbum:
    """A single Flickr photoset/album to ingest."""

    photoset_id: str
    user_id: str
    owner_label: str = ""  # human-readable, e.g. "NASA Johnson" or "NASA HQ PHOTO"


@dataclass
class MissionConfig:
    name: str
    slug: str  # e.g. "artemis-i"
    mission_start: str  # ISO date, UTC
    mission_end: str
    ia_subject_tag: str
    ia_collections: list[str] = field(default_factory=list)
    ia_comm_collection: str | None = None
    ia_stills_collection: str | None = None
    io_parent_cid: str | None = None
    io_nasatv_cid: str | None = None  # IO collection CID for NASA TV video assets
    io_flight_collections: list[str] = field(default_factory=list)
    yt_search_terms: list[str] = field(default_factory=list)
    flickr_album_id: str | None = None  # legacy single-album field
    flickr_albums: list[FlickrAlbum] = field(default_factory=list)  # preferred multi-album list
    flickr_album_keywords: list[str] = field(default_factory=list)
    nasa_id_patterns: list[str] = field(default_factory=list)

    # ── derived paths ─────────────────────────────────────────────────────

    @property
    def data_dir(self) -> Path:
        return DATA_DIR / self.slug

    @property
    def raw_video_ia(self) -> Path:
        return self.data_dir / "raw" / "video" / "ia"

    @property
    def raw_video_yt(self) -> Path:
        return self.data_dir / "raw" / "video" / "yt"

    @property
    def raw_comm(self) -> Path:
        return self.data_dir / "raw" / "comm"

    # ── Raw photo source dirs (all under data_dir/raw/photos/) ─────────────
    # These are the canonical locations. The intake scripts download originals
    # straight here; the ledger build walks them to populate copies[*].path.

    @property
    def photos_raw_crew(self) -> Path:
        """Crew-captured raws — NEF (Nikon SLRs) and DNG (iPhone/other).
        Walked recursively (the on-disk layout has FD_01/FD_02/… subfolders)."""
        return self.data_dir / "raw" / "photos" / "5_Crew-Captured-Imagery"

    @property
    def photos_eol(self) -> Path:
        """Large JPEGs downloaded from the EOL portal."""
        return self.data_dir / "raw" / "photos" / "eol" / "jpeg_high"

    @property
    def photos_ia_stills(self) -> Path:
        """JPEGs downloaded from IA still-imagery items."""
        return self.data_dir / "raw" / "photos" / "ia_stills"

    @property
    def photos_flickr_orig(self) -> Path:
        """Originals downloaded from Flickr (url_o)."""
        return self.data_dir / "raw" / "photos" / "flickr"

    @property
    def photos_nasa_orig(self) -> Path:
        """Originals downloaded from images.nasa.gov (~orig)."""
        return self.data_dir / "raw" / "photos" / "images_nasa_gov"

    # ── Per-source intake metadata (raw, source-shaped) ───────────────────

    @property
    def raw_photos_flickr(self) -> Path:
        """Flickr album_metadata.json lives here."""
        return self.data_dir / "raw" / "photos" / "flickr"

    @property
    def raw_photos_nasa(self) -> Path:
        """images.nasa.gov catalog.json lives here."""
        return self.data_dir / "raw" / "photos" / "images_nasa_gov"

    # Legacy aliases — keep until callers are migrated.
    @property
    def raw_photos_ia(self) -> Path:
        return self.photos_ia_stills

    @property
    def raw_photos_eol(self) -> Path:
        return self.photos_eol

    @property
    def videos_io_dir(self) -> Path:
        return VIDEO_ASSETS_DIR / self.slug

    # ── Web outputs ────────────────────────────────────────────────────────

    @property
    def web_photos_dir(self) -> Path:
        """Parent for thumb/, lowres/, hires/ tier directories (step 4d)."""
        return self.data_dir / "web" / "photos"

    @property
    def web_photos_thumb(self) -> Path:
        return self.web_photos_dir / "thumb"

    @property
    def web_photos_lowres(self) -> Path:
        return self.web_photos_dir / "lowres"

    @property
    def web_photos_hires(self) -> Path:
        return self.web_photos_dir / "hires"

    # ── Processed caches (EXIF + ledger) ──────────────────────────────────

    @property
    def exif_cache_dir(self) -> Path:
        """Per-source EXIF JSONs under processed/exif/{source}/{nasa_id}.json."""
        return self.data_dir / "processed" / "exif"

    @property
    def eol_json_path(self) -> Path:
        """EOL metadata manifest (step 3g) — intermediate, not for web."""
        return self.data_dir / "processed" / "eol_photos.json"

    @property
    def photos_ledger_path(self) -> Path:
        return self.data_dir / "processed" / "photos_ledger.jsonl"

    @property
    def bracket_sets_path(self) -> Path:
        return self.io_cache / "bracket_sets.jsonl"

    @property
    def processed_transcripts(self) -> Path:
        return self.data_dir / "processed" / "transcripts"

    @property
    def io_cache(self) -> Path:
        return self.data_dir / "processed" / "io_cache"

    @property
    def web_dir(self) -> Path:
        return self.data_dir / "web"

    @property
    def yt_video_dir(self) -> Path:
        return YT_VIDEO_DIR / self.slug

    def ensure_dirs(self) -> None:
        """Create all output directories."""
        for d in [
            self.raw_video_ia,
            self.raw_video_yt,
            self.raw_comm,
            self.photos_raw_crew,
            self.photos_eol,
            self.photos_ia_stills,
            self.photos_flickr_orig,
            self.photos_nasa_orig,
            self.exif_cache_dir,
            self.web_photos_thumb,
            self.web_photos_lowres,
            self.web_photos_hires,
            self.processed_transcripts,
            self.io_cache,
            self.web_dir,
            self.yt_video_dir,
            self.videos_io_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


MISSIONS: dict[str, MissionConfig] = {
    "artemis-i": MissionConfig(
        name="Artemis I",
        slug="artemis-i",
        mission_start="2022-11-16",
        mission_end="2022-12-11",
        ia_subject_tag="Artemis I Resource Reel",
        ia_collections=["Artemis-I-Still-Imagery"],
        ia_comm_collection=None,
        ia_stills_collection="Artemis-I-Still-Imagery",
        io_parent_cid="2355140",
        io_nasatv_cid="2368197",
        io_flight_collections=[],
        yt_search_terms=["Artemis I", "Artemis 1"],
        flickr_album_id="72177720303788800",
        flickr_album_keywords=["Artemis I", "Artemis 1"],
        nasa_id_patterns=[r"art\d+[me]\d+", r"jsc\d{4}m\d+"],
    ),
    "artemis-ii": MissionConfig(
        name="Artemis II",
        slug="artemis-ii",
        mission_start="2026-03-31",
        mission_end="2026-04-11",
        ia_subject_tag="Artemis II Resource Reel",
        ia_collections=["Artemis-II"],
        ia_comm_collection="Artemis-II-ACR-Collection",
        ia_stills_collection=None,
        io_parent_cid="2380537",
        io_nasatv_cid="2408988",
        io_flight_collections=[
            "MISSION IMAGERY",
            "VIDEO",
            "Artemis-02 FCR",
            "Artemis-02 FCR Teams",
            "Artemis-02 Launch",
            "Artemis-02 Splashdown & Recovery",
            "Artemis-02 Events",
        ],
        yt_search_terms=["Artemis II", "Artemis 2"],
        flickr_album_id=None,
        flickr_albums=[
            FlickrAlbum(
                photoset_id="72177720307234654",
                user_id="29988733@N04",
                owner_label="NASA Johnson",
            ),
            FlickrAlbum(
                photoset_id="72177720331487648",
                user_id="35067687@N04",
                owner_label="NASA HQ PHOTO",
            ),
        ],
        flickr_album_keywords=["Artemis II", "Artemis 2"],
        nasa_id_patterns=[r"jsc\d{4}m\d+", r"art\d+[me]\d+"],
    ),
}


def get_mission(name: str) -> MissionConfig:
    """Get mission config by slug. Raises KeyError if not found."""
    return MISSIONS[name]
