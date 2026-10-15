"""Readable local asset status, derived from existing catalogs; no new database.

Report files live in processed/asset-status, outside the publishable web folder.
Public evidence, local holdings, web preparation, and exclusions are separate.
"""
import argparse
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config import MISSIONS


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def read_lines(path):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def photo_rows(mission):
    indexed = {p["id"] for p in read_json(mission.web_dir / "photos.json", [])}
    brackets = read_json(mission.web_photos_dir / "brackets.json", {})
    alternates = set()
    for group in (brackets.get("sets", brackets) if isinstance(brackets, dict) else {}).values():
        if isinstance(group, dict):
            alternates.update(p if isinstance(p, str) else p.get("id", "") for p in group.get("members", []))
    for r in read_lines(mission.photos_ledger_path):
        nid = r["nasa_id"]
        paths = [c["path"] for c in r.get("copies", {}).values() if c and c.get("path")]
        paths.extend(p["path"] for p in r.get("pds_products", []))
        paths = sorted({p for p in paths if Path(p).is_file()})
        public = bool(r.get("exported"))
        tiers = all((mission.web_photos_dir / tier / (nid + ".jpg")).is_file()
                    for tier in ("thumb", "lowres", "hires"))
        in_window = mission.mission_start <= (r.get("utc") or "")[:10] <= mission.mission_end
        if not public:
            state = "Hold — public release unconfirmed"
        elif not paths:
            state = "Public — original missing locally"
        elif not r.get("utc"):
            state = "Public — timestamp missing"
        elif not in_window:
            state = "Public — outside mission dates"
        elif tiers and nid in indexed:
            state = "Ready on F"
        elif tiers and nid in alternates:
            state = "Ready on F — bracket alternate"
        elif tiers:
            state = "Public — tiers exist, absent from index"
        else:
            state = "Public — needs web tiers"
        note = "PDS/other timestamp conflict" if r.get("pds_time_conflicts") else ""
        if any(p.get("bundle") == "artemis2_orion_camera" for p in r.get("pds_products", [])):
            note = "PDS camera/version identity retained separately; compare numeric-ID aliases before merging"
        yield {"kind": "Photo", "id": nid, "title": r.get("title", ""),
               "release": "Public" if public else "Unconfirmed", "evidence": ", ".join(r.get("exported_in", [])),
               "state": state, "utc": r.get("utc", ""), "time_source": r.get("utc_source", ""),
               "paths": paths, "note": note}


