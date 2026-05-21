import { JSX, useEffect, useMemo, useRef, useState } from "react";
import styles from "./CommTest.module.css";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

// ── Data shapes produced by 1c / 2k ────────────────────────────────────────

interface CommSegment {
  s: number;
  e: number;
  text: string;
}

interface CommEntry {
  t: string;
  s?: number;
  d: number;
  src?: string;
  text: string;
  lang: string;
  segments?: CommSegment[];
}

interface PaoEntry {
  t: string;
  text: string;
}

type Row = { kind: "comm"; entry: CommEntry } | { kind: "pao"; entry: PaoEntry };

// ── Channel helpers ───────────────────────────────────────────────────────────

type ChannelId = "OE-1" | "OE-2" | "unknown";

function channelFromSrc(src: string | undefined): ChannelId {
  if (!src) return "unknown";
  const m = src.match(/_OE_Comp_(\d+)/i);
  if (m?.[1] === "1") return "OE-1";
  if (m?.[1] === "2") return "OE-2";
  return "unknown";
}

/** Derive the date subfolder from the AAC filename, e.g. "2026-04-01/file.aac". */
function aacPath(src: string): string {
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
  rows: Row[];
}

// ── Component ─────────────────────────────────────────────────────────────────

const CHANNELS: ChannelId[] = ["OE-1", "OE-2"];

