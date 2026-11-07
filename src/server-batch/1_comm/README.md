# Pipeline 1: Communications (Audio) — Artemis II Only

Artemis I was uncrewed — this entire pipeline is skipped for `artemis-i`.

## Data Flow

```
archive.org (Artemis-II-ACR-Collection)
    │
    ▼
┌──────────────────────────────────────┐
│  1a — Download ZIPs from IA          │
│  Reads: IA metadata API              │
│  Saves: raw/comm/*.zip               │
│         raw/comm/{date}/*.wav        │  (auto-extracted from ZIPs)
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  1b — Transcribe with WhisperX       │
│  Reads: raw/comm/**/*.wav            │
│  Saves: processed/transcripts/comm/  │
│         {date}/{filename}.json       │  (one JSON per WAV)
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  1c — Web-ready JSON                 │
│  Reads: processed/transcripts/comm/  │
│  Saves: web/comm.json                │
└──────────────────────────────────────┘
```

## Step Details

### 1a: Download IA ZIPs (`1a_download_ia_zips.py`)

|                 |                                                                |
| --------------- | -------------------------------------------------------------- |
| **Source**      | `https://archive.org/download/Artemis-II-ACR-Collection`       |
| **Input**       | IA metadata API (item file list)                               |
| **Output**      | `raw/comm/*.zip` + auto-extracted `raw/comm/{date_stem}/*.wav` |
| **Idempotent**  | Yes — skips already-downloaded ZIPs by filename                |
| **Re-runnable** | Yes — designed for it. NASA is still uploading days 6–11.      |

Downloads ZIP archives, then auto-extracts WAV files into per-date subdirectories.

### 1b: Transcribe (`1b_transcribe.py`)

|                  |                                                            |
| ---------------- | ---------------------------------------------------------- |
| **Input**        | `raw/comm/**/*.wav`                                        |
| **Output**       | `processed/transcripts/comm/{date}/{wav_stem}.json`        |
| **Audio cache**  | `processed/comm_audio/{date}/{wav_stem}.aac` (published by `1c` only if referenced) |
| **Idempotent**   | Yes — skips WAVs that already have a `.json` output        |
| **GPU required** | Yes — WhisperX large-v3 on CUDA                            |
| **Flags**        | `--force` re-transcribes all; `--test N` limits to N files |

Each output JSON contains:

```json
{
  "utcTime": "2026-04-01T12:42:51Z",
  "duration": 45.2,
  "language": "en",
  "segments": [{ "start": 0.0, "end": 2.5, "text": "..." }]
}
```

### 1c: Publish comm transcripts and audio (`1c_web_comm.py`)

|                |                                        |
| -------------- | -------------------------------------- |
| **Input**      | `processed/transcripts/comm/**/*.json` |
| **Output**     | `web/comm.json`, `processed/transcripts/inputs/comm.csv`, `web/comm_sources.json` |
| **Idempotent** | Yes — overwrites output each run       |

Audio producers (`1b`, standalone `1b2`, `1b3`, and `1b4`) prepare playback
files under `processed/comm_audio/`, outside the publishable web tree.
After hallucination filtering and corrected-loop precedence, `1c`:

1. Checks that every surviving source has nonempty prepared audio, adopting
   existing `web/comm` files into the cache on the first run. Missing audio
   fails the build before changing the published transcript indexes.
2. Publishes only referenced recordings into `web/comm`, using hard links
   where supported and copies otherwise. A file with any surviving utterance
   stays published; M4A recordings retain their playback offsets and range seeking.
3. Writes `comm.csv`, `comm.json`, and the referenced PDS source metadata.
4. Moves every unreferenced file out of `web/comm` into the processing cache,
   then removes empty date folders. This includes superseded AI clips, filtered
   clips, and excluded Mission Audio. Conflicting cached versions are retained
   under `processed/comm_audio/_retired/{run}/`.

`processed/comm_audio_manifest.json` records published paths, byte sizes,
utterance counts, and retired files. Runs that retire files also keep a receipt
under `processed/comm_audio_reconciliations/`. Raw audio and transcript JSONs
remain available, and a later build can restore a source directly from the cache.
An empty comm export is rejected so missing inputs cannot retire all audio.

This finalization is part of `1c` itself: normal runs, `pcd-comm`, `loops-comm`,
PDS integration/refresh groups, and `refresh_assets.py --stage build` all use
the same publication policy. To reconcile existing assets without transcription:

```powershell
uv run run_all.py --mission artemis-ii --step 1c
```

Aggregates per-file transcripts into chronologically sorted pipe-delimited CSV.
Columns are `t|s|d|src|text`: segment UTC, audio offset, segment duration,
audio source relative to `web/comm/`, and text. Machine transcripts receive
hallucination filtering; supplied PDS transcripts retain their text.

