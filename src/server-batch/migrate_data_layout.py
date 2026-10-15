"""Move legacy bulk holdings into mission trees without copying media.

Run from server-batch: python migrate_data_layout.py [--apply]
The default previews the moves. Run with ingestion stopped; the PDS run lock
is acquired during application. Existing destinations are never overwritten.
"""

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import BULK_ASSETS_DIR, MISSIONS


def layout_moves(root: Path) -> list[tuple[Path, Path]]:
    """Only the missionless drops below are known to belong to Artemis II."""
    moves = []
    for slug in MISSIONS:
        for old_type, new_type in (("photos", "photos"), ("videos", "video/io")):
            source = root / old_type / slug
            target = root / slug / "raw" / new_type
            if old_type == "photos" and source.exists():
                for child in sorted(source.iterdir()):
                    name = {"flickr_orig": "flickr", "nasa_orig": "images_nasa_gov"}.get(child.name, child.name)
                    moves.append((child, target / name))
            else:
                moves.append((source, target))
    mission = root / "artemis-ii"
    moves.extend([
        (root / "5_Crew-Captured-Imagery", mission / "raw/photos/5_Crew-Captured-Imagery"),
        (root / "5_Crew-Captured-Imagery.zip", mission / "raw/photos/5_Crew-Captured-Imagery.zip"),
        (root / "nef_all", mission / "raw/photos/nef_all"),
        (root / "vehicle", mission / "raw/audio/vehicle"),
        (root / "ArtemisII_Science_Data_Tracking.xlsx", mission / "raw/science/ArtemisII_Science_Data_Tracking.xlsx"),
    ])
    # Include photo prefixes even after a previous run moved their contents.
    for slug in MISSIONS:
        for name, renamed in (("eol", "eol"), ("flickr_orig", "flickr"), ("nasa_orig", "images_nasa_gov")):
            pair = (root / "photos" / slug / name, root / slug / "raw/photos" / renamed)
            if pair not in moves:
                moves.append(pair)
    return moves


def rebase(value, moves):
    """Rewrite absolute path prefixes, accepting Windows and POSIX separators."""
    prefixes = [(source.as_posix().casefold(), target.as_posix())
                for source, target in sorted(moves, key=lambda pair: len(str(pair[0])), reverse=True)]

    def visit(item):
        if isinstance(item, str):
            normalized = item.replace("\\", "/")
            # Large EXIF/ledger documents contain millions of non-path strings.
            if not normalized.startswith("/") and not (len(normalized) >= 3 and normalized[1:3] == ":/"):
                return item
            folded = normalized.casefold()
            for prefix, target in prefixes:
                if folded == prefix or folded.startswith(prefix + "/"):
                    result = target + normalized[len(prefix):]
                    return result.replace("/", "\\") if "\\" in item else result
            return item
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {visit(key): visit(child) for key, child in item.items()}
        return item

    return visit(value)


def snapshot(path):
    """Record file identity, length and modification time, without reading bytes."""
    files = [path] if path.is_file() else (p for p in path.rglob("*") if p.is_file())
    result = {}
    for file in files:
        st = file.stat()
        result[str(file.relative_to(path))] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    return result


def preflight(root, moves):
    for source, target in moves:
        for path in (source, target):
            resolved = path.resolve()
            if resolved == root or not resolved.is_relative_to(root):
                raise ValueError(f"Path escapes bulk storage root: {path}")
        if source.exists() and target.exists():
            raise FileExistsError(f"Destination already exists: {target}")


def repair_database(path, moves, backup):
    """Keep cached hashes and transfer evidence while updating local filenames."""
    c = sqlite3.connect(path, timeout=60)
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(backup)) as saved:
            c.backup(saved)
        changed = 0
        with c:
            for table, column in (("local", "path"), ("members", "path"), ("remote", "source")):
                rows = c.execute(f"SELECT rowid,{column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
                for rowid, old in rows:
                    new = rebase(old, moves)
                    if new != old:
                        c.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (new, rowid))
                        changed += 1
            for key, old in c.execute("SELECT key,value FROM meta").fetchall():
                value = json.loads(old)
                new = rebase(value, moves)
                if new != value:
                    c.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(new), key))
                    changed += 1
        return changed
    finally:
        c.close()