function CommTest(): JSX.Element {
  const [entries, setEntries] = useState<CommEntry[]>([]);
  const [paoEntries, setPaoEntries] = useState<PaoEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [visible, setVisible] = useState<Record<ChannelId, boolean>>({
    "OE-1": true,
    "OE-2": true,
    unknown: true,
  });
  const [showPao, setShowPao] = useState(true);

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

    Promise.all([
      fetch(`${base}/comm.csv`).then((r) => {
        if (!r.ok) throw new Error(`comm.csv HTTP ${r.status}`);
        return r.text();
      }),
      fetch(`${base}/PAO.csv`).then((r) => (r.ok ? r.text() : Promise.resolve(""))),
    ])
      .then(([commCsv, paoCsv]) => {
        if (cancelled) return;

        // comm.csv is pipe-delimited: t|s|d|src|text
        // text is last so it may safely contain "|"
        const enriched: CommEntry[] = [];
        for (const line of commCsv.split("\n")) {
          const trimmed = line.replace(/\r$/, "");
          if (!trimmed) continue;
          const idx1 = trimmed.indexOf("|");
          const idx2 = trimmed.indexOf("|", idx1 + 1);
          const idx3 = trimmed.indexOf("|", idx2 + 1);
          const idx4 = trimmed.indexOf("|", idx3 + 1);
          if (idx4 === -1) continue;
          const t = trimmed.slice(0, idx1);
          const s = parseFloat(trimmed.slice(idx1 + 1, idx2));
          const d = parseFloat(trimmed.slice(idx2 + 1, idx3));
          const src = trimmed.slice(idx3 + 1, idx4);
          const text = trimmed.slice(idx4 + 1);
          enriched.push({ t, s, d, src, text, lang: "en" });
        }

        // PAO.csv is pipe-delimited: t|text
        const paoList: PaoEntry[] = [];
        for (const line of paoCsv.split("\n")) {
          const trimmed = line.replace(/\r$/, "");
          if (!trimmed) continue;
          const idx = trimmed.indexOf("|");
          if (idx === -1) continue;
          paoList.push({ t: trimmed.slice(0, idx), text: trimmed.slice(idx + 1) });
        }

        setEntries(enriched);
        setPaoEntries(paoList);
        setLoading(false);
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
    const counts: Record<ChannelId, number> = { "OE-1": 0, "OE-2": 0, unknown: 0 };
    for (const e of entries) counts[channelFromSrc(e.src)]++;
    return counts;
  }, [entries]);

  const merged = useMemo(() => {
    const commRows: Row[] = entries
      .filter((e) => visible[channelFromSrc(e.src)])
      .map((e) => ({ kind: "comm" as const, entry: e }));
    const paoRows: Row[] = showPao
      ? paoEntries.map((e) => ({ kind: "pao" as const, entry: e }))
      : [];
    const all = [...commRows, ...paoRows];
    all.sort((a, b) => (a.entry.t < b.entry.t ? -1 : a.entry.t > b.entry.t ? 1 : 0));
    return all;
  }, [entries, paoEntries, visible, showPao]);

  const groups = useMemo(() => {
    const out: FdGroup[] = [];
    let cur: FdGroup | null = null;
    for (const row of merged) {
      const date = groupDate(row.entry.t);
      if (!cur || cur.date !== date) {
        cur = { date, rows: [] };
        out.push(cur);
      }
      cur.rows.push(row);
    }
    return out;
  }, [merged]);

  const timeRange = useMemo(() => {
    if (entries.length === 0) return null;
    return { first: formatTime(entries[0].t), last: formatTime(entries[entries.length - 1].t) };
  }, [entries]);

  function toggleClass(ch: ChannelId): string {
    const on = visible[ch];
    if (ch === "OE-1") return on ? styles.toggleOE1Active : styles.toggleOE1Inactive;
    if (ch === "OE-2") return on ? styles.toggleOE2Active : styles.toggleOE2Inactive;
    return "";
  }

  function channelBadgeClass(ch: ChannelId): string {
    if (ch === "OE-1") return styles.channelOE1;
    if (ch === "OE-2") return styles.channelOE2;
    return styles.channelUnknown;
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Comm Test — Artemis II</h1>

      {activeEntry && (
        <div className={styles.playerBar}>
          <div className={styles.playerMeta}>
            <span
              className={`${styles.channel} ${channelBadgeClass(channelFromSrc(activeEntry.src))}`}
            >
              {channelFromSrc(activeEntry.src)}
            </span>
            <span className={styles.playerTime}>{formatTime(activeEntry.t)}</span>
            <span className={styles.playerText}>{activeEntry.text}</span>
          </div>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption -- no caption tracks for mission audio */}
          <audio
            ref={audioRef}
            key={`${activeEntry.t}|${activeEntry.src ?? ""}`}
            controls
            className={styles.audioEl}
            src={`${ASSETS_BASE}/artemis-ii/web/comm/${aacPath(activeEntry.src ?? "")}`}
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

      {loading && <p className={styles.status}>Loading comm.csv…</p>}
      {error && <p className={styles.status}>Error: {error}</p>}

      {!loading && !error && (
        <>
          {/* Stats */}
          <div className={styles.statsStrip}>
            <div className={styles.statCard}>
              <div className={styles.statBig}>{entries.length.toLocaleString()}</div>
              <div className={styles.statLabel}>total entries</div>
            </div>
            {CHANNELS.map((ch) => (
              <div key={ch} className={styles.statCard}>
                <div className={styles.statBig}>{channelCounts[ch].toLocaleString()}</div>
                <div className={styles.statLabel}>{ch} entries</div>
              </div>
            ))}
            <div className={styles.statCard}>
              <div className={styles.statBig}>{paoEntries.length.toLocaleString()}</div>
              <div className={styles.statLabel}>PAO entries</div>
            </div>
            {timeRange && (
              <div className={styles.statCard}>
                <div className={styles.statLabel}>time range (UTC)</div>
                <div className={styles.statBig} style={{ fontSize: "0.9rem" }}>
                  {timeRange.first}
                </div>
                <div style={{ fontSize: "0.8rem", color: "#888888" }}>to {timeRange.last}</div>
              </div>
            )}
          </div>

          {/* Channel toggles */}
          <div className={styles.channelToggles}>
            <span className={styles.toggleLabel}>Channels</span>
            {CHANNELS.map((ch) => (
              <div
                key={ch}
                className={`${styles.toggle} ${toggleClass(ch)}`}
                role="button"
                tabIndex={0}
                aria-pressed={visible[ch]}
                aria-label={`Toggle ${ch} ${visible[ch] ? "off" : "on"}`}
                onClick={() => toggleChannel(ch)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggleChannel(ch);
                  }
                }}
              >
                {ch}
              </div>
            ))}
            <div
              className={`${styles.toggle} ${showPao ? styles.togglePaoActive : styles.togglePaoInactive}`}
              role="button"
              tabIndex={0}
              aria-pressed={showPao}
              aria-label={`Toggle PAO commentary ${showPao ? "off" : "on"}`}
              onClick={() => setShowPao((v) => !v)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setShowPao((v) => !v);
                }
              }}
            >
              PAO
            </div>
            <span className={styles.toggleLabel} style={{ marginLeft: "0.5rem" }}>
              {merged.length.toLocaleString()} shown
            </span>
          </div>

          {/* Transcript list */}
          <div className={styles.list}>
            {merged.length === 0 && (
              <div className={styles.empty}>No entries match the selected filters.</div>
            )}
            {groups.map((group) => (
              <section key={group.date} className={styles.fdSection}>
                <h2 className={styles.fdHeading}>
                  {group.date} <span className={styles.fdCount}>({group.rows.length})</span>
                </h2>
                {group.rows.map((row, i) => {
                  if (row.kind === "pao") {
                    return (
                      <div key={`pao-${row.entry.t}`} className={styles.entryPao}>
                        <div className={styles.time}>{formatTime(row.entry.t)}</div>
                        <div className={`${styles.channel} ${styles.channelPao}`}>PAO</div>
                        <div className={styles.text}>{row.entry.text}</div>
                      </div>
                    );
                  }
                  const entry = row.entry;
                  const ch = channelFromSrc(entry.src);
                  const isActive = activeEntry === entry;
                  return (
                    <button
                      key={`${entry.t}-${entry.src ?? i}`}
                      type="button"
                      className={`${styles.entry}${isActive ? ` ${styles.entryActive}` : ""}`}
                      onClick={() => setActiveEntry(isActive ? null : entry)}
                    >
                      <div className={styles.time}>{formatTime(entry.t)}</div>
                      <div className={`${styles.channel} ${channelBadgeClass(ch)}`}>{ch}</div>
                      <div className={styles.text}>{entry.text}</div>
                    </button>
                  );
                })}
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default CommTest;
