"""PDS discovery, local reconciliation, retrieval, coverage and catalog integration."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from lxml import html

from config import MISSIONS, MissionConfig
from shared.pds import (ATLAS, CIS_BUNDLES, GEO, SPICE, RELEASE, HostLimits,
    atomic_json, db, destination, get_meta, hashes, issue, locked, nasa_id,
    parse_label, priority, run_id, safe_relative, session, set_meta,
    stream_download, utc, valid_evidence, verify_file)


def remote_insert(c, run, bundle, relative, uri, url, size, md5):
    relative = safe_relative(relative)
    md5 = md5.lower() if md5 else None
    old = c.execute("SELECT * FROM remote WHERE bundle=? AND relative=? COLLATE NOCASE", (bundle, relative)).fetchone()
    if old and old["relative"] != relative:
        raise ValueError(f"Windows case collision: {old['relative']} and {relative}")
    if old and (old["size"] != size or old["md5"] != md5):
        issue(c, "changed_remote", uri, f"Previous size/checksum {old['size']}/{old['md5']}")
        c.execute("UPDATE remote SET status='pending',sha256=NULL,source=NULL,member=NULL,action='network' WHERE id=?", (old["id"],))
    collection = relative.split("/")[0] if "/" in relative else "root"
    c.execute('''INSERT INTO remote(bundle,collection,relative,uri,url,size,md5,discovery)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(bundle,relative) DO UPDATE SET
        uri=excluded.uri,url=excluded.url,size=excluded.size,md5=excluded.md5,
        discovery=excluded.discovery,active=1''', (bundle, collection, relative, uri, url, size, md5, run))


def discover_directory(c, s, run, bundle, base, folder, md5_name=None):
    checksum = {}
    checksum_bytes = None
    if md5_name:
        response = s.get(urljoin(base, md5_name), timeout=(20, 60))
        response.raise_for_status()
        checksum_bytes = response.content
        (folder / md5_name).write_bytes(checksum_bytes)
        for line in response.text.splitlines():
            m = re.match(r"([a-fA-F0-9]{32})\s+\*?(.+)", line)
            if m:
                checksum[safe_relative(m[2].strip().replace("\\", "/").lstrip("/"))] = m[1].lower()
    queue, seen, paths = [base], set(), set()
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        response = s.get(url, timeout=(20, 60))
        response.raise_for_status()
        (folder / f"directory-{len(seen):04}.html").write_bytes(response.content)
        tree = html.fromstring(response.content)
        for href in tree.xpath("//a/@href"):
            target = urljoin(url, href)
            if not target.startswith(base) or urlparse(target).query or urlparse(target).fragment:
                continue
            relative = unquote(target[len(base):])
            if not relative:
                continue
            if target.endswith("/"):
                queue.append(target)
                continue
            relative = safe_relative(relative)
            if relative in paths:
                continue
            head = s.head(target, timeout=(20, 60))
            head.raise_for_status()
            size = int(head.headers["Content-Length"]) if "Content-Length" in head.headers else None
            remote_insert(c, run, bundle, relative, target, target, size, checksum.get(relative))
            paths.add(relative)
        c.commit()
    for missing in checksum.keys() - paths:
        # Manifest is also an authority: discover published files omitted by listings.
        target = urljoin(base, missing)
        head = s.head(target, timeout=(20, 60))
        if head.ok:
            size = int(head.headers["Content-Length"]) if "Content-Length" in head.headers else None
            remote_insert(c, run, bundle, missing, target, target, size, checksum[missing])
        else:
            issue(c, "missing_checksum_member", target, head.status_code)
    c.commit()


def discover(mission):
    c, s, run = db(mission), session(mission), run_id()
    folder = mission.pds_processed / "manifests" / run
    folder.mkdir(parents=True)
    c.execute("INSERT INTO runs VALUES(?,?,NULL,'discovering')", (run, utc()))
    c.commit()
    set_meta(c, "discovery_in_progress", run)
    try:
        release = s.get(RELEASE, timeout=(20, 60))
        release.raise_for_status()
        (folder / "release-page.html").write_bytes(release.content)
        for bundle in CIS_BUNDLES:
            after, count, previous, total = None, 0, None, None
            output = folder / f"{bundle}.jsonl"
            with open(output, "w", encoding="utf-8") as f:
                while True:
                    query = {"size": 1000, "track_total_hits": True, "_source": ["uri", "archive"],
                        "query": {"bool": {"filter": [{"term": {"archive.bundle_id": bundle}},
                            {"term": {"archive.fs_type": "file"}}]}}, "sort": [{"uri": "asc"}]}
                    if after:
                        query["search_after"] = after
                    response = s.post(ATLAS, json=query, timeout=(20, 90))
                    response.raise_for_status()
                    data = response.json()
                    if data.get("timed_out") or data.get("_shards", {}).get("failed"):
                        raise RuntimeError("Incomplete Atlas search")
                    hits = data["hits"]
                    if hits["total"]["relation"] != "eq":
                        raise RuntimeError("Atlas count is not exact")
                    advertised = hits["total"]["value"]
                    if total is not None and advertised != total:
                        raise RuntimeError("Index changed during discovery; repeat discovery")
                    total = advertised
                    page = hits["hits"]
                    if not page:
                        break
                    for hit in page:
                        source = hit["_source"]
                        archive = source["archive"]
                        uri = source["uri"]
                        if previous and uri <= previous:
                            raise RuntimeError("Repeated or unordered Atlas page")
                        previous = uri
                        relative = uri.split("/" + bundle + "/", 1)[1]
                        remote_insert(c, run, bundle, relative, uri, None, archive.get("size"), archive.get("md5"))
                        f.write(json.dumps(source) + "\n")
                        count += 1
                    after = page[-1]["sort"]
                    c.commit()
                    atomic_json(folder / "checkpoint.json", {"bundle": bundle, "count": count, "total": total, "search_after": after})
                    print(f"Discovery {bundle}: {count:,}/{total:,}", flush=True)
            if count != total:
                raise RuntimeError(f"Atlas enumeration mismatch {bundle}: {count} != {total}")
        geo_folder = folder / "artemis2_mission"
        geo_folder.mkdir()
        discover_directory(c, s, run, "artemis2_mission", GEO, geo_folder, "urn-nasa-pds-artemis2_mission.md5")
        pending = [{"source": "iPhone imagery", "status": "pending_release", "evidence": "artemis2_data_user_guide.pdf section 1.1"},
                   {"source": "HERA radiation archive", "status": "pending_release", "evidence": "artemis2_data_user_guide.pdf"}]
        spice = s.get(SPICE, timeout=(20, 60))
        (folder / "spice-status.txt").write_text(f"{utc()} HTTP {spice.status_code}\n{SPICE}\n", encoding="utf-8")
        if spice.status_code == 404:
            pending.append({"source": "artemis2_spice", "status": "pending_release", "evidence": RELEASE})
        else:
            spice.raise_for_status()
            spice_folder = folder / "artemis2_spice"
            spice_folder.mkdir()
            discover_directory(c, s, run, "artemis2_spice", SPICE, spice_folder)
        c.execute("UPDATE remote SET active=0 WHERE discovery != ?", (run,))
        c.execute("UPDATE runs SET finished=?,status='complete' WHERE id=?", (utc(), run))
        c.commit()
        set_meta(c, "discovery", run)
        set_meta(c, "discovery_in_progress", None)
        set_meta(c, "pending_releases", pending)
        rows = [dict(r) for r in c.execute("SELECT bundle,collection,relative,uri,url,size,md5 FROM remote WHERE active=1 ORDER BY bundle,relative")]
        with open(folder / "release-manifest.jsonl", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        atomic_json(folder / "summary.json", {"run": run, "files": len(rows), "bytes": sum(r["size"] or 0 for r in rows),
                                                "pending_releases": pending, "sources": RELEASE})
        atomic_json(folder / "snapshot-hashes.json", {str(p.relative_to(folder)): hashes(p)[1]
                    for p in folder.rglob("*") if p.is_file()})
        print(f"Discovery complete: {len(rows):,} files; {sum(r['size'] or 0 for r in rows):,} bytes", flush=True)
    except BaseException:
        c.execute("UPDATE runs SET finished=?,status='failed' WHERE id=?", (utc(), run))
        c.commit()
        raise
    finally:
        c.close()
        s.close()


def inventory(mission):
    c = db(mission)
    c.execute("DELETE FROM issues WHERE kind IN ('scan_error','zip_error')")
    seen, roots, scanned, members = set(), [], 0, 0
    excluded = [mission.pds_raw.resolve(), mission.pds_processed.resolve(), mission.pds_staging.resolve()]
    for root in sorted(mission.pds_inventory_roots, key=lambda p: len(str(p))):
        root = root.resolve()
        if not root.exists():
            issue(c, "optional_root_absent", str(root), "Not present")
            continue
        if any(root.is_relative_to(previous) for previous in roots):
            continue
        roots.append(root)
        def onerror(e):
            issue(c, "scan_error", str(e.filename), str(e))
        for directory, dirs, files in os.walk(root, onerror=onerror):
            dirs[:] = [n for n in dirs if not any((Path(directory) / n).resolve().is_relative_to(p) for p in excluded)]
            for name in files:
                path = Path(directory) / name
                try:
                    st = path.stat()
                    key = str(path)
                    if key in seen:
                        continue
                    seen.add(key)
                    old = c.execute("SELECT * FROM local WHERE path=?", (key,)).fetchone()
                    # ReFS file IDs can exceed SQLite's signed 64-bit INTEGER range.
                    file_identity = f"id:{st.st_ino:x}"
                    unchanged = old and old["size"] == st.st_size and old["mtime"] == st.st_mtime_ns and old["inode"] == file_identity
                    c.execute("INSERT OR REPLACE INTO local VALUES(?,?,?,?,?,?,?,?,?)", (key, st.st_size, st.st_mtime_ns,
                        f"device:{st.st_dev:x}", file_identity, nasa_id(name), path.suffix.lower(), old["md5"] if unchanged else None,
                        old["sha256"] if unchanged else None))
                    scanned += 1
                    if path.suffix.lower() == ".zip":
                        if not unchanged:
                            c.execute("DELETE FROM members WHERE path=?", (key,))
                        with zipfile.ZipFile(path) as archive:
                            for member in archive.infolist():
                                if member.is_dir():
                                    continue
                                safe_relative(member.filename)
                                c.execute("INSERT OR IGNORE INTO members VALUES(?,?,?,?,?,?,NULL,NULL)", (key, member.filename,
                                    member.file_size, member.CRC, nasa_id(member.filename), Path(member.filename).suffix.lower()))
                                members += 1
                except (OSError, ValueError, zipfile.BadZipFile) as e:
                    issue(c, "zip_error" if path.suffix.lower() == ".zip" else "scan_error", str(path), str(e))
            c.commit()
    # Deleted holdings must not survive as candidates on a later scan.
    for row in c.execute("SELECT path FROM local").fetchall():
        if row[0] not in seen:
            c.execute("DELETE FROM local WHERE path=?", (row[0],))
            c.execute("DELETE FROM members WHERE path=?", (row[0],))
    c.commit()
    set_meta(c, "inventory", {"date": utc(), "files": scanned, "zip_members": members, "roots": [str(p) for p in roots]})
    c.close()
    print(f"Local inventory: {scanned:,} files, {members:,} ZIP members", flush=True)


def reconcile(mission, reserve):
    import hashlib
    c = db(mission)
    c.execute("DELETE FROM issues WHERE kind='different_bytes'")
    if not get_meta(c, "discovery") or get_meta(c, "discovery_in_progress"):
        raise RuntimeError("A complete discovery is required")
    rows = c.execute("SELECT * FROM remote WHERE active=1 ORDER BY bundle,relative").fetchall()
    for index, r in enumerate(rows):
        dest = destination(mission.pds_raw, r["bundle"] + "/" + r["relative"])
        if valid_evidence(r, dest):
            continue
        action, source, member = "network", None, None
        if dest.exists() and r["md5"]:
            try:
                sha = verify_file(r, dest)
                st = dest.stat()
                c.execute("UPDATE remote SET status=?,sha256=?,stat_size=?,stat_mtime=?,error=NULL WHERE id=?",
                          ("verified" if r["md5"] else "verified_size_only", sha, st.st_size, st.st_mtime_ns, r["id"]))
                continue
            except ValueError as e:
                issue(c, "destination_conflict", str(dest), str(e))
        if r["md5"] and r["size"] is not None:
            nid, ext = nasa_id(r["relative"]), Path(r["relative"]).suffix.lower()
            candidates = c.execute("SELECT * FROM local WHERE size=? AND extension=? AND (nasa_id=? OR lower(path) LIKE ?) ORDER BY path",
                (r["size"], ext, nid, "%" + Path(r["relative"]).name.lower())).fetchall()
            # Also permit renamed files of the same size/format: full checksum proves identity.
            if not candidates:
                candidates = c.execute("SELECT * FROM local WHERE size=? AND extension=?", (r["size"], ext)).fetchall()
            for local in candidates:
                p = Path(local["path"])
                st = p.stat()
                if st.st_size != local["size"] or st.st_mtime_ns != local["mtime"]:
                    continue
                md5, sha = (local["md5"], local["sha256"]) if local["md5"] else hashes(p)
                c.execute("UPDATE local SET md5=?,sha256=? WHERE path=?", (md5, sha, str(p)))
                if md5 == r["md5"]:
                    action, source = ("link" if st.st_dev == mission.pds_processed.stat().st_dev else "copy"), str(p)
                    break
                if nid and local["nasa_id"] == nid:
                    issue(c, "different_bytes", r["uri"], str(p))
            if source is None:
                for local in c.execute("SELECT * FROM members WHERE size=? AND extension=?", (r["size"], ext)).fetchall():
                    md5, sha = local["md5"], local["sha256"]
                    if not md5:
                        hm, hs = hashlib.md5(), hashlib.sha256()
                        try:
                            with zipfile.ZipFile(local["path"]) as archive, archive.open(local["member"]) as f:
                                while chunk := f.read(4 * 1024 * 1024):
                                    hm.update(chunk)
                                    hs.update(chunk)
                            md5, sha = hm.hexdigest(), hs.hexdigest()
                            c.execute("UPDATE members SET md5=?,sha256=? WHERE path=? AND member=?", (md5, sha, local["path"], local["member"]))
                        except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error, EOFError, NotImplementedError) as e:
                            issue(c, "zip_error", local["path"], str(e))
                            continue
                    if md5 == r["md5"]:
                        action, source, member = "extract", local["path"], local["member"]
                        break
        c.execute("UPDATE remote SET status='pending',action=?,source=?,member=?,error=NULL WHERE id=?", (action, source, member, r["id"]))
        if index % 100 == 0:
            c.commit()
            if index % 1000 == 0:
                print(f"Reconciliation: {index:,}/{len(rows):,}", flush=True)
    c.commit()
    planned = [dict(r) for r in c.execute("SELECT id,bundle,collection,relative,uri,size,md5,action,source,member,status FROM remote WHERE active=1 ORDER BY bundle,relative")]
    additional = sum(r["size"] or 0 for r in planned if r["status"] == "pending" and r["action"] != "link")
    # Budget possible hard-link fallbacks, partials/quarantine and integration growth.
    fallback = sum(r["size"] or 0 for r in planned if r["status"] == "pending" and r["action"] == "link")
    partials = sum(p.stat().st_size for p in mission.pds_staging.rglob("*.part")) if mission.pds_staging.exists() else 0
    free = shutil.disk_usage(mission.pds_processed).free
    plan = run_id()
    path = mission.pds_processed / "manifests" / get_meta(c, "discovery") / f"transfer-plan-{plan}.json"
    atomic_json(path, {"id": plan, "discovery": get_meta(c, "discovery"), "created": utc(), "files": planned,
        "capacity": {"free": free, "additional": additional, "link_fallback": fallback, "existing_partials": partials,
                     "reserve": reserve, "safe": free >= additional + fallback + reserve + 10_000_000_000}})
    set_meta(c, "plan", {"id": plan, "path": str(path), "discovery": get_meta(c, "discovery"), "safe": free >= additional + fallback + reserve + 10_000_000_000})
    print(f"Transfer plan saved: {path}\nAdditional bytes: {additional:,}; link reuse: {fallback:,}; free: {free:,}", flush=True)
    if free < additional + fallback + reserve + 10_000_000_000:
        c.close()
        raise RuntimeError("Insufficient space after reserve and copy fallback budget")
    c.close()


def fetch_one(mission, row, limits, reserve, bandwidth):
    r = dict(row)
    c, s = db(mission), session(mission)
    dest = destination(mission.pds_raw, r["bundle"] + "/" + r["relative"])
    part = destination(mission.pds_staging, r["bundle"] + "/" + r["relative"] + ".part")
    checkpoint = part.with_name(part.name + ".json")
    started, method, network = utc(), r["action"], 0
    try:
        if valid_evidence(r, dest):
            return True
        part.parent.mkdir(parents=True, exist_ok=True)
        if method == "link":
            source = Path(r["source"])
            part.unlink(missing_ok=True)
            try:
                os.link(source, part)
            except OSError:
                method = "copy"
        if method == "copy":
            if shutil.disk_usage(part.parent).free < reserve + (r["size"] or 0):
                raise OSError("Insufficient space for copy")
            shutil.copyfile(r["source"], part)
        elif method == "extract":
            safe_relative(r["member"])
            if shutil.disk_usage(part.parent).free < reserve + (r["size"] or 0):
                raise OSError("Insufficient space for extraction")
            with zipfile.ZipFile(r["source"]) as archive, archive.open(r["member"]) as src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
        elif method == "network":
            network = stream_download(s, r, part, checkpoint, limits, reserve, bandwidth)
        sha = verify_file(r, part)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            quarantine = mission.pds_processed / "quarantine" / run_id() / r["bundle"] / r["relative"]
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(dest, quarantine)
        os.replace(part, dest)
        checkpoint.unlink(missing_ok=True)
        st = dest.stat()
        status = "verified" if r["md5"] else "verified_size_only"
        c.execute("UPDATE remote SET status=?,sha256=?,stat_size=?,stat_mtime=?,network_bytes=network_bytes+?,method=?,error=NULL WHERE id=?",
                  (status, sha, st.st_size, st.st_mtime_ns, network, method, r["id"]))
        c.execute("INSERT INTO attempts VALUES(?,?,?,?,NULL)", (r["id"], started, utc(), method))
        c.commit()
        return True
    except Exception as e:
        c.execute("UPDATE remote SET status='failed',error=? WHERE id=?", (str(e), r["id"]))
        c.execute("INSERT INTO attempts VALUES(?,?,?,?,?)", (r["id"], started, utc(), method, str(e)))
        c.commit()
        print(f"FAILED {r['bundle']}/{r['relative']}: {e}", flush=True)
        # A corrupt fully received partial cannot be resumed as a completed file.
        if isinstance(e, ValueError):
            part.unlink(missing_ok=True)
            checkpoint.unlink(missing_ok=True)
        return False
    finally:
        c.close()
        s.close()


def download(mission, reserve, workers=4, limit=None, max_priority=None, bandwidth=0):
    c = db(mission)
    plan = get_meta(c, "plan")
    if not plan or not plan["safe"] or plan["discovery"] != get_meta(c, "discovery") or get_meta(c, "discovery_in_progress"):
        raise RuntimeError("Run reconciliation to save a current safe transfer plan")
    rows = c.execute("SELECT * FROM remote WHERE active=1").fetchall()
    todo = [r for r in rows if not valid_evidence(r, destination(mission.pds_raw, r["bundle"] + "/" + r["relative"]))]
    todo.sort(key=lambda r: (priority(r), r["bundle"], r["relative"]))
    if max_priority is not None:
        todo = [r for r in todo if priority(r) <= max_priority]
    if limit:
        todo = todo[:limit]
    if shutil.disk_usage(mission.pds_processed).free < reserve + sum(r["size"] or 0 for r in todo if r["action"] != "link"):
        raise RuntimeError("Current capacity below remaining transfer requirement plus reserve")
    set_meta(c, "download", {"started": utc(), "pid": os.getpid(), "workers": workers, "queued": len(todo), "status": "running"})
    c.close()
    print(f"Starting {len(todo):,} files with {workers} workers (2 per host)", flush=True)
    limits, failed, count, start, last = HostLimits(), 0, 0, time.monotonic(), time.monotonic()
    # Bounded submission keeps large manifests cheap and progress snapshots frequent.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        iterator = iter(todo)
        pending = {}
        for _ in range(workers):
            r = next(iterator, None)
            if r is not None:
                pending[pool.submit(fetch_one, mission, r, limits, reserve, bandwidth / workers)] = r
        while pending:
            from concurrent.futures import wait, FIRST_COMPLETED
            done, _ = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                failed += not future.result()
                count += 1
                r = next(iterator, None)
                if r is not None:
                    pending[pool.submit(fetch_one, mission, r, limits, reserve, bandwidth / workers)] = r
            if time.monotonic() - last >= 30 or count == len(todo):
                elapsed = time.monotonic() - start
                print(f"Transfer progress: {count:,}/{len(todo):,}; failed={failed}; elapsed={elapsed:.0f}s", flush=True)
                report(mission, quiet=True)
                last = time.monotonic()
    c = db(mission)
    set_meta(c, "download", {"finished": utc(), "pid": os.getpid(), "completed": count, "failed": failed, "status": "incomplete" if failed else "finished"})
    c.close()
    if failed:
        raise RuntimeError(f"{failed} transfers failed; rerun 7d to resume")


def verify(mission, full_hash=False, require_complete=True):
    c = db(mission)
    c.execute("DELETE FROM products")
    c.execute("DELETE FROM issues WHERE kind IN ('label_parse','missing_file_reference','file_reference_mismatch','inventory_member_missing','bundle_member_missing')")
    paths, product_ids, labels, inventories = {}, set(), [], []
    missing = 0
    for r in c.execute("SELECT * FROM remote WHERE active=1").fetchall():
        path = destination(mission.pds_raw, r["bundle"] + "/" + r["relative"])
        if not valid_evidence(r, path):
            missing += 1
            if r["status"].startswith("verified"):
                c.execute("UPDATE remote SET status='pending',error='Verification evidence changed' WHERE id=?", (r["id"],))
            continue
        if full_hash:
            try:
                if verify_file(r, path) != r["sha256"]:
                    raise ValueError("SHA-256 changed")
            except ValueError as e:
                c.execute("UPDATE remote SET status='failed',error=? WHERE id=?", (str(e), r["id"]))
                missing += 1
                continue
        paths[str(path).casefold()] = r
        if path.suffix.lower() == ".xml":
            try:
                metadata = parse_label(path)
                c.execute("INSERT OR REPLACE INTO products VALUES(?,?,?)", (str(path), metadata.get("lidvid"), json.dumps(metadata)))
                if metadata.get("lidvid"):
                    product_ids.add(metadata["lidvid"])
                labels.append((path, metadata))
            except Exception as e:
                issue(c, "label_parse", str(path), str(e))
        if path.name.endswith("inventory.csv"):
            inventories.append(path)
    for path, metadata in labels:
        for reference in metadata["file_records"]:
            try:
                target = (path.parent / safe_relative(reference["file_name"])).resolve()
                if not target.is_relative_to(mission.pds_raw.resolve()):
                    raise ValueError("Reference escapes bundle root")
                r = paths.get(str(target).casefold())
                if r is None:
                    issue(c, "missing_file_reference", str(path), reference["file_name"])
                else:
                    expected_size = reference.get("file_size")
                    expected_md5 = reference.get("md5_checksum")
                    if expected_size and int(expected_size) != r["stat_size"]:
                        issue(c, "file_reference_mismatch", str(path), reference)
                    if expected_md5 and expected_md5.lower() != (r["md5"] or "").lower():
                        actual, _ = hashes(target)
                        if actual != expected_md5.lower():
                            issue(c, "file_reference_mismatch", str(path), reference)
                        elif not r["md5"]:
                            c.execute("UPDATE remote SET md5=?,status='verified' WHERE id=?", (actual, r["id"]))
            except (ValueError, OSError) as e:
                issue(c, "missing_file_reference", str(path), str(e))
        for member in metadata["members"]:
            if member not in product_ids:
                issue(c, "bundle_member_missing", str(path), member)
    for path in inventories:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip() == "P" and row[1].strip() not in product_ids:
                    issue(c, "inventory_member_missing", str(path), row[1].strip())
    c.commit()
    set_meta(c, "verification", {"date": utc(), "full_hash": full_hash, "missing": missing, "labels": len(labels)})
    errors = c.execute("SELECT count(*) FROM issues WHERE kind IN ('label_parse','missing_file_reference','file_reference_mismatch','inventory_member_missing','bundle_member_missing')").fetchone()[0]
    c.close()
    print(f"Verification: {len(labels):,} labels; {missing:,} unavailable files; {errors:,} reference issues", flush=True)
    if require_complete and (missing or errors):
        raise RuntimeError("Available release is incomplete; see coverage report")


def media_type(r):
    ext = Path(r["relative"]).suffix.lower()
    if ext in {".nef", ".dng", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}:
        return "photos"
    if ext in {".mp4", ".mxf", ".mov"}:
        return "video"
    if ext in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".aif", ".aiff"}:
        return "audio"
    if ext == ".srt" or "transcript" in r["collection"]:
        return "transcripts"
    return "science"


def integrate(mission):
    c = db(mission)
    catalogs = {k: [] for k in ("photos", "video", "audio", "transcripts", "science")}
    for r in c.execute("SELECT * FROM remote WHERE active=1 AND status LIKE 'verified%'"):
        path = destination(mission.pds_raw, r["bundle"] + "/" + r["relative"])
        if not valid_evidence(r, path):
            continue
        label_path = path.with_suffix(".xml")
        label = c.execute("SELECT metadata FROM products WHERE path=?", (str(label_path),)).fetchone()
        metadata = json.loads(label[0]) if label else {}
        record = {"bundle": r["bundle"], "collection": r["collection"], "path": str(path), "relative": r["relative"],
            "uri": r["uri"], "nasa_id": nasa_id(path.name), "md5": r["md5"], "sha256": r["sha256"],
            "size": r["stat_size"], "assurance": r["status"], "aliases": [r["source"]] if r["source"] else [],
            "label": str(label_path) if label else None, "metadata": metadata,
            "role": r["collection"], "lidvid": metadata.get("lidvid"),
            "start_utc": (metadata.get("start_date_time") or [None])[0],
            "stop_utc": (metadata.get("stop_date_time") or [None])[0]}
        record["channel"] = next((p for p in Path(r["relative"]).parts if re.fullmatch(r"channel\d+", p)), None)
        record["codec"] = (metadata.get("video_codec") or metadata.get("codec") or metadata.get("encoding_type") or [None])[0]
        record["duration_seconds"] = None
        if record["start_utc"] and record["stop_utc"]:
            from datetime import datetime
            try:
                record["duration_seconds"] = (datetime.fromisoformat(record["stop_utc"].replace("Z", "+00:00"))
                        - datetime.fromisoformat(record["start_utc"].replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                pass
        catalogs[media_type(r)].append(record)
    output = mission.data_dir / "processed" / "pds"
    output.mkdir(parents=True, exist_ok=True)
    for family, records in catalogs.items():
        path = output / f"{family}.jsonl"
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in sorted(records, key=lambda x: x["path"]):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    # Transcript import preserves supplied formats and provenance separately.
    dest = mission.processed_transcripts / "pds"
    for rec in catalogs["transcripts"]:
        if Path(rec["path"]).suffix.lower() in {".csv", ".srt"}:
            target = destination(dest, rec["bundle"] + "/" + rec["relative"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or hashes(target)[1] != rec["sha256"]:
                shutil.copyfile(rec["path"], target)
    from shared.pds_photos import merge_pds_photos
    from shared.photos_ledger import load_ledger, save_ledger
    if mission.photos_ledger_path.exists():
        records = load_ledger(mission.photos_ledger_path)
        merge_pds_photos(records, output / "photos.jsonl", mission.exif_cache_dir)
        backup = mission.pds_processed / "checkpoints" / (run_id() + "-photos_ledger.jsonl")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mission.photos_ledger_path, backup)
        save_ledger(mission.photos_ledger_path, records.values())
    set_meta(c, "integration", {"date": utc(), "counts": {k: len(v) for k, v in catalogs.items()},
                               "catalog_dir": str(output), "browser": "PDS video index and local range serving; codec compatibility requires playback verification"})
    import importlib
    importlib.import_module("2_video.2g_web_video").build_web_video_pds(mission)
    c.close()
    print(f"Integrated catalogs: { {k: len(v) for k,v in catalogs.items()} }", flush=True)


def report(mission, quiet=False):
    c = db(mission)
    groups = [dict(r) for r in c.execute('''SELECT bundle,collection,count(*) expected_files, sum(size) expected_bytes,
        sum(CASE WHEN status LIKE 'verified%' THEN 1 ELSE 0 END) verified_files,
        sum(CASE WHEN status LIKE 'verified%' THEN stat_size ELSE 0 END) verified_bytes,
        sum(CASE WHEN status='verified_size_only' THEN 1 ELSE 0 END) weaker_assurance_files,
        sum(CASE WHEN method='network' THEN stat_size ELSE 0 END) downloaded_bytes,
        sum(CASE WHEN method='link' THEN stat_size ELSE 0 END) linked_bytes,
        sum(CASE WHEN method='copy' THEN stat_size ELSE 0 END) copied_bytes,
        sum(CASE WHEN method='extract' THEN stat_size ELSE 0 END) extracted_bytes,
        sum(CASE WHEN status NOT LIKE 'verified%' AND action='network' THEN size ELSE 0 END) remaining_network_bytes,
        sum(CASE WHEN size IS NULL THEN 1 ELSE 0 END) unknown_sizes
        FROM remote WHERE active=1 GROUP BY bundle,collection ORDER BY bundle,collection''')]
    issues = [dict(r) for r in c.execute("SELECT * FROM issues ORDER BY kind,identity")]
    unresolved = [dict(r) for r in c.execute("SELECT bundle,relative,status,error FROM remote WHERE active=1 AND status NOT LIKE 'verified%'")]
    verified = sum(r["verified_files"] or 0 for r in groups)
    expected = sum(r["expected_files"] for r in groups)
    verified_bytes = sum(r["verified_bytes"] or 0 for r in groups)
    downloaded_bytes = sum(r["downloaded_bytes"] or 0 for r in groups)
    status = {"date": utc(), "pid": os.getpid(), "discovery": get_meta(c, "discovery"), "plan": get_meta(c, "plan"),
        "expected_files": expected, "verified_files": verified, "expected_bytes": sum(r["expected_bytes"] or 0 for r in groups),
        "verified_bytes": verified_bytes, "downloaded_bytes": downloaded_bytes,
        "remaining_network_bytes": sum(r["remaining_network_bytes"] or 0 for r in groups),
        "free_bytes": shutil.disk_usage(mission.pds_processed).free, "groups": groups, "issues": issues,
        "pending_releases": get_meta(c, "pending_releases", []), "download": get_meta(c, "download"),
        "integration": get_meta(c, "integration"), "verification": get_meta(c, "verification"),
        "source_format_gaps": [{"format": "original MXF", "status": "not_in_current_manifest",
            "detail": "Science report describes original MXF; current public video products are MP4"}]
            if not c.execute("SELECT 1 FROM remote WHERE active=1 AND lower(relative) LIKE '%.mxf'").fetchone() else [],
        "report_source_count_comparison": {"crew_observations_in_science_report": 10407,
            "published_source_nef_files": c.execute("SELECT count(*) FROM remote WHERE active=1 AND bundle='artemis2_crew_camera' AND lower(relative) LIKE '%.nef'").fetchone()[0],
            "note": "Report observations and public archive files differ; absent filenames are not guessed."},
        "available_archive_complete": expected > 0 and verified == expected and not any(i["kind"] in {
            "missing_file_reference", "file_reference_mismatch", "inventory_member_missing", "bundle_member_missing", "label_parse", "missing_checksum_member"} for i in issues)}
    prior_path = mission.pds_processed / "reports" / "latest.json"
    if prior_path.exists():
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        from datetime import datetime
        elapsed = (datetime.fromisoformat(status["date"]) - datetime.fromisoformat(prior["date"])).total_seconds()
        delta = downloaded_bytes - prior.get("downloaded_bytes", 0)
        if elapsed > 0 and delta > 0:
            status["downloaded_bytes_per_second"] = delta / elapsed
            status["eta_seconds"] = status["remaining_network_bytes"] / (delta / elapsed)
    atomic_json(prior_path, status)
    if not quiet:
        folder = mission.pds_processed / "reports" / run_id()
        atomic_json(folder / "coverage.json", status)
        atomic_json(folder / "unresolved.json", unresolved)
        print(f"Coverage {verified:,}/{expected:,} files; {verified_bytes:,} bytes verified; {len(issues):,} issues", flush=True)
    c.close()
    return status


def run_stage(stage):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=f"PDS {stage}")
    parser.add_argument("--mission", required=True, choices=["artemis-ii"])
    parser.add_argument("--reserve-gb", type=float, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--bandwidth-mbps", type=float, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-priority", type=int)
    parser.add_argument("--full-hash", action="store_true")
    parser.add_argument("--use-saved-plan", action="store_true", help="Resume the completed reconciliation without rescanning holdings")
    args = parser.parse_args()
    if args.workers < 1 or args.reserve_gb < 500:
        parser.error("workers must be positive; reserve must be at least 500 GB")
    mission = MISSIONS[args.mission]
    reserve = int(args.reserve_gb * 1_000_000_000)
    with locked(mission):
        try:
            if stage == "discover":
                discover(mission)
            elif stage == "inventory":
                inventory(mission)
            elif stage == "reconcile":
                reconcile(mission, reserve)
            elif stage == "download":
                download(mission, reserve, args.workers, args.limit, args.max_priority, args.bandwidth_mbps * 1_000_000 / 8)
            elif stage == "verify":
                verify(mission, args.full_hash)
            elif stage == "integrate":
                integrate(mission)
            elif stage == "report":
                report(mission)
            elif stage == "retrieve":
                # A failed transfer pass still produces catalogs and reports; next run resumes.
                check = db(mission)
                discovered = get_meta(check, "discovery") and not get_meta(check, "discovery_in_progress")
                check.close()
                if not discovered:
                    discover(mission)
                if not args.use_saved_plan:
                    inventory(mission)
                    reconcile(mission, reserve)
                report(mission)
                try:
                    download(mission, reserve, args.workers, args.limit, args.max_priority, args.bandwidth_mbps * 1_000_000 / 8)
                finally:
                    verify(mission, require_complete=False)
                    integrate(mission)
                verify(mission)
                # Save a fresh release snapshot and retrieve any additions before final coverage.
                discover(mission)
                reconcile(mission, reserve)
                download(mission, reserve, args.workers, bandwidth=args.bandwidth_mbps * 1_000_000 / 8)
                verify(mission)
                integrate(mission)
                # Rebuild affected photos using existing precedence and idempotent tier generation.
                import importlib
                before = mission.pds_processed / "checkpoints" / (run_id() + "-web")
                for name in ("photos.json", "videoIA.json", "videoYt.json"):
                    path = mission.web_dir / name
                    if path.exists():
                        before.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, before / name)
                for module_name in ("3_photos.4a_extract_all_exif", "3_photos.4b_detect_brackets",
                        "3_photos.4c_build_ledger", "3_photos.4d_generate_tiers", "3_photos.4e_web_photos_json"):
                    previous = sys.argv
                    try:
                        sys.argv = [module_name, "--mission", args.mission]
                        importlib.import_module(module_name).main()
                    finally:
                        sys.argv = previous
        finally:
            report(mission)
