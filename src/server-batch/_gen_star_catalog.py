#!/usr/bin/env python3
"""
Generate bright-stars.json for the trajectory canvas star background.

Primary  : d3-celestial stars.6.json  (BSD 3-Clause)
           https://github.com/ofrohn/d3-celestial
           ~5 000 stars, mag ≤ 6.0, coordinates in decimal degrees.

Fallback : VizieR TAP  –  Hipparcos I/239/hip_main (CDS, free for research)
           Queries stars to MAG_LIMIT using ADQL over HTTPS.

Output   : ../public/bright-stars.json
           Parallel arrays { n, ra[], dec[], mag[], bv[] }
           RA in decimal degrees (0–360), Dec in decimal degrees (−90 to +90).

Usage    : python _gen_star_catalog.py
           (or: uv run _gen_star_catalog.py from the server-batch directory)

No external dependencies — only Python stdlib.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

MAG_LIMIT = 6.0   # apparent magnitude ceiling  (5 000 + stars visible to naked eye)

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "public" / "bright-stars.json"

# Primary: d3-celestial – pre-processed GeoJSON, not in LFS, BSD 3-Clause
D3_STARS_URL = (
    "https://raw.githubusercontent.com/ofrohn/d3-celestial/master/data/stars.6.json"
)

# Fallback: VizieR TAP – Hipparcos catalog, returns CSV directly
VIZIER_URL = (
    "https://tapvizier.u-strasbg.fr/TAPVizieR/tap/sync"
    "?REQUEST=doQuery&LANG=ADQL&FORMAT=csv"
    "&QUERY=SELECT+RAICRS%2CDEICRS%2CVmag%2C%22B-V%22+FROM+%22I%2F239%2Fhip_main%22"
    "+WHERE+Vmag+%3C%3D+6.0"
)

# ── Download ──────────────────────────────────────────────────────────────────


def fetch(url: str) -> bytes:
    print(f"Fetching {url} …", flush=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ArtemisInRealTime/1.0 (star-catalog-generator)"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    print(f"  → {len(data):,} bytes", flush=True)
    return data


# ── Parsers ───────────────────────────────────────────────────────────────────


def parse_d3_geojson(
    data: bytes,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """
    Parse d3-celestial GeoJSON.
    geometry.coordinates = [ra_degrees, dec_degrees]
    properties.mag = apparent magnitude (float)
    properties.bv  = B-V colour index (str or float)
    """
    fc = json.loads(data)
    ra_list: list[float] = []
    dec_list: list[float] = []
    mag_list: list[float] = []
    bv_list: list[float] = []

    for feat in fc["features"]:
        props = feat["properties"]
        coords = feat["geometry"]["coordinates"]
        try:
            mag = float(props["mag"])
        except (ValueError, KeyError):
            continue
        if mag > MAG_LIMIT:
            continue
        try:
            ra_deg = float(coords[0])
            dec = float(coords[1])
        except (ValueError, IndexError):
            continue
        try:
            bv = float(props.get("bv", 0.6) or 0.6)
        except ValueError:
            bv = 0.6

        ra_list.append(round(ra_deg % 360, 3))  # ensure 0–360
        dec_list.append(round(dec, 3))
        mag_list.append(round(mag, 2))
        bv_list.append(round(bv, 2))

    print(f"  → parsed {len(ra_list):,} stars from GeoJSON", flush=True)
    return ra_list, dec_list, mag_list, bv_list


def parse_vizier_csv(
    data: bytes,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """
    Parse VizieR TAP CSV (Hipparcos I/239/hip_main).
    Columns: RAICRS, DEICRS, Vmag, B-V
    """
    text = data.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    ra_list: list[float] = []
    dec_list: list[float] = []
    mag_list: list[float] = []
    bv_list: list[float] = []
    skipped = 0

    for row in reader:
        try:
            mag = float(row["Vmag"])
        except (ValueError, KeyError):
            skipped += 1
            continue
        if mag > MAG_LIMIT:
            continue
        try:
            ra_deg = float(row["RAICRS"])
            dec = float(row["DEICRS"])
        except (ValueError, KeyError):
            skipped += 1
            continue
        try:
            bv_str = row.get("B-V", "").strip()
            bv = float(bv_str) if bv_str else 0.6
        except ValueError:
            bv = 0.6

        ra_list.append(round(ra_deg, 3))
        dec_list.append(round(dec, 3))
        mag_list.append(round(mag, 2))
        bv_list.append(round(bv, 2))

    print(f"  → parsed {len(ra_list):,} stars (skipped {skipped:,})", flush=True)
    return ra_list, dec_list, mag_list, bv_list


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    sources: list[tuple[str, str]] = [
        (D3_STARS_URL, "d3-celestial"),
        (VIZIER_URL, "VizieR-Hipparcos"),
    ]

    ra: list[float] = []
    dec: list[float] = []
    mag: list[float] = []
    bv: list[float] = []
    source_name = ""

    for url, name in sources:
        try:
            raw = fetch(url)
            if name == "d3-celestial":
                ra, dec, mag, bv = parse_d3_geojson(raw)
            else:
                ra, dec, mag, bv = parse_vizier_csv(raw)
            if ra:
                source_name = name
                break
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {name} failed: {exc}", flush=True)

    if not ra:
        print("ERROR: could not obtain star catalog from any source.", file=sys.stderr)
        sys.exit(1)

    # Sort brightest first so the canvas draws large stars before dim ones
    order = sorted(range(len(mag)), key=lambda i: mag[i])
    ra   = [ra[i]  for i in order]
    dec  = [dec[i] for i in order]
    mag  = [mag[i] for i in order]
    bv   = [bv[i]  for i in order]

    catalog = {
        "source": source_name,
        "mag_limit": MAG_LIMIT,
        "n": len(ra),
        "ra": ra,
        "dec": dec,
        "mag": mag,
        "bv": bv,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, separators=(",", ":"))

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Written {len(ra):,} stars → {OUTPUT_PATH}  ({size_kb:.0f} KB)", flush=True)


if __name__ == "__main__":
    main()
