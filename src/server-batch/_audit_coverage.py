"""_audit_coverage.py — Media coverage audit for a given mission.

Reads processed metadata files (no API calls) and reports how well each
source can place video and photos on the mission timeline, and which
mission days have the most / least coverage.

Usage:
    python _audit_coverage.py --mission artemis-i
    python _audit_coverage.py --mission artemis-i --out report.txt
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MISSIONS, MissionConfig
from shared.io_api import load_jsonl

# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string to a UTC-aware datetime, or None."""
    if not s:
        return None
    # Normalise: strip trailing Z, collapse sub-second beyond microseconds
    s = s.strip()
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    # Try datetime.fromisoformat first (handles +HH:MM offsets cleanly, Python 3.7+)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    # Fallback for bare date strings
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def parse_yt_duration(s: str | None) -> int:
    """Parse ISO 8601 duration like PT3H32M15S → seconds."""
    if not s:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return 0
    h, mn, sec = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + sec


def fmt_duration(secs: int | float) -> str:
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def bar(n: int, max_n: int, width: int = 28, char: str = "█") -> str:
    if max_n == 0 or n == 0:
        return "░" * width
    filled = max(1, int(round(n / max_n * width)))
    return char * filled + "░" * (width - filled)


def _split_coll_path(path_str: str) -> list[str]:
    """Split a collection path string on | or / separator."""
    if "|" in path_str:
        return path_str.split("|")
    return path_str.split("/")


def _day_n(dt: datetime, start: date) -> int | None:
    """Return 1-based mission day, or None if before start."""
    delta = (dt.date() - start).days
    return delta + 1 if delta >= 0 else None


# ── Per-source audit functions ─────────────────────────────────────────────────

def audit_ia_video(mission: MissionConfig, start: date, end: date) -> dict:
    path = mission.data_dir / "processed" / "ia_video_metadata.json"
    if not path.exists():
        return {"exists": False}

    items: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    total = len(items)
    by_day: Counter = Counter()
    dur_by_day: Counter = Counter()
    no_ts = in_window = out_window = 0
    source_counts: Counter = Counter()
    total_dur = 0.0

    for item in items:
        dt = parse_iso(item.get("recorded_at"))
        raw_dur = item.get("duration") or 0
        if isinstance(raw_dur, str):
            dur = float(parse_yt_duration(raw_dur))
        else:
            dur = float(raw_dur)
        total_dur += dur
        source_counts[item.get("date_source", "unknown")] += 1

        if dt is None:
            no_ts += 1
            continue
        if dt.date() > end:
            out_window += 1
            continue
        dn = _day_n(dt, start)
        if dn is None:
            out_window += 1
            continue
        in_window += 1
        by_day[dn] += 1
        dur_by_day[dn] += dur

    return {
        "exists": True,
        "total": total,
        "no_ts": no_ts,
        "in_window": in_window,
        "out_window": out_window,
        "by_day": by_day,
        "dur_by_day": dur_by_day,
        "source_counts": source_counts,
        "total_dur": total_dur,
    }


def audit_yt_video(mission: MissionConfig, start: date, end: date) -> dict:
    path = mission.data_dir / "processed" / "yt_metadata.json"
    if not path.exists():
        return {"exists": False}

    items: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    total = len(items)
    by_day: Counter = Counter()
    dur_by_day: Counter = Counter()
    no_ts = in_window = out_window = 0
    total_dur = 0

    for item in items:
        dt = parse_iso(item.get("actual_start_time") or item.get("published_at"))
        dur = parse_yt_duration(item.get("duration"))
        total_dur += dur

        if dt is None:
            no_ts += 1
            continue
        if dt.date() > end:
            out_window += 1
            continue
        dn = _day_n(dt, start)
        if dn is None:
            out_window += 1
            continue
        in_window += 1
        by_day[dn] += 1
        dur_by_day[dn] += dur

    return {
        "exists": True,
        "total": total,
        "no_ts": no_ts,
        "in_window": in_window,
        "out_window": out_window,
        "by_day": by_day,
        "dur_by_day": dur_by_day,
        "total_dur": total_dur,
    }


