"""Step 5a — Build the canonical Orion track for a mission.

Two sources, selected by `mission.ephemeris_source`:

* **oem** (Artemis II onward) — parse all CCSDS OEM v2.0 zips in
  `raw/ephemeris/`, merge by latest CREATION_DATE wins on overlap, preserve
  the native (non-uniform) sample cadence.
* **horizons** (Artemis I) — read the cached Horizons VECTORS text dump in
  `raw/ephemeris/horizons_*.txt` (fetched once with `--refresh-horizons`),
  parse, and emit the same normalized record.

Output: `processed/ephemeris/orion_track.jsonl`
        one JSON object per line:
        {"t": iso, "x": km, "y": km, "z": km,
         "vx": km/s, "vy": km/s, "vz": km/s, "src": basename}
        Sorted by `t`, deduplicated to within 1 ms.

The frame is Earth-centered inertial (EME2000 for OEM, ICRF for Horizons —
they differ by <0.1° and we treat them as the same frame for the top-down
2-D rendering).
"""

import argparse
import json
import re
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MISSIONS, MissionConfig  # noqa: E402

# ── OEM parser ───────────────────────────────────────────────────────────────


def _parse_oem_text(text: str, src_name: str) -> tuple[datetime, list[dict]]:
    """Return (creation_date, rows). Rows are dicts with t/x/y/z/vx/vy/vz/src."""
    creation_date = None
    rows: list[dict] = []
    in_data = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("COMMENT"):
            continue
        if line.startswith("CREATION_DATE"):
            creation_date = datetime.fromisoformat(line.split("=", 1)[1].strip())
            if creation_date.tzinfo is None:
                creation_date = creation_date.replace(tzinfo=timezone.utc)
            continue
        if line.startswith("META_START"):
            in_data = False
            continue
        if line.startswith("META_STOP"):
            in_data = True
            continue
        if not in_data:
            continue
        parts = line.split()
        if len(parts) != 7:
            continue
        try:
            t = parts[0]
            # Validate it's an ISO timestamp
            datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            continue
        try:
            x, y, z, vx, vy, vz = (float(p) for p in parts[1:])
        except ValueError:
            continue
        rows.append({"t": t, "x": x, "y": y, "z": z, "vx": vx, "vy": vy, "vz": vz, "src": src_name})

    if creation_date is None:
        # Fall back to file mtime — OEM should always have CREATION_DATE.
        creation_date = datetime.fromtimestamp(0, tz=timezone.utc)
    return creation_date, rows


def _iter_oem_files(mission: MissionConfig) -> Iterator[tuple[str, str]]:
    """Yield (display_name, text) for every .asc inside every OEM zip."""
    for zip_path in sorted(mission.raw_ephemeris.glob("*.zip")):
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # Some zips ship a bare basename without an .asc extension.
                with zf.open(info) as fh:
                    text = fh.read().decode("utf-8", errors="replace")
                yield zip_path.name, text


def build_from_oem(mission: MissionConfig) -> list[dict]:
    """Merge every OEM in the raw folder. Newer CREATION_DATE owns its window.

    Each OEM covers a [start, stop] time window. When a newer OEM overlaps an
    older one, the newer OEM's samples replace the older OEM's samples within
    the overlap. This avoids interleaved sample times from different
    reconstructions appearing in the merged track.
    """
    if not mission.raw_ephemeris.exists():
        raise SystemExit(f"No raw ephemeris dir: {mission.raw_ephemeris}")

    files = list(_iter_oem_files(mission))
    if not files:
        raise SystemExit(f"No OEM zips found in {mission.raw_ephemeris}")

    parsed: list[tuple[datetime, list[dict]]] = []
    for name, text in files:
        cd, rows = _parse_oem_text(text, name)
        if rows:
            print(f"  {name}: {len(rows)} points  (CREATION_DATE={cd.isoformat()})")
            parsed.append((cd, rows))

    # Sort oldest-CREATION_DATE first; each subsequent OEM overwrites samples
    # in its own [start, stop] window.
    parsed.sort(key=lambda x: x[0])

    def _t(s: str) -> str:
        return s[:23]  # ms-truncated ISO timestamp, monotonic-comparable

    merged: dict[str, dict] = {}
    for _cd, rows in parsed:
        start = _t(rows[0]["t"])
        stop = _t(rows[-1]["t"])
        # Drop any existing keys inside this OEM's window.
        for k in [k for k in merged if start <= k <= stop]:
            del merged[k]
        for r in rows:
            merged[_t(r["t"])] = r
    out = [merged[k] for k in sorted(merged.keys())]
    return out


# ── Horizons fetcher / parser ────────────────────────────────────────────────

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
_J2000_JD = 2451545.0  # JD of 2000-01-01 12:00 TT


