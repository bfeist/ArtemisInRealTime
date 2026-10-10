# PDS retrieval

Artemis II's complete available PDS archive is discovered without image, target,
flight-day or extension filters. Original downloads, partials, manifests, SQLite
state and reports default to `D:/ArtemisInRealTime_assets/artemis-ii`. Set
`PDS_ASSETS_DIR` to configure this separately from the existing `DATA_DIR`.

Run from `src/server-batch` using the existing Python environment:

```powershell
.venv/Scripts/python.exe -u -m 7_pds.retrieve --mission artemis-ii
```

This inventories existing files and ZIP members, hashes candidates, saves a
capacity-checked transfer plan, reuses verified bytes, downloads the full
available snapshot, checks labels/inventories, creates integration catalogs,
and refreshes discovery for additions. It resumes an existing complete discovery
on restart. A final discovery is saved separately; earlier manifests are retained.
Failures leave staged data and produce nonzero exit status and coverage reports.
Restart the same command to resume. A filesystem lock prevents overlapping runs.
This is a normal local process, not a recurring automation.

To leave the run working in the background with all logs on D: use
`./7_pds/start_retrieval.ps1`. After an already completed `pds-plan`, pass
`-UseSavedPlan` to skip rescanning and rehashing before transfer. Process details
are saved in `processed/pds/logs/current-process.json`; stdout/stderr are saved
beside it. Keep the machine awake and the D: drive connected for the transfer.

Individual stages and groups are available through `run_all.py`:

```powershell
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step pds-plan
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step pds-fetch
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step pds-integrate
.venv/Scripts/python.exe -m 7_pds.7d_download --mission artemis-ii --workers 4 --bandwidth-mbps 500
.venv/Scripts/python.exe -m 7_pds.7e_verify --mission artemis-ii --full-hash
.venv/Scripts/python.exe -m 7_pds.7g_report --mission artemis-ii
```

The runner's default all-steps run excludes the multi-terabyte PDS retrieval.
PDS groups stop after failed prerequisites, emitting a coverage report.
The download CLI also supports `--limit` and `--max-priority` for bounded pilots.
Defaults: four workers, two connections per payload host, 500 GB reserve, no
bandwidth cap. Resuming partials requires a stored validator and correct HTTP
206 range. Ignored ranges and changed objects restart cleanly. TLS uses verified
Windows trust roots and certifi; certificate validation is never disabled.

Read `processed/pds/reports/latest.json` on D: for file/byte progress, remaining
network bytes, per-collection coverage, pending releases and integration status.
It updates every 30 seconds during transfer. Complete run reports also save
the list of unresolved files. `processed/pds/inventory.sqlite` records candidates,
ZIP members, cached hashes, transfers, label metadata and reconciliation issues.

Matching D: files are hard linked; matching other-volume files are copied;
verified ZIP members are extracted individually. Treat both source and linked
PDS aliases as immutable: editing either name changes the same bytes. No legacy
source is deleted, moved or overwritten. Distinct archive paths and roles remain
distinct even when content is equal. Existing conflicting PDS destinations are
retained under `processed/pds/quarantine` before replacement.

Integration catalogs live under the existing mission's `processed/pds`:
`photos.jsonl`, `audio.jsonl`, `video.jsonl`, `transcripts.jsonl`, `science.jsonl`.
They point to original bytes on D:, retain labels, checksums, versions and aliases,
and preserve all channels and representations. Photo ledger rows have additive
`pds_products` and `pds_time_conflicts` fields; existing sources and UTC preferences
are preserved. Only missing timestamps receive PDS label UTC. Missing crew raws
can use a verified PDS NEF/DNG. Raw Bayer TIFFs are never treated as RGB photos.
Step `4d` supports explicit `--prefer-pds-processed` selection of verified
processed RGB TIFFs. Unmapped scientific pixel modes are rejected; default tier
generation keeps its existing source precedence.
Supplied transcript formats are imported separately with UTC from labels; PCD
filenames do not determine start times. Web playback/proxy selection and timeline
deduplication are separate presentation work. `2g` emits `web/videoPDS.json`;
the Vite server serves originals from D: through `/pds-assets` with HTTP ranges
for seeking. Codec compatibility remains to be verified against actual playback.
These catalogs do not silently replace existing video, comm or trajectory output.

SPICE, iPhone imagery and the separate HERA archive are tracked as pending releases.
They do not count as failed transfers or as present data. Files lacking upstream
checksums retain explicit weaker assurance after canonical retrieval and local
SHA-256 hashing. Full archival completion requires all manifest paths plus valid
label/inventory references; browser presentation has separate status.

Focused tests (temporary holdings on D:):

```powershell
.venv/Scripts/python.exe -m unittest 7_pds.test_pds -v
```
