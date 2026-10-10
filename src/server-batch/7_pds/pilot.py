"""Bounded real-archive transfer pilot, including a local and ZIP match when present."""
import argparse
from pathlib import Path

from config import MISSIONS
from shared.pds import HostLimits, atomic_json, db, get_meta, locked, run_id
from .pipeline import fetch_one, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", required=True, choices=["artemis-ii"])
    args = parser.parse_args()
    mission = MISSIONS[args.mission]
    with locked(mission):
        c = db(mission)
        plan = get_meta(c, "plan")
        if not plan or not plan["safe"]:
            raise RuntimeError("Complete reconciliation before the live pilot")
        rows = []
        for bundle in ("artemis2_crew_camera", "artemis2_orion_camera", "artemis2_mission_audio", "artemis2_mission"):
            row = c.execute("SELECT * FROM remote WHERE active=1 AND bundle=? AND action='network' AND relative LIKE '%.xml' ORDER BY size LIMIT 1", (bundle,)).fetchone()
            if row:
                rows.append(row)
        for action in ("link", "extract", "copy"):
            row = c.execute("SELECT * FROM remote WHERE active=1 AND action=? ORDER BY size LIMIT 1", (action,)).fetchone()
            if row:
                rows.append(row)
        row = c.execute("SELECT * FROM remote WHERE active=1 AND action='network' AND size BETWEEN 20000000 AND 100000000 AND relative NOT LIKE '%.xml' ORDER BY size LIMIT 1").fetchone()
        if row:
            rows.append(row)
        results = []
        limits = HostLimits()
        for row in rows:
            print(f"Pilot {row['action']}: {row['bundle']}/{row['relative']} ({row['size']:,} bytes)", flush=True)
            ok = fetch_one(mission, row, limits, 500_000_000_000, 0)
            results.append({"bundle": row["bundle"], "relative": row["relative"], "method": row["action"], "size": row["size"], "ok": ok})
        c.close()
        atomic_json(mission.pds_processed / "reports" / (run_id() + "-pilot.json"), results)
        report(mission)
        if not rows or not all(r["ok"] for r in results):
            raise RuntimeError("Live pilot failed")


if __name__ == "__main__":
    main()