def video_rows(mission):
    processed = mission.data_dir / "processed"
    prepared = read_json(processed / "web-media.json", {})
    for name in ("ia_video_downloads.json", "ia_video_originals.json"):
        for r in read_json(processed / name, {}).get("records", []):
            path = r.get("local_path")
            state = r.get("status", "unknown")
            if state == "complete" and (not path or not Path(path).is_file()):
                state = "Manifest complete — file missing"
            elif state == "complete":
                state = "Original on disk — web preparation needed" if name == "ia_video_originals.json" else "MP4 on disk — web index/preparation needed"
                media = prepared.get("ia:" + r["identifier"], {})
                if media.get("status") == "ready" and Path(media["path"]).is_file():
                    state = "Ready on F"
            yield {"kind": "IA original" if name == "ia_video_originals.json" else "IA video", "id": r["identifier"],
                   "title": r.get("title", ""), "release": "Public", "evidence": r.get("source_url", "Internet Archive"),
                   "state": "Unneeded — covered elsewhere" if state == "unneeded" else state,
                   "utc": r.get("recorded_at", ""), "time_source": "IO", "paths": [path] if path and Path(path).is_file() else [],
                   "note": r.get("unneeded_reason") or r.get("error") or ""}
    public_video_ids = {r.get("nasa_id", "").lower() for r in read_json(processed / "ia_video_downloads.json", {}).get("records", [])}
    seen = set()
    for r in read_lines(mission.io_cache / "io_nasatv_catalog.jsonl"):
        nid = r.get("nasa_id", "").lower()
        if not nid or nid in public_video_ids or nid in seen:
            continue
        seen.add(nid)
        yield {"kind": "IO video", "id": nid, "title": r.get("md_title", ""), "release": "Unconfirmed",
               "evidence": "IO catalog only", "state": "Hold — public release unconfirmed", "utc": r.get("vmd_start_gmt") or "",
               "time_source": "IO", "paths": [], "note": "Catalog metadata retained; excluded from web publication"}
    yt_files = list(mission.yt_video_dir.rglob("*")) if mission.yt_video_dir.exists() else []
    for r in read_json(processed / "yt_metadata.json", []):
        nid = r["video_id"]
        paths = [str(p) for p in yt_files if nid in p.name and p.suffix.lower() in {".mp4", ".mkv", ".webm"}]
        yield {"kind": "YouTube", "id": nid, "title": r.get("title", ""), "release": "Public",
               "evidence": "https://www.youtube.com/watch?v=" + nid,
               "state": "Source available — local recording held" if paths else "Source available — no local recording",
               "utc": r.get("actual_start_time") or r.get("published_at", ""), "time_source": "YouTube", "paths": paths, "note": ""}
    for family in ("video", "audio", "transcripts"):
        for r in read_lines(processed / "pds" / (family + ".jsonl")):
            path = Path(r["path"])
            if path.suffix.lower() == ".xml":
                continue
            media = prepared.get("pds:" + r["uri"], {})
            yield {"kind": "PDS " + family, "id": r.get("lidvid") or r["relative"], "title": path.name,
                   "release": "Public", "evidence": r.get("uri", "PDS"),
                   "state": "Ready on F" if media.get("status") == "ready" and Path(media["path"]).is_file() else "Verified original — web preparation needed" if path.is_file() else "Catalog original missing",
                   "utc": r.get("start_utc") or "", "time_source": "PDS label", "paths": [str(path)] if path.is_file() else [], "note": r.get("channel") or ""}
    for r in read_json(processed / "comm_source.json", {}).get("records", []):
        yield {"kind": "Comm archive", "id": r["filename"], "title": "Internet Archive mission comm ZIP",
               "release": "Public", "evidence": r["public_url"], "state": "Original archive held" if r["status"] == "held" else "Public — original missing locally",
               "utc": "", "time_source": "", "paths": [r["path"]] if r["status"] == "held" else [], "note": "Existing comm.csv and web/comm are built separately"}


def pds_summary(mission):
    path = mission.pds_processed / "inventory.sqlite"
    if not path.exists():
        return {"groups": [], "pending": [], "unresolved": []}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        groups = [dict(r) for r in c.execute("""SELECT bundle,collection,count(*) files,
            sum(size) bytes,sum(status LIKE 'verified%') verified
            FROM remote WHERE active=1 GROUP BY bundle,collection ORDER BY bundle,collection""")]
        pending = c.execute("SELECT value FROM meta WHERE key='pending_releases'").fetchone()
        unresolved = [dict(r) for r in c.execute("SELECT bundle,relative,status,error FROM remote WHERE active=1 AND status NOT LIKE 'verified%'")]
    return {"groups": groups, "pending": json.loads(pending[0]) if pending else [], "unresolved": unresolved}


def other_holdings(mission):
    # Historical mixed hand-off and tracking material must remain visible even
    # when they have no adapter or public-release evidence yet.
    for root in (mission.bulk_dir / "raw/science", mission.bulk_dir / "raw/audio",
                 mission.bulk_dir / "raw/photos/nef_all"):
        for p in sorted(root.rglob("*")) if root.exists() else []:
            if not p.is_file() or (root.name == "nef_all" and p.suffix.lower() in {".nef", ".dng", ".jpg", ".jpeg", ".xmp"}):
                continue
            yield {"kind": "Local holding", "id": p.name, "title": "Historical hand-off / science tracking",
                   "release": "Unconfirmed", "evidence": "Local file only; needs provenance review",
                   "state": "Hold — public release unconfirmed", "utc": "", "time_source": "", "paths": [str(p)], "note": "Original preserved on D; not included in web publication"}