def audit_io_photos(mission: MissionConfig, start: date, end: date) -> dict:
    path = mission.io_cache / "io_photo_catalog.jsonl"
    if not path.exists():
        return {"exists": False}

    # Load second-precision datetime overrides from step 3a3 original-filename scrape
    dt_overrides: dict[str, str] = {}
    dt_overrides_path = mission.io_cache / "photo-datetime-overrides.json"
    if dt_overrides_path.exists():
        import json as _json
        with open(dt_overrides_path, "r", encoding="utf-8") as f:
            dt_overrides = _json.load(f)

    print("  Loading IO photo catalog (may take a moment)...", file=sys.stderr)
    total = 0
    in_window = out_window = no_ts = 0
    public_in_window = 0
    # Precision tiers for items placed in window
    precise = 0      # second-precision from original filename (3a3)
    day_only = 0     # day-only from md_creation_date that falls in-window (ground photos)
    no_datetime = 0  # no usable timestamp at all
    by_day: Counter = Counter()
    public_by_day: Counter = Counter()
    sub_coll_counts: Counter = Counter()

    for doc in load_jsonl(path):
        total += 1
        is_public = bool(doc.get("on_public_site"))
        nasa_id = doc.get("nasa_id", "")

        # Count sub-collections
        for coll_path in doc.get("collections_string") or []:
            parts = _split_coll_path(coll_path)
            if len(parts) >= 3:
                sub_coll_counts[parts[2]] += 1
            elif len(parts) >= 2:
                sub_coll_counts[parts[-1]] += 1

        # Priority 1: second-precision datetime from original filename (step 3a3)
        dt = parse_iso(dt_overrides.get(nasa_id))

        # Priority 2: md_creation_date (reliable for jsc*/nhq* ground photos)
        if dt is None:
            dt = parse_iso(doc.get("md_creation_date"))

        if dt is None:
            no_ts += 1
            no_datetime += 1
            continue
        if dt.date() > end:
            out_window += 1
            continue
        dn = _day_n(dt, start)
        if dn is None:
            out_window += 1
            continue
        in_window += 1
        by_day[dn] += 1
        if is_public:
            public_in_window += 1
            public_by_day[dn] += 1
        if dt_overrides.get(nasa_id):
            precise += 1
        else:
            day_only += 1

    return {
        "exists": True,
        "total": total,
        "in_window": in_window,
        "out_window": out_window,
        "no_ts": no_ts,
        "public_in_window": public_in_window,
        "precise": precise,
        "day_only": day_only,
        "has_overrides": bool(dt_overrides),
        "by_day": by_day,
        "public_by_day": public_by_day,
        "top_collections": sub_coll_counts.most_common(12),
    }


def audit_flickr(mission: MissionConfig, start: date, end: date) -> dict:
    path = mission.raw_photos_flickr / "album_metadata.json"
    if not path.exists():
        return {"exists": False}

    items: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    total = len(items)
    by_day: Counter = Counter()
    no_ts = in_window = out_window = 0
    exact = day_only = 0

    for item in items:
        taken = item.get("datetaken")
        granularity = int(item.get("datetakengranularity") or 0)
        dt = parse_iso(taken)

        if dt is None:
            no_ts += 1
            continue
        # granularity 0 = exact, 4 = approximate day, 6 = approximate month, etc.
        if granularity == 0:
            exact += 1
        else:
            day_only += 1

        if dt.date() > end:
            out_window += 1
            continue
        dn = _day_n(dt, start)
        if dn is None:
            out_window += 1
            continue
        in_window += 1
        by_day[dn] += 1

    return {
        "exists": True,
        "total": total,
        "no_ts": no_ts,
        "in_window": in_window,
        "out_window": out_window,
        "by_day": by_day,
        "exact": exact,
        "day_only": day_only,
    }


def audit_nasa_images(mission: MissionConfig, start: date, end: date) -> dict:
    path = mission.raw_photos_nasa / "catalog.json"
    if not path.exists():
        return {"exists": False}

    items: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    total = len(items)
    by_day: Counter = Counter()
    no_ts = in_window = out_window = 0

    for item in items:
        dt = parse_iso(item.get("date_created"))
        if dt is None:
            no_ts += 1
            continue
        if dt.date() > end:
            out_window += 1
            continue
        dn = _day_n(dt, start)
        if dn is None:
            out_window += 1
            continue
        in_window += 1
        by_day[dn] += 1

    return {
        "exists": True,
        "total": total,
        "no_ts": no_ts,
        "in_window": in_window,
        "out_window": out_window,
        "by_day": by_day,
    }


# ── Report rendering ────────────────────────────────────────────────────────────

_W = 64  # line width


def _sep(title: str = "", out=sys.stdout) -> None:
    if title:
        pad = _W - len(title) - 4
        print(f"\n── {title} {'─' * max(pad, 2)}", file=out)
    else:
        print("─" * _W, file=out)


