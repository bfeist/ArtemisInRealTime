"""PDS identities, safe paths, persistent inventory, and verified HTTP transfers."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

import certifi
import requests
from lxml import etree
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from shared.eol_naming import to_canonical_nasa_id

ATLAS = "https://pds-imaging.jpl.nasa.gov/api/search/atlas/_search"
RESOLVER = "https://pds-imaging.jpl.nasa.gov/api/data/"
CIS_BUNDLES = ("artemis2_crew_camera", "artemis2_orion_camera", "artemis2_mission_audio")
GEO = "https://pds-geosciences.wustl.edu/artemis2/urn-nasa-pds-artemis2_mission/"
SPICE = "https://naif.jpl.nasa.gov/pub/naif/pds/pds4/artemis2/artemis2_spice/"
RELEASE = "https://pds-geosciences.wustl.edu/missions/artemis2/index.htm"


def utc():
    return datetime.now(timezone.utc).isoformat()


def run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def nasa_id(name):
    m = re.match(r"(art\d{3}[a-z]\d{6,})(?:_|\.|$)", Path(name).name, re.I)
    if m:
        return m[1].lower()
    return to_canonical_nasa_id(Path(name).name) if re.match(r"art\d{3}-", Path(name).name, re.I) else None


def safe_relative(value):
    if not value or "\\" in value or value.startswith("/"):
        raise ValueError(f"Unsafe archive path: {value!r}")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", "..") or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")):
            raise ValueError(f"Unsafe archive path: {value!r}")
        if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I):
            raise ValueError(f"Reserved archive path: {value!r}")
    return PurePosixPath(value).as_posix()


def destination(root, relative):
    root = Path(root).resolve()
    path = (root / safe_relative(relative)).resolve()
    if not path.is_relative_to(root) or len(str(path)) > 245:
        raise ValueError(f"Unrepresentable destination: {path}")
    return path


def hashes(path):
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def session(mission):
    """Use verified TLS with the OS trust store, including local enterprise CAs."""
    ca = mission.pds_processed / "windows-ca.pem"
    if os.name == "nt" and not ca.exists():
        ca.parent.mkdir(parents=True, exist_ok=True)
        data = Path(certifi.where()).read_text(encoding="ascii")
        for store in ("ROOT", "CA"):
            for cert, encoding, trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
                    data += "\n" + ssl.DER_cert_to_PEM_cert(cert)
        ca.write_text(data, encoding="ascii")
    s = requests.Session()
    s.verify = str(ca) if ca.exists() else True
    s.headers.update({"User-Agent": "ArtemisInRealTime-PDS/1.0", "Accept-Encoding": "identity"})
    retry = Retry(total=4, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD", "POST"], respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=4))
    return s


def db(mission):
    mission.pds_processed.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(mission.pds_processed / "inventory.sqlite", timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started TEXT, finished TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS remote(
          id INTEGER PRIMARY KEY, bundle TEXT, collection TEXT, relative TEXT,
          uri TEXT UNIQUE, url TEXT, size INTEGER, md5 TEXT, discovery TEXT,
          active INTEGER DEFAULT 1, status TEXT DEFAULT 'pending', action TEXT DEFAULT 'network',
          source TEXT, member TEXT, sha256 TEXT, stat_size INTEGER, stat_mtime INTEGER,
          error TEXT, network_bytes INTEGER DEFAULT 0, method TEXT,
          UNIQUE(bundle,relative COLLATE NOCASE));
        CREATE TABLE IF NOT EXISTS local(
          path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, device INTEGER, inode INTEGER,
          nasa_id TEXT, extension TEXT, md5 TEXT, sha256 TEXT);
        CREATE INDEX IF NOT EXISTS local_size ON local(size);
        CREATE TABLE IF NOT EXISTS members(
          path TEXT, member TEXT, size INTEGER, crc INTEGER, nasa_id TEXT, extension TEXT,
          md5 TEXT, sha256 TEXT, PRIMARY KEY(path,member));
        CREATE INDEX IF NOT EXISTS members_size ON members(size);
        CREATE TABLE IF NOT EXISTS issues(kind TEXT, identity TEXT, detail TEXT,
          UNIQUE(kind,identity,detail));
        CREATE TABLE IF NOT EXISTS products(
          path TEXT PRIMARY KEY, lidvid TEXT, metadata TEXT);
        CREATE TABLE IF NOT EXISTS attempts(
          remote_id INTEGER, started TEXT, finished TEXT, method TEXT, error TEXT);
    ''')
    return c


def set_meta(c, key, value):
    c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))
    c.commit()


def get_meta(c, key, default=None):
    row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def issue(c, kind, identity, detail):
    c.execute("INSERT OR IGNORE INTO issues VALUES(?,?,?)", (kind, identity, str(detail)))


@contextlib.contextmanager
def locked(mission):
    """OS advisory lock releases on exit/crash; the file itself is durable."""
    mission.pds_processed.mkdir(parents=True, exist_ok=True)
    path = mission.pds_processed / "run.lock"
    with open(path, "a+b") as f:
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise RuntimeError("Another PDS run is holding the run lock") from e
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def priority(r):
    ext = Path(r["relative"]).suffix.lower()
    if ext in {".xml", ".csv", ".srt", ".txt", ".md5", ".pdf", ".json", ".html"}:
        return 0
    if "browse" in r["collection"] or r["bundle"] == "artemis2_mission_audio":
        return 1
    if r["collection"] in {"data_source", "data_video", "browse_source"}:
        return 2
    return 4 if r["collection"] == "data_processed" else 3


def parse_label(path):
    root = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    def values(tag):
        return [str(v).strip() for v in root.xpath(f"//*[local-name()='{tag}']/text()")]
    result = {key: values(key) for key in ("logical_identifier", "version_id", "start_date_time",
        "stop_date_time", "processing_level", "instrument_name", "file_name", "file_size",
        "md5_checksum", "name", "description", "unit", "local_identifier", "instrument_id",
        "channel", "channel_id", "encoding_type", "codec", "video_codec", "duration", "sampling_rate")}
    if result["logical_identifier"]:
        result["lidvid"] = result["logical_identifier"][0] + "::" + (result["version_id"] or [""])[0]
    result["file_records"] = []
    for element in root.xpath("//*[local-name()='File']"):
        fields = {etree.QName(child).localname: child.text for child in element}
        if fields.get("file_name"):
            result["file_records"].append(fields)
    result["members"] = [str(v).strip() for v in root.xpath("//*[local-name()='lidvid_reference']/text()")]
    return result


def valid_evidence(r, path):
    if not path.is_file() or r["status"] not in ("verified", "verified_size_only") or not r["sha256"]:
        return False
    st = path.stat()
    return st.st_size == r["stat_size"] and st.st_mtime_ns == r["stat_mtime"]


def verify_file(r, path):
    if r["size"] is not None and path.stat().st_size != r["size"]:
        raise ValueError(f"Size mismatch: {path.stat().st_size} != {r['size']}")
    md5, sha = hashes(path)
    if r["md5"] and md5.lower() != r["md5"].lower():
        raise ValueError(f"MD5 mismatch: {md5} != {r['md5']}")
    # Reject transport error pages even for objects without a published hash.
    if Path(r["relative"]).suffix.lower() not in {".html", ".htm"}:
        with open(path, "rb") as f:
            prefix = f.read(512).lstrip().lower()
        if prefix.startswith((b"<!doctype html", b"<html")):
            raise ValueError("Unexpected HTML response")
    return sha


class HostLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.hosts = {}

    def for_url(self, url):
        with self.lock:
            return self.hosts.setdefault(urlparse(url).netloc, threading.BoundedSemaphore(2))


def resolve_url(s, r, limits=None):
    if r["url"]:
        return r["url"]
    url = RESOLVER + quote(r["uri"], safe=":/")
    with limits.for_url(url) if limits else contextlib.nullcontext():
        response = s.get(url, params={"output": "url"}, timeout=(20, 60))
    response.raise_for_status()
    value = response.text.strip().strip('"')
    try:
        parsed = response.json()
        value = parsed if isinstance(parsed, str) else parsed.get("url", value)
    except ValueError:
        pass
    if not value.startswith("https://"):
        raise ValueError(f"Resolver did not return an HTTPS URL: {value[:120]}")
    return value


def stream_download(s, r, part, checkpoint, limits, reserve, bandwidth=0):
    """Resume only with a validator and confirmed Content-Range, never append 200."""
    part.parent.mkdir(parents=True, exist_ok=True)
    network = 0
    for attempt in range(5):
        try:
            url = resolve_url(s, r, limits)
            old = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
            identity = [r["uri"], r["size"], r["md5"]]
            offset = part.stat().st_size if part.exists() and old.get("identity") == identity and old.get("validator") else 0
            headers = {"Range": f"bytes={offset}-", "If-Range": old["validator"]} if offset else {}
            with limits.for_url(url), s.get(url, headers=headers, stream=True, timeout=(30, 120)) as response:
                if response.status_code == 416:
                    if r["size"] is not None and offset == r["size"]:
                        return network
                    part.unlink(missing_ok=True)
                    checkpoint.unlink(missing_ok=True)
                    continue
                response.raise_for_status()
                if response.headers.get("Content-Encoding", "identity") not in ("identity", ""):
                    raise ValueError("Unexpected transport content encoding")
                if response.status_code == 206:
                    cr = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
                    validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
                    if not cr or int(cr[1]) != offset or (old.get("validator") and validator != old["validator"]):
                        part.unlink(missing_ok=True)
                        checkpoint.unlink(missing_ok=True)
                        raise ValueError("Range or validator changed; discarded partial")
                    if r["size"] is not None and cr[3] != str(r["size"]):
                        raise ValueError("Content-Range total changed")
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise ValueError(f"Unexpected transfer status {response.status_code}")
                if "text/html" in response.headers.get("Content-Type", "") and Path(r["relative"]).suffix.lower() not in {".htm", ".html"}:
                    raise ValueError("HTML transport error instead of product")
                validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
                if validator and validator.startswith("W/"):
                    validator = response.headers.get("Last-Modified")
                atomic_json(checkpoint, {"identity": identity, "validator": validator, "updated": utc()})
                started, received, checked = time.monotonic(), 0, 0
                with open(part, "ab" if offset else "wb") as f:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        if checked <= 0:
                            if shutil.disk_usage(part.parent).free < reserve + len(chunk):
                                raise OSError("D: reserve reached; transfer stopped")
                            checked = 64 * 1024 * 1024
                        f.write(chunk)
                        checked -= len(chunk)
                        received += len(chunk)
                        network += len(chunk)
                        if bandwidth:
                            time.sleep(max(0, received / bandwidth - (time.monotonic() - started)))
                return network
        except (requests.RequestException, ValueError) as e:
            if attempt == 4:
                raise
            print(f"Retry {attempt + 1}: {r['relative']}: {e}", flush=True)
            time.sleep(min(30, 2 ** attempt + random.random()))
    raise RuntimeError("Transfer retry limit reached")
