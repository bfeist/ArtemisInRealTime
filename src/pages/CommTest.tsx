import { JSX, useEffect, useMemo, useRef, useState } from "react";
import { GroupedVirtuoso, type GroupedVirtuosoHandle } from "react-virtuoso";
import styles from "./CommTest.module.css";
import CommFilterPicker from "./CommFilterPicker.tsx";
import type { CommEntry, PaoEntry } from "../types/transcripts.ts";
import { loadEnrichedTranscripts } from "../data/comm.ts";
import { loadVideoSync, videoPositions, type VideoSync } from "../data/videoSync.ts";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

// ── Data shapes produced by 1c / 2k ────────────────────────────────────────

type Row =
  { kind: "comm"; entry: CommEntry; channel: ChannelId } | { kind: "pao"; entry: PaoEntry };

// ── Channel helpers ───────────────────────────────────────────────────────────

type ChannelId = "OE-1" | "OE-2" | "PCD" | "unknown";

function channelFromSrc(src: string | undefined): ChannelId {
  if (!src) return "unknown";
  if (src.startsWith("pcd/")) return "PCD";
  const m = src.match(/_OE_Comp_(\d+)/i);
  if (m?.[1] === "1") return "OE-1";
  if (m?.[1] === "2") return "OE-2";
  const loop = src.match(/_oe([12])\./i);
  if (loop?.[1] === "1") return "OE-1";
  if (loop?.[1] === "2") return "OE-2";
  return "unknown";
}

/** Derive the date subfolder from the AAC filename, e.g. "2026-04-01/file.aac". */
function aacPath(src: string): string {
  if (src.includes("/")) return src;
  const m = src.match(/(\d{4}-\d{2}-\d{2})/);
  return m ? `${m[1]}/${src}` : src;
}

function formatTime(isoStr: string): string {
  // Extract HH:MM:SS from an ISO-8601 UTC string
  const match = isoStr.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : isoStr;
}

/** Segment start offset in seconds within the AAC file.
 *  Uses the stored `s` field if present; otherwise derives it from the
 *  recording-start timestamp embedded in the filename vs. the entry's UTC time.
 *  e.g. "…_2026-04-01_13_10_34_….aac" + t="2026-04-01T13:10:41.559Z" → 7.559 s */
function segmentStart(entry: CommEntry): number {
  if (entry.s != null) return entry.s;
  const m = (entry.src ?? "").match(/(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})_(\d{2})/);
  if (!m) return 0;
  const fileStartMs = Date.parse(`${m[1]}T${m[2]}:${m[3]}:${m[4]}Z`);
  const offset = (Date.parse(entry.t) - fileStartMs) / 1000;
  return offset >= 0 ? offset : 0;
}

function groupDate(isoStr: string): string {
  const t = Date.parse(isoStr);
  if (Number.isNaN(t)) return "Unknown date";
  return new Date(t).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

interface FdGroup {
  date: string;
  count: number;
  firstIndex: number;
}

// ── Component ─────────────────────────────────────────────────────────────────

const CHANNELS: ChannelId[] = ["OE-1", "OE-2", "PCD"];
const CATEGORY_LABELS: Record<string, string> = {
  maneuver: "Maneuver",
  systems: "Systems",
  photography: "Photography",
  science: "Science",
  crew_life: "Crew life",
  milestone: "Milestone",
  media_event: "Media event",
  communications: "Communications",
  mission_background: "Mission background",
  social: "Social",
  unspecified: "Unspecified",
  pao_commentary: "PAO commentary",
  pao_filler: "PAO filler",
  uncategorized: "Uncategorized",
};

const PAO_TYPE_LABELS: Record<string, string> = {
  commentary: "PAO commentary",
  filler: "PAO filler",
  other: "Other PAO",
  unspecified: "Unspecified type",
};

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replaceAll("_", " ");
}

function countCategories(entries: PaoEntry[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const entry of entries) {
    const category = entry.category || "uncategorized";
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }
  return [...counts.entries()].sort(([a], [b]) => categoryLabel(a).localeCompare(categoryLabel(b)));
}

