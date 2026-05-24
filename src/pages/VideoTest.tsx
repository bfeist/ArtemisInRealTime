import { JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./VideoTest.module.css";

// ── Hard-coded mission data ───────────────────────────────────────────────────
// Stream start UTC from:
//   ArtemisInRealTime_assets/artemis-ii/processed/transcripts/yt/_stream_start.json
const STREAM_START_UTC = "2026-04-01T11:43:14.521Z";
const STREAM_START_MS = Date.parse(STREAM_START_UTC);
const LAUNCH_UTC = "2026-04-01T22:35:12Z"; // actual Artemis II liftoff (from config.py)
const LAUNCH_MS = Date.parse(LAUNCH_UTC);

interface VideoPart {
  partNum: number;
  videoId: string;
  durationS: number;
  startOffsetS: number; // cumulative seconds from stream start
}

// YouTube video IDs from D:\chunks\.yt_upload_state.json
// Part durations (seconds) from WhisperX transcript JSONs in processed/transcripts/yt/
const PARTS: VideoPart[] = (() => {
  const raw: [string, number][] = [
    ["HOMxD7rFO8o", 28800.01],
    ["_AgVLxn1_xs", 28802.44],
    ["aJyjKCdcxxQ", 28802.47],
    ["TY0T116_LHg", 28802.44],
    ["iPJ6vx2Yz-M", 28803.21],
    ["Tpt9ttd9TBw", 28803.24],
    ["cc1Dr07RZHA", 28803.21],
    ["8m70VsfS7AQ", 28803.14],
    ["ebZrZQ0CNHg", 28800.31],
    ["ic05aZxGRw8", 28800.27],
    ["RRlXxTd3C8o", 28800.91],
    ["TNisxxlu7PI", 28800.87],
    ["fpFpqGvAzXo", 28800.9],
    ["N-oC0gRsxiQ", 28800.87],
    ["kaR78OOhcqs", 28800.88],
    ["0XMvABg4gdc", 28800.9],
    ["K5YUJdtwmzs", 28800.88],
    ["BmZP28dodWM", 28800.9],
    ["ry3yW2wtYN8", 28800.94],
    ["vWKdFNQdUsE", 28800.9],
    ["KcJEc79xL4Y", 28800.94],
    ["GzoIWEbXCm0", 28800.88],
    ["u-h1QxAWop4", 28800.85],
    ["3v6eJL8GLGE", 28800.87],
    ["EEi-TBx0ig8", 28800.84],
    ["u6rDQXA-Hf0", 28801.87],
    ["HBH4TNj4MUI", 28801.91],
    ["EONDC7J9p14", 28800.87],
    ["V7eSc7Y0CNM", 23884.83],
  ];
  let cum = 0;
  return raw.map(([videoId, durationS], i) => {
    const p: VideoPart = { partNum: i + 1, videoId, durationS, startOffsetS: cum };
    cum += durationS;
    return p;
  });
})();

const TOTAL_DURATION_S = PARTS.reduce((s, p) => s + p.durationS, 0);
const STREAM_END_MS = STREAM_START_MS + TOTAL_DURATION_S * 1000;

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

// ── Mission phase / event types ───────────────────────────────────────────────

interface Phase {
  name: string;
  t: string;
  color: string;
}

interface MissionEvent {
  id: string;
  name: string;
  description: string;
  t: string;
  confidence: "confirmed" | "nominal" | "approximate";
  color: string;
}

interface Itinerary {
  events: MissionEvent[];
}

// ── YouTube IFrame API types (minimal) ────────────────────────────────────────

interface YTPlayer {
  loadVideoById(opts: { videoId: string; startSeconds?: number }): void;
  cueVideoById(opts: { videoId: string; startSeconds?: number }): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getPlayerState(): number;
  destroy(): void;
}

declare global {
  interface Window {
    onYouTubeIframeAPIReady?: () => void;
    YT?: {
      Player: new (element: HTMLElement, config: object) => YTPlayer;
      PlayerState: { PLAYING: number; PAUSED: number; ENDED: number };
    };
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatUtc(ms: number): string {
  return new Date(ms)
    .toISOString()
    .replace("T", " ")
    .replace(/\.\d{3}Z$/, " UTC");
}

function formatMet(startMs: number, nowMs: number): string {
  const raw = nowMs - startMs;
  const sign = raw < 0 ? "-" : "+";
  const dt = Math.abs(raw);
  const hrs = Math.floor(dt / 3_600_000);
  const mins = Math.floor((dt % 3_600_000) / 60_000);
  const secs = Math.floor((dt % 60_000) / 1000);
  return `T${sign}${String(hrs).padStart(3, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function findPartForOffset(offsetS: number): VideoPart {
  for (const p of PARTS) {
    if (offsetS < p.startOffsetS + p.durationS) return p;
  }
  return PARTS[PARTS.length - 1];
}

function parseUtc(iso: string): number {
  return Date.parse(iso.endsWith("Z") ? iso : iso + "Z");
}

function activePhase(phases: Phase[], nowMs: number): Phase | null {
  let active: Phase | null = null;
  for (const p of phases) {
    if (parseUtc(p.t) <= nowMs) active = p;
    else break;
  }
  return active;
}

// ── Component ─────────────────────────────────────────────────────────────────

function VideoTest(): JSX.Element {
  const [scrubMs, setScrubMs] = useState(STREAM_START_MS);
  const [playerReady, setPlayerReady] = useState(false);
  const [following, setFollowing] = useState(false);
  const [phases, setPhases] = useState<Phase[]>([]);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);

  // Ref to the div that will be replaced by the YT iframe
  const playerTargetRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<YTPlayer | null>(null);
  const activePartRef = useRef<VideoPart>(PARTS[0]);
  const followingRef = useRef(false);

  useEffect(() => {
    followingRef.current = following;
  }, [following]);

  // ── Load trajectory phases + itinerary ──────────────────────────────────────
  useEffect(() => {
    const base = `${ASSETS_BASE}/artemis-ii/web/ephemeris`;
    Promise.all([
      fetch(`${base}/trajectory.json`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${base}/itinerary.json`).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([traj, itin]) => {
        if (traj?.phases) setPhases(traj.phases as Phase[]);
        if (itin?.events) setItinerary(itin as Itinerary);
      })
      .catch(() => {});
  }, []);

  // ── Load YouTube IFrame API once ──────────────────────────────────────────
  useEffect(() => {
    const initPlayer = () => {
      const target = playerTargetRef.current;
      if (!target || !window.YT?.Player) return;
      playerRef.current = new window.YT.Player(target, {
        width: "100%",
        height: "100%",
        videoId: PARTS[0].videoId,
        playerVars: {
          autoplay: 0,
          controls: 1,
          modestbranding: 1,
          rel: 0,
          origin: window.location.origin,
        },
        events: {
          onReady: () => {
            activePartRef.current = PARTS[0];
            setPlayerReady(true);
          },
        },
      });
    };

    if (window.YT?.Player) {
      initPlayer();
    } else {
      window.onYouTubeIframeAPIReady = initPlayer;
      if (!document.getElementById("yt-iframe-api")) {
        const tag = document.createElement("script");
        tag.id = "yt-iframe-api";
        tag.src = "https://www.youtube.com/iframe_api";
        document.head.appendChild(tag);
      }
    }

    return () => {
      playerRef.current?.destroy();
      playerRef.current = null;
    };
  }, []);

  // ── Poll video position while playing ──────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => {
      if (!playerRef.current || !window.YT?.PlayerState) return;
      if (playerRef.current.getPlayerState() !== window.YT.PlayerState.PLAYING) return;
      const t = playerRef.current.getCurrentTime();
      const newMs = STREAM_START_MS + (activePartRef.current.startOffsetS + t) * 1000;
      setScrubMs(Math.min(newMs, STREAM_END_MS));
    }, 500);
    return () => clearInterval(id);
  }, []);

  // ── Seek video to a mission UTC instant ──────────────────────────────────
  const seekToMs = useCallback(
    (ms: number) => {
      const clamped = Math.max(STREAM_START_MS, Math.min(STREAM_END_MS, ms));
      setScrubMs(clamped);

      if (!playerReady || !playerRef.current) return;

      const offsetS = (clamped - STREAM_START_MS) / 1000;
      const part = findPartForOffset(offsetS);
      const seekS = Math.max(0, offsetS - part.startOffsetS);

      if (activePartRef.current.partNum !== part.partNum) {
        activePartRef.current = part;
        playerRef.current.loadVideoById({ videoId: part.videoId, startSeconds: seekS });
      } else {
        playerRef.current.seekTo(seekS, true);
      }
    },
    [playerReady]
  );

  const currentPart = useMemo(
    () => findPartForOffset((scrubMs - STREAM_START_MS) / 1000),
    [scrubMs]
  );

  const currentPhase = useMemo(() => activePhase(phases, scrubMs), [phases, scrubMs]);

  const lastEvent = useMemo(() => {
    if (!itinerary) return null;
    let last: MissionEvent | null = null;
    for (const ev of itinerary.events) {
      if (parseUtc(ev.t) <= scrubMs) last = ev;
      else break;
    }
    return last;
  }, [itinerary, scrubMs]);

  const nextEvent = useMemo(() => {
    if (!itinerary) return null;
    return itinerary.events.find((ev) => parseUtc(ev.t) > scrubMs) ?? null;
  }, [itinerary, scrubMs]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Video Test — Artemis II</h1>

      {/* HUD */}
      <div className={styles.statsStrip}>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>UTC</div>
          <div className={styles.statBig}>{formatUtc(scrubMs)}</div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>MET</div>
          <div className={styles.statBig}>{formatMet(LAUNCH_MS, scrubMs)}</div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>Phase</div>
          <div className={styles.statBig} style={{ color: currentPhase?.color ?? "#888888" }}>
            <span className={styles.phaseChip}>{currentPhase?.name ?? "—"}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>Part</div>
          <div className={styles.statBig}>
            {currentPart.partNum} / {PARTS.length}
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>Video</div>
          <div className={styles.statBig}>
            <a
              href={`https://youtu.be/${currentPart.videoId}`}
              target="_blank"
              rel="noreferrer"
              className={styles.vidLink}
            >
              {currentPart.videoId}
            </a>
          </div>
        </div>
      </div>

      {/* Last / next event bar */}
      {itinerary && (
        <div className={styles.eventBar}>
          <div className={styles.eventBarItem}>
            <span className={styles.eventBarLabel}>Last</span>
            {lastEvent ? (
              <span
                className={styles.eventBarName}
                style={{ color: lastEvent.color }}
                title={lastEvent.description}
              >
                {lastEvent.confidence === "confirmed" ? "✓ " : ""}
                {lastEvent.name}
              </span>
            ) : (
              <span className={styles.eventBarEmpty}>—</span>
            )}
          </div>
          <div className={styles.eventBarSep}>/</div>
          <div className={styles.eventBarItem}>
            <span className={styles.eventBarLabel}>Next</span>
            {nextEvent ? (
              <span
                className={styles.eventBarName}
                style={{ color: nextEvent.color }}
                title={nextEvent.description}
              >
                {nextEvent.name}
                <span className={styles.eventBarMet}>
                  {" "}
                  ({formatMet(scrubMs, parseUtc(nextEvent.t))})
                </span>
              </span>
            ) : (
              <span className={styles.eventBarEmpty}>—</span>
            )}
          </div>
        </div>
      )}

      {/* YouTube player */}
      <div className={styles.playerWrap}>
        <div ref={playerTargetRef} className={styles.playerTarget} />
        {!playerReady && <div className={styles.playerOverlay}>Loading YouTube player…</div>}
      </div>

      {/* Timeline */}
      <div className={styles.timelineBar}>
        <div className={styles.controlsRow}>
          <button
            type="button"
            className={`${styles.ctrlBtn} ${following ? styles.ctrlBtnActive : ""}`}
            title="Keep timeline in sync with video playback position"
            onClick={() => setFollowing((v) => !v)}
          >
            ⏺ {following ? "Following" : "Follow"}
          </button>
          <button
            type="button"
            className={styles.ctrlBtn}
            onClick={() => {
              setFollowing(false);
              seekToMs(STREAM_START_MS);
            }}
          >
            ⏮ Start
          </button>
          <button
            type="button"
            className={styles.ctrlBtn}
            onClick={() => {
              setFollowing(false);
              seekToMs(STREAM_END_MS);
            }}
          >
            ⏭ End
          </button>
          {!playerReady && <span className={styles.playerStatus}>Player loading…</span>}
        </div>

        {/* Clickable part-boundary ticks */}
        <div className={styles.partTicks}>
          {PARTS.map((p) => {
            const pct = (p.startOffsetS / TOTAL_DURATION_S) * 100;
            const isActive = currentPart.partNum === p.partNum;
            return (
              <div
                key={p.partNum}
                className={`${styles.partTick} ${isActive ? styles.partTickActive : ""}`}
                style={{ left: `${pct}%` }}
                title={`Part ${p.partNum} — ${formatUtc(STREAM_START_MS + p.startOffsetS * 1000)}`}
                role="button"
                tabIndex={0}
                onClick={() => {
                  setFollowing(false);
                  seekToMs(STREAM_START_MS + p.startOffsetS * 1000);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setFollowing(false);
                    seekToMs(STREAM_START_MS + p.startOffsetS * 1000);
                  }
                }}
              />
            );
          })}
        </div>

        {/* Event ticks */}
        {itinerary && itinerary.events.length > 0 && (
          <div className={styles.eventTicks}>
            {itinerary.events.map((ev) => {
              const pct =
                ((parseUtc(ev.t) - STREAM_START_MS) / (STREAM_END_MS - STREAM_START_MS)) * 100;
              if (pct < 0 || pct > 100) return null;
              return (
                <div
                  key={ev.id}
                  className={styles.eventTick}
                  style={{
                    left: `${pct}%`,
                    borderColor: ev.color,
                    opacity: ev.confidence === "confirmed" ? 1 : 0.6,
                  }}
                  title={`${ev.confidence === "confirmed" ? "✓ " : ""}${ev.name}\n${formatUtc(parseUtc(ev.t))}`}
                />
              );
            })}
          </div>
        )}

        <input
          type="range"
          className={styles.slider}
          min={STREAM_START_MS}
          max={STREAM_END_MS}
          step={1000}
          value={scrubMs}
          onChange={(e) => {
            setFollowing(false);
            seekToMs(Number(e.target.value));
          }}
        />

        <div className={styles.timelineLabels}>
          <span>{formatUtc(STREAM_START_MS)}</span>
          <span className={styles.timelineLabelCenter}>
            Part {currentPart.partNum} — {formatUtc(scrubMs)}
          </span>
          <span>{formatUtc(STREAM_END_MS)}</span>
        </div>
      </div>
    </div>
  );
}

export default VideoTest;
