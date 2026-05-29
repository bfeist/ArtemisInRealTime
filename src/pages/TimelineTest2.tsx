/**
 * TimelineTest2.tsx — Three-tier interactive mission timeline
 *
 * Layout (top to bottom):
 *   Tier 1 (ZoomBar, compact)   — full mission overview (flight days + phases).
 *                                  Hosts magWindow #1 = the time range shown in Tier 2.
 *
 *   Connector #1                — bezier trapezoid from Tier 1 magWindow → Tier 2.
 *
 *   Tier 2 (ZoomBar, same h)    — covers Tier 1's magWindow. Hosts magWindow #2 =
 *                                  the time range shown in Tier 3.
 *
 *   Connector #2                — bezier from Tier 2 magWindow → Tier 3.
 *
 *   Tier 3 (DetailStrip)        — zoomed-in detail view (axis, events, photos, comm).
 *
 * Both magWindows are always centered on the current playhead (displayMs), so
 * any pan/seek action keeps the three views aligned.
 *
 * The 3-tier split lets us get Tier 3 down to ~1 second-per-pixel without having
 * to shrink Tier 1's magWindow to an impractically tiny sliver.
 */

import { CSSProperties, JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./TimelineTest2.module.css";
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
  t: string;
  d: number;
  src?: string;
  text: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

// Outer window (Tier 2's width within Tier 1).
const OUTER_ZOOM_STEPS_MS = [
  10 * 24 * 3_600_000, // ~full mission
  5 * 24 * 3_600_000,
  2 * 24 * 3_600_000,
  24 * 3_600_000, // 1 day
  12 * 3_600_000,
  6 * 3_600_000,
  3 * 3_600_000,
  3_600_000, // 1 hour
];

// Inner window (Tier 3's width within Tier 2).
// Goal: smallest step ≈ 1 sec/px on a ~1500px wide bar → ~25 min window.
const INNER_ZOOM_STEPS_MS = [
  24 * 3_600_000,
  6 * 3_600_000,
  3_600_000,
  30 * 60_000,
  10 * 60_000,
  5 * 60_000,
  60_000, // 1 minute
  30_000,
];

const MIN_WINDOW_MS = 30_000;

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

function formatDetailTick(ms: number, windowMs: number): string {
  const d = new Date(ms);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  if (windowMs <= 5 * 60_000) return `${hh}:${mm}:${ss}`;
  return `${hh}:${mm}`;
}

function tickStepMs(windowMs: number): number {
  if (windowMs >= 24 * 3_600_000) return 2 * 3_600_000;
  if (windowMs >= 12 * 3_600_000) return 3_600_000;
  if (windowMs >= 3 * 3_600_000) return 30 * 60_000;
  if (windowMs >= 3_600_000) return 10 * 60_000;
  if (windowMs >= 30 * 60_000) return 5 * 60_000;
  if (windowMs >= 10 * 60_000) return 60_000;
  if (windowMs >= 2 * 60_000) return 10_000;
  return 5_000;
}

function isMidnightUtc(iso: string): boolean {
  const d = new Date(iso);
  return d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
}

function nearestZoomIndex(steps: readonly number[], v: number): number {
  return steps.reduce(
    (best, cur, i) => (Math.abs(cur - v) < Math.abs(steps[best] - v) ? i : best),
    0
  );
}

// ── ZoomBar (Tier 1 and Tier 2) ──────────────────────────────────────────────
// Generic overview-style bar: shows a coverage range, draws flight-day segments,
// phases and event ticks inside it, and hosts a draggable magWindow rectangle
// indicating the time range shown in the tier below.

interface ZoomBarProps {
  heightPx: number;
  coverageStartMs: number;
  coverageEndMs: number;
  launchMs: number;
  scrubMs: number;
  windowStartMs: number;
  windowEndMs: number;
  events: MissionEvent[];
  phases: Phase[];
  onSeek: (ms: number) => void;
  onWindowChange: (newScrubMs: number, newDurationMs: number) => void;
  previewMs?: number | null;
  onScrubStart?: () => void;
  onScrubEnd?: () => void;
  zoomLabel?: string;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  photos?: Photo[];
  commEntries?: CommEntry[];
  /** When true, day segments are rendered as labels only (no click-to-zoom buttons). */
  hideDayButtons?: boolean;
  /**
   * When true, dragging the bar uses delta-from-start semantics instead of
   * pan/seek. Drag left = go back, drag right = go forward. The magWindow
   * stays centered because windows derive from displayMs. No jump on click.
   */
  deltaDrag?: boolean;
}

function ZoomBar({
  heightPx,
  coverageStartMs,
  coverageEndMs,
  launchMs,
  scrubMs,
  windowStartMs,
  windowEndMs,
  events,
  phases,
  onSeek,
  onWindowChange,
  previewMs,
  onScrubStart,
  onScrubEnd,
  zoomLabel,
  onZoomIn,
  onZoomOut,
  photos = [],
  commEntries = [],
  hideDayButtons = false,
  deltaDrag = false,
}: ZoomBarProps): JSX.Element {
  const totalMs = Math.max(coverageEndMs - coverageStartMs, 1);
  const toPercent = useCallback(
    (ms: number) => ((ms - coverageStartMs) / totalMs) * 100,
    [coverageStartMs, totalMs]
  );

  const wrapRef = useRef<HTMLDivElement>(null);
  const pointerActionRef = useRef<
    "idle" | "seek" | "pan" | "resize-left" | "resize-right" | "delta"
  >("idle");
  const panOffsetMsRef = useRef(0);
  const deltaStartXRef = useRef(0);
  const deltaStartScrubMsRef = useRef(0);
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
      const rect = el.getBoundingClientRect();
      const pointerMs =
        coverageStartMs + clamp((e.clientX - rect.left) / rect.width, 0, 1) * totalMs;
      if (action === "seek") {
        onSeek(pointerMs);
      } else if (action === "delta") {
        const el2 = wrapRef.current;
        if (!el2) return;
        const r = el2.getBoundingClientRect();
        const deltaX = e.clientX - deltaStartXRef.current;
        const deltaMs = -(deltaX / r.width) * totalMs;
        onSeek(clamp(deltaStartScrubMsRef.current + deltaMs, coverageStartMs, coverageEndMs));
      } else if (action === "pan") {
        const windowDur = windowEndMsRef.current - windowStartMsRef.current;
        const newCenter = pointerMs - panOffsetMsRef.current;
        onWindowChange(newCenter, windowDur);
      } else if (action === "resize-left") {
        const newDur = Math.max(windowEndMsRef.current - pointerMs, MIN_WINDOW_MS);
        onWindowChange(windowEndMsRef.current - newDur / 2, newDur);
      } else if (action === "resize-right") {
        const newDur = Math.max(pointerMs - windowStartMsRef.current, MIN_WINDOW_MS);
        onWindowChange(windowStartMsRef.current + newDur / 2, newDur);
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
  }, [seekFromClientX, onWindowChange, onSeek, coverageStartMs, totalMs]);

  // Flight-day segments visible within coverage
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
    if (launchMs > coverageStartMs) {
      segs.push({
        key: "prelaunch",
        leftPct: toPercent(coverageStartMs),
        widthPct: ((launchMs - coverageStartMs) / totalMs) * 100,
        label: "PRE",
        fullStartMs: coverageStartMs,
        fullEndMs: launchMs,
      });
    }
    for (let d = 0; d < 16; d++) {
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

  // Bucket ticks to ~1200 slots so DOM count stays bounded regardless of data size
  const TICK_BUCKETS = 1200;

  const eventTicks = useMemo(() => {
    return events
      .map((ev) => ({ key: ev.id, ms: parseUtc(ev.t), name: ev.name }))
      .filter(({ ms }) => ms >= coverageStartMs && ms <= coverageEndMs)
      .map(({ key, ms, name }) => ({ key, leftPct: toPercent(ms), name }));
  }, [events, coverageStartMs, coverageEndMs, toPercent]);

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

  const winLeft = toPercent(windowStartMs);
  const winRight = toPercent(windowEndMs);
  const winWidth = winRight - winLeft;
  const playheadPct = toPercent(scrubMs);

  return (
    <div
      ref={wrapRef}
      className={styles.zoomBarWrap}
      style={{ height: heightPx }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        onScrubStart?.();
        const el = wrapRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const clickMs =
          coverageStartMs + clamp((e.clientX - rect.left) / rect.width, 0, 1) * totalMs;
        if (deltaDrag) {
          pointerActionRef.current = "delta";
          deltaStartXRef.current = e.clientX;
          deltaStartScrubMsRef.current = scrubMs;
        } else if (clickMs >= windowStartMsRef.current && clickMs <= windowEndMsRef.current) {
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
      {/* Flight-day segments fill the bar */}
      <div className={styles.dayRow}>
        {daySegments.map((seg) =>
          hideDayButtons ? (
            <div
              key={seg.key}
              className={styles.daySegment}
              style={{ left: `${seg.leftPct}%`, width: `${seg.widthPct}%`, pointerEvents: "none" }}
            >
              {seg.label}
            </div>
          ) : (
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
          )
        )}
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

      {/* Photo + comm ticks */}
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
        className={styles.zoomBarSvg}
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <rect x="0" y="0" width={winLeft} height="100" fill="rgba(2,5,11,0.45)" />
        <rect x={winRight} y="0" width={100 - winRight} height="100" fill="rgba(2,5,11,0.45)" />
        <rect
          x={winLeft}
          y="2"
          width={winWidth}
          height="96"
          fill="none"
          stroke="rgba(110,154,255,0.6)"
          strokeWidth="0.4"
        />
      </svg>

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

      {zoomLabel && <div className={styles.zoomLabel}>{zoomLabel}</div>}
      {(onZoomIn || onZoomOut) && (
        <div className={styles.zoomControls}>
          {onZoomIn && (
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
          )}
          {onZoomOut && (
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
          )}
        </div>
      )}

      {previewMs != null && (
        <div
          className={styles.previewPlayhead}
          style={{ left: `${toPercent(previewMs)}%` }}
          aria-hidden="true"
        />
      )}
      <div className={styles.playhead} style={{ left: `${playheadPct}%` }} aria-hidden="true" />
    </div>
  );
}

// ── ConnectorRegion ──────────────────────────────────────────────────────────

interface ConnectorRegionProps {
  winLeftPct: number;
  winRightPct: number;
}

function ConnectorRegion({ winLeftPct, winRightPct }: ConnectorRegionProps): JSX.Element {
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

// ── DetailStrip (Tier 3) ─────────────────────────────────────────────────────

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
  windowEndMs: _windowEndMs,
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

  // Rail-offset approach: anchor all content positions to the nearest tick-step
  // boundary before windowStartMs so element `left` values stay stable between
  // step crossings.  A single `transform: translateX(railTranslatePct%)` on the
  // scrolling content container handles sub-pixel motion via the GPU compositor,
  // eliminating the per-element CSS layout recalculation that causes jitter.
  //
  // CRITICAL: useMemo deps below use `railStart` / `railEnd` (which only change
  // at tick-step crossings), NOT `windowStartMs` / `windowEndMs` (which change
  // every animation frame).  This is what keeps the marker arrays from being
  // rebuilt 60 times per second.
  const tickStep = tickStepMs(windowDurationMs);
  const railStart = Math.floor(windowStartMs / tickStep) * tickStep;
  const railEnd = railStart + windowDurationMs + tickStep;
  const railTranslatePct = -((windowStartMs - railStart) / windowDurationMs) * 100;

  const ticks = useMemo(() => {
    const result: { key: string; pct: number; label: string }[] = [];
    for (let t = railStart; t <= railEnd; t += tickStep) {
      const pct = ((t - railStart) / windowDurationMs) * 100;
      result.push({ key: String(t), pct, label: formatDetailTick(t, windowDurationMs) });
    }
    return result;
  }, [railStart, railEnd, tickStep, windowDurationMs]);

  const visibleEvents = useMemo(() => {
    return events
      .map((ev) => ({ ev, ms: parseUtc(ev.t) }))
      .filter(({ ms }) => ms >= railStart && ms <= railEnd)
      .map(({ ev, ms }) => ({ ev, pct: ((ms - railStart) / windowDurationMs) * 100 }));
  }, [events, railStart, railEnd, windowDurationMs]);

  const photoMarks = useMemo(() => {
    // Bucket to ~1200 slots so zoomed-out views don't create thousands of DOM nodes
    const BUCKETS = 1200;
    const buckets = new Map<number, number>();
    for (const p of photos) {
      if (isMidnightUtc(p.date)) continue;
      const ms = parseUtc(p.date);
      if (ms < railStart || ms > railEnd) continue;
      const pct = ((ms - railStart) / windowDurationMs) * 100;
      const bucket = Math.floor((pct / 100) * BUCKETS);
      if (!buckets.has(bucket)) buckets.set(bucket, pct);
    }
    return Array.from(buckets.entries()).map(([b, pct]) => ({ key: `ph${b}`, pct }));
  }, [photos, railStart, railEnd, windowDurationMs]);

  const commSegments = useMemo(() => {
    // For detail strip, render actual segments when zoomed in, else bucket to ticks
    const BUCKETS = 1200;
    const results: { key: string; leftPct: number; widthPct: number; title: string }[] = [];
    const seenBuckets = new Set<number>();
    let idx = 0;
    for (const e of commEntries) {
      const s = parseUtc(e.t);
      const end = s + e.d * 1000;
      if (end < railStart || s > railEnd) {
        idx++;
        continue;
      }
      const cs = Math.max(s, railStart);
      const ce = Math.min(end, railEnd);
      const leftPct = ((cs - railStart) / windowDurationMs) * 100;
      const widthPct = Math.max(((ce - cs) / windowDurationMs) * 100, 0.2);
      // If segment is very narrow (<0.1%), deduplicate by bucket
      if (widthPct < 0.1) {
        const bucket = Math.floor((leftPct / 100) * BUCKETS);
        if (seenBuckets.has(bucket)) {
          idx++;
          continue;
        }
        seenBuckets.add(bucket);
      }
      results.push({ key: `c${idx}`, leftPct, widthPct, title: e.text });
      idx++;
    }
    return results;
  }, [commEntries, railStart, railEnd, windowDurationMs]);

  const railStyle = useMemo<CSSProperties>(
    () => ({
      position: "absolute",
      inset: 0,
      transform: `translateX(${railTranslatePct}%)`,
    }),
    [railTranslatePct]
  );

  // Second-level grid lines — only rendered when zoomed in to 5 min or less
  const secLines = useMemo(() => {
    if (windowDurationMs > 5 * 60_000) return [];
    const lines: { key: string; pct: number }[] = [];
    const firstSec = Math.ceil(railStart / 1000) * 1000;
    for (let t = firstSec; t <= railEnd; t += 1000) {
      const pct = ((t - railStart) / windowDurationMs) * 100;
      lines.push({ key: String(t), pct });
    }
    return lines;
  }, [railStart, railEnd, windowDurationMs]);

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
          <div style={railStyle}>
            {secLines.map((l) => (
              <div key={l.key} className={styles.secLine} style={{ left: `${l.pct}%` }} />
            ))}
          </div>
        </div>
      )}

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

      <div className={styles.timeAxis}>
        <div style={railStyle}>
          {ticks.map((tick) => (
            <div key={tick.key} className={styles.timeTick} style={{ left: `${tick.pct}%` }}>
              <div className={styles.timeTickLine} />
              <div className={styles.timeTickLabel}>{tick.label}</div>
            </div>
          ))}
        </div>
      </div>

      <div className={styles.eventsRow}>
        <div className={styles.eventsRowLabel}>Events</div>
        <div style={railStyle}>
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
      </div>

      <div className={styles.trackRow}>
        <div className={styles.trackLabel}>Photos</div>
        <div style={railStyle}>
          {photoMarks.map((m) => (
            <div
              key={m.key}
              className={`${styles.trackSegment} ${styles.trackSegmentPhoto}`}
              style={{ left: `${m.pct}%`, width: "2px" }}
            />
          ))}
        </div>
      </div>

      <div className={styles.trackRow}>
        <div className={styles.trackLabel}>Comm</div>
        <div style={railStyle}>
          {commSegments.map((seg) => (
            <div
              key={seg.key}
              className={`${styles.trackSegment} ${styles.trackSegmentComm}`}
              style={{ left: `${seg.leftPct}%`, width: `${seg.widthPct}%` }}
              title={seg.title}
            />
          ))}
        </div>
      </div>

      {previewMs != null && (
        <div
          className={styles.detailPreviewPlayhead}
          style={{ left: `${toLocalPct(previewMs)}%` }}
          aria-hidden="true"
        />
      )}
      <div className={styles.detailPlayhead} style={{ left: `${playheadPct}%` }} />
    </div>
  );
}

