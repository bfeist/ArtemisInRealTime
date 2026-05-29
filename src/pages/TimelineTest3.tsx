/**
 * TimelineTest.tsx — Two-tier interactive mission timeline
 *
 * Layout (top to bottom):
 *   Tier 1 (OverviewBar)   — full mission overview, spans launch to splashdown.
 *                            Contains a "magWindow" highlight rectangle that shows
 *                            exactly which time range is currently magnified in Tier 2.
 *                            Clicking/dragging Tier 1 seeks the playhead.
 *
 *   ConnectorRegion        — bezier trapezoid visually connecting the magWindow
 *                            on Tier 1 to the full-width Tier 2 strip below it.
 *
 *   Tier 2 (DetailStrip)   — zoomed-in view of the time window defined by the
 *                            magWindow. Shows time-axis ticks, mission events,
 *                            photos, and comm segments. Drag to pan; zoom +/−
 *                            buttons resize the magWindow / windowDurationMs.
 *
 * Naming conventions used throughout:
 *   magWindow              — the zoom-window rectangle shown on Tier 1; its
 *                            left/right edges are winLeftPct / winRightPct.
 *   windowDurationMs       — width of the magWindow in milliseconds.
 *   windowStartMs/EndMs    — absolute UTC millisecond bounds of the magWindow.
 *   coverageStartMs/EndMs  — absolute UTC millisecond bounds of the full mission.
 *   scrubMs                — current playhead position in absolute UTC ms.
 */

import { JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./TimelineTest3.module.css";
import type { Photo } from "../types/photos.ts";
import { usePlaybackClock } from "../hooks/usePlaybackClock.ts";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

// ── Data shapes ──────────────────────────────────────────────────────────────

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
  met_s: number;
  confidence: "confirmed" | "nominal" | "approximate";
  category: string;
  color: string;
}

interface Itinerary {
  mission_id: string;
  launch_utc: string;
  splashdown_utc: string | null;
  source_asflown: string;
  events: MissionEvent[];
  phases?: Phase[];
}

