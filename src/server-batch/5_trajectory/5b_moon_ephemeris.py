"""Step 5b — Compute geocentric Moon position and velocity at every Orion timestamp.

Uses Skyfield + JPL DE440s (small SPK, ~30 MB) to evaluate the Moon's ICRF
position and velocity at each `t` in `processed/ephemeris/orion_track.jsonl`.
The kernel is downloaded once to `{DATA_DIR}/_shared/kernels/de440s.bsp` and
reused across missions.

Output: `processed/ephemeris/moon_track.jsonl`
        {"t": iso, "x": km, "y": km, "z": km, "vx": km/s, "vy": km/s, "vz": km/s}
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR, MISSIONS, MissionConfig  # noqa: E402

KERNEL_URL = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp"
KERNEL_PATH = DATA_DIR / "_shared" / "kernels" / "de440s.bsp"


def ensure_kernel() -> Path:
    if KERNEL_PATH.exists():
        return KERNEL_PATH
    KERNEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading DE440s kernel → {KERNEL_PATH}")
    with urllib.request.urlopen(KERNEL_URL, timeout=120) as resp, open(KERNEL_PATH, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    print(f"  done ({KERNEL_PATH.stat().st_size / 1e6:.1f} MB)")
    return KERNEL_PATH


def _parse_iso(t: str) -> datetime:
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_moon_track(mission: MissionConfig) -> Path:
    from skyfield.api import load_file, load

    kernel_path = ensure_kernel()
    eph = load_file(str(kernel_path))
    earth = eph["earth"]
    moon = eph["moon"]
    ts = load.timescale()

    in_path = mission.processed_ephemeris / "orion_track.jsonl"
    if not in_path.exists():
        raise SystemExit(f"Run 5a first — missing {in_path}")
    out_path = mission.processed_ephemeris / "moon_track.jsonl"

    n = 0
    with open(in_path, encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            row = json.loads(line)
            dt = _parse_iso(row["t"])
            t = ts.from_datetime(dt)
            # Geocentric ICRF position and velocity of the Moon.
            state = (moon - earth).at(t)
            pos = state.position.km
            vel = state.velocity.km_per_s
            fout.write(json.dumps({
                "t": row["t"],
                "x": pos[0], "y": pos[1], "z": pos[2],
                "vx": vel[0], "vy": vel[1], "vz": vel[2],
            }) + "\n")
            n += 1
            if n % 5000 == 0:
                print(f"    {n} points…")
    print(f"  → wrote {n} Moon points to {out_path}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute geocentric Moon ephemeris at Orion timestamps.")
    ap.add_argument("--mission", required=True, choices=list(MISSIONS))
    args = ap.parse_args()
    print(f"=== Computing Moon track for {args.mission} ===")
    compute_moon_track(MISSIONS[args.mission])


if __name__ == "__main__":
    main()
