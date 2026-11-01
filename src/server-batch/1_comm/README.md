![alt text](image.png)# Pipeline 1: Communications (Audio) — Artemis II Only

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
| **Output**     | `web/comm.json`, `web/comm.csv`, `web/comm_sources.json` |
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
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step 1c
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
to import PCD and rebuild `web/comm.csv`. The normal all-steps comm run also
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
for details. Step `1d` uses the updated comm CSV and assesses the new corrected
utterance identities; its append-only ledger retains historical assessments.

## Dependencies

Radio comm follows **1a → 1b → 1c**. The optional crew PCD branch follows
**7f → 1b3 → 1c**. Corrected mission loops follow **7f → 1b4 → 1c** and
supersede overlapping AI radio utterances before enrichment.

### 1d: Transcript enrichment and milestone discovery (`1d_enrich_transcripts.py`)

Merges `web/comm.csv` and `web/PAO.csv` chronologically, scores and categorizes
each utterance, and extracts granular mission developments from the transcript.
Nearby itinerary, flight-plan, science, photo, and video references provide
context; they do not define which milestones may be discovered.

Each batch uses two separate LLM requests:

1. **Extraction** reconstructs interleaved threads and statements, distinguishes
   reports from hypotheses/plans, and groups developments with their source
   indices, one evidence anchor, and supported crew involvement. Python validates
   target coverage, references, statuses and exact evidence before labeling.
2. **Labeling** receives the original targets and the validated extraction and
   returns only importance, category, tags and topic for each target. Python
   supplies crew and milestones from extraction; labeling cannot add, remove,
   move or duplicate those milestones. An anchored target's primary topic uses
   its first development title, preserving the extracted observation/uncertainty
   instead of letting labeling rewrite it as a causal claim.

Category describes the subject, importance measures the information contributed
in context, and milestone discovery tracks supported developments independently.
Meaningful continuations retain their context; adjacent troubleshooting threads
are not assumed to share a cause. Reports preserve diagnostic uncertainty and
distinguish involved crew from identified speakers. Newly reported past events
remain eligible, with timestamps locating the report rather than execution.
Milestones use the clearest target evidence, with earliest-target tie breaking.

Capture concrete steps such as crew walkout, departure, pad arrival, boarding,
hatch closure, readiness approvals, burn ignition/cutoff, configuration changes,
science session starts/completions, and anomaly resolution. Distinct stages and
multiple developments in one utterance receive separate milestone objects.
Future plans, generic explanations, and repetitive recaps do not establish new
developments. A milestone need not have the highest importance score or the
`milestone` category.

Each assessment includes a `milestones` array (empty when none). Each milestone
has a `title`, `status`, and exact supporting `evidence` quote from its utterance.
Statuses are `started`, `in_progress`, `completed`, `confirmed` (for example a
readiness approval), `observed`, `anomaly`, `resolved`, and `cancelled`.
An optional `event_id` links a directly matching catalog reference. Missing
catalog links do not prevent discovery; photo filenames and video IDs are
media references and cannot be used as catalog event IDs.

Outputs under `processed/transcripts/`:

- `enriched_utterances.jsonl`: append-only assessment ledger, with a schema
  version and a `milestones` array on each assessed utterance. Latest record
  per utterance wins. Assessments from the previous schema are automatically
  revisited on the next run, within any requested start/batch limits.
- `detected_milestones.jsonl`: derived stream with one record per development,
  including a deterministic occurrence `id`, `source_uid`, channel, title,
  status, evidence, source text, and optional catalog `event_id`. The `t` field
  is the utterance timestamp and `time_basis` is `report`; it is not a claimed
  exact execution time. Rebuilt atomically at startup and after each saved
  batch, so resuming or forcing reassessment removes superseded detections.
  This replaces the old format that copied only utterances with event IDs.

`web/comm_enriched.csv` and `web/PAO_enriched.csv` retain their existing column
layouts. Their `event_id` column contains the first matched catalog link, if
any; use the milestone JSONL for the full set of discovered developments.
Preceding context includes already captured milestones to reduce repeated
reports at batch boundaries. Following context helps interpret ambiguous
activity without extracting events from context-only lines. `--context-lines`
sets the number of utterances on each side (default 5).
Pending targets separated by cached rows form separate batches so intervening
conversation is available as neighboring context when resuming or replacing a model.
Occurrence IDs identify source evidence, not a
globally reconciled event; distant recaps can still require editorial merging.

```bash
python 1_comm/1d_enrich_transcripts.py --mission artemis-ii
python 1_comm/1d_enrich_transcripts.py --mission artemis-ii --test-batches 1
```

HGX is the default backend, using `HGX_URL`, `HGX_API_KEY`, and model
`LLM-Alpha-512K` (GLM-5.3-Flash). To use the local Strata instance on the 4090,
set `STRATA_URL` in the repo-root `.env` to its OpenAI-compatible API base URL,
including the port and any `/v1` prefix. The script appends `/chat/completions`.
For this local instance, use `STRATA_URL=http://127.0.0.1:47831/v1`.
`STRATA_MODEL` defaults to `iq3_s`, the API ID for Qwen3.8-Flash-Next IQ3_S;
use the ID returned by `/v1/models` if you load a different variant.
`STRATA_API_KEY` is optional.
Strata does not require HGX credentials. `--model` overrides the selected
backend's default model for one run.

```bash
# Benchmark one local batch (still saves results)
python 1_comm/1d_enrich_transcripts.py --mission artemis-ii --backend strata --test-batches 1
# Run enrichment locally, then build the hierarchy
python run_all.py --mission artemis-ii --step 1d 1e --backend strata
```