interface CommEntry {
  t: string; // ISO-8601 UTC
  d: number; // duration seconds
  src?: string;
  text: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

const ZOOM_STEPS_MS = [
  10 * 24 * 3_600_000, // ~full mission
  24 * 3_600_000, // 1 day
  12 * 3_600_000,
  6 * 3_600_000,
  3_600_000, // 1 hour
  30 * 60_000,
  10 * 60_000,
  60_000, // 1 minute
];

const MIN_WINDOW_MS = 60_000; // 1-minute minimum zoom window

const SPEEDS = [1, 24, 60, 600, 3600] as const;
type Speed = (typeof SPEEDS)[number];

// ── Helpers ──────────────────────────────────────────────────────────────────

function parseUtc(iso: string): number {
  return Date.parse(iso.endsWith("Z") ? iso : iso + "Z");
}

function formatUtc(ms: number): string {
  return new Date(ms).toISOString().replace(".000", "").replace("T", " ");
}

function formatMet(launchMs: number, nowMs: number): string {
  const dt = nowMs - launchMs;
  const sign = dt < 0 ? "-" : "+";
  const abs = Math.abs(dt);
  const d = Math.floor(abs / 86_400_000);
  const h = Math.floor((abs % 86_400_000) / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const s = Math.floor((abs % 60_000) / 1000);
  return `T${sign}${d}d ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** Format a UTC timestamp as an adaptive time label depending on window width */
function formatDetailTick(ms: number, windowMs: number): string {
  const d = new Date(ms);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  if (windowMs <= 5 * 60_000) return `${hh}:${mm}:${ss}`;
  return `${hh}:${mm}`;
}

/** Adaptive tick step in ms for the detail-strip time axis */
function tickStepMs(windowMs: number): number {
  if (windowMs >= 24 * 3_600_000) return 2 * 3_600_000;
  if (windowMs >= 12 * 3_600_000) return 3_600_000;
  if (windowMs >= 3 * 3_600_000) return 30 * 60_000;
  if (windowMs >= 3_600_000) return 10 * 60_000;
  if (windowMs >= 30 * 60_000) return 5 * 60_000;
  if (windowMs >= 10 * 60_000) return 60_000;
  return 10_000;
}

/** Returns true when a photo's time is midnight-exactly (day-level precision only) */
function isMidnightUtc(iso: string): boolean {
  const d = new Date(iso);
  return d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
}

// ── Sub-components ───────────────────────────────────────────────────────────

// ---- OverviewBar (Tier 1) --------------------------------------------------
// Full-mission overview bar. Renders the magWindow highlight to show which
// time range is currently magnified in the Tier 2 DetailStrip below.

interface OverviewBarProps {
  coverageStartMs: number;
  coverageEndMs: number;
  launchMs: number;
  scrubMs: number;
  windowStartMs: number;
  windowEndMs: number;
  events: MissionEvent[];
  phases: Phase[];
  photos?: Photo[];
  commEntries?: CommEntry[];
  onSeek: (ms: number) => void;
  onWindowChange: (newScrubMs: number, newDurationMs: number) => void;
  previewMs?: number | null;
  onScrubStart?: () => void;
  onScrubEnd?: () => void;
}

function OverviewBar({
  coverageStartMs,
  coverageEndMs,
  launchMs,
  scrubMs,
  windowStartMs,
  windowEndMs,
  events,
  phases,
  photos = [],
  commEntries = [],
  onSeek,
  onWindowChange,
  previewMs,
  onScrubStart,
  onScrubEnd,
}: OverviewBarProps): JSX.Element {
  const totalMs = coverageEndMs - coverageStartMs;
  const toPercent = useCallback(
    (ms: number) => ((ms - coverageStartMs) / totalMs) * 100,
    [coverageStartMs, totalMs]
  );

  const wrapRef = useRef<HTMLDivElement>(null);
  const pointerActionRef = useRef<"idle" | "seek" | "pan" | "resize-left" | "resize-right">("idle");
  const panOffsetMsRef = useRef(0);
  const onScrubEndRef = useRef(onScrubEnd);
  useEffect(() => {
    onScrubEndRef.current = onScrubEnd;
  }, [onScrubEnd]);
  const windowStartMsRef = useRef(windowStartMs);
  const windowEndMsRef = useRef(windowEndMs);
  useEffect(() => {
    windowStartMsRef.current = windowStartMs;
  }, [windowStartMs]);
  useEffect(() => {
    windowEndMsRef.current = windowEndMs;
  }, [windowEndMs]);

  const seekFromClientX = useCallback(
    (clientX: number) => {
      const el = wrapRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const pct = clamp((clientX - rect.left) / rect.width, 0, 1);
      onSeek(coverageStartMs + pct * totalMs);
    },
    [coverageStartMs, totalMs, onSeek]
  );

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const action = pointerActionRef.current;
      if (action === "idle") return;
      const el = wrapRef.current;
      if (!el) return;
      if (action === "seek") {
        seekFromClientX(e.clientX);
      } else if (action === "pan") {
        const rect = el.getBoundingClientRect();
        const clickMs =
          coverageStartMs + clamp((e.clientX - rect.left) / rect.width, 0, 1) * totalMs;
        const windowDur = windowEndMsRef.current - windowStartMsRef.current;
        const newCenter = clamp(
          clickMs - panOffsetMsRef.current,
          coverageStartMs + windowDur / 2,
          coverageEndMs - windowDur / 2
        );
        onWindowChange(newCenter, windowDur);
      } else {
        const rect = el.getBoundingClientRect();
        const ms = coverageStartMs + clamp((e.clientX - rect.left) / rect.width, 0, 1) * totalMs;
        if (action === "resize-left") {
          const newDur = Math.max(windowEndMsRef.current - ms, MIN_WINDOW_MS);
          onWindowChange(windowEndMsRef.current - newDur / 2, newDur);
        } else {
          const newDur = Math.max(ms - windowStartMsRef.current, MIN_WINDOW_MS);
          onWindowChange(windowStartMsRef.current + newDur / 2, newDur);
        }
      }
    };
    const onUp = () => {
      pointerActionRef.current = "idle";
      onScrubEndRef.current?.();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [seekFromClientX, onWindowChange, coverageStartMs, coverageEndMs, totalMs]);

  // Calendar-day segments
  const dateSegments = useMemo(() => {
    const segs: { key: string; leftPct: number; widthPct: number; label: string }[] = [];
    const DAY = 86_400_000;
    const startDay = new Date(coverageStartMs);
    let cursor = Date.UTC(startDay.getUTCFullYear(), startDay.getUTCMonth(), startDay.getUTCDate());
    while (cursor < coverageEndMs) {
      const next = cursor + DAY;
      const s = Math.max(cursor, coverageStartMs);
      const e = Math.min(next, coverageEndMs);
      if (e > s) {
        const label = new Date(cursor).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          timeZone: "UTC",
        });
        segs.push({
          key: label + cursor,
          leftPct: toPercent(s),
          widthPct: ((e - s) / totalMs) * 100,
          label,
        });
      }
      cursor = next;
    }
    return segs;
  }, [coverageStartMs, coverageEndMs, totalMs, toPercent]);

  // Flight-day segments (from launch)
  const daySegments = useMemo(() => {
    const DAY = 86_400_000;
    const segs: {
      key: string;
      leftPct: number;
      widthPct: number;
      label: string;
      fullStartMs: number;
      fullEndMs: number;
    }[] = [];
    // pre-launch segment
    if (launchMs > coverageStartMs) {
      const s = coverageStartMs;
      const e = launchMs;
      segs.push({
        key: "prelaunch",
        leftPct: toPercent(s),
        widthPct: ((e - s) / totalMs) * 100,
        label: "PRE",
        fullStartMs: s,
        fullEndMs: e,
      });
    }
    for (let d = 0; d < 12; d++) {
      const s = launchMs + d * DAY;
      const e = launchMs + (d + 1) * DAY;
      if (e < coverageStartMs || s > coverageEndMs) continue;
      const cs = Math.max(s, coverageStartMs);
      const ce = Math.min(e, coverageEndMs);
      segs.push({
        key: `fd${d}`,
        leftPct: toPercent(cs),
        widthPct: ((ce - cs) / totalMs) * 100,
        label: `FD${String(d + 1).padStart(2, "0")}`,
        fullStartMs: s,
        fullEndMs: e,
      });
    }
    return segs;
  }, [coverageStartMs, coverageEndMs, launchMs, totalMs, toPercent]);

  // Phase color band
  const phaseBands = useMemo(() => {
    const bands: { key: string; leftPct: number; widthPct: number; color: string }[] = [];
    for (let i = 0; i < phases.length; i++) {
      const s = parseUtc(phases[i].t);
      const e = i + 1 < phases.length ? parseUtc(phases[i + 1].t) : coverageEndMs;
      if (e < coverageStartMs || s > coverageEndMs) continue;
      const cs = Math.max(s, coverageStartMs);
      const ce = Math.min(e, coverageEndMs);
      bands.push({
        key: phases[i].name + i,
        leftPct: toPercent(cs),
        widthPct: ((ce - cs) / totalMs) * 100,
        color: phases[i].color,
      });
    }
    return bands;
  }, [phases, coverageStartMs, coverageEndMs, totalMs, toPercent]);

  // Event tick marks
  const eventTicks = useMemo(() => {
    return events.map((ev) => ({
      key: ev.id,
      leftPct: toPercent(parseUtc(ev.t)),
      name: ev.name,
    }));
  }, [events, toPercent]);

  // Photo + comm ticks — bucketed to ~1200 slots to cap DOM count
  const TICK_BUCKETS = 1200;
  const photoTicks = useMemo(() => {
    const buckets = new Map<number, number>();
    for (const p of photos) {
      if (isMidnightUtc(p.date)) continue;
      const ms = parseUtc(p.date);
      if (ms < coverageStartMs || ms > coverageEndMs) continue;
      const pct = ((ms - coverageStartMs) / totalMs) * 100;
      const bucket = Math.floor((pct / 100) * TICK_BUCKETS);
      if (!buckets.has(bucket)) buckets.set(bucket, pct);
    }
    return Array.from(buckets.entries()).map(([b, pct]) => ({ key: `ph${b}`, leftPct: pct }));
  }, [photos, coverageStartMs, coverageEndMs, totalMs]);

  const commTicks = useMemo(() => {
    const buckets = new Map<number, number>();
    for (const e of commEntries) {
      const ms = parseUtc(e.t);
      if (ms < coverageStartMs || ms > coverageEndMs) continue;
      const pct = ((ms - coverageStartMs) / totalMs) * 100;
      const bucket = Math.floor((pct / 100) * TICK_BUCKETS);
      if (!buckets.has(bucket)) buckets.set(bucket, pct);
    }
    return Array.from(buckets.entries()).map(([b, pct]) => ({ key: `cm${b}`, leftPct: pct }));
  }, [commEntries, coverageStartMs, coverageEndMs, totalMs]);

  // Zoom window geometry (in percent of total width)
  const winLeft = toPercent(windowStartMs);
  const winRight = toPercent(windowEndMs);
  const winWidth = winRight - winLeft;
  // Playhead
  const playheadPct = toPercent(scrubMs);

  return (
    <div
      ref={wrapRef}
      className={styles.overviewWrap}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        onScrubStart?.();
        const el = wrapRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const clickMs =
          coverageStartMs + clamp((e.clientX - rect.left) / rect.width, 0, 1) * totalMs;
        if (clickMs >= windowStartMsRef.current && clickMs <= windowEndMsRef.current) {
          // Click inside window box: drag with offset so center doesn't jump
          const windowCenterMs = (windowStartMsRef.current + windowEndMsRef.current) / 2;
          panOffsetMsRef.current = clickMs - windowCenterMs;
          pointerActionRef.current = "pan";
        } else {
          pointerActionRef.current = "seek";
          seekFromClientX(e.clientX);
        }
      }}
      role="slider"
      aria-label="Mission timeline scrubber"
      aria-valuemin={coverageStartMs}
      aria-valuemax={coverageEndMs}
      aria-valuenow={Math.floor(scrubMs)}
    >
      {/* Calendar date labels */}
      <div className={styles.dateRow}>
        {dateSegments.map((seg) => (
          <div
            key={seg.key}
            className={styles.dateLabel}
            style={{ left: `${seg.leftPct}%`, width: `${seg.widthPct}%` }}
          >
            {seg.label}
          </div>
        ))}
      </div>

      {/* Flight-day segments */}
      <div className={styles.dayRow}>
        {daySegments.map((seg) => (
          <div
            key={seg.key}
            className={styles.daySegment}
            role="button"
            tabIndex={0}
            style={{ left: `${seg.leftPct}%`, width: `${seg.widthPct}%` }}
            onPointerDown={(e) => {
              e.stopPropagation();
              pointerActionRef.current = "idle";
            }}
            onClick={(e) => {
              e.stopPropagation();
              const mid = (seg.fullStartMs + seg.fullEndMs) / 2;
              const dur = seg.fullEndMs - seg.fullStartMs;
              onWindowChange(mid, dur);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                const mid = (seg.fullStartMs + seg.fullEndMs) / 2;
                const dur = seg.fullEndMs - seg.fullStartMs;
                onWindowChange(mid, dur);
              }
            }}
          >
            {seg.label}
          </div>
        ))}
      </div>

      {/* Event ticks */}
      <div className={styles.eventTicks}>
        {eventTicks.map((ev) => (
          <div
            key={ev.key}
            className={styles.eventTick}
            style={{ left: `${ev.leftPct}%` }}
            title={ev.name}
          />
        ))}
      </div>

      {/* Photo + comm tick rows */}
      <div className={styles.mediaTicks}>
        {photoTicks.map((t) => (
          <div key={t.key} className={styles.photoTick} style={{ left: `${t.leftPct}%` }} />
        ))}
        {commTicks.map((t) => (
          <div key={t.key} className={styles.commTick} style={{ left: `${t.leftPct}%` }} />
        ))}
      </div>

      {/* Phase color band */}
      <div className={styles.phaseStrip}>
        {phaseBands.map((b) => (
          <div
            key={b.key}
            className={styles.phaseSegment}
            style={{ left: `${b.leftPct}%`, width: `${b.widthPct}%`, background: b.color }}
          />
        ))}
      </div>

      {/* SVG overlay: magWindow highlight + dim regions outside it */}
      <svg
        className={styles.overviewSvg}
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {/* Dim regions outside window */}
        <rect x="0" y="0" width={winLeft} height="100" fill="rgba(2,5,11,0.45)" />
        <rect x={winRight} y="0" width={100 - winRight} height="100" fill="rgba(2,5,11,0.45)" />
        {/* Window border */}
        <rect
          x={winLeft}
          y="4"
          width={winWidth}
          height="92"
          fill="none"
          stroke="rgba(110,154,255,0.6)"
          strokeWidth="0.4"
        />
        {/* Connector lines pointing downward (to detail strip) */}
        <line
          x1={winLeft}
          y1="96"
          x2={winLeft}
          y2="100"
          stroke="rgba(110,154,255,0.5)"
          strokeWidth="0.4"
        />
        <line
          x1={winRight}
          y1="96"
          x2={winRight}
          y2="100"
          stroke="rgba(110,154,255,0.5)"
          strokeWidth="0.4"
        />
      </svg>

      {/* magWindow grip — shown when window is very narrow */}
      {winWidth < 3 && (
        <div
          className={styles.magWindowGrip}
          style={{ left: `${winLeft + winWidth / 2}%` }}
          aria-hidden="true"
        />
      )}

      {/* magWindow resize handles */}
      <div
        className={styles.winHandle}
        style={{ left: `${winLeft}%` }}
        onPointerDown={(e) => {
          e.stopPropagation();
          e.currentTarget.setPointerCapture(e.pointerId);
          pointerActionRef.current = "resize-left";
        }}
        aria-hidden="true"
      />
      <div
        className={styles.winHandle}
        style={{ left: `${winRight}%` }}
        onPointerDown={(e) => {
          e.stopPropagation();
          e.currentTarget.setPointerCapture(e.pointerId);
          pointerActionRef.current = "resize-right";
        }}
        aria-hidden="true"
      />
      {/* Preview playhead (shown while drag-scrubbing during playback) */}
      {previewMs != null && (
        <div
          className={styles.previewPlayhead}
          style={{ left: `${toPercent(previewMs)}%` }}
          aria-hidden="true"
        />
      )}
      {/* Playhead */}
      <div className={styles.playhead} style={{ left: `${playheadPct}%` }} aria-hidden="true" />
    </div>
  );
}

// ---- ConnectorRegion --------------------------------------------------------
// Draws the tapering bezier trapezoid connecting the Tier 1 magWindow edges
// to the full-width Tier 2 DetailStrip, using a percentage-coordinate SVG.

interface ConnectorRegionProps {
  winLeftPct: number; // left edge of zoom window, 0-100
  winRightPct: number; // right edge of zoom window, 0-100
}

function ConnectorRegion({ winLeftPct, winRightPct }: ConnectorRegionProps): JSX.Element {
  // Bezier path from the narrow zoom window edges at y=0
  // to the full-width detail strip edges at y=100.
  const fill = `M ${winLeftPct} 0 C ${winLeftPct} 60 0 80 0 100 L 100 100 C 100 80 ${winRightPct} 60 ${winRightPct} 0 Z`;
  const leftLine = `M ${winLeftPct} 0 C ${winLeftPct} 60 0 80 0 100`;
  const rightLine = `M ${winRightPct} 0 C ${winRightPct} 60 100 80 100 100`;
  return (
    <div className={styles.connectorRegion}>
      <svg
        className={styles.connectorSvg}
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d={fill} fill="rgba(14,23,38,0.55)" />
        <path d={leftLine} fill="none" stroke="rgba(110,154,255,0.3)" strokeWidth="0.5" />
        <path d={rightLine} fill="none" stroke="rgba(110,154,255,0.3)" strokeWidth="0.5" />
      </svg>
    </div>
  );
}

// ---- DetailStrip (Tier 2) --------------------------------------------------
// Zoomed-in view of the time range defined by the Tier 1 magWindow.
// Drag to pan; zoom +/- buttons resize the magWindow (windowDurationMs).

interface DetailStripProps {
  windowStartMs: number;
  windowEndMs: number;
  windowDurationMs: number;
  scrubMs: number;
  events: MissionEvent[];
  photos: Photo[];
  commEntries: CommEntry[];
  coverageStartMs: number;
  coverageEndMs: number;
  onSeek: (ms: number) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  zoomLabel: string;
  previewMs?: number | null;
  onScrubStart?: () => void;
  onScrubEnd?: () => void;
}

function DetailStrip({
  windowStartMs,
  windowEndMs,
  windowDurationMs,
  scrubMs,
  events,
  photos,
  commEntries,
  coverageStartMs,
  coverageEndMs,
  onSeek,
  onZoomIn,
  onZoomOut,
  zoomLabel,
  previewMs,
  onScrubStart,
  onScrubEnd,
}: DetailStripProps): JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);
  const dragStartXRef = useRef(0);
  const dragStartScrubMsRef = useRef(0);
  const windowDurationMsRef = useRef(windowDurationMs);
  const onScrubEndRef = useRef(onScrubEnd);
  useEffect(() => {
    onScrubEndRef.current = onScrubEnd;
  }, [onScrubEnd]);
  useEffect(() => {
    windowDurationMsRef.current = windowDurationMs;
  }, [windowDurationMs]);

  const toLocalPct = useCallback(
    (ms: number) => ((ms - windowStartMs) / windowDurationMs) * 100,
    [windowStartMs, windowDurationMs]
  );

  const playheadPct = toLocalPct(scrubMs);

  // Time-axis ticks
  const ticks = useMemo(() => {
    const step = tickStepMs(windowDurationMs);
    const firstTick = Math.ceil(windowStartMs / step) * step;
    const result: { key: string; pct: number; label: string }[] = [];
    for (let t = firstTick; t <= windowEndMs; t += step) {
      const pct = ((t - windowStartMs) / windowDurationMs) * 100;
      if (pct < -5 || pct > 105) continue;
      result.push({ key: String(t), pct, label: formatDetailTick(t, windowDurationMs) });
    }
    return result;
  }, [windowStartMs, windowEndMs, windowDurationMs]);

  // Events visible in window
  const visibleEvents = useMemo(() => {
    return events
      .map((ev) => ({ ev, ms: parseUtc(ev.t) }))
      .filter(({ ms }) => ms >= windowStartMs && ms <= windowEndMs)
      .map(({ ev, ms }) => ({ ev, pct: toLocalPct(ms) }));
  }, [events, windowStartMs, windowEndMs, toLocalPct]);

  // Photo marks
  const photoMarks = useMemo(() => {
    return photos
      .filter((p) => !isMidnightUtc(p.date))
      .map((p) => ({ id: p.id, ms: parseUtc(p.date) }))
      .filter(({ ms }) => ms >= windowStartMs && ms <= windowEndMs)
      .map(({ id, ms }) => ({ key: id, pct: toLocalPct(ms) }));
  }, [photos, windowStartMs, windowEndMs, toLocalPct]);

  // Comm segments visible in window
  const commSegments = useMemo(() => {
    return commEntries
      .filter((e) => {
        const s = parseUtc(e.t);
        const end = s + e.d * 1000;
        return end >= windowStartMs && s <= windowEndMs;
      })
      .map((e) => {
        const s = parseUtc(e.t);
        const end = s + e.d * 1000;
        const cs = Math.max(s, windowStartMs);
        const ce = Math.min(end, windowEndMs);
        return {
          key: e.t + e.text.slice(0, 8),
          leftPct: toLocalPct(cs),
          widthPct: Math.max(((ce - cs) / windowDurationMs) * 100, 0.2),
          title: e.text,
        };
      });
  }, [commEntries, windowStartMs, windowEndMs, windowDurationMs, toLocalPct]);

  // Second-level grid lines — only rendered when zoomed in to 5 min or less
  const secLines = useMemo(() => {
    if (windowDurationMs > 5 * 60_000) return [];
    const lines: { key: string; pct: number }[] = [];
    const firstSec = Math.ceil(windowStartMs / 1000) * 1000;
    for (let t = firstSec; t <= windowEndMs; t += 1000) {
      const pct = ((t - windowStartMs) / windowDurationMs) * 100;
      if (pct < 0 || pct > 100) continue;
      lines.push({ key: String(t), pct });
    }
    return lines;
  }, [windowStartMs, windowEndMs, windowDurationMs]);

  // Pan drag handling
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!isDraggingRef.current || !wrapRef.current) return;
      const rect = wrapRef.current.getBoundingClientRect();
      const deltaX = e.clientX - dragStartXRef.current;
      const deltaMs = -(deltaX / rect.width) * windowDurationMsRef.current;
      onSeek(clamp(dragStartScrubMsRef.current + deltaMs, coverageStartMs, coverageEndMs));
    };
    const onUp = () => {
      isDraggingRef.current = false;
      onScrubEndRef.current?.();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [onSeek, coverageStartMs, coverageEndMs]);

  return (
    <div
      ref={wrapRef}
      className={styles.detailWrap}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        onScrubStart?.();
        isDraggingRef.current = true;
        dragStartXRef.current = e.clientX;
        dragStartScrubMsRef.current = scrubMs;
      }}
    >
      {secLines.length > 0 && (
        <div className={styles.secGrid} aria-hidden="true">
          {secLines.map((l) => (
            <div key={l.key} className={styles.secLine} style={{ left: `${l.pct}%` }} />
          ))}
        </div>
      )}

      {/* Zoom controls */}
      <div className={styles.zoomControls}>
        <button
          type="button"
          className={styles.zoomBtn}
          onPointerDown={(e) => {
            e.stopPropagation();
            onZoomIn();
          }}
          aria-label="Zoom in"
          title="Zoom in"
        >
          +
        </button>
        <button
          type="button"
          className={styles.zoomBtn}
          onPointerDown={(e) => {
            e.stopPropagation();
            onZoomOut();
          }}
          aria-label="Zoom out"
          title="Zoom out"
        >
          −
        </button>
      </div>

      <div className={styles.zoomLabel}>{zoomLabel}</div>

      {/* Time axis */}
      <div className={styles.timeAxis}>
        {ticks.map((tick) => (
          <div key={tick.key} className={styles.timeTick} style={{ left: `${tick.pct}%` }}>
            <div className={styles.timeTickLine} />
            <div className={styles.timeTickLabel}>{tick.label}</div>
          </div>
        ))}
      </div>

      {/* Mission events row */}
      <div className={styles.eventsRow}>
        <div className={styles.eventsRowLabel}>Events</div>
        {visibleEvents.map(({ ev, pct }) => (
          <div
            key={ev.id}
            className={styles.eventMarker}
            style={{ left: `${pct}%` }}
            title={`${ev.name}: ${ev.description}`}
          >
            <div className={styles.eventDiamond} style={{ background: ev.color || "#6e9aff" }} />
            <div className={styles.eventLine} />
            <div className={styles.eventLabel}>{ev.name}</div>
          </div>
        ))}
      </div>

      {/* Photos row */}
      <div className={styles.trackRow}>
        <div className={styles.trackLabel}>Photos</div>
        {photoMarks.map((m) => (
          <div
            key={m.key}
            className={`${styles.trackSegment} ${styles.trackSegmentPhoto}`}
            style={{ left: `${m.pct}%`, width: "2px" }}
          />
        ))}
      </div>

      {/* Comm/audio row */}
      <div className={styles.trackRow}>
        <div className={styles.trackLabel}>Comm</div>
        {commSegments.map((seg) => (
          <div
            key={seg.key}
            className={`${styles.trackSegment} ${styles.trackSegmentComm}`}
            style={{ left: `${seg.leftPct}%`, width: `${seg.widthPct}%` }}
            title={seg.title}
          />
        ))}
      </div>

      {/* Preview playhead (shown while drag-scrubbing during playback) */}
      {previewMs != null && (
        <div
          className={styles.detailPreviewPlayhead}
          style={{ left: `${toLocalPct(previewMs)}%` }}
          aria-hidden="true"
        />
      )}
      {/* Center-line playhead */}
      <div className={styles.detailPlayhead} style={{ left: `${playheadPct}%` }} />
    </div>
  );
}

// ---- MagnifierStrip ---------------------------------------------------------
// Behaves like a real hand-held magnifying glass: a fixed-size OVAL lens
// (widthPx × heightPx, wider than tall) centered over the DetailStrip's
// playhead that magnifies BOTH axes — time (X) and the row stack (Y).
// Time is magnified by computing pixel positions at the magnified scale;
// the Y axis is magnified by giving the lens's interior larger row heights
// / fonts / icons than the underlying detail strip, so an event diamond /
// photo tick / comm bar all visibly grow as they enter the lens.
//
//   detailSecPerPx              — seconds-per-pixel of the strip beneath
//   lensSecPerPx  = detailSecPerPx / factor
//   lensTimeSpan  = widthPx * lensSecPerPx * 1000
//
// All tick / event / photo / comm positions are computed in *pixels relative
// to the lens center*, then everything inside the lens rides on a single
// CSS `transform: translate3d(...)` so sub-pixel motion is smooth (no
// integer-pixel snap that you'd see with `left: X%`).
//
// The magnification factor is owned by the parent and cycled by clicking
// the badge in the bottom of the lens.

const MAGNIFIER_FACTORS = [5, 10, 20] as const;
type MagnifierFactor = (typeof MAGNIFIER_FACTORS)[number];
const MIN_LENS_SEC_PER_PX = 0.05; // never finer than 50ms/px (avoids absurd zooms)

interface MagnifierStripProps {
  centerMs: number;
  widthPx: number; // oval lens — horizontal radius * 2
  heightPx: number; // oval lens — vertical radius * 2
  factor: MagnifierFactor; // current magnification (5×, 10×, or 20×)
  onCycleFactor: () => void; // called when the user clicks the badge
  detailSecPerPx: number; // seconds-per-pixel of the strip beneath
  events: MissionEvent[];
  photos: Photo[];
  commEntries: CommEntry[];
}

/** Pick a "nice" tick step (ms) so we get ~3-5 labels across the lens. */
function lensTickStepMs(lensDurationMs: number): number {
  const target = lensDurationMs / 4; // aim for ~4 ticks
  const candidates = [1000, 2000, 5000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000];
  for (const c of candidates) {
    if (c >= target) return c;
  }
  return candidates[candidates.length - 1];
}

/** Always-`HH:MM:SS` formatter so all magnifier labels are the same width. */
function formatLensTick(ms: number): string {
  const d = new Date(ms);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function MagnifierStrip({
  centerMs,
  widthPx,
  heightPx,
  factor,
  onCycleFactor,
  detailSecPerPx,
  events,
  photos,
  commEntries,
}: MagnifierStripProps): JSX.Element {
  // Seconds per pixel inside the lens (always finer than the strip beneath).
  const lensSecPerPx = Math.max(detailSecPerPx / factor, MIN_LENS_SEC_PER_PX);
  const msPerPx = lensSecPerPx * 1000;
  const lensDurationMs = widthPx * msPerPx;
  const halfMs = lensDurationMs / 2;
  const windowStartMs = centerMs - halfMs;
  const windowEndMs = centerMs + halfMs;

  // Convert any absolute UTC ms to a pixel offset relative to the lens center
  // (negative = left of center). Used for the inner content that gets
  // translated as a single rigid unit; this is what avoids per-element
  // sub-pixel snap.
  const toCenterPx = useCallback((ms: number) => (ms - centerMs) / msPerPx, [centerMs, msPerPx]);

  // Major time ticks (with labels). Step adapts so we get ~4 across the lens.
  const tickStep = lensTickStepMs(lensDurationMs);
  const ticks = useMemo(() => {
    const firstTick = Math.ceil(windowStartMs / tickStep) * tickStep;
    const result: { key: number; px: number; label: string }[] = [];
    for (let t = firstTick; t <= windowEndMs; t += tickStep) {
      result.push({ key: t, px: (t - centerMs) / msPerPx, label: formatLensTick(t) });
    }
    return result;
  }, [windowStartMs, windowEndMs, tickStep, centerMs, msPerPx]);

  // Minor (per-second) grid lines — only if a second is wide enough to be useful.
  const secLines = useMemo(() => {
    if (lensSecPerPx > 2) return []; // no point — seconds would overlap
    const lines: { key: number; px: number }[] = [];
    const firstSec = Math.ceil(windowStartMs / 1000) * 1000;
    for (let t = firstSec; t <= windowEndMs; t += 1000) {
      lines.push({ key: t, px: (t - centerMs) / msPerPx });
    }
    return lines;
  }, [windowStartMs, windowEndMs, centerMs, msPerPx, lensSecPerPx]);

  const visibleEvents = useMemo(() => {
    return events
      .map((ev) => ({ ev, ms: parseUtc(ev.t) }))
      .filter(({ ms }) => ms >= windowStartMs && ms <= windowEndMs)
      .map(({ ev, ms }) => ({ ev, px: toCenterPx(ms) }));
  }, [events, windowStartMs, windowEndMs, toCenterPx]);

  const photoMarks = useMemo(() => {
    return photos
      .filter((p) => !isMidnightUtc(p.date))
      .map((p) => ({ id: p.id, ms: parseUtc(p.date) }))
      .filter(({ ms }) => ms >= windowStartMs && ms <= windowEndMs)
      .map(({ id, ms }) => ({ key: id, px: toCenterPx(ms) }));
  }, [photos, windowStartMs, windowEndMs, toCenterPx]);

  const commSegments = useMemo(() => {
    return commEntries
      .filter((e) => {
        const s = parseUtc(e.t);
        const end = s + e.d * 1000;
        return end >= windowStartMs && s <= windowEndMs;
      })
      .map((e) => {
        const s = parseUtc(e.t);
        const end = s + e.d * 1000;
        return {
          key: e.t + e.text.slice(0, 8),
          startPx: toCenterPx(s),
          widthPx: Math.max((end - s) / msPerPx, 1),
          title: e.text,
        };
      });
  }, [commEntries, windowStartMs, windowEndMs, msPerPx, toCenterPx]);

  // Half-width used by CSS (`--lens-half`) to place the row labels at the
  // lens's left edge.
  const halfPx = widthPx / 2;

  return (
    <div
      className={styles.magnifier}
      style={{
        width: `${widthPx}px`,
        height: `${heightPx}px`,
        ["--lens-half" as string]: `${halfPx}px`,
      }}
    >
      {/* Inner content positioned in pixels relative to the lens center
          (which sits at left: 50%). Everything inside uses transform-based
          positioning for smooth, sub-pixel-accurate motion. */}
      <div className={styles.magnifierInner}>
        {/* Per-second grid lines. NOTE: we use a 2D `translate(Xpx, 0)`
            here rather than `translate3d(Xpx, 0, 0)`. translate3d implicitly
            promotes each element to its own composited GPU layer, and on
            many browsers such layers can escape the lens's rounded-corner
            `overflow: hidden` clip — leaving "stuck" pixel artifacts near
            the lens edge as you pan. The parent `.magnifier` is the only
            promoted layer; that's enough to keep motion smooth without
            breaking the rounded clip. */}
        {secLines.length > 0 && (
          <div className={styles.magnifierSecGrid}>
            {secLines.map((l) => (
              <div
                key={l.key}
                className={styles.magSecLine}
                style={{ transform: `translate(${l.px}px, 0)` }}
              />
            ))}
          </div>
        )}

        {/* Major time-axis ticks + labels */}
        <div className={styles.magnifierTimeAxis}>
          {ticks.map((tick) => (
            <div
              key={tick.key}
              className={styles.magTimeTick}
              style={{ transform: `translate(${tick.px}px, 0)` }}
            >
              <div className={styles.magTimeTickLine} />
              <div className={styles.magTimeTickLabel}>{tick.label}</div>
            </div>
          ))}
        </div>

        {/* Mission events */}
        <div className={styles.magnifierEventsRow}>
          {visibleEvents.map(({ ev, px }) => (
            <div
              key={ev.id}
              className={styles.magEventMarker}
              style={{ transform: `translate(${px}px, 0)` }}
              title={`${ev.name}: ${ev.description}`}
            >
              <div className={styles.eventDiamond} style={{ background: ev.color || "#6e9aff" }} />
              <div className={styles.eventLine} />
              <div className={styles.magEventLabel}>{ev.name}</div>
            </div>
          ))}
        </div>

        {/* Photos */}
        <div className={styles.magnifierTrackRow}>
          <div className={styles.trackLabelMag}>Photos</div>
          {photoMarks.map((m) => (
            <div
              key={m.key}
              className={`${styles.magTrackSegment} ${styles.trackSegmentPhoto}`}
              style={{ transform: `translate(${m.px}px, 0)`, width: "2px" }}
            />
          ))}
        </div>

        {/* Comm */}
        <div className={styles.magnifierTrackRow}>
          <div className={styles.trackLabelMag}>Comm</div>
          {commSegments.map((seg) => (
            <div
              key={seg.key}
              className={`${styles.magTrackSegment} ${styles.trackSegmentComm}`}
              style={{
                transform: `translate(${seg.startPx}px, 0)`,
                width: `${seg.widthPx}px`,
              }}
              title={seg.title}
            />
          ))}
        </div>
      </div>

      {/* Glass rim — sits above the content, doesn't intercept clicks */}
      <div className={styles.magnifierRim} aria-hidden="true" />

      {/* Center playhead inside the lens */}
      <div className={styles.magnifierPlayhead} aria-hidden="true" />

      {/* Magnification badge — click to cycle through 5× / 10× / 20× */}
      <button
        type="button"
        className={styles.magnifierBadge}
        onClick={onCycleFactor}
        onPointerDown={(e) => {
          // The detail strip beneath uses pointerdown to start a pan drag;
          // swallow it here so clicking the badge doesn't also start a pan.
          e.stopPropagation();
        }}
        title="Click to cycle magnification (5× / 10× / 20×)"
        aria-label={`Magnification ${factor}×. Click to cycle.`}
      >
        {factor}×
      </button>
    </div>
  );
}

// ── Page component ────────────────────────────────────────────────────────────

function TimelineTest3(): JSX.Element {
  // ── Data ──────────────────────────────────────────────────────────────────
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [commEntries, setCommEntries] = useState<CommEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const base = `${ASSETS_BASE}/artemis-ii/web`;

    const fetchItinerary = fetch(`${base}/ephemeris/itinerary.json`).then((r) => {
      if (!r.ok) throw new Error(`itinerary.json HTTP ${r.status}`);
      return r.json() as Promise<Itinerary>;
    });

    const fetchPhotos = fetch(`${base}/photos.json`).then((r) =>
      r.ok ? (r.json() as Promise<Photo[]>) : Promise.resolve([] as Photo[])
    );

    const fetchComm = fetch(`${base}/comm.csv`).then((r) => {
      if (!r.ok) return [] as CommEntry[];
      return r.text().then((csv) => {
        const entries: CommEntry[] = [];
        for (const line of csv.split("\n")) {
          const trimmed = line.replace(/\r$/, "");
          if (!trimmed) continue;
          const idx1 = trimmed.indexOf("|");
          const idx2 = trimmed.indexOf("|", idx1 + 1);
          const idx3 = trimmed.indexOf("|", idx2 + 1);
          const idx4 = trimmed.indexOf("|", idx3 + 1);
          if (idx4 === -1) continue;
          const t = trimmed.slice(0, idx1);
          const d = parseFloat(trimmed.slice(idx2 + 1, idx3));
          const src = trimmed.slice(idx3 + 1, idx4);
          const text = trimmed.slice(idx4 + 1);
          entries.push({ t, d, src, text });
        }
        return entries;
      });
    });

    Promise.all([fetchItinerary, fetchPhotos, fetchComm])
      .then(([itin, ph, comm]) => {
        if (cancelled) return;
        setItinerary(itin);
        setPhotos(ph);
        setCommEntries(comm);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // ── Time state ─────────────────────────────────────────────────────────────
  const coverageStartMs = itinerary ? parseUtc(itinerary.events[0]?.t ?? itinerary.launch_utc) : 0;
  const coverageEndMs = itinerary
    ? parseUtc(
        itinerary.splashdown_utc ??
          itinerary.events[itinerary.events.length - 1]?.t ??
          itinerary.launch_utc
      )
    : 0;
  const launchMs = itinerary ? parseUtc(itinerary.launch_utc) : 0;

  const [previewMs, setPreviewMs] = useState<number | null>(null);
  const [windowDurationMs, setWindowDurationMs] = useState(ZOOM_STEPS_MS[1]); // 1 day

  // Playback clock: derive-don't-accumulate (see usePlaybackClock).  We use
  // rAF mode so the timeline ribbon scrolls smoothly at deep zoom; this is
  // safe because the hook does NOT do dt accumulation inside the rAF loop —
  // every frame just reads Date.now() and recomputes from the immutable
  // anchor, so duplicate StrictMode loops can't introduce drift or backward
  // motion.
  const playback = usePlaybackClock({
    initialMs: 0,
    initialSpeed: 60,
    initialRunning: false,
    minMs: coverageStartMs,
    maxMs: coverageEndMs,
    updateMode: "raf",
  });
  const scrubMs = playback.currentMs;
  const setScrubMs = playback.setCurrent;
  const playing = playback.isRunning;
  const speed = playback.speed as Speed;
  const setSpeed = playback.setSpeed as (s: Speed) => void;

  const previewMsRef = useRef<number | null>(null);
  const isScrubbingRef = useRef(false);

  // Initialize scrub once coverage is known (only on first itinerary load).
  const scrubInitializedRef = useRef(false);
  useEffect(() => {
    if (itinerary && !scrubInitializedRef.current) {
      scrubInitializedRef.current = true;
      setScrubMs(parseUtc(itinerary.launch_utc));
    }
  }, [itinerary, setScrubMs]);

  useEffect(() => {
    previewMsRef.current = previewMs;
  }, [previewMs]);

  // ── Window geometry ────────────────────────────────────────────────────────
  // Guard against window > coverage (can happen briefly before data loads, or if
  // an absurd zoom step is selected on a short mission). Clamping with an inverted
  // [lo, hi] range would otherwise return NaN/negative geometry.
  const effectiveWindowMs = Math.min(
    windowDurationMs,
    Math.max(coverageEndMs - coverageStartMs, 1)
  );
  // While drag-scrubbing during playback, center the view on the seek target;
  // otherwise follow the live clock.
  const displayMs = previewMs ?? scrubMs;
  const windowStartMs = useMemo(
    () =>
      clamp(displayMs - effectiveWindowMs / 2, coverageStartMs, coverageEndMs - effectiveWindowMs),
    [displayMs, effectiveWindowMs, coverageStartMs, coverageEndMs]
  );
  const windowEndMs = windowStartMs + effectiveWindowMs;

  const winLeftPct = useMemo(() => {
    if (coverageEndMs === coverageStartMs) return 0;
    return ((windowStartMs - coverageStartMs) / (coverageEndMs - coverageStartMs)) * 100;
  }, [windowStartMs, coverageStartMs, coverageEndMs]);

  const winRightPct = useMemo(() => {
    if (coverageEndMs === coverageStartMs) return 100;
    return ((windowEndMs - coverageStartMs) / (coverageEndMs - coverageStartMs)) * 100;
  }, [windowEndMs, coverageStartMs, coverageEndMs]);

  // ── Zoom ───────────────────────────────────────────────────────────────────
  const stepZoom = useCallback((delta: 1 | -1) => {
    setWindowDurationMs((prev) => {
      const idx = ZOOM_STEPS_MS.reduce(
        (best, v, i) => (Math.abs(v - prev) < Math.abs(ZOOM_STEPS_MS[best] - prev) ? i : best),
        0
      );
      const next = clamp(idx + delta, 0, ZOOM_STEPS_MS.length - 1);
      return ZOOM_STEPS_MS[next];
    });
  }, []);
  const zoomIn = useCallback(() => stepZoom(1), [stepZoom]);
  const zoomOut = useCallback(() => stepZoom(-1), [stepZoom]);

  const zoomLabel = useMemo(() => {
    const h = windowDurationMs / 3_600_000;
    if (h >= 24) return `${Math.round(h / 24)}d window`;
    if (h >= 1) return `${Math.round(h)}h window`;
    return `${Math.round(windowDurationMs / 60_000)}m window`;
  }, [windowDurationMs]);

  // ── Seek ──────────────────────────────────────────────────────────────────
  const handleSeek = useCallback(
    (ms: number) => {
      if (isScrubbingRef.current && playing) {
        const clamped = clamp(ms, coverageStartMs, coverageEndMs);
        setPreviewMs(clamped);
        previewMsRef.current = clamped;
      } else {
        setScrubMs(clamp(ms, coverageStartMs, coverageEndMs));
      }
    },
    [playing, coverageStartMs, coverageEndMs, setScrubMs]
  );

  // ── Window change (edge-drag resize or day-click zoom) ────────────────────
  const handleWindowChange = useCallback(
    (newScrubMs: number, newDurationMs: number) => {
      if (isScrubbingRef.current && playing) {
        const clamped = clamp(newScrubMs, coverageStartMs, coverageEndMs);
        setPreviewMs(clamped);
        previewMsRef.current = clamped;
      } else {
        setScrubMs(clamp(newScrubMs, coverageStartMs, coverageEndMs));
      }
      setWindowDurationMs(clamp(newDurationMs, MIN_WINDOW_MS, coverageEndMs - coverageStartMs));
    },
    [playing, coverageStartMs, coverageEndMs, setScrubMs]
  );

  const handleScrubStart = useCallback(() => {
    isScrubbingRef.current = true;
  }, []);

  const handleScrubEnd = useCallback(() => {
    isScrubbingRef.current = false;
    const prev = previewMsRef.current;
    if (prev !== null) {
      setScrubMs(clamp(prev, coverageStartMs, coverageEndMs));
      setPreviewMs(null);
      previewMsRef.current = null;
    }
  }, [coverageStartMs, coverageEndMs, setScrubMs]);

  // ── Phases (may be in itinerary or trajectory) ─────────────────────────────
  const phases: Phase[] = itinerary?.phases ?? [];

  // ── Magnifier (Tier 3) ─────────────────────────────────────────────────────
  // Measure detail strip width to decide when to show the fixed-1s/px lens.
  const detailWrapRef = useRef<HTMLDivElement>(null);
  const [detailWidthPx, setDetailWidthPx] = useState(0);
  useEffect(() => {
    const el = detailWrapRef.current;
    if (!el) return;
    const update = () => setDetailWidthPx(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [loading]); // re-run once the loading overlay swaps out for the real DOM
  // Seconds-per-pixel of the main detail strip.
  const detailSecPerPx = detailWidthPx > 0 ? windowDurationMs / 1000 / detailWidthPx : 0;
  // Show magnifier when the detail strip is coarser than 1 sec/px.
  const showMagnifier = detailSecPerPx > 1;
  // Bulged-squircle lens dimensions — wider than tall, cropped tight at
  // the top so the silhouette sits just above the time-tick row, with a
  // deeply curved bottom (see border-radius in `.magnifier`).
  const magnifierWidthPx = 320;
  const magnifierHeightPx = 150;
  // Current magnification factor; default 20×. Clicking the badge cycles
  // 20 → 5 → 10 → 20 …
  const [magnifierFactor, setMagnifierFactor] = useState<MagnifierFactor>(20);
  const cycleMagnifierFactor = useCallback(() => {
    setMagnifierFactor((prev) => {
      const idx = MAGNIFIER_FACTORS.indexOf(prev);
      return MAGNIFIER_FACTORS[(idx + 1) % MAGNIFIER_FACTORS.length];
    });
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className={styles.page}>
        <h1 className={styles.heading}>Timeline Test</h1>
        <p className={styles.subhead}>Loading…</p>
      </div>
    );
  }

  if (error || !itinerary) {
    return (
      <div className={styles.page}>
        <h1 className={styles.heading}>Timeline Test</h1>
        <p className={styles.subhead} style={{ color: "#e06060" }}>
          Error: {error ?? "No itinerary data"}
        </p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Artemis II — Timeline</h1>
      <p className={styles.subhead}>
        Interactive two-tier mission timeline · drag overview to seek · drag detail strip to pan ·
        scroll zoom buttons to zoom
      </p>

      {/* Clock strip */}
      <div className={styles.clockStrip}>
        <div className={styles.clockItem}>
          <span className={styles.clockLabel}>{previewMs != null ? "Seeking UTC" : "UTC"}</span>
          <span className={styles.clockValue}>{formatUtc(displayMs)}</span>
        </div>
        <div className={styles.clockItem}>
          <span className={styles.clockLabel}>{previewMs != null ? "Seeking MET" : "MET"}</span>
          <span className={styles.clockValue}>{formatMet(launchMs, displayMs)}</span>
        </div>
        {previewMs != null && (
          <>
            <div
              className={styles.separator}
              style={{ alignSelf: "stretch", margin: "0 0.5rem" }}
            />
            <div className={styles.clockItem}>
              <span className={styles.clockLabelPreview}>Live UTC</span>
              <span className={styles.clockValuePreview}>{formatUtc(scrubMs)}</span>
            </div>
            <div className={styles.clockItem}>
              <span className={styles.clockLabelPreview}>Live MET</span>
              <span className={styles.clockValuePreview}>{formatMet(launchMs, scrubMs)}</span>
            </div>
          </>
        )}
      </div>

      {/* Playback controls */}
      <div className={styles.controls}>
        <button
          type="button"
          className={`${styles.controlBtn} ${playing ? styles.controlBtnActive : ""}`}
          onClick={playback.toggle}
        >
          {playing ? "⏸ Pause" : "▶ Play"}
        </button>
        <div className={styles.separator} />
        <div className={styles.speedGroup}>
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              className={`${styles.controlBtn} ${speed === s && playing ? styles.controlBtnActive : ""}`}
              onClick={() => {
                setSpeed(s);
                if (!playing) playback.play();
              }}
            >
              ×{s}
            </button>
          ))}
        </div>
      </div>

      {/* Overview bar */}
      <OverviewBar
        coverageStartMs={coverageStartMs}
        coverageEndMs={coverageEndMs}
        launchMs={launchMs}
        scrubMs={displayMs}
        windowStartMs={windowStartMs}
        windowEndMs={windowEndMs}
        events={itinerary.events}
        phases={phases}
        photos={photos}
        commEntries={commEntries}
        onSeek={handleSeek}
        onWindowChange={handleWindowChange}
        previewMs={previewMs != null ? scrubMs : null}
        onScrubStart={handleScrubStart}
        onScrubEnd={handleScrubEnd}
      />

      {/* Bezier connector region */}
      <ConnectorRegion winLeftPct={winLeftPct} winRightPct={winRightPct} />

      {/* Detail strip + magnifier overlay */}
      <div ref={detailWrapRef} className={styles.detailWithMagnifier}>
        <DetailStrip
          windowStartMs={windowStartMs}
          windowEndMs={windowEndMs}
          windowDurationMs={windowDurationMs}
          scrubMs={displayMs}
          events={itinerary.events}
          photos={photos}
          commEntries={commEntries}
          coverageStartMs={coverageStartMs}
          coverageEndMs={coverageEndMs}
          onSeek={handleSeek}
          onZoomIn={zoomIn}
          onZoomOut={zoomOut}
          zoomLabel={zoomLabel}
          previewMs={previewMs != null ? scrubMs : null}
          onScrubStart={handleScrubStart}
          onScrubEnd={handleScrubEnd}
        />
        {showMagnifier && (
          <MagnifierStrip
            centerMs={displayMs}
            widthPx={magnifierWidthPx}
            heightPx={magnifierHeightPx}
            factor={magnifierFactor}
            onCycleFactor={cycleMagnifierFactor}
            detailSecPerPx={detailSecPerPx}
            events={itinerary.events}
            photos={photos}
            commEntries={commEntries}
          />
        )}
      </div>
    </div>
  );
}

export default TimelineTest3;