def repair_metadata(missions, moves, backup_root):
    changed = []
    seen = set()
    prefixes = {s.as_posix().casefold() for s, _ in moves}
    prefixes.update(str(s).casefold() for s, _ in moves)
    for mission in missions:
        for root in dict.fromkeys([mission.data_dir / "processed", mission.pds_processed]):
            if not root.exists():
                continue
            print(f"Checking saved paths under {root}", flush=True)
            for directory, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in {"storage-migrations", "checkpoints", "quarantine", "logs", "reports"}]
                for name in files:
                    path = Path(directory) / name
                    if path in seen or path.suffix not in {".json", ".jsonl"}:
                        continue
                    seen.add(path)
                    original = path.read_text(encoding="utf-8")
                    # Most files contain no moved paths; avoid reserializing them.
                    decoded = original.replace("\\\\", "\\").casefold()
                    if not any(prefix in decoded for prefix in prefixes):
                        continue
                    if path.suffix == ".jsonl":
                        rows = [json.loads(line) for line in original.splitlines() if line.strip()]
                        updated = rebase(rows, moves)
                        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in updated)
                    else:
                        rows = json.loads(original)
                        updated = rebase(rows, moves)
                        content = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
                    if updated == rows:
                        continue
                    backup = backup_root / "metadata" / str(len(changed)) / path.name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)
                    tmp = path.with_name(path.name + ".layout-tmp")
                    tmp.write_text(content, encoding="utf-8")
                    os.replace(tmp, path)
                    changed.append({"path": str(path), "backup": str(backup)})
                    print(f"Updated saved paths in {path}", flush=True)
    return changed


def migrate(root, missions, apply=False):
    root = root.resolve(strict=True)
    moves = layout_moves(root)
    preflight(root, moves)
    active = [(s, t) for s, t in moves if s.exists()]
    for source, target in active:
        print(f"{source} -> {target}", flush=True)
    if not apply:
        print(f"Preview: {len(active)} moves on the same drive. Pass --apply to execute.")
        return
    from shared.pds import locked
    with contextlib.ExitStack() as stack:
        for mission in missions:
            stack.enter_context(locked(mission))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_root = root / "artemis-ii/processed/storage-migrations" / stamp
        backup_root.mkdir(parents=True)
        receipt = {"moves": [], "databases": [], "metadata": []}
        receipt_path = backup_root / "receipt.json"

        def save():
            receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

        save()
        for source, target in active:
            before = snapshot(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            # rename requires the same volume; it cannot silently copy gigabytes.
            os.rename(source, target)
            if snapshot(target) != before:
                raise RuntimeError(f"File identity/size/mtime changed while moving {source}")
            receipt["moves"].append({"source": str(source), "target": str(target),
                                     "files": len(before), "bytes": sum(st[2] for st in before.values())})
            save()
            print(f"Verified {len(before):,} files at {target}", flush=True)
        for mission in missions:
            database = mission.pds_processed / "inventory.sqlite"
            if database.exists():
                count = repair_database(database, moves, backup_root / mission.slug / "inventory.sqlite")
                receipt["databases"].append({"path": str(database), "updated_rows": count})
                save()
        receipt["metadata"] = repair_metadata(missions, moves, backup_root)
        save()
        # Remove only empty legacy containers, never recursively delete holdings.
        for slug in MISSIONS:
            old = root / "photos" / slug
            if old.is_dir() and not any(old.iterdir()):
                old.rmdir()
        for name in ("photos", "videos"):
            old = root / name
            if old.is_dir() and not any(old.iterdir()):
                old.rmdir()
        print(f"Migration complete; receipt and metadata backups: {backup_root}", flush=True)
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Execute the previewed same-drive moves")
    args = parser.parse_args()
    migrate(BULK_ASSETS_DIR, list(MISSIONS.values()), args.apply)