The app reads `web/comm.json`, using the shared TypeScript `CommEntry` type.
Each entry has `t`, `s`, `d`, `src`, `text`, and optional `speaker`. Speaker
names are separate from the utterance text and rendered when present in the
comm list, player and timeline tooltips. Utterances without speaker metadata
omit the field. All views can fall back to older `comm.csv` assets.
The five-column batch CSV keeps its existing `[speaker] text` representation
for downstream enrichment and PAO matching; it does not gain a new column.

### 1b3: Crew PCD audio (`1b3_pcd_audio.py`)

Imports verified PDS `data_audio_pcd` recordings into the same comm flow,
preferring edited `data_transcripts_pcd` CSV/SRT over WhisperX. Run `7f` first
to build the PDS catalogs, then `run_all.py --mission artemis-ii --step pcd-comm`
to import PCD and rebuild `processed/transcripts/inputs/comm.csv`. The normal all-steps comm run also
includes `1b3`; PDS archive retrieval remains explicit.

JSONs under `processed/transcripts/comm/pcd` retain NASA ID, speakers, exact
supplied utterance UTC, relative playback offsets and provenance. Recording
start UTC comes from labels, with a cached exact-ID Imagery Online fallback.
PCD never receives the IA radio timezone correction. Missing UTC and pending
supplied products are reported in `processed/pds/pcd_comm_report.json`.

