# Artemis II PDS retrieval and integration plan

Prepared October 7, 2026. Status updated October 10: retrieval implementation added; the background transfer ended October 8 with **87,126 of 87,128 files / 2,576,780,169,111 bytes verified**. Two files remain unavailable, so the available archive is incomplete.

Final verification checked **43,561 labels** with **zero reference issues**; a separate full rehash was not run. Integration catalogs were built for **43,340 photo products, 22 videos, 56 audio products, 228 transcript products, and 43,480 science/support products**. The missing files are `artemis2_mission_audio/data_audio_pcd/fd06/art002a000052_pcd1_src_v01.m4a` and its `.xml` label (**36,931,609 bytes combined**). Both returned HTTP 403 during the original run and five attempts each on October 10; fresh resolver checks returned the same URLs and HTTP 403. Retry with step `7d` when the publisher makes them available, then rerun verification and integration.

Execution snapshot: discovery enumerated **87,128 files / 2,576,817,100,720 bytes** across the four available bundles. Reconciliation verified **222,892,096,828 bytes** available for same-volume hard-link reuse and **53,750,341 bytes** for ZIP extraction; planned network retrieval is **2,353,871,253,551 bytes**. Two decompression errors in the existing `nef_all/FD_05.zip` are recorded for canonical network fallback. The live pilot passed label downloads from every available bundle, a local hard link, a ZIP extraction and a 20 MB browse image.

All archive downloads, partials, inventory state, manifests and logs reside on D:. Run configuration and commands are in [`../7_pds/README.md`](../7_pds/README.md). Progress is saved to `D:/ArtemisInRealTime_assets/artemis-ii/processed/pds/reports/latest.json`; process/log paths are in `processed/pds/logs/current-process.json`. The sections below retain the original design and acceptance criteria. SPICE, iPhone imagery and HERA remain tracked as pending; browser playback and timeline selection have separate status from archival completeness.

## Objective

Retrieve every publicly available file in the Artemis II science release, including source media, processed products, browse products, labels, inventories, transcripts, documents, and ancillary data. Reuse verified existing files, preserve distinct products and versions, and integrate the results through `src/server-batch`.

New bulk assets belong under `D:\ArtemisInRealTime_assets`. Existing assets must remain usable throughout the work. Completion means a verified local holding for every file in a saved release manifest, with an explicit list of products still awaiting public release.

## 1. Findings that determine the approach

### Release scope and availability

The supplied **Artemis II Preliminary Lunar Science Report**, pages 29-31 and Appendix 2 on page 128, identifies five PDS bundles. Its Table 4.1 reports **11,591 source files / 814.51 GB**. That is a source-data summary, not the size or file count of the downloadable PDS archive.

