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

Outputs under `processed/transcripts/1d_enrichment/`:

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

`processed/transcripts/1d_enrichment/comm_enriched.csv` and
`processed/transcripts/1d_enrichment/PAO_enriched.csv` retain their existing column layouts.
Their `event_id` column contains the first matched catalog link, if
any; use the milestone JSONL for the full set of discovered developments.
Preceding context includes already captured milestones to reduce repeated
reports at batch boundaries. Following context helps interpret ambiguous
activity without extracting events from context-only lines. `--context-lines`
sets the number of utterances on each side (default 5). `--batch-size` sets the
number of target utterances (default 50), plus 5 preceding and 5 following
utterances. Extraction also receives bounded carryover from validated results:
the six most recent threads (two statements each, preserving report/plan/hypothesis
kinds) and the last 20 captured developments. Prior batch indices are removed;
new evidence and indices must refer to the current targets. Approvals,
preparation, execution starts, and completions remain separate developments.
The carryover updates after each saved batch, using only accepted assessments
for captured developments. On restart or a gap between pending batches, thread
state resets and captured developments are seeded from saved assessments in
the preceding 300 utterances, capped at 20; future cached rows are excluded.
Pending targets separated by cached rows form separate batches so intervening
conversation is available as neighboring context when resuming or replacing a model.
Occurrence IDs identify source evidence, not a
globally reconciled event; distant recaps can still require editorial merging.

```bash
uv run python 1_comm/1d_enrich_transcripts.py --mission artemis-ii
uv run python 1_comm/1d_enrich_transcripts.py --mission artemis-ii --test-batches 1
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
uv run python 1_comm/1d_enrich_transcripts.py --mission artemis-ii --backend strata --test-batches 1
# Run enrichment, recovery, investigation, then build the hierarchy
uv run run_all.py --mission artemis-ii --step 1d 1e 1f 1g --backend strata
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

Step 1g makes no LLM requests: it builds the hierarchy from 1f's investigated
results. Steps 1e and 1f use the selected LLM backend. Changing backend or model
resumes the existing 1d ledger;
use `--force` on 1d to reassess cached utterances for a model comparison.
The pipeline runner also accepts `--force` and forwards it only to step 1d:

```bash
uv run run_all.py --mission artemis-ii --step 1d 1e 1f 1g --force
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
Milestone records carry this metadata in `assessment`. Investigation saves its
own model settings, exact prompts, retrieved source text and decisions.
Processing CSV columns stay the same.

To replace results from a particular model with the selected backend/model:

