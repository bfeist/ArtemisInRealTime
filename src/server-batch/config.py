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

# ── IO API ────────────────────────────────────────────────────────────────────

IO_API_BASE = "https://io.jsc.nasa.gov/api/search"
IO_ORIGIN_HEADER = "coda.fit.nasa.gov"

# ── Mission configuration ────────────────────────────────────────────────────


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
    io_flight_collections: list[str] = field(default_factory=list)
    yt_search_terms: list[str] = field(default_factory=list)
    flickr_album_id: str | None = None
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

    @property
    def raw_photos_ia(self) -> Path:
        return self.data_dir / "raw" / "photos" / "ia_stills"

    @property
    def raw_photos_flickr(self) -> Path:
        return self.data_dir / "raw" / "photos" / "flickr"

    @property
    def raw_photos_nasa(self) -> Path:
        return self.data_dir / "raw" / "photos" / "images_nasa_gov"

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
            self.raw_photos_ia,
            self.raw_photos_flickr,
            self.raw_photos_nasa,
            self.processed_transcripts,
            self.io_cache,
            self.web_dir,
            self.yt_video_dir,
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
        io_flight_collections=[],
        yt_search_terms=["Artemis I", "Artemis 1"],
        flickr_album_id="72177720303788800",
        flickr_album_keywords=["Artemis I", "Artemis 1"],
        nasa_id_patterns=[r"art\d+[me]\d+", r"jsc\d{4}m\d+"],
    ),
    "artemis-ii": MissionConfig(
        name="Artemis II",
        slug="artemis-ii",
        mission_start="2026-04-01",
        mission_end="2026-04-11",
        ia_subject_tag="Artemis II Resource Reel",
        ia_collections=["Artemis-II"],
        ia_comm_collection="Artemis-II-ACR-Collection",
        ia_stills_collection=None,
        io_parent_cid="2380537",
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
        flickr_album_id="72177720307234654",
        flickr_album_keywords=["Artemis II", "Artemis 2"],
        nasa_id_patterns=[r"jsc\d{4}m\d+", r"art\d+[me]\d+"],
    ),
}


def get_mission(name: str) -> MissionConfig:
    """Get mission config by slug. Raises KeyError if not found."""
    return MISSIONS[name]
