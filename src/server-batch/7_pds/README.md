# PDS retrieval

Artemis II's complete available PDS archive is discovered without image, target,
flight-day or extension filters. Original downloads, partials, manifests, SQLite
state and reports default to `D:/ArtemisInRealTime_assets/artemis-ii`. Set
`BULK_ASSETS_DIR` to configure bulk storage, or `PDS_ASSETS_DIR` for a separate
archive root. Both append the mission slug first, independently of `DATA_DIR`.
Other bulk holdings now live in this same mission's `raw/photos`, `raw/video/io`,
`raw/audio/vehicle` and `raw/science` directories; the migration is described in
[`../README.md`](../README.md#storage-layout). PDS reuse inventory scans the
selected mission's roots only.

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
PDS aliases as immutable: editing either name changes the same bytes. Retrieval
does not delete, move or overwrite source holdings. Distinct archive paths and roles remain
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
Supplied transcript formats are retained separately. Crew PCD recordings also
feed the comm pipeline through step `1b3` (see below); PCD filenames do not
determine start times. Web playback/proxy selection and timeline
deduplication are separate presentation work. `2g` emits `web/videoPDS.json`;
the Vite server serves originals from D: through `/pds-assets` with HTTP ranges
for seeking. Codec compatibility remains to be verified against actual playback.
These catalogs do not silently replace existing video, comm or trajectory output.

## Crew PCD audio and comm transcripts

The mission-audio delivery includes `data_transcripts_pcd` with edited CSV and
SRT transcripts for `data_audio_pcd`. Step `1b3` imports these in preference to
WhisperX. It selects one version per NASA ID while retaining all product
representations in provenance. Label UTC supplies recording starts. If a start
is missing, an exact NASA-ID Imagery Online lookup supplies it only when the
returned UTC is unambiguous; successful results are cached under
`processed/pds/io_pcd` on the bulk drive. Dates and NASA IDs never imply UTC.

CSV utterance UTC, speaker and text are retained independently of SRT playback
offsets and ends. Some delivered SRT cues differ from the CSV's coarse offsets,
so they must not replace the CSV UTC. Invalid/out-of-range subtitle playback
times are bounded to the actual audio duration; original offsets and every
adjustment remain in the normalized transcript. CSV-only ends use the next cue
or audio end and explicitly record that inference.

```powershell
# After 7f has produced verified audio/transcript catalogs:
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step pcd-comm
# Bounded pilot (imports only N recordings; does not rebuild comm.csv):
.venv/Scripts/python.exe -m 1_comm.1b3_pcd_audio --mission artemis-ii --test 1
```

`pds-integrate` and `pds-refresh` include `1b3` and `1c`. Normalized JSONs live
in `processed/transcripts/comm/pcd`; playback AAC/M4A files are cached in
`processed/comm_audio/pcd`. Step `1c` publishes only referenced recordings to
`web/comm/pcd`. Originals retain their channels in `raw/pds`; playback uses the
dated comm profile (mono AAC, 22,050 Hz, 64 kbps), in indexed M4A containers.
Both `1b3` and routine `1c` builds upgrade older playback encodes without
re-transcribing, and cache the encoding profile for resumable reruns. Step `1c` includes
these in `processed/transcripts/inputs/comm.csv` with its existing five columns, using `pcd/{NASAID}.m4a`
as the source and including speaker labels in text. `web/comm_sources.json`
provides channel, time source, transcript origin, checksums and PDS provenance.
The app uses `web/comm.json` with an optional `speaker` field and clean utterance
`text`, rendering names separately in the comm list/player and timeline tooltips.
The CSV retains its five-column layout for batch consumers.
Comm Test exposes a separate PCD filter; downstream enrichment consumes the
same comm CSV as before.

PDS audio and supplied transcript originals remain under the bulk drive's
`raw/pds`. Generic web-media preparation skips both; comm steps produce playback
under `web/comm`, normalized text under `processed/transcripts`, and `comm.csv`.
There are no separate `web/audio` or `web/transcripts` copies, or `audioPDS.json`
and `transcriptsPDS.json` indexes. Step `2g` removes any obsolete copies of those
indexes on each run.

WhisperX is used only when no supplied transcript is available or advertised
in the delivery inventory. Advertised transcripts awaiting download are reported
instead of retranscribed. `processed/pds/pcd_comm_report.json` under the regular
data directory reports imported/cached recordings, missing UTC, pending
transcripts, errors, timing adjustments and unavailable product representations.
`--force` regenerates selected outputs; otherwise unchanged imports and web
audio are reused. Actual transcription needs the existing WhisperX/CUDA setup;
supplied imports require only ffmpeg/ffprobe.

The local delivery inspected on 2026-10-31 contained 53 distinct PCD recordings,
all with CSV/SRT transcripts and label UTC. The alternate `src` audio and label
for `art002a000052` returned upstream HTTP 403; its `raw` recording is available.

## Corrected voice-loop transcripts supersede AI comm

`data_transcripts_loops` contains corrected mission voice-loop transcripts.
Step `1b4` imports the latest verified CSV and its matching `data_audio_loops`
audio into `processed/transcripts/comm/loops`. Edited text, speaker names,
per-utterance UTC, original CSV row IDs and product provenance are retained.
AAC originals are remuxed into seeking-friendly
`processed/comm_audio/loops/*.m4a` without re-encoding. Step `1c` publishes
referenced files to `web/comm/loops` and retires unused AI clips to the cache.
No GPU or transcription model is needed.

```powershell
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step loops-comm
```

This runs `1b4` then `1c`; normal comm runs and the PDS integration/refresh
groups also include `1b4`. Corrected records appear on their existing OE-1 or
OE-2 channel. `1c` applies corrected authority by channel and time interval,
independently of utterance segmentation: one corrected paragraph can replace
many AI rows, and many corrected utterances can replace one AI paragraph.
Any overlapping AI utterance is superseded as a whole, avoiding guessed word
boundaries. AI outside the covered intervals, the other channel, and crew PCD
transcripts remain eligible. The source AI JSON files remain unchanged.

CSV audio positions and absolute UTC are separate coordinates: loop audio can
omit hours of mission time. Authority ends at each cut in that clock mapping;
it never takes over an entire calendar day merely because of a filename or
label. Date-only/uncorroborated UTC rows are retained as `untimed_segments` in
the normalized JSON and listed in the import report; they do not enter the
timeline or suppress AI. Row order is sorted by audio position with original
row IDs retained. Missing-decimal UTC punctuation is repaired only when both
adjacent UTC/audio clocks corroborate it, and the original value is recorded.

Loop SRTs are retained in the archive but do not define alignment: the current
delivery has missing cues and incorrect minute/second positions. Playback ends
are estimates based on the next CSV audio position, with trailing quiet and
isolated squelch clicks removed using cached ffmpeg silence detection. This
affects playback stops only; authority intervals use the CSV clock mapping.

Reports under the regular data directory's `processed/pds`:

- `loop_comm_import_report.json`: imported/cached products, untimed rows,
  corroborated UTC repairs, row reordering and covered intervals.
- `loop_comm_merge_report.json`: superseded AI counts and coverage by channel.
- `superseded_comm.jsonl`: every superseded AI row, its original text/audio
  reference, corrected replacement sources, and any boundary overhang outside
  the corrected coverage. These boundary rows are deliberately replaced whole;
  the audit makes possible loss at a coarse AI boundary reviewable.

The 2026-10-31 delivery has 1,144 timed corrected utterances across three loop
products and three date-only utterances retained without invented timestamps.
The initial merge superseded 1,240 AI utterances. Four intersected coverage
boundaries (three by less than a second); their overhangs are recorded in the
audit. Downstream step `1d` reads the resulting comm CSV; corrected text/time
identities receive fresh assessments rather than inheriting AI assessments.

SPICE, iPhone imagery and the separate HERA archive are tracked as pending releases.
They do not count as failed transfers or as present data. Files lacking upstream
checksums retain explicit weaker assurance after canonical retrieval and local
SHA-256 hashing. Full archival completion requires all manifest paths plus valid
label/inventory references; browser presentation has separate status.

Focused tests (temporary holdings on D:):

```powershell
.venv/Scripts/python.exe -m unittest 7_pds.test_pds -v
```