Step `1c` emits the existing five-column `t|s|d|src|text` CSV, including PCD
sources such as `pcd/art002a000005.m4a`. Step `1b3` caches playback under
`processed/comm_audio/pcd`; `1c` publishes referenced files to `web/comm/pcd`.
`web/comm_sources.json` maps each published PCD source to metadata and
PDS provenance. Comm Test has a separate PCD channel toggle. Supplied transcript
text is preserved, including phrases filtered as hallucinations for machine
transcription. See [PDS details](../7_pds/README.md#crew-pcd-audio-and-comm-transcripts)
for timing adjustments, fallback transcription and rerun behavior.

PCD playback uses the dated comm encoding: mono AAC at 22,050 Hz and 64 kbps.
It retains the `.m4a` container and front-loaded index for seeking within a
recording. Original multichannel PDS audio remains in `raw/pds`. The shared
profile lives in `shared/comm_encoding.py`; both `1b3` and routine `1c` builds
upgrade older playback encodes using the original recording, without changing
transcript text, UTC, offsets or durations. The saved `audio_encoding` profile
makes subsequent runs skip unchanged audio.

Corrected loop recordings already contain low-bitrate AAC and remain unchanged;
raising those to 64 kbps would increase their size.

### 1b4: Corrected mission voice loops (`1b4_loop_audio.py`)

Imports hand-corrected `data_transcripts_loops` CSVs and their verified PDS
audio. Run `run_all.py --mission artemis-ii --step loops-comm` after `7f`.
Step `1c` gives these transcripts priority over AI utterances on the same
channel wherever their time coverage overlaps, even when either source splits
the speech into different utterances. Whole overlapping AI utterances are
superseded; the originals are preserved and boundary overhangs are audited.
AI in omitted time intervals and crew PCD data remain available.

Corrected JSONs live under `processed/transcripts/comm/loops`; prepared audio
lives under `processed/comm_audio/loops`. Step `1c` publishes referenced audio
under `web/comm/loops`. CSV UTC determines mission time and CSV audio offsets
determine seeking. Clock discontinuities delimit coverage; calendar-day labels
and loop SRT timing do not determine it. Unresolvable date-only rows remain in
`untimed_segments`, outside the timeline. No GPU is needed.

`processed/pds/loop_comm_import_report.json`, `loop_comm_merge_report.json`, and
`superseded_comm.jsonl` report coverage, timing repairs and every replaced AI
row. See the [replacement policy](../7_pds/README.md#corrected-voice-loop-transcripts-supersede-ai-comm)
for details. Step `1d` classifies the corrected utterances from the updated comm CSV.

## Transcript classification and verified history

The canonical flow is **1c + 2k ? 1d ? 1e ? 1f ? 1g ? 1h ? 1i**.
The ordinary all-steps runner prepares both source inputs before starting 1d.
`comm-history` runs 1d through 1i, including classification. From the repository
root, after source transcription/import, rebuild inputs and run the whole
transcript/history pipeline with:

```powershell
uv run --directory src/server-batch run_all.py --mission artemis-ii --step 1c 2k comm-history --backend hgx --model LLM-Alpha-512K --reasoning-effort low
```

This command can perform substantial model work and publishes web assets.
It is a future-run command, not a validation command for a refactor. Existing
completed processing outputs, research checkpoints and published assets need no
migration or reclassification to remain on disk.

### 1d: Classify transcripts (`1d_enrich_transcripts.py`)

Reads `processed/transcripts/inputs/comm.csv` and `PAO.csv`, with legacy web CSV
fallback for older inputs. Uses **HGX**, model alias **LLM-Alpha-512K
(GLM-5.3-Flash)**, low reasoning, and the tested constrained `transcript_labels`
JSON schema. A normal 50-row window needs one model request. Source-balanced
context includes neighboring turns from each target recording within ten minutes.

Subject category, importance, tags, topic, statement kinds and conversation
threads are independent fields. Source identity comes from metadata: **PAO,
OE-1, OE-2, PCD**, or explicitly unknown. Simultaneous recording sources do
not become one conversation thread. Classification never discovers milestones,
attributes crew, or requires event extraction.

The subject categories are `maneuver`, `systems`, `photography`, `science`,
`crew_life`, `media_event`, `milestone`, `communications`, `mission_background`,
`social`, and `unspecified`. PAO-named filler categories are retired. A low-value
acknowledgment retains its supported technical subject; a genuine social turn
can have a separate social thread. An unrecoverable subject stays `unspecified`.
Future countdowns require `plan`; observations and suspected causes retain their
separate contribution kinds. Hypotheses must not become asserted causal facts.

Each response is validated per row. Valid rows are saved atomically before any
repair request. Only rejected or missing rows are retried, once per invocation,
with original source context and field-specific validation errors. Another run
continues from saved successes. Contextual subject checks also use already
accepted rows during repair and resume. No invalid category is silently renamed.

### 1e: Discover candidate history (`1e_discover_history.py`)

Consumes the canonical 1d manifest and utterances. Reconstructs statements and
candidate developments in 25-row groups using the existing extraction contract,
with supplementary itinerary, media and science context. It preserves every
classification field unchanged, and adds supported crew involvement and anchored
candidate milestones. It does not request labels.

Exact evidence, target coverage, thread membership, statement kind, lifecycle
status and crew are validated before an extraction is checkpointed. Plans,
countdowns, questions and hypotheses cannot anchor executed developments.
Reports locate when a development was reported; they do not invent execution time.
A rejected group is split into smaller groups. Saved successful groups are
replayed without model requests on resume; only missing groups are investigated.

### 1f: Verify candidate history (`1f_investigate_history.py`)

Consumes canonical 1e candidates and classifications. Investigates each candidate
and planned-reference lead against searchable original transcripts, photo
captions, reference material and PDF pages. Searches must include original
sources; generated candidates are never evidence. Retrieval keeps recording
sources distinct. Citations must quote exact supplied source text.

Supported events need separate evidence for event, crew, cause and execution
time assertions. Configuration, a future countdown or a planned activity does
not prove execution. Observations remain distinct from suspected causes.
Rejected claims require positive contradictory evidence; otherwise they remain
unresolved. Explicit duplicate links preserve distinct lifecycle stages.

Each completed investigation is saved immediately. Transport failures stay
pending for the next run. Exhausted/unresolved investigations remain auditable;
use `--retry-unresolved` to retry those event tasks explicitly. There is no
transcript-wide labeling review or final label reconciliation during investigation.
The utterance classifications pass through unchanged.

### 1g?1i: Build and publish

- **1g** builds the mission hierarchy from canonical 1f verified history and
  classifications. Planned activities remain unconfirmed until supported by events.
- **1h** publishes `web/timeline.json` and optional crew tracks under
  `web/timeline/crew/`. Detailed evidence remains in processing.
- **1i** publishes `web/transcripts.json` directly from canonical 1d classifications,
  retaining current source text, audio offsets, durations and separate speaker names.
  It can run after 1d without waiting for event research.

These stages make no model requests. Manifests verify upstream inputs and output
hashes; changed or incomplete inputs fail instead of selecting alternate outputs.

### Artifacts and resumption

```text
processed/transcripts/
  inputs/              # comm.csv and PAO.csv from 1c and 2k
  1d_enrichment/       # Classified utterances; work checkpoints and HGX diagnostics
  1e_discovery/        # Classified utterances plus unverified milestone candidates
  1f_investigation/    # Verified events, investigation audit, source snapshots
  1g_hierarchy/        # mission_activity_tree.json and mission_activities.json
  1h_web_timeline/     # Timeline publication report
  1i_web_transcripts/  # Transcript publication report
```

Stages 1d?1f use `utterances.jsonl`, `milestones.jsonl`, processing CSV projections
and `report.json` artifact manifests. The classification stage has no milestones.
Work checkpoints and request diagnostics live inside each stage. A manifest is
committed only when all pending work is finished; validated work survives an
interruption before that point. The runner stops at an incomplete stage.

Repeat the same command with the same inputs, model and batching settings to
resume. `--history-limit N` bounds pending work per model stage without declaring
an incomplete dataset complete. Individual stage modules also accept `--limit`,
`--batch-size` (1d/1e), `--context-lines` (1d/1e) and `--max-rounds` (1f).
Settings and source identities determine checkpoints; retry limits and unrelated
code edits do not discard validated work. `HGX_URL` and `HGX_API_KEY` come from
`.env`; the transcript stages require HGX explicitly.

Focused validation uses isolated temporary fixtures and mocked model responses:

```powershell
cd src/server-batch
.venv/Scripts/python.exe -m unittest test_single_classification test_transcript_stages test_transcript_enrichment test_history_pipeline test_mission_hierarchy test_web_transcripts test_web_timeline
```
