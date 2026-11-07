"""Searchable original sources for history investigations (no generated text as proof)."""
from __future__ import annotations

import bisect
import json
import re
import sqlite3
from pathlib import Path

if __package__:
    from .history_common import digest, transcript, file_digest, atomic_json
else:
    from history_common import digest, transcript, file_digest, atomic_json


def reference_pdfs():
    """Known source documents; plan vs retrospective roles are explicit, not model guesses."""
    from config import REPO_ROOT
    return [(REPO_ROOT / "pdfs" / "artemis-ii-overview-timeline-public-final.pdf", True),
            (REPO_ROOT / "pdfs" / "Artemis II_Preliminary Science Report.pdf", False)]


class SourceIndex:
    def __init__(self, utterances: list[dict], context, mission=None, candidates=(), extra_paths=(), pdf_paths=()):
        self.docs = {}
        self.warnings = []
        for u in utterances:
            self.add({"id": "u:" + u["uid"], "uid": u["uid"], "t": u["t"],
                      "ts": u["ts"], "channel": u["channel"], "source": transcript.transcript_source(u),
                      "src": u.get("src", ""), "kind": "transcript",
                      "text": u["text"], "origin": "comm.csv" if u["channel"] == "comm" else "PAO.csv",
                      "planned_only": False})
        for event in context.events:
            self.add({"id": "ref:" + event["id"], "t": event["t"], "ts": event["start_ts"],
                      "kind": "reference", "text": event["label"], "planned_only": event.get("planned_only", False),
                      "as_executed": event.get("as_executed", False), "reference_kind": event["kind"],
                      "origin": "mission_context.py reference catalog; consult original record for precision"})
        for block in context.planned_blocks:
            self.add({"id": "plan:" + block["id"], "t": block["start_utc"], "ts": block["start_ts"],
                      "kind": "reference", "planned_only": True, "text": block["title"] + ": " + block["description"],
                      "origin": "preflight overview timeline (planned)"})
        # Unlike 1d's context snippets, retain full available captions for citation verification.
        photo_path = mission.web_dir / "photos.json" if mission else None
        if photo_path and photo_path.exists():
            for photo in json.loads(photo_path.read_text(encoding="utf-8")):
                if not photo.get("date"):
                    continue
                self.add({"id": "photo:" + str(photo["id"]), "t": photo["date"],
                          "ts": transcript.parse_utc_ts(photo["date"]), "kind": "caption",
                          "planned_only": False, "text": f"{photo.get('title', '')}\n{photo.get('description', '')}",
                          "origin": "photos.json:" + str(photo["id"])})
        for path in extra_paths:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                doc = json.loads(line)
                if (not all(isinstance(doc.get(k), str) and doc[k] for k in ("id", "t", "text", "origin"))
                        or doc.get("kind") not in {"transcript", "reference", "caption"}
                        or type(doc.get("planned_only")) is not bool):
                    raise ValueError(f"Invalid reference JSONL record in {path}")
                self.add({**doc, "id": "extra:" + doc["id"], "ts": transcript.parse_utc_ts(doc["t"])})
        if pdf_paths:
            import pypdf
            import fontTools
            for path, planned in pdf_paths:
                if not path.exists():
                    self.warnings.append(f"Missing reference PDF: {path}")
                    continue
                cache = (mission.transcript_investigation / "source_cache" /
                         f"{digest([str(path), file_digest(path), pypdf.__version__, fontTools.__version__])}.json") if mission else None
                if cache and cache.exists():
                    pages = json.loads(cache.read_text(encoding="utf-8"))
                else:
                    pages = [page.extract_text() or "" for page in pypdf.PdfReader(path).pages]
                    if cache:
                        atomic_json(cache, pages)
                for number, text in enumerate(pages, 1):
                    if not text.strip():
                        self.warnings.append(f"No searchable text: {path.name}, page {number}")
                        continue
                    self.add({"id": f"pdf:{path.name}:p{number}", "kind": "document", "text": text,
                              "t": None, "ts": None, "planned_only": planned,
                              "document": str(path), "page": number, "origin": f"{path}#page={number}",
                              "time_basis": "undated document page; NOT an event timestamp"})
        for candidate in candidates:
            self.add({"id": candidate["id"], "t": candidate["t"], "ts": transcript.parse_utc_ts(candidate["t"]),
                      "kind": "candidate", "planned_only": False, "text": json.dumps(candidate, ensure_ascii=False),
                      "origin": "unverified 1e output; NOT evidence"})
        self.identity = digest(sorted(self.docs.values(), key=lambda d: d["id"]))
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE VIRTUAL TABLE sources USING fts5(id UNINDEXED, text)")
        self.db.executemany("INSERT INTO sources VALUES (?, ?)", [(d["id"], d["text"]) for d in self.docs.values()])
        self.timeline = sorted((d["ts"], d["id"]) for d in self.docs.values()
                               if d["kind"] != "candidate" and d["ts"] is not None)
        self.times = [ts for ts, _ in self.timeline]
        self.channel_rows = {}
        self.locations = {}
        for u in utterances:
            source = transcript.transcript_source(u)
            group = self.channel_rows.setdefault(source, [])
            self.locations["u:" + u["uid"]] = (source, len(group))
            group.append("u:" + u["uid"])

    def add(self, doc):
        if doc["id"] in self.docs:
            raise ValueError(f"Duplicate source ID: {doc['id']}")
        self.docs[doc["id"]] = doc

    def search(self, query: str, limit=10, start=None, end=None, kind=None) -> list[dict]:
        if kind not in {None, "transcript", "reference", "caption", "document", "candidate"}:
            raise ValueError("Unknown source kind")
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)[:16]
        if not tokens:
            return []
        match = " OR ".join('"' + t + '"' for t in tokens)
        found = self.db.execute("SELECT id FROM sources WHERE sources MATCH ? ORDER BY bm25(sources)", (match,))
        lower = transcript.parse_utc_ts(start) if start else float("-inf")
        upper = transcript.parse_utc_ts(end) if end else float("inf")
        docs = []
        for (sid,) in found:
            doc = self.docs[sid]
            if (kind and doc["kind"] != kind) or (not kind and doc["kind"] == "candidate"):
                continue
            if doc["ts"] is None or lower <= doc["ts"] <= upper:
                docs.append(doc)
                if len(docs) >= min(20, max(1, limit)):
                    break
        return docs

    def read(self, source_id: str, before=5, after=5) -> list[dict]:
        if source_id not in self.docs:
            raise ValueError("Unknown source_id")
        doc = self.docs[source_id]
        if doc["kind"] == "document":
            return sorted((d for d in self.docs.values() if d.get("document") == doc["document"]
                           and doc["page"] - min(3, max(0, before)) <= d["page"] <= doc["page"] + min(3, max(0, after))),
                          key=lambda d: d["page"])
        if source_id not in self.locations:
            return [self.docs[source_id]]
        channel, index = self.locations[source_id]
        ids = self.channel_rows[channel]
        return [self.docs[s] for s in ids[max(0, index - min(15, max(0, before))):index + min(15, max(0, after)) + 1]]

    def window(self, start: str, end: str, offset=0, limit=30) -> dict:
        lo, hi = transcript.parse_utc_ts(start), transcript.parse_utc_ts(end)
        if hi < lo or offset < 0:
            raise ValueError("Invalid window or offset")
        left, right = bisect.bisect_left(self.times, lo), bisect.bisect_right(self.times, hi)
        ids = self.timeline[left:right]
        limit = min(40, max(1, limit))
        return {"sources": [self.docs[sid] for _, sid in ids[offset:offset + limit]],
                "total": len(ids), "next_offset": offset + limit if offset + limit < len(ids) else None}

    def tool(self, action: dict) -> dict:
        name = action.get("action")
        if name == "search":
            return {"sources": self.search(str(action.get("query", "")), int(action.get("limit", 10)),
                                           action.get("start"), action.get("end"), action.get("kind"))}
        if name == "read":
            return {"sources": self.read(action["source_id"], int(action.get("before", 5)), int(action.get("after", 5)))}
        if name == "window":
            return self.window(action["start"], action["end"], int(action.get("offset", 0)), int(action.get("limit", 30)))
        raise ValueError("Choose search, read, window, or finish")

    def close(self):
        self.db.close()