def build_report(mission):
    rows = list(photo_rows(mission)) + list(video_rows(mission)) + list(other_holdings(mission))
    sources = [mission.io_cache / "io_photo_catalog.jsonl", mission.eol_json_path,
               mission.raw_photos_flickr / "album_metadata.json", mission.raw_photos_nasa / "catalog.json",
               mission.data_dir / "processed" / "yt_metadata.json", mission.data_dir / "processed" / "ia_video_downloads.json"]
    freshness = [{"path": str(p), "updated": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() if p.exists() else "Missing"} for p in sources]
    runs = []
    refresh_root = mission.bulk_dir / "processed" / "refresh"
    if refresh_root.exists():
        runs = [read_json(p) for p in sorted(refresh_root.glob("*/run.json"))[-12:]]
    data = {"mission": mission.name, "generated": datetime.now(timezone.utc).isoformat(), "rows": rows,
            "states": dict(Counter(r["state"] for r in rows)), "pds": pds_summary(mission), "freshness": freshness, "runs": runs,
            "storage": [{"root": str(p), "free_gib": round(shutil.disk_usage(p).free / 2**30, 1)}
                        for p in dict.fromkeys([mission.data_dir, mission.bulk_dir, mission.yt_video_dir]) if p.exists()]}
    output = mission.data_dir / "processed" / "asset-status"
    output.mkdir(parents=True, exist_ok=True)
    (output / "status.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    encoded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    template = HTML.replace(r"\\n", r"\n")
    (output / "index.html").write_text(template.replace("__DATA__", encoded), encoding="utf-8")
    print(f"Report: {output / 'index.html'}\n{json.dumps(data['states'], indent=2)}")
    return data


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Artemis asset status</title><style>
body{font:15px system-ui;background:#f3f5f7;color:#182736;margin:0;padding:32px}main{max-width:1500px;margin:auto}h1{font-size:30px;margin-bottom:8px}h2{margin-top:32px}p{line-height:1.6}small{color:#526578}.cards{display:flex;flex-wrap:wrap;gap:12px}.card{background:white;padding:16px;border:1px solid #d4dce4;border-radius:8px;min-width:150px}.card b{display:block;font-size:24px}input,select,button{font:inherit;padding:10px;border:1px solid #b9c6d2;border-radius:5px;background:white;margin:4px}input{width:32%}table{border-collapse:collapse;width:100%;background:white;font-size:13px}td,th{text-align:left;padding:10px;border-bottom:1px solid #e0e6ec;vertical-align:top}th{background:#e6edf3;position:sticky;top:0}td{overflow-wrap:anywhere}td:first-child{width:22%}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px monospace}.hold{color:#8a5410}.ready{color:#086844}details{margin:12px 0}.table-wrap{overflow-x:auto}
</style><main><h1 id="title"></h1><small id="stamp"></small>
<p>Public means release evidence exists in a public source or an explicit manual release assertion. <strong>Unconfirmed</strong> means we have no release evidence; it does not prove the asset is private. IO metadata alone is not release evidence. <strong>Ready on F</strong> means photo tiers and a local index exist, not that a remote website was updated.</p>
<div class="cards" id="cards"></div><h2>Find an asset</h2>
<input id="search" placeholder="NASA ID, filename, title, source, or path" aria-label="Search"><select id="holding" aria-label="Local availability"><option value="held">Local files and exclusions</option><option value="">All catalog records</option></select><select id="kind" aria-label="Kind"><option value="">All media</option></select><select id="release" aria-label="Release"><option value="">All release statuses</option><option>Public</option><option>Unconfirmed</option></select><select id="state" aria-label="Preparation"><option value="">All preparation statuses</option></select>
<p><span id="count"></span> <button id="prev">Previous</button><button id="next">Next</button></p>
<div class="table-wrap"><table><thead><tr><th>Asset</th><th>Public status / evidence</th><th>Preparation</th><th>Time</th><th>Local paths / notes</th></tr></thead><tbody id="rows"></tbody></table></div>
<h2>PDS holdings and pending releases</h2><p>Counts below are archive files, including labels and multiple representations of the same observation. They are not unique photo counts. Verification uses saved inventory evidence.</p><div id="pds"></div>
<h2>Source refreshes and storage</h2><div id="sources"></div><div id="runs"></div><p>This report is local processing data. Publish assets from the mission's <strong>web/</strong> folder only.</p></main>
<script>const data=__DATA__;const el=id=>document.getElementById(id);const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
el('title').textContent=data.mission+' — asset status';el('stamp').textContent='Generated '+new Date(data.generated).toLocaleString();
const photos=data.rows.filter(r=>r.kind==='Photo');const nums=[['Photo catalog records',photos.length],['Public photos',photos.filter(r=>r.release==='Public').length],['Unconfirmed photos held',photos.filter(r=>r.release==='Unconfirmed'&&r.paths.length).length],['Photos ready on F',photos.filter(r=>r.state==='Ready on F').length],['Unneeded recordings',new Set(data.rows.filter(r=>r.state.startsWith('Unneeded')).map(r=>r.id)).size]];
el('cards').innerHTML=nums.map(([t,n])=>`<div class="card"><b>${n.toLocaleString()}</b>${esc(t)}</div>`).join('');
for(const [id,key] of [['kind','kind'],['state','state']])for(const v of [...new Set(data.rows.map(r=>r[key]))].sort()){const opt=document.createElement('option');opt.value=v;opt.textContent=v;el(id).append(opt)}
let page=0,filtered=[];function draw(reset=false){if(reset)page=0;const q=el('search').value.toLowerCase();filtered=data.rows.filter(r=>(!el('holding').value||r.paths.length||r.state.startsWith('Unneeded'))&&(!el('kind').value||r.kind===el('kind').value)&&(!el('release').value||r.release===el('release').value)&&(!el('state').value||r.state===el('state').value)&&(!q||JSON.stringify(r).toLowerCase().includes(q)));page=Math.min(page,Math.max(0,Math.ceil(filtered.length/100)-1));el('count').textContent=filtered.length.toLocaleString()+' matches · page '+(page+1)+' of '+Math.max(1,Math.ceil(filtered.length/100));el('prev').disabled=page===0;el('next').disabled=(page+1)*100>=filtered.length;el('rows').innerHTML=filtered.slice(page*100,page*100+100).map(r=>`<tr><td><b>${esc(r.id)}</b><br>${esc(r.kind)}<br>${esc(r.title)}</td><td class="${r.release==='Public'?'ready':'hold'}">${esc(r.release)}<br><small>${esc(r.evidence)}</small></td><td>${esc(r.state)}</td><td>${esc(r.utc)}<br><small>${esc(r.time_source)}</small></td><td>${r.paths.length?'<details><summary>'+r.paths.length+' local file(s)</summary><pre>'+esc(r.paths.join('\n'))+'</pre></details>':'No local original recorded'}${esc(r.note)}</td></tr>`).join('')}
for(const id of ['search','holding','kind','release','state'])el(id).addEventListener('input',()=>draw(true));el('prev').onclick=()=>{page--;draw()};el('next').onclick=()=>{page++;draw()};draw();
el('pds').innerHTML='<table><tr><th>Bundle / collection</th><th>Verified / expected</th><th>GiB</th></tr>'+data.pds.groups.map(g=>`<tr><td>${esc(g.bundle)} / ${esc(g.collection)}</td><td>${g.verified.toLocaleString()} / ${g.files.toLocaleString()}</td><td>${(g.bytes/2**30).toFixed(1)}</td></tr>`).join('')+'</table><p>'+data.pds.pending.map(p=>'<b>'+esc(p.source)+'</b>: '+esc(p.status)+' ('+esc(p.evidence)+')').join('<br>')+'</p><details><summary>Unresolved PDS files ('+data.pds.unresolved.length+')</summary><pre>'+esc(JSON.stringify(data.pds.unresolved,null,2))+'</pre></details>';
el('sources').innerHTML=data.storage.map(s=>'<p>'+esc(s.root)+' — '+s.free_gib+' GiB free</p>').join('')+'<details><summary>Catalog update times</summary><pre>'+esc(data.freshness.map(f=>f.updated+'  '+f.path).join('\n'))+'</pre></details>';
el('runs').innerHTML='<details><summary>Run history</summary>'+data.runs.reverse().map(r=>'<details><summary>'+esc(r.stage)+' · '+esc(r.status||'running')+' · '+new Date(r.started).toLocaleString()+'</summary><pre>'+esc(r.jobs.map(j=>j.name+': '+j.status+'\n'+j.log).join('\n\n'))+'</pre></details>').join('')+'</details>';
</script></html>'''

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, choices=list(MISSIONS))
    args = parser.parse_args()
    build_report(MISSIONS[args.mission])