The [PDS Geosciences Artemis II landing page](https://pds-geosciences.wustl.edu/missions/artemis2/index.htm) confirms the October 7 release and explicitly says **SPICE is delayed**. The [Lunar Science Data User Guide](https://pds-geosciences.wustl.edu/artemis2/urn-nasa-pds-artemis2_mission/document/artemis2_data_user_guide.pdf), section 1.1, also says **iPhone imagery is being prepared for a later release**. Track both as pending; their absence must not prevent retrieval of the available release or silently become a claim of full mission completeness.

| Bundle | Entry point | Retrieval scope |
| --- | --- | --- |
| `artemis2_crew_camera` | [CIS Archive Explorer](https://pds-imaging.jpl.nasa.gov/tools/atlas/archive-explorer?mission=artemis2&bundle=artemis2_crew_camera) | `data_source`, `data_raw`, `data_processed`, `browse`, and all supporting files |
| `artemis2_orion_camera` | [CIS Archive Explorer](https://pds-imaging.jpl.nasa.gov/tools/atlas/archive-explorer?mission=artemis2&bundle=artemis2_orion_camera) | `browse_source`, `data_raw_image`, `data_video`, and all supporting files |
| `artemis2_mission_audio` | [CIS Archive Explorer](https://pds-imaging.jpl.nasa.gov/tools/atlas/archive-explorer?mission=artemis2&bundle=artemis2_mission_audio) | PCD audio, voice-loop audio, both transcript collections, annotations, and supporting files |
| `artemis2_mission` | [GEO directory](https://pds-geosciences.wustl.edu/artemis2/urn-nasa-pds-artemis2_mission/) | Entire document and environmental-data collections, bundle/collection labels, inventories, README, and checksum manifest |
| `artemis2_spice` | [NAIF bundle location](https://naif.jpl.nasa.gov/pub/naif/pds/pds4/artemis2/artemis2_spice/) | Entire bundle when published, including kernels, labels, inventories, documentation, and metakernels |

This scope covers the complete public science release described by the PDF. Continue the existing IA, EOL, Flickr, NASA Library, and YouTube intake separately for broader mission coverage. The user guide also mentions a separate future HERA radiation archive; record that as an additional future source, not an available file set in today's five bundles.

### Live size estimate

A metadata-only query of the public [CIS Atlas search endpoint](https://pds-imaging.jpl.nasa.gov/api/search/atlas/_search) on October 7 returned the following. Counts are indexed **files**, including labels and supporting files, not observations or unique images.

| Bundle | Indexed files | Indexed bytes | Decimal TB |
| --- | ---: | ---: | ---: |
| Crew camera | 82,642 | 2,531,854,498,514 | 2.5319 |
| Orion camera | 4,100 | 43,442,915,034 | 0.0434 |
| Mission audio | 360 | 847,738,089 | 0.0008 |
| **CIS subtotal** | **87,102** | **2,576,145,151,637** | **2.5761** |

The crew processed collection alone accounts for about 1.694 TB; raw TIFFs account for another 568 GB. Include both, even if their source NEFs already exist locally. Add the GEO bundle and later releases to the capacity calculation. These index aggregates are preliminary sizing evidence: the implementation must enumerate every page, reconcile inventories, deduplicate remote identities, and compute an exact manifest before reporting final totals.

Source-count differences between the report and the current archive must be documented rather than filled with guessed filenames. For example, the indexed crew collections each contain 20,660 files; that is not the report's 10,407 crew observations. The report also describes original MXF video while the archive distributes converted MP4 products. Retrieve all actually published files and record unpublished source formats as gaps where relevant.

### Existing holdings

A read-only filesystem scan found these logical file sizes on D:. ZIPs and extracted contents are both counted; these are not unique-byte totals or verified reusable-byte totals.

| Existing path under `D:\ArtemisInRealTime_assets` | Files | Approx. decimal GB |
| --- | ---: | ---: |
| `5_Crew-Captured-Imagery` | 10,405 | 224.11 |
| `nef_all` | 2,755 | 79.88 |
| `photos` | 14,094 | 38.78 |
| `vehicle` | 55 | 88.20 |
| `videos` | 11 | 8.84 |
| Root ZIP and tracking workbook | 2 | 215.56 |

Important existing containers include `5_Crew-Captured-Imagery.zip`, the flight-day ZIPs in `nef_all`, and `vehicle\1_PCD-Audio-Files.zip`, `9_Voice-Loop-Audio.zip`, `SAW3.zip`, and `Vehicle Camera Video (SAW3)-selected.zip`. Inventory archive members before deciding to fetch their contents again. Preserve XMP files and camera voice notes as well as images.

The active mission tree also exists at **`F:\_repos\ArtemisInRealTime_assets\artemis-ii`**, including about 269.77 GB of raw photos, 65.32 GB of comms, processed metadata, and web outputs. `config.py` defaults to this sibling assets root when `DATA_DIR` is unset. The inspected environment had no `DATA_DIR` override. `vite.config.mts` also serves the sibling assets root directly.

Free space at inspection: **D: 5.942 TB / 5.404 TiB**, **F: 0.767 TB / 0.698 TiB**. Recheck immediately before transfer. D: appears adequate for this release; F: is unsuitable for its bulk payloads.

One concrete reuse check succeeded:

```text
Existing NASA ID: art002e023072
PDS filename: art002e023072_nkd5015_src_v01.nef
Size: 16,836,672 bytes
PDS MD5: b347f2fe7de9a4c3d9b85a5bd163ff69
D: FD_01/D5_15/art002e023072.NEF: same size and MD5
F: mission raw crew copy: same size and MD5
```

That confirms the feasibility of checksum-based reuse across renamed files. It does not establish equivalence for the rest of the holdings.

## 2. Storage and integration layout

Add a configurable **`PDS_ASSETS_DIR`** with a local value of `D:/ArtemisInRealTime_assets`. Keep the existing `DATA_DIR` for current processing and web output. Do not change `DATA_DIR` to D: as a shortcut: doing so would separate the pipeline from its existing metadata and the development asset server.

Proposed layout:

```text
D:/ArtemisInRealTime_assets/
  [existing folders retained]
  artemis-ii/
    raw/pds/
      artemis2_crew_camera/<original archive-relative paths>
      artemis2_orion_camera/<original archive-relative paths>
      artemis2_mission_audio/<original archive-relative paths>
      artemis2_mission/<original archive-relative paths>
      artemis2_spice/<original archive-relative paths, once released>
    processed/pds/
      inventory.sqlite
      manifests/<discovery-run-id>/
      reports/<run-id>/
      checkpoints/
    staging/pds/<run-id>/<relative path>.part

F:/_repos/ArtemisInRealTime_assets/artemis-ii/
  processed/pds/             # integration catalogs referencing D: files
  processed/photos_ledger.jsonl
  processed/transcripts/     # existing and imported transcript products
  web/                      # existing web output contracts
```

Preserve PDS filenames and directory structure so detached XML labels still resolve their data files. Integrate through catalogs and the existing media ledger, rather than copying terabytes into the old photo directories. Record original locations and all aliases.

For a checksum-identical existing file on D:, prefer a same-volume hard link at its PDS location if the filesystem supports it and the source is treated as immutable. Otherwise use a verified copy. Hard links are shared bytes: never edit metadata in place through either name. A matching file found only on F: can be copied and verified onto D: without network transfer; a hard link cannot cross drives. Do not delete or relocate existing files during ingestion.

## 3. Discover everything before transferring bulk payloads

1. Save dated snapshots of bundle labels, collection labels/inventories, checksums, release notes, and the user guide. Record source URLs and content hashes.
2. Enumerate all files in each available bundle, including root files and browse collections. Do not use a lunar-target, flight-day, extension, or image-only filter: those can omit valid products.
3. Use the CIS Atlas API for the three CIS bundles. A public JSON POST to `https://pds-imaging.jpl.nasa.gov/api/search/atlas/_search` worked without credentials during inspection. Filter by `archive.bundle_id` and `archive.fs_type=file`; request `uri`, `archive`, and available PDS identity fields. The index exposes byte size and MD5 for sampled files.
4. Implement complete pagination using the service's supported stable cursor/PIT/search-after or scroll mechanism, validated with a small request first. Do not rely on a single result page, default limits, or repeated `from` offsets beyond the service window. Store discovery checkpoints, detect repeated pages, and compare unique enumerated counts with advertised totals.
5. The tested download resolver is `https://pds-imaging.jpl.nasa.gov/api/data/<atlas-uri>?output=url`. It returned an official CloudFront archive URL for a sampled label; the same API path without the query redirected to the label. Store the stable Atlas URI and resolve the download URL when needed. Do not synthesize CDN paths from a filename or assume current URLs will remain permanent.
6. Crawl the GEO bundle directory recursively within that bundle root. Fetch `urn-nasa-pds-artemis2_mission.md5` and reconcile it with the crawl and PDS inventories. Follow document assets and all referenced product files, including HTML companions. Avoid parent-directory traversal and unrelated missions.
7. Parse XML with namespace support and external entity resolution disabled. Read LID, version ID, file references, processing level, instrument, observation times, file size, and checksums when present. Some checksums may be in the index or manifest rather than product labels.
8. Cross-check bundle/collection inventories against discovered labels and payloads. Distinguish primary membership from references to products in another collection; preserve relationships without double-counting downloads. Missing references and unindexed published files become explicit reconciliation items.
9. Capture SPICE and iPhone as `pending_release`, not failed transfers. The tested NAIF URL returned 404 and the release page explains why. Refresh discovery on subsequent manual runs; scheduling is a separate optional action.

Use the actual archive as the download authority. The user guide's example filenames and directory diagrams are explanatory and contain inconsistencies; do not generate an assumed file list from them.

## 4. Local inventory and deconfliction

Inventory D: first, then the active F: mission tree and any additional asset roots resolved from configuration, including the configured YouTube root when it exists. Record scan errors and incomplete folders; inaccessible paths must not silently count as empty.

Store records in SQLite so tens of thousands of files, archive members, retries, and matches remain queryable and resumable. Suggested entities: discovery runs, remote files, PDS products, local files, archive members, matches, transfer attempts, and integration results.

For each remote file retain bundle, collection, LIDVID where applicable, archive-relative path, Atlas URI/source URL, expected bytes and checksum, local destination, verification status, and discovery date. For local files retain path, volume/file identity where available, size, modification time, format, normalized NASA ID, hash values, and provenance. ZIP member records also need container path, member path, uncompressed size, and CRC.

Use three different identities:

- **Scientific identity:** PDS LIDVID and processing role. Preserve every distinct product/version.
- **Byte identity:** full-file checksum and size. This decides whether a transfer can be skipped.
- **Observation identity:** normalized NASA ID, instrument/channel and time. This joins media in the application; it does not prove byte equivalence.

Reuse `shared/eol_naming.py` for legacy NASA IDs, and add a dedicated parser for PDS names such as `art002e023072_nkd5015_src_v01.nef`. Do not use the entire PDS filename stem as a NASA ID. Do not infer media class from `art002a` alone: PCD audio uses that prefix.

| Match outcome | Action |
| --- | --- |
| Same expected size and full upstream checksum | Reuse; materialize at the PDS path and retain source aliases |
| Matching ZIP member verified against the upstream checksum | Extract just that member to staging, verify, then adopt |
| Same NASA ID with a different format or processing role | Keep both: NEF, raw TIFF, processed TIFF, browse PNG, and JPEG are separate products |
| Same basename/size but unverified content | Hash and compare; do not skip solely on existence or size |
| Same NASA ID/format but different bytes | Preserve both, record conflict/version information, and download the public product to its own PDS path |
| No upstream checksum | Obtain the published checksum if possible; otherwise retain a weaker-verification status and fetch the canonical object when equivalence cannot be established |
| Truncated or mismatched existing destination | Retain evidence/quarantine that destination and replace via verified staging; do not overwrite a legacy source |

Hash candidate matches first, caching results by path/file identity, size, and modification time. Compute upstream MD5 where supplied and local SHA-256 for provenance. Inspect ZIP central directories without expanding whole archives; decompress candidate members as streams when verification is needed. ZIP CRC, timestamps, EXIF similarity, and perceptual hashes are candidate signals, not proof of equality.

Do not collapse two SAW video channels just because they look alike: the guide says both downlink channels are preserved. Likewise, identical observation times do not make separate audio tracks or transcript formats redundant. Maintain every remote path even when multiple paths share one physical byte sequence.

## 5. Resumable transfer and verification

Produce a dry-run report before bulk transfer with file/byte totals by bundle and collection, verified reusable bytes, local-copy bytes, extraction bytes, new network bytes, conflicts, pending releases, and unknown sizes.

Calculate capacity from **additional physical bytes**, including local copies, extraction output, partial files, temporary overlap, and processing output. Existing archive sizes cannot be subtracted from remote sizes until member equivalence is verified. Keep a configurable reserve of at least 500 GB on D: and separately budget F: web/processed growth. With no reuse, the currently indexed CIS payload leaves approximately 3.366 TB of D: free before other additions; this is an estimate, not a transfer guarantee.

Download in this order: manifests/labels/documents and transcripts, missing audio and browse products, missing original media, raw TIFFs, then processed TIFFs. All collections remain required; the order simply makes useful data available early.

- Start with four workers globally and two per host, with configurable bandwidth limits. Tune from measured throughput and throttling, not assumptions.
- Stream to `.part` files on D:. Persist expected size, checksum, source identity, validators and progress. Use finite connect/read timeouts, bounded retries, exponential backoff with jitter, and `Retry-After` support.
- Resume only when the server supports it and the object is unchanged. Validate `206` and `Content-Range`; if a range request returns `200`, restart instead of appending. Handle changed ETags, expired URLs, and `416` explicitly.
- Verify final byte count and the published checksum before atomically promoting a file. Record a local SHA-256 as part of verification. A `200` HTML error page must never be accepted as an image, label, or audio product.
- Revalidate destinations on reruns using persisted verification evidence; file existence alone is insufficient. Hold a run lock so concurrent workers/runs cannot write the same destination or manifest inconsistently.
- Validate downloaded/extracted paths against the intended root, including Windows case collisions, absolute paths, `..`, reserved names, and excessive path lengths. Preserve a deterministic mapping if a path cannot be represented directly.
- Report failed/unverified files with reasons and return a nonzero status for incomplete available data. Keep pending-release categories distinct from transfer failures.

For scale, 2.576 TB takes about 57.2 hours at a sustained 100 Mbps, 11.4 hours at 500 Mbps, or 5.7 hours at 1 Gbps before overhead and verification. Compute the operational ETA from the actual remaining-byte count and measured transfer rate.

## 6. Add the implementation to `src/server-batch`

Use existing Python, `requests`, `lxml`, standard-library SQLite/hash/ZIP facilities, mission configuration, and the `--mission` convention. Add dependencies only when a demonstrated requirement cannot be met by the existing environment.

Proposed modules and step IDs (not implemented yet):

| Step | Module | Responsibility |
| --- | --- | --- |
| `7a` | `7_pds/7a_discover.py` | Enumerate and snapshot public bundles; track pending releases |
| `7b` | `7_pds/7b_inventory_local.py` | Inventory existing roots and ZIP members; cache candidate hashes |
| `7c` | `7_pds/7c_reconcile.py` | Match remote/local identities and emit an immutable transfer plan |
| `7d` | `7_pds/7d_download.py` | Adopt verified local copies and download missing files with resume |
| `7e` | `7_pds/7e_verify.py` | Verify files, labels, references, checksums, and coverage |
| `7f` | `7_pds/7f_integrate.py` | Build photo/audio/video/science catalogs from verified products |
| `7g` | `7_pds/7g_report.py` | Emit completeness, conflicts, capacity, and pending-release reports |

Add `7_pds/__init__.py`, shared PDS parsing/discovery helpers, shared transfer and inventory helpers, and `7_pds/README.md`. Extend `config.py` with PDS paths and legacy inventory roots. Update the main README and `run_all.py` registry/groups.

Proposed groups:

```text
pds-plan       = 7a 7b 7c
pds-fetch      = 7d 7e 7g
pds-integrate  = 7f 4a 4b 4c 4d 4e 7g
pds-refresh    = 7a 7b 7c 7d 7e 7f 4a 4b 4c 4d 4e 7g
```

Runner changes are required, not just new registry rows:

- `run_step()` currently reconstructs `sys.argv` with only `--mission`. Add structured forwarding for relevant options such as plan ID, dry run, workers, bandwidth and byte limits; alternatively expose module-specific options only through direct module commands and document that clearly.
- The runner currently continues after a failed step. Stop dependent PDS steps when discovery, reconciliation, download or verification fails; still write the final diagnostic report. Independent bundles may continue under an explicit partial-run policy.
- Keep bulk PDS operations explicitly selected. Adding them to `STEPS` must not make an ordinary default run unexpectedly start a multi-terabyte download. Mark them as opt-in when selecting default steps.
- Make `pds-fetch` consume a saved plan with validated source/configuration fingerprints. Refuse an absent or incompatible plan. `pds-refresh` generates a fresh plan as part of its run.
- Do not include `4f_cleanup_web` in these groups. Validate new outputs before any separate cleanup operation.

Future command examples, after implementation:

```powershell
Set-Location F:\_repos\ArtemisInRealTime\src\server-batch
$env:PDS_ASSETS_DIR = 'D:/ArtemisInRealTime_assets'
uv run run_all.py --mission artemis-ii --step pds-plan
uv run run_all.py --mission artemis-ii --step pds-fetch
uv run run_all.py --mission artemis-ii --step pds-integrate
```

## 7. Integrate each data family

### Photos

Extend `shared/photos_ledger.py` and `4c_build_ledger.py` to represent multiple PDS product roles/versions for one NASA ID without replacing existing copies. The current closed `SOURCES` set and one-copy-per-source schema cannot represent the entire PDS holding by adding a single `pds` copy slot. Keep a product catalog plus selected ledger copy references, with explicit source-role keys or a compatible multi-product extension.

Teach `4a_extract_all_exif.py` and the ledger to read verified PDS catalogs on D:. Existing raw-crew fallback logic chooses the mission tree when nonempty and ignores the legacy tree; it must not become the mechanism for discovering the combined holding. Audit legacy-only candidates explicitly.

Record public PDS membership as publication evidence for the matching product/NASA ID. Preserve existing export evidence and unrelated legacy-only records. Add PDS label UTC with provenance and per-instrument precedence; compare it with current overrides and report conflicts instead of globally replacing all times.

Do not run Bayer raw TIFFs through a normal RGB TIFF path. Preserve NEF-based bracket metadata and existing rendering by default; support processed PDS TIFFs as selectable render sources with regression checks. Build web tiers only for affected records, and keep all archival variants even if the website shows one preferred image.

### Audio and transcripts

Import supplied CSV/SRT transcripts and speaker information before considering new transcription. Preserve their provenance separately from existing WhisperX output. PCD embedded timestamps represent recording **end** time; use label `start_date_time` for timeline placement. Do not apply the existing comm filename CDT-to-UTC offset to PDS UTC timestamps.

Reconcile overlapping PDS voice loops and existing IA comms by channel, time coverage and content. Keep both archival sources while selecting timeline segments without duplicate playback. Overlap alone is not proof of identical audio. Camera voice-note text may be included in image metadata rather than a separate public WAV collection; retain existing WAVs and link the public metadata.

### Video

Create a PDS video catalog with NASA ID, channel, start/stop UTC, duration, codec, product version and file path. Preserve both SAW channels and original MP4 bytes. Join existing `videos/artemis-ii` holdings by checksum first, then record related encodings as separate representations.

`2g_web_video.py` currently emits IA and YouTube catalogs; merely downloading PDS MP4s will not expose them. Add an explicit PDS integration path and browser-compatible serving support when generating playable output. Generate proxies only when needed and budget them separately.

### Documents, environment, and SPICE

Create a searchable science-assets catalog for annotations, OTR JSON, LTP versions, user guide, OSDMP, label definitions, lens documentation, and environment tables. Preserve original files, units, timestamps, sensor IDs, and product labels. Download complete environment records, including prelaunch/postflight measurements; do not filter discovery to `mission_start`/`mission_end`.

When SPICE is released, archive it in full and resolve metakernel dependencies. Integration into `5_trajectory` is a separate validated adapter: compare frame, center, time scale, units and coverage against the existing OEM trajectory before changing the preferred trajectory source. Preserve OEM inputs and existing documented coverage gaps until the new products establish otherwise.

## 8. Implementation sequence and acceptance criteria

1. **Discovery and inventory:** implement `7a`-`7c`, configuration and report schemas. Enumerate all available files and local candidates without fetching bulk media. Resolve index/inventory discrepancies and produce a capacity budget.
2. **Transfer pilot:** implement `7d`-`7e`; exercise one existing checksum match, one ZIP-member match, one new file from each bundle, and a bounded larger file. Test interruption/resume and a changed remote object before expanding the run.
3. **Full retrieval:** run the saved plan across all available collections. Resume until every available file is verified or explicitly reported as unresolved. Refresh discovery once at the end to catch release-day additions without changing the original snapshot silently.
4. **Pipeline integration:** implement `7f`, media adapters and runner groups. Build affected processed/web outputs; review changes in counts, timestamps and selected media. Preserve prior manifests and output snapshots for rollback.
5. **Later additions:** use the same discovery/reconciliation workflow for iPhone, SPICE and corrections. New LIDVIDs and changed checksums create new work; unchanged verified objects create none. No background schedule is created by this plan.

Required focused tests for the implementation:

- Pagination over multiple pages; duplicate/stale records; missing inventory members; namespace-aware labels; multiple files per product; unexpected file formats.
- PDS and EOL ID normalization; same observation with different roles; same basename with different bytes; both SAW channels retained.
- Local reuse, ZIP-member streaming, checksum mismatch, interrupted transfer, ignored Range request, changed validator, low disk space and a second concurrent run.
- Preservation of legacy photo sources, public-release evidence, PCD start-time interpretation, transcript overlap handling and stable existing web records.
- A second completed run downloads zero unchanged payload bytes and does not create duplicate ledger entries or physical copies unnecessarily.

Completion reports must show, per bundle/collection: expected and verified counts/bytes; downloaded, locally reused and locally copied bytes; unresolved conflicts; checksum assurance; missing label references; and pending releases. Reconcile these against the saved manifest, not the report's source-data totals.

The available October 7 snapshot is complete only when every enumerated required file is verified and integrated into an appropriate catalog. Full five-bundle/iPhone coverage remains explicitly pending until those releases arrive and pass the same checks. Archival completeness and browser presentation are separate reported statuses; every science file needs catalog integration even when it has no current website viewer.

## Sources and evidence

- Supplied PDF: `F:\_repos\ArtemisInRealTime\pdfs\Artemis II_Preliminary Science Report (1).pdf`, especially pages 29-31 and 128.
- [PDS Artemis II release page](https://pds-geosciences.wustl.edu/missions/artemis2/index.htm), checked October 7, 2026.
- [Artemis II Lunar Science Data User Guide](https://pds-geosciences.wustl.edu/artemis2/urn-nasa-pds-artemis2_mission/document/artemis2_data_user_guide.pdf), especially sections 1.1, 1.3, 1.5, 4, 5.3, 5.4 and appendices C/D. PDF page numbers are one greater than printed page numbers in this guide.
- [CIS Atlas API](https://pds-imaging.jpl.nasa.gov/api/search/atlas/_search): live file-count/byte aggregates and sampled file metadata. Query used `archive.mission=artemis2`, `archive.fs_type=file`, terms aggregations on bundle/collection, and sums of `archive.size`; all five shards succeeded and no aggregation buckets were omitted.
- Repository inspection: `config.py`, `run_all.py`, `shared/photos_ledger.py`, `shared/eol_naming.py`, photo steps `3l` and `4a`-`4f`, video step `2g`, comm scripts, trajectory documentation, and `vite.config.mts`.
- Read-only inventories of D: and the active F: mission tree; full MD5 comparison of one representative existing NEF against its published PDS checksum. No full-holding hash audit has yet been performed.
