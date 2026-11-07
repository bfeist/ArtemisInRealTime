# COM consolidation implementation

Date: 2026-11-07. Implements the [joint audit and plan](comm-pipeline-audit-and-consolidation-plan.md) against repository baseline `b9b9f4d`.

Publication follow-up: the existing local WSL rsync script is now adopted as
the simpler [direct asset publication workflow](asset-publication.md), per the
user's preference. It uploads/replaces web files directly, with an optional
dry run; it does not require remote release verification or activation steps.
The original implementation measurements below predate this deployment integration.

The migration preserves the accepted transcript and completed research without model calls. Source preparation, impact planning, optional bounded research, reviewed PAO playback selection, and staged publication are separate operations. IA PAO has **not** been declared equivalent to the full YouTube broadcast: measured clock differences and unresolved opening/closing coverage prevent that cutover.

## Implemented paths

| Area | Implementation and behavior |
| --- | --- |
| Existing work | `1_comm/transcript_dataset.py` freezes an inventory and imports immutable legacy artifacts under an explicit compatibility contract. Exact source/assessment mappings and evidence resolution are checked; incomplete imports cannot publish. |
| Source reconstruction | `1_comm/prepare_dataset_inputs.py` reconstructs private inputs into a new directory, including CRLF serialization, exact historical hash comparison, PDS precedence audit, duplicate identities, and current-loader differences. It does not re-encode or modify normalized originals. |
| PDS integration | `comm-prepare` explicitly runs `7f -> 1b3 -> 1b4 -> 1c`. `1b3` imports supplied PCD text/speakers/audio; `1b4` imports corrected voice-loop text and timing. PDS discovery, download, and verification remain explicit upstream operations. |
| Routine work | `run_all.py` defaults to `1c, 1i`, excluding transcription, downloads, uploads, discovery/research, and YouTube deduplication. `refresh_assets.py` includes PDS transcript imports before COMM preparation. Existing legacy publication uses `--dataset-manifest`; absence of current assessments does not cause automatic inference. |
| Reuse | `assessment_reuse.py` compares semantic rows and source-local context, imports accepted legacy labels with provenance, and writes an impact plan. `1d/1e/1f` cache supplied dependencies rather than invalidating solely on whole input-file hashes. Ten-minute partitions bound regrouping after insertions. |
| Model limits | Actual model clients require a matching impact plan and positive work limit. Transport calls, including repair/tool rounds, consume the configured request cap. No real model clients were used for this migration. |
| PAO/video evidence | `2m` inventories coverage; `2n` imports split/upload identities; `2o` measures original-to-split AAC packet alignment; `2p` compares cached transcripts and performs bounded acoustic checks. Candidate coverage is distinct from verified synchronization. |
| Reviewed source policy | `2q_apply_pao_policy.py` creates a new dataset from reviewed feed intervals and verified playback evidence. It preserves accepted wording, UTC, UIDs, assessments, and history; only playback metadata changes. Unknown intervals retain their existing selection. It never activates a release. |
| Publication | `1i` builds an immutable allowlisted release, selected AAC/M4A audio, compact indexes, deterministic gzip representations, optional CSV exports, and verified playback metadata. Referenced prepared videos are hash-pinned shared dependencies, avoiding a 95 GB copy per release. |
| Cutover/rollback | Staging verifies dependencies before finalizing a release. Activation verifies the selected and previous releases under a lock, then atomically replaces `active-release.json`. First activation over legacy assets requires a semantically equivalent preserved baseline. No automatic deletion or retirement occurs. |
| Client | Transcript and timeline loaders pin the active release once per page. COMM/PAO audio uses the same release as its text; reviewed PAO rows can play selected audio or link to the uploaded video. The video test page reads an uploaded-part catalog and leaves gaps unmapped. Only a missing legacy catalog uses the historical fallback. |
| Local serving | Vite negotiates JSON gzip sidecars, emits `Vary: Accept-Encoding`, revalidates the active pointer, and marks immutable release paths cacheable. Audio/video byte ranges retain `206`/`Content-Range` support. This does not configure the production media host. |