def _fetch_horizons(mission: MissionConfig, step: str = "10m") -> str:
    """Pull a full-mission VECTORS dump from JPL Horizons.

    Returns the raw response text and caches it to
    `raw/ephemeris/horizons_{slug}.txt`.
    """
    if not mission.horizons_id:
        raise SystemExit(f"{mission.slug}: horizons_id not configured")
    mission.raw_ephemeris.mkdir(parents=True, exist_ok=True)
    cache = mission.raw_ephemeris / f"horizons_{mission.slug}.txt"

    start = f"{mission.mission_start} 00:00"
    end = f"{mission.mission_end} 23:59"
    params = {
        "format": "text",
        "COMMAND": f"'{mission.horizons_id}'",
        "OBJ_DATA": "'NO'",
        "EPHEM_TYPE": "'VECTORS'",
        "CENTER": "'500@399'",
        "START_TIME": f"'{start}'",
        "STOP_TIME": f"'{end}'",
        "STEP_SIZE": f"'{step}'",
        "VEC_TABLE": "'2'",
        "REF_PLANE": "'FRAME'",
    }
    qs = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params.items())
    url = f"{HORIZONS_URL}?{qs}"
    print(f"  GET {url[:120]}…")
    with urllib.request.urlopen(url, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    cache.write_text(text, encoding="utf-8")
    print(f"  cached {len(text):,} chars → {cache}")
    return text


def _jd_tdb_to_utc_iso(jd: float) -> str:
    """Convert a JD-TDB to an ISO-8601 UTC string.

    TDB ≈ TT to <2 ms; TT − UTC = 32.184 s + TAI−UTC (37 s since 2017).
    For 2022 / 2026 the offset is constant at 69.184 s.
    """
    delta_sec = jd * 86400.0 - _J2000_JD * 86400.0
    tt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=delta_sec)
    utc = tt - timedelta(seconds=69.184)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


_HORIZONS_BLOCK_RE = re.compile(
    r"^(?P<jd>[\d.]+)\s*=.*?\n"
    r"\s*X\s*=\s*(?P<x>[-+\dE.]+)\s+Y\s*=\s*(?P<y>[-+\dE.]+)\s+Z\s*=\s*(?P<z>[-+\dE.]+)\s*\n"
    r"\s*VX\s*=\s*(?P<vx>[-+\dE.]+)\s+VY\s*=\s*(?P<vy>[-+\dE.]+)\s+VZ\s*=\s*(?P<vz>[-+\dE.]+)",
    re.MULTILINE,
)


def parse_horizons(text: str, src_name: str) -> list[dict]:
    soe = text.find("$$SOE")
    eoe = text.find("$$EOE")
    if soe < 0 or eoe < 0:
        raise SystemExit("Horizons response missing $$SOE/$$EOE markers")
    body = text[soe:eoe]
    rows: list[dict] = []
    for m in _HORIZONS_BLOCK_RE.finditer(body):
        rows.append({
            "t": _jd_tdb_to_utc_iso(float(m["jd"])),
            "x": float(m["x"]),
            "y": float(m["y"]),
            "z": float(m["z"]),
            "vx": float(m["vx"]),
            "vy": float(m["vy"]),
            "vz": float(m["vz"]),
            "src": src_name,
        })
    return rows


def build_from_horizons(mission: MissionConfig, refresh: bool) -> list[dict]:
    cache = mission.raw_ephemeris / f"horizons_{mission.slug}.txt"
    # Legacy filename used during initial research:
    legacy = mission.raw_ephemeris / "horizons_em1_full.txt"
    if refresh or not (cache.exists() or legacy.exists()):
        text = _fetch_horizons(mission)
        src = cache.name
    else:
        path = cache if cache.exists() else legacy
        text = path.read_text(encoding="utf-8")
        src = path.name
        print(f"  read cached {path} ({len(text):,} chars)")
    rows = parse_horizons(text, src)
    print(f"  parsed {len(rows)} Horizons points")
    return rows


# ── Driver ───────────────────────────────────────────────────────────────────


def build_orion_track(mission: MissionConfig, refresh_horizons: bool = False) -> Path:
    if mission.ephemeris_source == "oem":
        rows = build_from_oem(mission)
    elif mission.ephemeris_source == "horizons":
        rows = build_from_horizons(mission, refresh=refresh_horizons)
    else:
        raise SystemExit(f"Unknown ephemeris_source: {mission.ephemeris_source}")

    mission.processed_ephemeris.mkdir(parents=True, exist_ok=True)
    out_path = mission.processed_ephemeris / "orion_track.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"  → wrote {len(rows)} merged points to {out_path}")
    print(f"  span: {rows[0]['t']}  →  {rows[-1]['t']}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build canonical Orion ephemeris track.")
    ap.add_argument("--mission", required=True, choices=list(MISSIONS))
    ap.add_argument(
        "--refresh-horizons",
        action="store_true",
        help="Force re-fetch from JPL Horizons even if a local cache exists.",
    )
    args = ap.parse_args()
    print(f"=== Building Orion track for {args.mission} ===")
    build_orion_track(MISSIONS[args.mission], refresh_horizons=args.refresh_horizons)


if __name__ == "__main__":
    main()