```bash
uv run run_all.py --mission artemis-ii --step 1d 1e 1f 1g --backend hgx --reassess-model iq3_s
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
and the derived milestone stream, but skips the final processing CSV export.
`--force` reassesses the selected range while retaining cached assessments
outside it. Older ledger entries remain available on disk for audit.

Each run also writes `processed/transcripts/1d_enrichment/diagnostics/{run_id}/`:

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
with separate hashes/settings/stats. Unclean extraction streams fail that batch
directly; an invalid correction skips its labeling and ledger writes.
Request failures, incomplete/invalid labels and unhealthy streams are logged,
valid rows are saved, and processing continues with the remaining batches.
After all batches, any batch errors produce a failure exit. When 1e is explicitly
next in the same pipeline invocation, the runner continues to recovery only if
this invocation's 1d diagnostics prove every batch was attempted. Otherwise it
stops. Ctrl+C still interrupts immediately. Rerunning 1d processes pending
targets; use `--force` to deliberately replace already valid cached assessments.
Filler labels may have empty tags when there is no substantive topic.

### Post-enrichment history pipeline: 1e → 1f → 1g

These steps normally run **after 1d finishes**, including a 1d run that finished
with rejected rows. Step 1e also supports `--follow` while 1d is active (below).
The former `1e_mission_context.py` is now `1g_mission_context.py`.
The new stages never append to or rewrite 1d's original ledger. All generated
artifacts stay in `processed/transcripts`; no stage publishes into `web/`.
The original `web/comm.csv`, `web/PAO.csv` and media catalogs remain read-only
source inputs. Steps 1e and 1f read the original ledger and diagnostics directly.

From the repository root, after 1d finishes:

```powershell
uv run --project src/server-batch python src/server-batch/run_all.py --mission artemis-ii --step comm-history --source-run RUN_ID --hide-reasoning
```

`comm-history` expands to **1e, 1f, 1g**, without rerunning 1d. Omit
`--source-run` to select the latest diagnostics directory. Prefer the explicit
ID when recovering a particular forced run. Without `--follow`, an
interrupted/running run is rejected; every batch number must be present.

#### Running 1e alongside an active 1d

In a second terminal, from the repository root:

```powershell
uv run --directory src/server-batch run_all.py --mission artemis-ii --step 1e --source-run RUN_ID --follow --hide-reasoning
```

Follow mode reads only newline-terminated completed batch diagnostics. The 1d
producer flushes accepted ledger rows and finishes its batch detail files
before appending each diagnostic summary. Recovery can therefore work on those
finished failures without opening the active batch's incomplete detail files.
Its ledger read is bounded to a byte snapshot and ignores any unfinished final
line. It never writes into 1d's ledger, milestones, or diagnostics.

Only failures from already completed batches are recovery targets while 1d is
active; future utterances remain 1d's responsibility. Corrections are saved by
source run and utterance, with fingerprints of their completed batch inputs,
so new ledger appends do not invalidate earlier repairs. Model requests and
repairs do not repeat while waiting for another completed batch. Polling is
every 30 seconds by default (`--poll-seconds N`). The selected run is pinned
even if another run is later created.

Live exports have `complete=false` and distinguish `awaiting_1d` rows from
unresolved recovery attempts. Steps 1f/1g refuse them. After the final batch is
logged, 1e performs the full missing/outdated-row check, reuses live repairs,
and publishes its completed, hash-verified dataset. `--limit N` does one limited
sweep and exits instead of waiting. `--retry-unresolved` makes one additional
attempt per invocation, rather than retrying old failures after every append.

Ctrl+C stops following without losing saved repairs. If 1d itself is interrupted,
1e waits for that specific run; stop it and select the resumed run's ID rather
than treating an interrupted producer as complete. Using the same HGX backend
shares inference capacity; `--backend strata` runs recovery on the local model.

#### 1e: Recovery (`1e_recover_enrichment.py`)

Builds a source-complete working dataset from COMM + PAO and the selected 1d
run. Missing IDs in that run's diagnostics are recovery targets even when the
ledger contains an older assessment with the same schema version. It also
recovers absent/outdated assessments. Successful, unaffected rows are retained.

Recovery reuses saved validated extractions for label-only repairs first.
Other failures are retried in groups of at most 25 targets, recursively split
down to individual utterances, with preceding/following context. Requests use
low reasoning and a 16,384-token ceiling by default. If extraction still fails,
an individual label-only request can recover the utterance's labels while
explicitly deferring milestone discovery to 1f. If all attempts fail, the source
row is still retained with `category=unassessed`, null importance, and an explicit
unresolved recovery record; it is never silently discarded or called filler.
`complete` means every recovery target was attempted and every source row is
retained, not that every model request succeeded or every assertion is true.

Outputs under `processed/transcripts/1e_recovery/`:

- `utterances.jsonl`, `milestones.jsonl`: complete source rows and candidate events.
- `comm_enriched.csv`, `PAO_enriched.csv`: processing-only CSV projections.
- `report.json`: coverage, recovered/unresolved counts, input and artifact hashes.
- `work/{snapshot}/row_*.json`: individual resumable repairs, including provenance.
- `requests/`: exact model requests, responses, partial failures and settings.

#### 1f: Reassessment and investigation (`1f_investigate_history.py`)

Starts from the recovered output and investigates against searchable source
material. It does more than schema repair:

1. Reviews **every source utterance**, including low-importance and unassessed
   rows, in windows of 40. Reassesses labels/crew and proposes omitted events.
2. Investigates every candidate event and each planned/as-flown reference lead.
   A bounded model loop can search the whole mission, read same-channel context,
   retrieve time windows with paging, read adjacent PDF pages, and follow up.
3. Separates original claims about execution, time, crew and cause; returns
   supported/corrected/rejected/unresolved decisions with exact source citations.
   It can split over-combined events or explicitly link duplicates at the same
   lifecycle stage. Generated candidate text is searchable for deduplication
   but cannot be cited as factual evidence.
4. Produces a revised event history, label corrections, and a persistent audit
   record. Every approved event needs supported claims and exact retrieved-source
   quotes; invented quotes and planning-only evidence fail validation. Execution
   time remains unknown unless separately supported. Undated retrospective PDF
   evidence needs a supported execution time to place its event on the timeline.
5. Reconciles labels on affected utterances after global event investigations,
   so an overturned event cannot silently remain asserted in its original topic.

Search sources include full COMM/PAO text, full available photo captions,
itinerary/science/LTP/video reference catalogs, and page text from the two PDFs
under the repo's `pdfs/`. The overview timeline is marked **planned**; the
preliminary science report is retrospective evidence. Searchable PDF text is
cached using the original PDF hash. Missing PDFs or pages with no extractable
text are reported in `source_warnings`; no OCR or visual interpretation is implied.
Additional original-source records can be supplied with repeated
`--reference-jsonl PATH` arguments to the direct script. Each JSONL record needs
unique `id`, ISO UTC `t`, `text`, `origin`, `kind` (`transcript`, `reference`, or
`caption`), and boolean `planned_only`. A reference intended as execution evidence
also needs `as_executed=true`; catalog/media metadata alone cannot prove execution.

Outputs under `processed/transcripts/1f_investigation/`:

- `utterances.jsonl`, `milestones.jsonl`, `comm_enriched.csv`, `PAO_enriched.csv`:
  revised rows and only evidence-supported events. Rows with unresolved labels
  remain present and flagged. Old per-utterance catalog links are cleared rather
  than inherited as verified facts; canonical event IDs and citations live in
  the investigated JSONL.
- `candidates.jsonl`, `investigations.jsonl`, `unresolved_events.jsonl`: all original
  and discovered proposals, decisions, tool retrievals, exclusions and gaps.
- `report.json`: source warnings, pending/exhausted investigations, unresolved
  event/label counts, lineage and artifact hashes.
- `work/{snapshot}/`: per-task checkpoints and an exact searchable-source snapshot.

Defaults: 8 retrieval/validation turns per task, low reasoning, 16,384 output
tokens per request. A task can finish sooner. A failed transport request remains
pending and is retried on the next invocation. Exhausted investigations remain
explicitly unresolved; `--retry-unresolved` retries them. There is no automatic
claim of exhaustive historical correctness merely because processing finished.
Use `--retry-reasoning-effort medium` and optionally `--retry-max-tokens 32768`
with `--retry-unresolved` to change generation settings for previously failed
or unresolved tasks. Keep the original base settings to reuse the checkpoint
snapshot. New work uses the base settings, and saved results record the actual
settings of each attempt in the request diagnostics and resulting checkpoint.
Completed review/event results returned without the `action=finish` wrapper are
accepted through the same coverage, source-search and citation validation as
wrapped results; unsupported evidence still fails validation.

To recover excluded utterances without retrying existing event investigations,
use selective review recovery from the repository root:

```powershell
.\src\server-batch\.venv\Scripts\python.exe src/server-batch/1_comm/1f_investigate_history.py --mission artemis-ii --backend hgx --retry-unresolved --retry-types coverage labels --retry-batch-size 5 --retry-reasoning-effort low --retry-max-tokens 32768 --hide-reasoning
```

This keeps the original snapshot, base coverage size and successful checkpoints.
Only failed reviews or explicitly unresolved targets are retried. Failed review
batches are split into five-target groups, checkpointed independently under
`work/{snapshot}/reviews/`. Validation failures exclude only that group's
targets; successful groups survive interruption and are reused on resumption.
Transport failures and `--limit` still leave the parent task pending. The limit
counts small groups, not their parent batch. Retry settings also apply to new
label groups generated by recovered coverage or changed investigation findings.
New discoveries from recovered coverage are still investigated before entering
milestones; existing unresolved events remain cached. All citations retain the
same evidence checks. Empty crew lists are valid and do not cause exclusion.
Label reconciliation uses a focused label-only prompt; tag and citation validation
errors identify the offending value so the model can repair it on its next turn.
Original sources cited in existing investigation findings are supplied directly,
including those outside the small target window. Generated findings remain leads;
all returned quotes must still match the supplied original text exactly.
The selective options require an existing snapshot; keep base settings unchanged.
After recovery finishes, rerun 1g to include the newly reviewed utterances.
The runner forwards `--retry-types` and `--retry-batch-size` to 1f.

#### Resuming and testing small amounts of work

Run the same command to resume completed work items. Changes to source content,
base model settings, base batching or retrieval code produce a new snapshot.
Retry settings and small recovery groups preserve the original namespace;
incompatible investigation-result changes require a new investigation version. A stage's
exclusive writer lock prevents concurrent writers; source and artifact hashes
prevent downstream steps from consuming changed or stale inputs. These stages
should wait for 1d, except for 1e's explicit `--follow` mode. Completed failed
batch repairs are reused as the producer's append-only queues grow.

```powershell
# Run at most three recovery work items; later run the same command without --limit.
uv run --project src/server-batch python src/server-batch/1_comm/1e_recover_enrichment.py --mission artemis-ii --source-run RUN_ID --limit 3 --hide-reasoning
# After 1e completes, inspect two investigation work items without a full run.
uv run --project src/server-batch python src/server-batch/1_comm/1f_investigate_history.py --mission artemis-ii --limit 2 --hide-reasoning
# Resume investigation and revisit exhausted/unsupported work.
uv run --project src/server-batch python src/server-batch/1_comm/1f_investigate_history.py --mission artemis-ii --retry-unresolved --hide-reasoning
```

`--limit` exits nonzero when work remains and marks its export incomplete, so the
next stage cannot mistake a sample for a finished dataset. The runner's
`--history-limit N` forwards the same limit to 1e/1f. Backend/model, reasoning,
token ceiling and display options are also forwarded to both. `--force` and
`--reassess-model` still apply only to 1d.

#### 1g: Hierarchy (`1g_mission_context.py`)

Consumes only a completed, hash-verified 1f dataset matching its 1e recovery
snapshot. It does not fall back to raw 1d milestones. It preserves investigated
event/stage identities instead of fuzzy-merging them again. Empty crew lists
stay unknown rather than being expanded to everyone. Unresolved labels are
excluded from asserted activity clusters, and reference-only sub-blocks remain
explicitly unconfirmed. Planned blocks are marked executed only when their own
investigated lead establishes execution; nearby talk or a photo does not suffice.

Writes `processed/transcripts/1g_hierarchy/mission_activity_tree.json` and
`processed/transcripts/1g_hierarchy/mission_activities.json`, with unresolved counts in the
summary and investigation provenance on milestone reports. These are processing
artifacts for review and subsequent publication, not automatic web updates.

The version 2 hierarchy separates event chronology from report/audio time:

- An explicitly supported `event_time` determines a milestone's activity placement.
  Otherwise its placement is explicitly marked `time_basis=report_time`.
  `first_reported_t`, `last_reported_t`, the original reports, and
  `report_utterance_ids` retain the audio/source links, even for a later recap.
- Prelaunch material is grouped by UTC calendar date. FD01 starts at launch;
  subsequent flight days use the planned crew-day boundaries, with 24-hour
  fallback days after the final reference boundary. Blocks are split at those
  boundaries. Discovered activity is also split at 30-minute gaps or a two-hour
  maximum span; it is not collected into one mission-wide block.
- Every reviewed utterance belongs to exactly one block and one sub-block.
  `utterance_ids` includes both substantive evidence and low-importance context;
  `substantive_utterance_ids` and `context_utterance_ids` distinguish them without
  losing acknowledgments or comm without a crew designation. Context-only
  segments are marked `context_only=true`.
- Crew tracks use each investigated milestone's own crew attribution, or an
  individual reviewed utterance's topic and crew. A cluster's title is never
  copied to everyone mentioned nearby. Unknown crew remains in the mission
  timeline without being assigned to a person. Entries identify their
  `source_type`, milestone IDs, report timestamps and utterance IDs.
  Adjacent discussions for one crew member can be grouped when their category
  and tags agree; the group retains every original `topics` entry and source UID.
  Discussion groups are explicitly marked `reported_discussion`.
- Composite planned groups expose their `constituent_activities` and use
  `partially_executed` when completed/started activities coexist with cancelled
  ones. `start_utc`/`end_utc` delimit the displayed day segment, not an inferred
  execution interval. `confirmed_start_utc` requires explicit-time start
  evidence; `confirmed_end_utc` requires explicit-time completion evidence.
  These refer to the confirmed constituent activities. A schedule delta is
  emitted only when a supported start exists, including starts outside the
  planned window. Each segment retains its original `planned_block_id`.
  Undated late reports link to their old plan through
  `related_planned_block_ids`, without recreating that planned activity on the
  report day.

Before writing, 1g verifies unique coverage of every reviewed utterance and
milestone, day containment, and source-specific crew attribution. The outputs
include `schema_version=2` and the exact 1f artifact hashes for lineage. The
summary counts mission flight days separately from prelaunch date groups.

## How to Run

> ⚠️ This pipeline is **Artemis II only**. Artemis I was uncrewed — skip all steps.

Run from `src/server-batch/`:

```bash
# Run all steps in order
uv run run_all.py --mission artemis-ii

# Run individual steps
uv run run_all.py --mission artemis-ii --step 1a
uv run run_all.py --mission artemis-ii --step 1b
uv run run_all.py --mission artemis-ii --step 1c

# Run steps directly (useful for flags like --force or --test)
uv run python -m 1_comm.1a_download_ia_zips --mission artemis-ii
uv run python -m 1_comm.1b_transcribe --mission artemis-ii
uv run python -m 1_comm.1b_transcribe --mission artemis-ii --test 5   # transcribe only 5 files
uv run python -m 1_comm.1b_transcribe --mission artemis-ii --force    # re-transcribe all
uv run python -m 1_comm.1c_web_comm --mission artemis-ii
```

**Required env vars:** `HF_TOKEN` (Hugging Face token for WhisperX diarization), GPU with CUDA for step 1b.