## Real preserved dataset

All paths below are under `F:/_repos/ArtemisInRealTime_assets/artemis-ii/` unless otherwise stated. Generated holdings and receipts are outside the repository.

- Freeze: `processed/transcript-consolidation/legacy-freeze-20261107.json`. Inventoried 113,766 entries, hashing 70,960 transcript/work/index files. Large media received metadata inventory, not a claim of byte-for-byte hashing. Logical inventory bytes include shared/hard-linked holdings and are not reclaimable disk-space estimates.
- Accepted dataset: `processed/transcripts/datasets/legacy-20261107-v3/manifest.json`. All **52,870** published entries map exactly: **15,561 COMM + 37,309 PAO**, no unmatched or ambiguous entries, no missing assessments.
- Research preserved: **8,180** events and **154** unresolved outcomes. The evidence audit resolves **28,659** citations against **68,103** frozen source records. This preserves the historical verification contract; it does not claim a new version-3 investigation.
- PAO types: all **37,309** reproduce from the saved category projection: `pao_commentary -> commentary`, `pao_filler -> filler`, other categories -> `other`. The import records the rule, source artifact, and hashes. Newer subject classifications remain separate.
- Early incomplete import directories remain intact as failure receipts. The accepted import uses exact timestamp, recording, offset, duration, and raw-text or exactly reconstructed speaker-prefix matching. Literal bracket annotations and nested speaker names are not stripped heuristically.

`processed/transcript-consolidation/legacy-evidence-resolution-v3.json` records the evidence audit. The import and audit made **zero model requests**.

The final preservation check (`processed/transcript-consolidation/legacy-freeze-verification-20261107.json`) found all 70,960 original hashes unchanged, all 113,766 original paths present, and no metadata-only changes. New output directories were excluded from that comparison.

## Exact source recovery and incremental impact

`processed/transcript-consolidation/source-inputs-20261107/` contains the recovered projections and duplicate report:

| Artifact | Recovered SHA-256, matching the historical report |
| --- | --- |
| `comm.csv` | `b60f43d2352b2014af74fc17e4a9e5ef3f7704d05254f8741c1a8135e256202f` |
| `PAO.csv` | `411bf2350ecd97192bfab062a93ff8c6b275e32373b5c84bf9365a7a6652f7a5` |

The 15,619 raw COMM rows include 58 old-UID collapse groups, each with distinct audio sources/full identities. In every group, the old accepted row is the first source row. These are preserved alternative recording evidence, not disposable byte-identical files.

The current loader retains 15,584 COMM rows: 23 additional OE-2 occurrences and no missing accepted identities. Their available audio hashes differ from the corresponding OE-1 recordings. Including them is an intentional source-selection change, not a harmless serialization update.

The exact accepted baseline inherits all **52,870** assessments with no model work. Applying the current loader to recovered inputs would produce **52,893** rows, of which **52,800** assessments are reusable and **93** rows need review: 23 restored occurrences plus 70 existing rows with changed source-local context. The read-only estimate is **16 logical classification batches**. Repairs and research are additional work, subject to their own limits. Receipt: `processed/transcript-consolidation/recovered-inputs-inheritance-audit-v3-context600.json`; it supersedes the earlier conservative context estimate.

The staged baseline deliberately retains the accepted 52,870-row selection. The new occurrences are available in the recovery report for a later explicit decision; no OE-1 label has been blindly transferred to OE-2.

The same impact is reproducible through the CLI using
`processed/transcript-consolidation/current-source-rows-20261107.jsonl`.
`current-source-impact-20261107.json` is its generated `1d` work-plan preview
(16-request cap, zero requests executed), not authorization to run new analysis.
New source snapshots now emit their own `current-source-rows.jsonl` directly.

## PAO and video findings

The candidate inventory contains 3,282 cached IA Mission Audio recordings and 29 YouTube parts. Coverage envelopes alone do not establish equal speech, equal delay, or authoritative UTC. The preferred high-level policy remains IA within verified broadcast coverage, YouTube for explicitly bounded opening/closing/internal exceptions, and explicit unknowns where neither source is established.