function CommTest(): JSX.Element {
  const [entries, setEntries] = useState<CommEntry[]>([]);
  const [paoEntries, setPaoEntries] = useState<PaoEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [audioBase, setAudioBase] = useState(`${ASSETS_BASE}/artemis-ii/web`);
  const [videoSync, setVideoSync] = useState<VideoSync>({ schemaVersion: 1, mappings: [] });
  const [videoError, setVideoError] = useState(false);

  const [visible, setVisible] = useState<Record<ChannelId, boolean>>({
    "OE-1": true,
    "OE-2": true,
    PCD: true,
    unknown: true,
  });
  const [showPao, setShowPao] = useState(true);
  const [hiddenPaoTypes, setHiddenPaoTypes] = useState<Set<string>>(new Set());
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(new Set());
  const listRef = useRef<GroupedVirtuosoHandle>(null);

  const [activeEntry, setActiveEntry] = useState<CommEntry | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !activeEntry) return;
    const start = segmentStart(activeEntry);
    const end = start + activeEntry.d;
    const onReady = () => {
      audio.currentTime = start;
      audio.play().catch((_e: unknown): void => {
        /* autoplay blocked */
      });
    };
    const onTimeUpdate = () => {
      if (audio.currentTime >= end) audio.pause();
    };
    audio.addEventListener("loadedmetadata", onReady, { once: true });
    audio.addEventListener("timeupdate", onTimeUpdate);
    return () => {
      audio.removeEventListener("loadedmetadata", onReady);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.pause();
    };
  }, [activeEntry]);

  useEffect(() => {
    let cancelled = false;
    const base = `${ASSETS_BASE}/artemis-ii/web`;

    loadEnrichedTranscripts(base)
      .then(({ comm, pao, assetBase }) => {
        if (cancelled) return;
        setAudioBase(assetBase ?? base);
        setEntries(comm);
        setPaoEntries(pao);
        setLoading(false);
        if (assetBase) {
          loadVideoSync(base)
            .then((sync) => {
              if (!cancelled) setVideoSync(sync);
            })
            .catch(() => {
              if (!cancelled) setVideoError(true);
            });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleChannel = (ch: ChannelId) => {
    setVisible((prev) => ({ ...prev, [ch]: !prev[ch] }));
  };

  const channelCounts = useMemo(() => {
    const counts: Record<ChannelId, number> = { "OE-1": 0, "OE-2": 0, PCD: 0, unknown: 0 };
    for (const e of entries) counts[channelFromSrc(e.src)]++;
    return counts;
  }, [entries]);

  const categoryCounts = useMemo(
    () => countCategories([...entries, ...paoEntries]),
    [entries, paoEntries]
  );

  const paoTypeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of paoEntries) {
      const type = entry.pao_type ?? "unspecified";
      counts.set(type, (counts.get(type) ?? 0) + 1);
    }
    return [...counts.entries()].sort(([a], [b]) =>
      (PAO_TYPE_LABELS[a] ?? a).localeCompare(PAO_TYPE_LABELS[b] ?? b)
    );
  }, [paoEntries]);

  // Sort and resolve channels once per dataset, rather than on every filter change.
  const chronological = useMemo(() => {
    const rows: Row[] = [
      ...entries.map((entry) => ({
        kind: "comm" as const,
        entry,
        channel: channelFromSrc(entry.src),
      })),
      ...paoEntries.map((entry) => ({ kind: "pao" as const, entry })),
    ];
    rows.sort((a, b) => Date.parse(a.entry.t) - Date.parse(b.entry.t));
    return rows;
  }, [entries, paoEntries]);

  const merged = useMemo(
    () =>
      chronological.filter(
        (row) =>
          (row.kind === "comm"
            ? visible[row.channel]
            : showPao && !hiddenPaoTypes.has(row.entry.pao_type ?? "unspecified")) &&
          !hiddenCategories.has(row.entry.category || "uncategorized")
      ),
    [chronological, visible, showPao, hiddenCategories, hiddenPaoTypes]
  );

  const groups = useMemo(() => {
    const out: FdGroup[] = [];
    let currentDay = "";
    for (let index = 0; index < merged.length; index++) {
      const day = merged[index].entry.t.slice(0, 10);
      if (day !== currentDay || out.length === 0) {
        currentDay = day;
        // Format once per day, not once per utterance.
        out.push({ date: groupDate(merged[index].entry.t), count: 0, firstIndex: index });
      }
      out[out.length - 1].count++;
    }
    return out;
  }, [merged]);
  const groupCounts = useMemo(() => groups.map((group) => group.count), [groups]);

  const filtersActive =
    hiddenCategories.size > 0 ||
    hiddenPaoTypes.size > 0 ||
    !showPao ||
    Object.values(visible).some((value) => !value);

  function resetFilters() {
    setVisible({ "OE-1": true, "OE-2": true, PCD: true, unknown: true });
    setShowPao(true);
    setHiddenCategories(new Set());
    setHiddenPaoTypes(new Set());
  }

  function channelBadgeClass(ch: ChannelId): string {
    if (ch === "OE-1") return styles.channelOE1;
    if (ch === "OE-2") return styles.channelOE2;
    if (ch === "PCD") return styles.channelPCD;
    return styles.channelUnknown;
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <div className={styles.eyebrow}>
            Artemis II <span>/</span> Comm test
          </div>
          <h1 className={styles.heading}>Mission communications</h1>
          <p className={styles.subtitle}>The conversations behind the mission.</p>
        </div>
        {!loading && !error && (
          <div className={styles.archiveSummary}>
            <strong>{(entries.length + paoEntries.length).toLocaleString()}</strong>
            <span>transcript entries · all times UTC</span>
          </div>
        )}
      </header>

      {activeEntry && (
        <div className={styles.playerBar}>
          <div className={styles.playerMeta}>
            <span
              className={`${styles.channel} ${activeEntry.source === "PAO" ? styles.channelPao : channelBadgeClass(channelFromSrc(activeEntry.src))}`}
            >
              {activeEntry.source === "PAO" ? "PAO" : channelFromSrc(activeEntry.src)}
            </span>
            <span className={styles.playerTime}>{formatTime(activeEntry.t)}</span>
            <span className={styles.playerText}>
              {activeEntry.speaker && (
                <span className={styles.speaker}>{activeEntry.speaker}: </span>
              )}
              {activeEntry.text}
            </span>
          </div>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption -- no caption tracks for mission audio */}
          <audio
            ref={audioRef}
            key={`${activeEntry.t}|${activeEntry.src ?? ""}`}
            controls
            className={styles.audioEl}
            src={`${audioBase}/comm/${aacPath(activeEntry.src ?? "")}`}
          />
          <button
            type="button"
            className={styles.playerClose}
            aria-label="Close player"
            onClick={() => setActiveEntry(null)}
          >
            ✕
          </button>
        </div>
      )}

      {loading && <p className={styles.status}>Loading transcripts…</p>}
      {error && <p className={styles.status}>Error: {error}</p>}
      {videoError && <p className={styles.status}>Video links are temporarily unavailable.</p>}

      {!loading && !error && (
        <>
          <section className={styles.filterBar} aria-label="Transcript filters">
            <div className={styles.filterTop}>
              <div className={styles.channels} role="group" aria-label="Channels">
                <span className={styles.controlLabel}>Channels</span>
                {[...CHANNELS, ...(channelCounts.unknown > 0 ? ["unknown" as const] : [])].map(
                  (ch) => (
                    <label key={ch} className={styles.channelSwitch}>
                      <input
                        type="checkbox"
                        role="switch"
                        checked={visible[ch]}
                        onChange={() => toggleChannel(ch)}
                        aria-label={ch === "unknown" ? "Unknown channel" : ch}
                      />
                      <span className={channelBadgeClass(ch)}>
                        {ch === "unknown" ? "Unknown" : ch}
                      </span>
                      <span className={styles.channelCount}>
                        {channelCounts[ch].toLocaleString()}
                      </span>
                    </label>
                  )
                )}
                <label className={styles.channelSwitch}>
                  <input
                    type="checkbox"
                    role="switch"
                    checked={showPao}
                    onChange={() => setShowPao((previous) => !previous)}
                    aria-label="PAO"
                  />
                  <span className={styles.channelPao}>PAO</span>
                  <span className={styles.channelCount}>{paoEntries.length.toLocaleString()}</span>
                </label>
              </div>
              <div className={styles.pickers}>
                <CommFilterPicker
                  label="Categories"
                  description="One set of subjects for both COMM and PAO. Counts include all channels."
                  options={categoryCounts.map(([value, count]) => ({
                    value,
                    count,
                    label: categoryLabel(value),
                  }))}
                  hidden={hiddenCategories}
                  onChange={setHiddenCategories}
                />
                <CommFilterPicker
                  label="PAO types"
                  description="Refine PAO within your selected categories. COMM entries are unaffected. Counts include all PAO."
                  options={paoTypeCounts.map(([value, count]) => ({
                    value,
                    count,
                    label: PAO_TYPE_LABELS[value] ?? value,
                  }))}
                  hidden={hiddenPaoTypes}
                  onChange={setHiddenPaoTypes}
                  disabled={!showPao}
                />
              </div>
            </div>
            <div className={styles.filterBottom}>
              <p className={styles.filterHint}>
                Categories apply across channels. PAO types refine PAO only.
              </p>
              <button
                type="button"
                className={styles.reset}
                disabled={!filtersActive}
                onClick={resetFilters}
              >
                Reset filters
              </button>
            </div>
          </section>

          <div className={styles.resultsBar}>
            <div className={styles.resultsCount} role="status">
              <strong>{merged.length.toLocaleString()} shown</strong>
              <span> of {chronological.length.toLocaleString()} entries</span>
            </div>
            {groups.length > 0 && (
              <div className={styles.dateNavigation}>
                <label className={styles.controlLabel} htmlFor="transcript-date">
                  Jump to date
                </label>
                <select
                  id="transcript-date"
                  className={styles.dateSelect}
                  value=""
                  onChange={(event) => {
                    if (event.target.value !== "") {
                      listRef.current?.scrollToIndex({
                        index: Number(event.target.value),
                        align: "start",
                      });
                    }
                  }}
                >
                  <option value="">Select a date</option>
                  {groups.map((group) => (
                    <option key={group.date} value={group.firstIndex}>
                      {group.date} ({group.count.toLocaleString()})
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Transcript list */}
          {merged.length === 0 ? (
            <div className={styles.empty}>
              <h2>No transmissions in this view</h2>
              <p>
                Try another category, enable a channel, or reset your filters to see everything.
              </p>
              <button type="button" className={styles.emptyReset} onClick={resetFilters}>
                Reset filters
              </button>
            </div>
          ) : (
            <GroupedVirtuoso
              ref={listRef}
              className={styles.list}
              useWindowScroll
              groupCounts={groupCounts}
              defaultItemHeight={48}
              increaseViewportBy={400}
              groupContent={(index) => (
                <h2 className={styles.fdHeading}>
                  {groups[index].date}{" "}
                  <span className={styles.fdCount}>
                    {groups[index].count.toLocaleString()} entries
                  </span>
                </h2>
              )}
              itemContent={(index) => {
                const row = merged[index];
                if (row.kind === "pao") {
                  const playback = row.entry.playback;
                  const audio = playback?.audio;
                  const isActive =
                    activeEntry?.source === "PAO" &&
                    activeEntry.t === row.entry.t &&
                    activeEntry.text === row.entry.text;
                  const selectPao = () => {
                    if (!audio) return;
                    setActiveEntry(
                      isActive
                        ? null
                        : {
                            ...row.entry,
                            source: "PAO",
                            src: audio.src.slice(5),
                            s: audio.s,
                            d: audio.d,
                          }
                    );
                  };
                  const videoLinks = playback?.video
                    ? [
                        {
                          id: playback.video.id,
                          seconds: playback.video.s,
                          key: playback.video.mappingId,
                        },
                      ]
                    : videoPositions(videoSync, row.entry.t, playback?.feed).map(
                        ({ mapping, seconds }) => ({
                          id: mapping.videoId,
                          seconds,
                          key: mapping.id,
                        })
                      );
                  const content = (
                    <>
                      <span className={styles.time}>{formatTime(row.entry.t)}</span>
                      <span className={`${styles.channel} ${styles.channelPao}`}>PAO</span>
                      <span className={styles.text}>
                        <span className={styles.categoryBadge}>
                          {categoryLabel(row.entry.category || "uncategorized")}
                        </span>
                        {row.entry.pao_type && (
                          <span className={styles.categoryBadge}>
                            {PAO_TYPE_LABELS[row.entry.pao_type]}
                          </span>
                        )}
                        {row.entry.text}
                      </span>
                    </>
                  );
                  return (
                    <div>
                      {audio ? (
                        <button
                          type="button"
                          onClick={selectPao}
                          aria-pressed={isActive}
                          className={`${styles.entry} ${styles.entryPao}${isActive ? ` ${styles.entryActive}` : ""}`}
                        >
                          {content}
                        </button>
                      ) : (
                        <div className={styles.entryPao}>{content}</div>
                      )}
                      {videoLinks.length > 0 && (
                        <div className={styles.paoLinks}>
                          {videoLinks.map(({ id, seconds, key }) => (
                            <a
                              key={key}
                              href={`https://www.youtube.com/watch?v=${id}&t=${Math.floor(seconds)}s`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {" "}
                              Watch video
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                }
                const entry = row.entry;
                const isActive = activeEntry === entry;
                return (
                  <button
                    type="button"
                    className={`${styles.entry}${isActive ? ` ${styles.entryActive}` : ""}`}
                    onClick={() => setActiveEntry(isActive ? null : entry)}
                  >
                    <div className={styles.time}>{formatTime(entry.t)}</div>
                    <div className={`${styles.channel} ${channelBadgeClass(row.channel)}`}>
                      {row.channel}
                    </div>
                    <div className={styles.text}>
                      <span className={styles.categoryBadge}>
                        {categoryLabel(entry.category || "uncategorized")}
                      </span>
                      {entry.speaker && <span className={styles.speaker}>{entry.speaker}: </span>}
                      {entry.text}
                    </div>
                  </button>
                );
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

export default CommTest;