Add `--hide-reasoning` to either command to hide reasoning tokens while keeping
live JSONL assessments and batch summaries visible. `--quiet` on 1d hides both
reasoning and JSONL token streaming. These flags change terminal display only;
the model still generates reasoning. Hiding it reduces terminal writes, not
the model's reasoning workload. Extraction defaults to `auto` reasoning and
labeling to `low`. Both direct 1d and the runner accept
`--extraction-reasoning-effort` and `--reasoning-effort` to override the respective
stages (`auto`, `low`, `medium`, `high`). `--max-tokens` on direct 1d sets the
output limit for each request. Batch summaries combine timing and tokens from
both stages; per-stage metrics are retained in diagnostics.

Step 1e makes no LLM requests: it builds the hierarchy from 1d's saved results
from either backend. Changing backend or model resumes the existing ledger;
use `--force` on 1d to reassess cached utterances for a model comparison.
The pipeline runner also accepts `--force` and forwards it only to step 1d:

```bash
python run_all.py --mission artemis-ii --step 1d 1e --force
```

Each new ledger assessment records the API `model` ID and `backend`, plus:

- `assessed_at` (UTC), `run_id`, and `batch` to identify the pass.
- `prompt_version` (independent of the assessment schema), `system_prompt_sha256`
  and `user_prompt_sha256` to distinguish revised instructions and batch inputs.
- `generation_settings`: reasoning effort, temperature and output token limit.
- `stage_provenance`: separate extraction/labeling prompt hashes and generation
  settings. The existing top-level prompt hashes/settings describe labeling.
- `model_provenance=request` for model attribution from the request settings.

`run_id` links to the diagnostics directory with the system prompt and run
arguments. Increment `PROMPT_VERSION` when changing prompt-building rules;
the system prompt hash also changes automatically when its text changes.
Milestone records carry this metadata in `assessment`, and step 1e preserves it
on each merged milestone's source report. Enriched web CSV columns stay the same.

To replace results from a particular model with the selected backend/model:

```bash
python run_all.py --mission artemis-ii --step 1d 1e --backend hgx --reassess-model iq3_s
```

`--reassess-model` accepts one or more exact API model IDs; `unknown` selects
older records with no model attribution. Normal pending/schema-outdated rows
are still processed. Other current cached models are retained. For a prompt
revision using the same model, run 1d with `--force` and any desired range/batch
limits. Changes to model or prompt do not automatically invalidate the cache.
The ledger retains previous assessments; the latest valid record per utterance
is used for exports. Legacy records may have only backend/model metadata, with
`model_provenance=user_confirmed_legacy` and a `provenance_recorded_at` timestamp.
That timestamp records the attribution, not when the original LLM ran; original
prompt settings and assessment times remain unknown.

`--test-batches` still saves assessments
and the derived milestone stream, but skips the final web CSV export.
`--force` reassesses the selected range while retaining cached assessments
outside it. Older ledger entries remain available on disk for audit.

Each run also writes `processed/transcripts/enrichment_diagnostics/{run_id}/`:

- `run.json` records the model, arguments, schema version and both system prompts.
- `batches.jsonl` records each batch's timing, token usage, stream finish reason,
  accepted count, and missing utterance indices, IDs, timestamps and channels.
- `batch_00001_stages.json` (and subsequent batch numbers) is always written,
  including successful batches. It preserves stage prompts, raw structured
  outputs, validated extraction, hashes, settings and metrics for inspection.
- `batch_00001.json` (and subsequent batch numbers) is written for problem
  batches: missing assessments, malformed JSON/indices, rejected milestone
  evidence, discarded catalog IDs, duplicate indices, abnormal stream endings,
  request failures or Ctrl+C. It includes the exact prompts, target utterances,
  raw model content and received stream lines, validation reasons, and any
  exception traceback or HTTP error body. Partial stream lines are retained
  when a request fails or is interrupted. Request authorization headers are
  not logged.

The terminal prints the diagnostics directory and a warning linking each
problem batch. Rejected or omitted utterances remain pending for the next run;
diagnostics do not change the assessment schema or invalidate saved results.
Extraction validation can request one correction at low reasoning using the
original source, previous JSON and validation error. Both attempts are retained
with separate hashes/settings/stats. Unclean extraction streams fail directly;
an invalid correction stops before labeling or ledger writes. Incomplete/invalid
labels or unhealthy streams preserve valid rows but exit with failure, so the
runner stops before step 1e. Resume processes pending targets; use `--force`
to deliberately replace already valid cached assessments.

## How to Run

> ⚠️ This pipeline is **Artemis II only**. Artemis I was uncrewed — skip all steps.

Run from `src/server-batch/`:

```bash
# Run all steps in order
python run_all.py --mission artemis-ii

# Run individual steps
python run_all.py --mission artemis-ii --step 1a
python run_all.py --mission artemis-ii --step 1b
python run_all.py --mission artemis-ii --step 1c

# Run steps directly (useful for flags like --force or --test)
python -m 1_comm.1a_download_ia_zips --mission artemis-ii
python -m 1_comm.1b_transcribe --mission artemis-ii
python -m 1_comm.1b_transcribe --mission artemis-ii --test 5   # transcribe only 5 files
python -m 1_comm.1b_transcribe --mission artemis-ii --force    # re-transcribe all
python -m 1_comm.1c_web_comm --mission artemis-ii
```

**Required env vars:** `HF_TOKEN` (Hugging Face token for WhisperX diarization), GPU with CUDA for step 1b.

## Issues

None — this pipeline is clean and straightforward.