The actual split files and upload state are in `D:/chunks`. Packet comparison against `D:/NASA Artemis II Live Mission Coverage m3kR2KK8TEs.mkv` verified all 29 original-to-split offsets. Stream-copy cuts include keyframe preroll. By part 29, the historical cumulative-duration clock differs from the measured original offset by **39.544262 seconds**. The public compatibility catalog keeps its existing clock as candidate; these measurements are not silently applied to transcript UTC.

The cached-text study compared 9,626 IA segments with 59,518 unfiltered YouTube segments. It found **6,900 unique exact phrase anchors**, with ambiguous phrases and unmatched passages retained in the report. Fifty representative acoustic candidates were tested with waveform and low-pass comparisons; the union contains **four bounded verified intervals** across parts 13, 16, and 26. Those matches expose a **10.45–11.33 second** discrepancy between the existing UTC clocks. Opening and closing acoustic checks remain unverified. Four short matches do not establish whole-mission equivalence.

Evidence is under `processed/pao_sync/`:

- `coverage-plan.json`: candidate mission-wide source intervals and explicit unknowns.
- `video-parts-manifest.json`, `video-parts.json`: actual upload identities and compatibility timing.
- `split-packet-evidence.json`, `video-parts-measured-manifest.json`: measured split offsets.
- `text-coverage-study-final.json`: bidirectional text coverage, per-part clock deltas, and unanchored runs.
- `alignment-study.json`, `alignment-study-lowpass.json`: bounded acoustic evidence.

No reviewed mission-wide source manifest or live IA-to-YouTube synchronization map was fabricated. `2q` is ready to apply reviewed boundaries and clocks without relabeling unchanged speech. Until that evidence is sufficient, the YouTube text/recordings remain retained. Routine builds already exclude the old `2k` pass; it is documented as a historical audit tool with unsafe global speaker-promotion behavior, not a recommended selection algorithm.

## Operating limits and remaining gates

1. **Research compatibility:** historical investigations retain their original contract. The current investigation cache conservatively depends on the full searchable evidence corpus so new contradictory evidence cannot be missed. Claim-specific retrieval dependency narrowing remains future optimization. The impact planner reports unknown research coverage when no dependency ledger is supplied. Classification estimates assume batches of at most 50 rows and 600-second partitions; overriding the batch size changes the estimate. The runtime also checks the complete supplied target/context group and known labels, so it may conservatively invalidate more cached groups than the row-window preview predicts.
2. **Request budgets:** the cap is per stage invocation, not a durable shared spending ledger. Selecting three model stages can consume up to three caps; rerunning resets the cap. Work limits are also batch/task limits, not forecasts of total transport calls. Keep the explicit impact plan and review stage selection before using live models.
3. **Source authority:** resolve the UTC discrepancy and verify opening/closing/internal handoffs before promoting IA PAO. The four corrected-loop boundary overhangs remain separate review items. No bulk ASR rerun is justified by the current findings.
4. **Hosting:** production deployment, CORS, negotiated compression, caching, and range responses have not been verified. The local Vite implementation and HTTP tests are not evidence of CDN behavior. CSV consumers outside this repository are unknown; exports remain opt-in.
5. **Retention:** no original recordings, historical assessments, downloads, old web assets, or previous releases were deleted. Reclaiming local storage requires a separate keep/retire receipt. Shared prepared video must remain unchanged while any retained release references it.