// ── Page component ───────────────────────────────────────────────────────────

function TimelineTest2(): JSX.Element {
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
  const [outerDurationMs, setOuterDurationMs] = useState(OUTER_ZOOM_STEPS_MS[3]); // 1 day
  const [innerDurationMs, setInnerDurationMs] = useState(INNER_ZOOM_STEPS_MS[2]); // 1 hour

  // Playback clock: derive-don't-accumulate.  See usePlaybackClock for details.
  // We use rAF for the React-reactive re-render because the timeline ribbon
  // needs visually buttery motion at deep zoom levels.  This is safe (unlike
  // the previous in-page rAF loop) because the hook performs zero dt
  // accumulation — each frame just reads Date.now() and recomputes from the
  // immutable anchor, so duplicate StrictMode loops can't introduce drift.
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

  // ── Window geometry (outer = Tier 2 viewport, inner = Tier 3 viewport) ───
  const totalCoverage = Math.max(coverageEndMs - coverageStartMs, 1);
  const effectiveOuterMs = Math.min(outerDurationMs, totalCoverage);
  // Inner must fit inside outer.
  const effectiveInnerMs = Math.min(innerDurationMs, effectiveOuterMs);
  const displayMs = previewMs ?? scrubMs;

  const outerStartMs = useMemo(
    () =>
      clamp(displayMs - effectiveOuterMs / 2, coverageStartMs, coverageEndMs - effectiveOuterMs),
    [displayMs, effectiveOuterMs, coverageStartMs, coverageEndMs]
  );
  const outerEndMs = outerStartMs + effectiveOuterMs;

  const innerStartMs = useMemo(
    () => clamp(displayMs - effectiveInnerMs / 2, outerStartMs, outerEndMs - effectiveInnerMs),
    [displayMs, effectiveInnerMs, outerStartMs, outerEndMs]
  );
  const innerEndMs = innerStartMs + effectiveInnerMs;

  // Connector geometries (each in coords of its upper bar).
  const outerLeftPct = useMemo(
    () => ((outerStartMs - coverageStartMs) / totalCoverage) * 100,
    [outerStartMs, coverageStartMs, totalCoverage]
  );
  const outerRightPct = useMemo(
    () => ((outerEndMs - coverageStartMs) / totalCoverage) * 100,
    [outerEndMs, coverageStartMs, totalCoverage]
  );
  const innerLeftPct = useMemo(
    () => ((innerStartMs - outerStartMs) / effectiveOuterMs) * 100,
    [innerStartMs, outerStartMs, effectiveOuterMs]
  );
  const innerRightPct = useMemo(
    () => ((innerEndMs - outerStartMs) / effectiveOuterMs) * 100,
    [innerEndMs, outerStartMs, effectiveOuterMs]
  );

  // ── Zoom ──────────────────────────────────────────────────────────────────
  const stepOuter = useCallback((delta: 1 | -1) => {
    setOuterDurationMs((prev) => {
      const idx = nearestZoomIndex(OUTER_ZOOM_STEPS_MS, prev);
      const next = clamp(idx + delta, 0, OUTER_ZOOM_STEPS_MS.length - 1);
      return OUTER_ZOOM_STEPS_MS[next];
    });
  }, []);
  const outerZoomIn = useCallback(() => stepOuter(1), [stepOuter]);
  const outerZoomOut = useCallback(() => stepOuter(-1), [stepOuter]);

  const stepInner = useCallback((delta: 1 | -1) => {
    setInnerDurationMs((prev) => {
      const idx = nearestZoomIndex(INNER_ZOOM_STEPS_MS, prev);
      const next = clamp(idx + delta, 0, INNER_ZOOM_STEPS_MS.length - 1);
      return INNER_ZOOM_STEPS_MS[next];
    });
  }, []);
  const innerZoomIn = useCallback(() => stepInner(1), [stepInner]);
  const innerZoomOut = useCallback(() => stepInner(-1), [stepInner]);

  const formatWindowLabel = (ms: number): string => {
    if (ms >= 24 * 3_600_000) return `${Math.round(ms / 86_400_000)}d window`;
    if (ms >= 3_600_000) return `${Math.round(ms / 3_600_000)}h window`;
    if (ms >= 60_000) return `${Math.round(ms / 60_000)}m window`;
    return `${Math.round(ms / 1000)}s window`;
  };
  const outerZoomLabel = useMemo(() => formatWindowLabel(effectiveOuterMs), [effectiveOuterMs]);
  const innerZoomLabel = useMemo(() => formatWindowLabel(effectiveInnerMs), [effectiveInnerMs]);

  // ── Seek / window change handlers ────────────────────────────────────────
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

  // Outer (Tier 1) window change — adjusts outerDurationMs + seeks playhead.
  const handleOuterWindowChange = useCallback(
    (newScrubMs: number, newDurationMs: number) => {
      if (isScrubbingRef.current && playing) {
        const clamped = clamp(newScrubMs, coverageStartMs, coverageEndMs);
        setPreviewMs(clamped);
        previewMsRef.current = clamped;
      } else {
        setScrubMs(clamp(newScrubMs, coverageStartMs, coverageEndMs));
      }
      setOuterDurationMs(clamp(newDurationMs, MIN_WINDOW_MS, totalCoverage));
    },
    [playing, coverageStartMs, coverageEndMs, totalCoverage, setScrubMs]
  );

  // Inner (Tier 2) window change — adjusts innerDurationMs + seeks playhead.
  const handleInnerWindowChange = useCallback(
    (newScrubMs: number, newDurationMs: number) => {
      if (isScrubbingRef.current && playing) {
        const clamped = clamp(newScrubMs, coverageStartMs, coverageEndMs);
        setPreviewMs(clamped);
        previewMsRef.current = clamped;
      } else {
        setScrubMs(clamp(newScrubMs, coverageStartMs, coverageEndMs));
      }
      setInnerDurationMs(clamp(newDurationMs, MIN_WINDOW_MS, effectiveOuterMs));
    },
    [playing, coverageStartMs, coverageEndMs, effectiveOuterMs, setScrubMs]
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

  const phases: Phase[] = itinerary?.phases ?? [];

  // ── Render ────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className={styles.page}>
        <h1 className={styles.heading}>Timeline Test 2</h1>
        <p className={styles.subhead}>Loading…</p>
      </div>
    );
  }

  if (error || !itinerary) {
    return (
      <div className={styles.page}>
        <h1 className={styles.heading}>Timeline Test 2</h1>
        <p className={styles.subhead} style={{ color: "#e06060" }}>
          Error: {error ?? "No itinerary data"}
        </p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Artemis II — Timeline (3-tier)</h1>
      <p className={styles.subhead}>
        Three-tier mission timeline · Tier 1: flight days · Tier 2: zoomed-in window · Tier 3:
        detail · drag any tier to seek / pan · use +/− on Tiers 2 & 3 to zoom
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

      {/* Tier 1: full-mission flight-day overview */}
      <ZoomBar
        heightPx={32}
        coverageStartMs={coverageStartMs}
        coverageEndMs={coverageEndMs}
        launchMs={launchMs}
        scrubMs={displayMs}
        windowStartMs={outerStartMs}
        windowEndMs={outerEndMs}
        events={itinerary.events}
        phases={phases}
        photos={photos}
        commEntries={commEntries}
        onSeek={handleSeek}
        onWindowChange={handleOuterWindowChange}
        previewMs={previewMs != null ? scrubMs : null}
        onScrubStart={handleScrubStart}
        onScrubEnd={handleScrubEnd}
      />

      <ConnectorRegion winLeftPct={outerLeftPct} winRightPct={outerRightPct} />

      {/* Tier 2: zoomed-in view of Tier 1's magWindow */}
      <ZoomBar
        heightPx={32}
        coverageStartMs={outerStartMs}
        coverageEndMs={outerEndMs}
        launchMs={launchMs}
        scrubMs={displayMs}
        windowStartMs={innerStartMs}
        windowEndMs={innerEndMs}
        events={itinerary.events}
        phases={phases}
        onSeek={handleSeek}
        onWindowChange={handleInnerWindowChange}
        previewMs={previewMs != null ? scrubMs : null}
        onScrubStart={handleScrubStart}
        onScrubEnd={handleScrubEnd}
        zoomLabel={outerZoomLabel}
        onZoomIn={outerZoomIn}
        onZoomOut={outerZoomOut}
        photos={photos}
        commEntries={commEntries}
        hideDayButtons
        deltaDrag
      />

      <ConnectorRegion winLeftPct={innerLeftPct} winRightPct={innerRightPct} />

      {/* Tier 3: detail strip */}
      <DetailStrip
        windowStartMs={innerStartMs}
        windowEndMs={innerEndMs}
        windowDurationMs={effectiveInnerMs}
        scrubMs={displayMs}
        events={itinerary.events}
        photos={photos}
        commEntries={commEntries}
        coverageStartMs={coverageStartMs}
        coverageEndMs={coverageEndMs}
        onSeek={handleSeek}
        onZoomIn={innerZoomIn}
        onZoomOut={innerZoomOut}
        zoomLabel={innerZoomLabel}
        previewMs={previewMs != null ? scrubMs : null}
        onScrubStart={handleScrubStart}
        onScrubEnd={handleScrubEnd}
      />
    </div>
  );
}

export default TimelineTest2;