def _day_table(
    mission_days: int,
    start: date,
    by_day: Counter,
    dur_by_day: Counter | None,
    max_count: int,
    out=sys.stdout,
) -> None:
    for dn in range(1, mission_days + 1):
        d = start + timedelta(days=dn - 1)
        count = by_day.get(dn, 0)
        dur = dur_by_day.get(dn, 0) if dur_by_day else None
        b = bar(count, max_count)
        dur_str = f"  {fmt_duration(dur)}" if dur else ""
        print(f"  Day {dn:2d}  {d.isoformat()}  {b}  {count:4d}{dur_str}", file=out)


def run_audit(mission: MissionConfig, out=None) -> None:
    if out is None:
        out = sys.stdout

    start = datetime.strptime(mission.mission_start, "%Y-%m-%d").date()
    end = datetime.strptime(mission.mission_end, "%Y-%m-%d").date()
    mission_days = (end - start).days + 1

    print(f"\n{'=' * _W}", file=out)
    print(f"  COVERAGE AUDIT: {mission.name}", file=out)
    print(f"  Mission window: {start}  →  {end}  ({mission_days} days)", file=out)
    print(f"{'=' * _W}", file=out)

    # ── IA Video ──────────────────────────────────────────────────────────
    _sep("VIDEO — Internet Archive", out)
    ia_v = audit_ia_video(mission, start, end)
    if not ia_v["exists"]:
        print("  No ia_video_metadata.json — run steps 2a + 2f first.", file=out)
    else:
        sc = ia_v["source_counts"]
        print(f"  Total items  :  {ia_v['total']}", file=out)
        print(f"  In window    :  {ia_v['in_window']}", file=out)
        print(f"  Out of window:  {ia_v['out_window']}", file=out)
        print(f"  No timestamp :  {ia_v['no_ts']}", file=out)
        print(f"  Total duration: {fmt_duration(ia_v['total_dur'])}", file=out)
        print(f"\n  Timestamp precision:", file=out)
        print(f"    filename_artdl  (precise UTC)   {sc.get('filename_artdl', 0):4d}  ← best", file=out)
        print(f"    filename_yymmdd (day-only)       {sc.get('filename_yymmdd', 0):4d}", file=out)
        print(f"    filename_ksc    (day-only)       {sc.get('filename_ksc', 0):4d}", file=out)
        print(f"    ia_metadata     (upload date)    {sc.get('ia_metadata', 0):4d}  ← unreliable", file=out)
        if ia_v["by_day"]:
            max_c = max(ia_v["by_day"].values())
            print(file=out)
            _day_table(mission_days, start, ia_v["by_day"], ia_v["dur_by_day"], max_c, out)
        else:
            print("\n  (no items placed in mission window)", file=out)

    # ── YouTube ───────────────────────────────────────────────────────────
    _sep("VIDEO — YouTube", out)
    yt_v = audit_yt_video(mission, start, end)
    if not yt_v["exists"]:
        print("  No yt_metadata.json — run step 2d first.", file=out)
    else:
        print(f"  Total items  :  {yt_v['total']}", file=out)
        print(f"  In window    :  {yt_v['in_window']}", file=out)
        print(f"  Out of window:  {yt_v['out_window']}", file=out)
        print(f"  No timestamp :  {yt_v['no_ts']}", file=out)
        print(f"  Total duration: {fmt_duration(yt_v['total_dur'])}", file=out)
        print(f"\n  Note: actual_start_time is precise UTC (livestream), publishedAt is fallback.", file=out)
        if yt_v["by_day"]:
            max_c = max(yt_v["by_day"].values())
            print(file=out)
            _day_table(mission_days, start, yt_v["by_day"], yt_v["dur_by_day"], max_c, out)
        else:
            print("\n  (no items placed in mission window)", file=out)

    # ── IO Photo Catalog ──────────────────────────────────────────────────
    _sep("PHOTOS — IO Catalog (3a2)", out)
    io_p = audit_io_photos(mission, start, end)
    if not io_p["exists"]:
        print("  No io_photo_catalog.jsonl — run step 3a2 first.", file=out)
    else:
        pct_in = io_p["in_window"] / io_p["total"] * 100 if io_p["total"] else 0
        print(f"  Total items  :  {io_p['total']}", file=out)
        print(f"  In window    :  {io_p['in_window']}  ({pct_in:.1f}%)", file=out)
        print(f"  Out of window:  {io_p['out_window']}", file=out)
        print(f"  No timestamp :  {io_p['no_ts']}", file=out)
        print(f"  Public & in window: {io_p['public_in_window']}", file=out)
        if io_p["has_overrides"]:
            print(f"\n  Timestamp precision (in window):", file=out)
            print(f"    Second-precision (original filename, step 3a3): {io_p['precise']:6d}", file=out)
            print(f"    md_creation_date (ground photos, camera local → UTC):  {io_p['day_only']:6d}", file=out)
        else:
            print(f"\n  NOTE: Run step 3a3 with --onboard-only to scrape second-precision", file=out)
            print(f"  datetimes from original filenames for onboard camera photos.", file=out)
        if io_p["top_collections"]:
            print(f"\n  Top sub-collections (breadth-3 of IO collection path):", file=out)
            for coll, cnt in io_p["top_collections"]:
                print(f"    {cnt:5d}  {coll}", file=out)
        if io_p["by_day"]:
            max_c = max(io_p["by_day"].values())
            print(file=out)
            _day_table(mission_days, start, io_p["by_day"], None, max_c, out)
        else:
            print("\n  (no items placed in mission window)", file=out)

    # ── Flickr ────────────────────────────────────────────────────────────
    _sep("PHOTOS — Flickr Album (3b)", out)
    fl = audit_flickr(mission, start, end)
    if not fl["exists"]:
        print("  No album_metadata.json — run step 3b first.", file=out)
    else:
        print(f"  Total items  :  {fl['total']}", file=out)
        print(f"  In window    :  {fl['in_window']}", file=out)
        print(f"  Out of window:  {fl['out_window']}", file=out)
        print(f"  No timestamp :  {fl['no_ts']}", file=out)
        print(f"\n  Timestamp precision (datetaken):", file=out)
        print(f"    Exact (granularity=0):  {fl['exact']:5d}  ← to-the-second (likely camera time, TZ unknown)", file=out)
        print(f"    Approximate:            {fl['day_only']:5d}", file=out)
        if fl["by_day"]:
            max_c = max(fl["by_day"].values())
            print(file=out)
            _day_table(mission_days, start, fl["by_day"], None, max_c, out)
        else:
            print("\n  (no items placed in mission window)", file=out)

    # ── images.nasa.gov ───────────────────────────────────────────────────
    _sep("PHOTOS — images.nasa.gov (3e)", out)
    ng = audit_nasa_images(mission, start, end)
    if not ng["exists"]:
        print("  No catalog.json — run step 3e first.", file=out)
    else:
        print(f"  Total items  :  {ng['total']}", file=out)
        print(f"  In window    :  {ng['in_window']}", file=out)
        print(f"  Out of window:  {ng['out_window']}", file=out)
        print(f"  No timestamp :  {ng['no_ts']}", file=out)
        print(f"\n  Note: date_created is typically day-only precision (no time of day).", file=out)
        if ng["by_day"]:
            max_c = max(ng["by_day"].values())
            print(file=out)
            _day_table(mission_days, start, ng["by_day"], None, max_c, out)
        else:
            print("\n  (no items placed in mission window)", file=out)

    # ── Combined Summary ──────────────────────────────────────────────────
    _sep("COMBINED TIMELINE COVERAGE", out)

    combined_photos: Counter = Counter()
    for src in (io_p, fl, ng):
        if src.get("exists"):
            for dn, c in src.get("by_day", {}).items():
                combined_photos[dn] += c

    combined_video: Counter = Counter()
    combined_dur: Counter = Counter()
    for src in (ia_v, yt_v):
        if src.get("exists"):
            for dn, c in src.get("by_day", {}).items():
                combined_video[dn] += c
            for dn, c in src.get("dur_by_day", {}).items():
                combined_dur[dn] += c

    max_video = max(combined_video.values(), default=1)
    max_photos = max(combined_photos.values(), default=1)

    print(
        f"\n  {'Day':>4}  {'Date':10s}  {'Video':>5}  {'Duration':>9}  {'Photos':>6}",
        file=out,
    )
    print(f"  {'─'*4}  {'─'*10}  {'─'*5}  {'─'*9}  {'─'*6}", file=out)

    empty_days: list[str] = []
    video_only_days: list[str] = []
    photo_only_days: list[str] = []

    for dn in range(1, mission_days + 1):
        d = start + timedelta(days=dn - 1)
        v = combined_video.get(dn, 0)
        p = combined_photos.get(dn, 0)
        dur = combined_dur.get(dn, 0)
        dur_str = fmt_duration(dur) if dur else "—"

        # Build a mini split bar: left = video (█), right = photos (▒)
        v_width = int(round(v / max_video * 10)) if v else 0
        p_width = int(round(p / max_photos * 10)) if p else 0
        mini_bar = "█" * v_width + "▒" * p_width

        print(
            f"  Day {dn:2d}  {d.isoformat()}  {v:5d}  {dur_str:>9s}  {p:6d}  {mini_bar}",
            file=out,
        )

        label = f"Day {dn:2d} ({d.isoformat()})"
        if v == 0 and p == 0:
            empty_days.append(label)
        elif v == 0:
            photo_only_days.append(label)
        elif p == 0:
            video_only_days.append(label)

    print(file=out)
    if empty_days:
        print(f"  Days with NO indexed media ({len(empty_days)}):", file=out)
        for d in empty_days:
            print(f"    {d}", file=out)
    else:
        print("  All mission days have at least one indexed media item.", file=out)

    if video_only_days:
        print(f"\n  Days with video but NO photos ({len(video_only_days)}):", file=out)
        for d in video_only_days:
            print(f"    {d}", file=out)

    if photo_only_days:
        print(f"\n  Days with photos but NO video ({len(photo_only_days)}):", file=out)
        for d in photo_only_days:
            print(f"    {d}", file=out)

    # ── Timeline assessment ───────────────────────────────────────────────
    _sep("TIMELINE PLACEMENT ASSESSMENT", out)

    total_v = sum(combined_video.values())
    total_p = sum(combined_photos.values())

    # Video: precise (artdl) vs day-only vs unreliable
    ia_precise = ia_v.get("source_counts", {}).get("filename_artdl", 0) if ia_v.get("exists") else 0
    ia_day_only = sum(
        ia_v.get("source_counts", {}).get(k, 0)
        for k in ("filename_yymmdd", "filename_ksc")
    ) if ia_v.get("exists") else 0
    ia_unreliable = ia_v.get("source_counts", {}).get("ia_metadata", 0) if ia_v.get("exists") else 0
    yt_count = yt_v.get("in_window", 0) if yt_v.get("exists") else 0

    flickr_in = fl.get("in_window", 0) if fl.get("exists") else 0
    io_in = io_p.get("in_window", 0) if io_p.get("exists") else 0
    io_precise = io_p.get("precise", 0) if io_p.get("exists") else 0
    io_day_only = io_p.get("day_only", 0) if io_p.get("exists") else 0
    ng_in = ng.get("in_window", 0) if ng.get("exists") else 0

    print(f"\n  VIDEO ({total_v} clips in mission window)", file=out)
    print(f"    Precise UTC (ART-DL filename):  {ia_precise:5d}  can place to the second", file=out)
    print(f"    Day-only (filename YYMMDD):      {ia_day_only:5d}  can place to the day", file=out)
    print(f"    Upload-date only (IA metadata):  {ia_unreliable:5d}  timeline placement unreliable", file=out)
    print(f"    YouTube livestreams:             {yt_count:5d}  precise UTC start/end", file=out)

    print(f"\n  PHOTOS ({total_p} dateable items in mission window)", file=out)
    if io_p.get("has_overrides"):
        print(f"    IO (original filename, UTC):        {io_precise:5d}  second-precision", file=out)
        print(f"    IO (md_creation_date, ground):      {io_day_only:5d}  camera local time → UTC (run 3a3 for TZ correction)", file=out)
    else:
        print(f"    IO photos in window:                {io_in:5d}  run step 3a3 --onboard-only for second-precision", file=out)
    print(f"    Flickr (datetaken):                 {flickr_in:5d}  camera time (TZ uncertain)", file=out)
    print(f"    images.nasa.gov (date_created):     {ng_in:5d}  day-only precision", file=out)

    io_public = io_p.get("public_in_window", 0) if io_p.get("exists") else 0
    print(f"\n  Public IO photos in window:   {io_public:5d}", file=out)
    print(f"  Restricted IO photos:         {io_in - io_public:5d}", file=out)

    print(f"\n{'=' * _W}\n", file=out)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit media coverage for a mission",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mission",
        required=True,
        choices=list(MISSIONS.keys()),
        help="Mission slug (e.g. artemis-i)",
    )
    parser.add_argument(
        "--out",
        help="Write report to this file instead of stdout",
    )
    args = parser.parse_args()

    mission = MISSIONS[args.mission]

    if args.out:
        out_path = Path(args.out)
        with open(out_path, "w", encoding="utf-8") as f:
            run_audit(mission, out=f)
        print(f"Report written to {out_path}")
    else:
        run_audit(mission)


if __name__ == "__main__":
    main()