Operational commands and contracts are in the [batch README](../README.md#preserve-and-publish-an-imported-baseline), [COMM README](../1_comm/README.md), and [video README](../2_video/README.md). Use the accepted `legacy-20261107-v3/manifest.json` for zero-model publication; do not point the publisher at an incomplete earlier import or run all historical stages.

## Staged baseline and hosting measurements

The real release is staged at `web/releases/legacy-preserved-20261107/`. Staging completed full hash/reference verification, including the shared prepared videos. Its `transcripts.json` is semantically identical to the prior root publication. The active pointer was not created or changed; this is a verified local staged release, not an Internet deployment.

| Item | Measured result |
| --- | --- |
| Release objects | 1,992 payload objects, plus `release-manifest.json` |
| Release logical bytes | 974,938,265, including JSON gzip sidecars |
| Selected COMM audio | 1,968 files, 934,027,712 bytes; no missing references |
| Shared prepared video | 220 unique files, 95,273,531,669 bytes, referenced and hash-pinned rather than copied into each release |
| Transcript JSON | 18,734,858 bytes compact; 3,269,698 bytes gzip, versus 26,675,675 bytes in the prior uncompressed file |
| All JSON gzip representations | 5,797,683 bytes |
| Timeline tracks | Main timeline plus all four referenced crew tracks |
| Uploaded video parts | 29 actual upload identities; compatibility clock remains candidate |
| Live PAO synchronization mappings | 0; unresolved evidence has not been promoted |

Local HTTP checks through Vite verified gzip negotiation and refusal, content type, `Vary`, immutable caching, a real `404` for the absent pointer, and byte-identical `206` responses for IA AAC and PCD/loop M4A sources, including a long loop recording. The actual TypeScript transcript, timeline, crew, part-catalog and sync loaders successfully read the staged payloads. Receipts: `processed/transcript-consolidation/release-stage-20261107.json`, `release-http-validation-20261107.json`, and `release-loader-validation-20261107.json`.

The 95.27 GB of prepared IA/IO/PDS video remains part of the selected site's dependency set. This consolidation does not classify distinct visual footage as redundant merely because audio overlaps. The 372.87 GB YouTube download holding and the original/split broadcast files remain private source material, outside the upload allowlist. The old 13 unreferenced COMM files and optional exports are absent from the new payload; original assets remain on disk for rollback.

## Retention classification

These are retention roles, not instructions to move or delete directories whose
paths occur in historical manifests.

| Holding | Role after consolidation |
| --- | --- |
| Accepted imported dataset, freeze/import/coverage/release receipts | Active private evidence and provenance; preserve exact bytes |
| `1da_classification`, `1ea_labeled_history`, `1f_investigation` | Historical producing artifacts for the accepted baseline; retain source/work/request evidence and unresolved outcomes |
| Earlier enrichment/recovery/relabel results and checkpoints | Historical audit and potential reusable work; retain actual contracts, never disguise them as new stage output |
| Cached IA/YouTube transcripts, supplied PDS text, raw source media and upload-state files | Active source archive and reconciliation evidence, including fallback intervals and unselected occurrences |
| `processed/comm_audio` and new semantic caches | Reusable derivatives/analysis cache; preserve while referenced or until a separate verified retention decision |
| Reconstructed CSV/JSON projections and optional public CSV exports | Reproducible projections; retained receipts establish exact lineage, public exports are optional |
| Existing root web assets and retained immutable releases | Rollback dependencies; no retirement while referenced |
| Unreferenced web audio and superseded public indexes | Excluded from new release allowlist; physical cleanup remains a separate verified action |

## Validation

- Python integration/regression suite: **210 tests run, 209 passed, one skipped**. Includes legacy import/reuse, PDS PCD/loop authority, audio preparation, reviewed PAO policy, alignment, release failures/rollback, runner boundaries, history/timeline, and existing PDS/video acquisition fixtures. Model/network work in those tests uses fixtures/mocks.
- Frontend: **42 tests passed** across ten files, including release pinning/failure behavior, filters/speakers, PAO playback and video links, UTC-midnight part seeking, coverage gaps, and local HTTP gzip/range contracts.
- TypeScript checking and production build passed. Changed TypeScript files pass ESLint; repository-wide ESLint has one pre-existing hook-dependency warning in `TimelineTest2.tsx`.
- `npm run test:all` stops at **121 pre-existing CSS formatting errors** in `MissionEventsTest2.module.css`. That file's Git object hash exactly matches `HEAD`; it was not modified. The remaining checks were run independently as listed above.
- `git diff --check` passed. Original artifact preservation and real staged HTTP/loader checks are recorded above. No commits, HGX/WhisperX runs, source deletion, remote uploads, production activation, or reviewed source-policy promotion were performed.
