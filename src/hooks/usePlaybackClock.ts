/**
 * usePlaybackClock — derive-don't-accumulate playback clock
 *
 * Inspired by the coda clock-store pattern. Instead of accumulating frame
 * deltas inside a `requestAnimationFrame` loop (which is fragile to
 * StrictMode double-mounts, dropped frames, and concurrent loops), we store
 * only three pieces of state:
 *
 *   anchorMs        — the playhead value the last time the user explicitly
 *                     set it (or the last time play/pause/speed changed)
 *   anchorWallMs    — Date.now() when anchorMs was set
 *   isRunning       — whether the clock is advancing
 *
 * The "current time" is then a pure function:
 *
 *   currentMs = isRunning
 *     ? anchorMs + (Date.now() - anchorWallMs) * speed
 *     : anchorMs
 *
 * Because every read calls Date.now() fresh, there is no accumulating error
 * and nothing to go wrong if a frame is dropped or two effect runs overlap.
 *
 * The hook returns:
 *
 *   - `clock`              : the underlying state (rarely needed directly).
 *   - `getCurrentMs()`     : pure reader, safe to call inside rAF callbacks.
 *   - `currentMs`          : React-reactive value, ticks at `tickIntervalMs`.
 *   - `setCurrent(ms)`     : seek the playhead (rebases anchor to Date.now()).
 *   - `play()`/`pause()`/`toggle()`/`setSpeed(n)`/`setRunning(b)`.
 *   - `isRunning`, `speed` : convenience selectors.
 *
 * The React-reactive `currentMs` is updated via `setInterval`, NOT via
 * requestAnimationFrame, so it stays bounded regardless of render cost
 * and can't fight with StrictMode double-invocation.  Components that need
 * sub-frame smoothness on a `<canvas>` should drive their own rAF loop and
 * read `getCurrentMs()` directly inside it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface PlaybackClockState {
  /** Playhead value (ms) at the moment the anchor was last set. */
  anchorMs: number;
  /** Date.now() at the moment the anchor was last set. */
  anchorWallMs: number;
  /** Whether the playhead is advancing. */
  isRunning: boolean;
  /** Multiplier applied to wall-clock time. 1 = real-time, 60 = 1min/sec. */
  speed: number;
}

export interface UsePlaybackClockOptions {
  /** Initial playhead value (ms). Default 0. */
  initialMs?: number;
  /** Initial speed multiplier. Default 1. */
  initialSpeed?: number;
  /** Initial running state. Default false. */
  initialRunning?: boolean;
  /** Lower bound (inclusive). Reads/seeks clamp to this. */
  minMs?: number;
  /** Upper bound (inclusive). When reached during playback, clock auto-pauses. */
  maxMs?: number;
  /**
   * How often the React-reactive `currentMs` value re-renders (ms).
   * Only consulted when `updateMode === "interval"`.
   * Default 100 (10 Hz). Use a larger number for cheap "wall clock" displays
   * or a smaller one for finer-grained UI. Canvas redraws should NOT depend
   * on this value — use `getCurrentMs()` from a local rAF loop instead.
   */
  tickIntervalMs?: number;
  /**
   * How the React-reactive `currentMs` value gets re-evaluated while the
   * clock is running:
   *
   *   - "interval" (default): a `setInterval` ticks every `tickIntervalMs`.
   *     Cheap; appropriate for HH:MM:SS displays and other low-frequency UI.
   *
   *   - "raf": a `requestAnimationFrame` loop re-reads each frame, producing
   *     buttery-smooth motion at the display refresh rate.  Safe despite the
   *     historical "rAF + dt accumulation" bug because this loop performs NO
   *     accumulation — every frame just reads `Date.now()` and recomputes
   *     from the immutable anchor, so dropped frames and StrictMode duplicate
   *     mounts can't introduce drift or backward motion.
   *
   * Default is "interval" to match coda's pattern; opt into "raf" only when
   * sub-100ms visual smoothness is required (e.g. a timeline ribbon).
   */
  updateMode?: "interval" | "raf";
  /** Called once when the running clock would advance past `maxMs`. */
  onReachedEnd?: () => void;
}

export interface UsePlaybackClockResult {
  /** Reactive current playhead (re-renders every `tickIntervalMs`). */
  currentMs: number;
  /** Pure reader; safe inside rAF callbacks and event handlers. */
  getCurrentMs: () => number;
  /** Seek the playhead. If running, the clock continues from here. */
  setCurrent: (ms: number) => void;
  /** Start the clock. No-op if already running. */
  play: () => void;
  /** Stop the clock and freeze the current value as the new anchor. */
  pause: () => void;
  /** Convenience: play() if paused, pause() if playing. */
  toggle: () => void;
  /** Change playback speed multiplier without losing position. */
  setSpeed: (speed: number) => void;
  isRunning: boolean;
  speed: number;
  /** Raw state, exposed mainly for debugging / persistence. */
  clock: PlaybackClockState;
}

function clamp(v: number, lo: number, hi: number): number {
  if (lo > hi) return lo;
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

export function usePlaybackClock(options: UsePlaybackClockOptions = {}): UsePlaybackClockResult {
  const {
    initialMs = 0,
    initialSpeed = 1,
    initialRunning = false,
    minMs = -Infinity,
    maxMs = Infinity,
    tickIntervalMs = 100,
    updateMode = "interval",
    onReachedEnd,
  } = options;

  const [clock, setClock] = useState<PlaybackClockState>(() => ({
    anchorMs: clamp(initialMs, minMs, maxMs),
    anchorWallMs: Date.now(),
    isRunning: initialRunning,
    speed: initialSpeed,
  }));

  // Keep refs in sync so the rAF/interval readers see the latest values without
  // having to re-subscribe.
  const clockRef = useRef(clock);
  useEffect(() => {
    clockRef.current = clock;
  }, [clock]);

  const minMsRef = useRef(minMs);
  const maxMsRef = useRef(maxMs);
  useEffect(() => {
    minMsRef.current = minMs;
  }, [minMs]);
  useEffect(() => {
    maxMsRef.current = maxMs;
  }, [maxMs]);

  const onReachedEndRef = useRef(onReachedEnd);
  useEffect(() => {
    onReachedEndRef.current = onReachedEnd;
  }, [onReachedEnd]);

  /**
   * Pure reader. The single source of truth for "what time is it right now."
   * Always derives from Date.now() and the stored anchor, so it's immune to
   * dropped frames, StrictMode duplicate effects, or any accumulating drift.
   */
  const getCurrentMs = useCallback((): number => {
    const c = clockRef.current;
    const raw = c.isRunning ? c.anchorMs + (Date.now() - c.anchorWallMs) * c.speed : c.anchorMs;
    return clamp(raw, minMsRef.current, maxMsRef.current);
  }, []);

  // React-reactive `currentMs`. Updated either via setInterval (cheap, for
  // wall-clock-style displays) or via requestAnimationFrame (smooth, for
  // timeline ribbons and similar).  The rAF path is safe from the historical
  // jitter bug because the read function performs zero accumulation; every
  // tick recomputes from the immutable anchor + Date.now(), so duplicate
  // loops under StrictMode just write identical values that React no-ops.
  const [currentMs, setCurrentMsState] = useState<number>(() => getCurrentMs());

  // Shared helper invoked by both subscription modes.  Reads the current
  // value, writes it to React state (which no-ops on equal values), and
  // auto-pauses when the maximum bound is hit.
  const sampleAndMaybeStop = useCallback(() => {
    const next = getCurrentMs();
    setCurrentMsState(next);
    if (next >= maxMsRef.current && clockRef.current.isRunning) {
      setClock((prev) => ({
        ...prev,
        anchorMs: maxMsRef.current,
        anchorWallMs: Date.now(),
        isRunning: false,
      }));
      onReachedEndRef.current?.();
    }
  }, [getCurrentMs]);

  useEffect(() => {
    // Always update once immediately so a fresh mount has up-to-date value.
    setCurrentMsState(getCurrentMs());
    if (!clock.isRunning) return;

    if (updateMode === "raf") {
      let rafId = 0;
      let cancelled = false;
      const tick = () => {
        if (cancelled) return;
        sampleAndMaybeStop();
        // Only continue scheduling while still running — sampleAndMaybeStop
        // may have just cleared isRunning if we hit the end.
        if (clockRef.current.isRunning) {
          rafId = requestAnimationFrame(tick);
        }
      };
      rafId = requestAnimationFrame(tick);
      return () => {
        cancelled = true;
        if (rafId) cancelAnimationFrame(rafId);
      };
    }

    const id = setInterval(sampleAndMaybeStop, tickIntervalMs);
    return () => clearInterval(id);
  }, [
    clock.isRunning,
    clock.speed,
    clock.anchorMs,
    clock.anchorWallMs,
    tickIntervalMs,
    updateMode,
    getCurrentMs,
    sampleAndMaybeStop,
  ]);

  // ── Mutators ────────────────────────────────────────────────────────────
  const setCurrent = useCallback((ms: number) => {
    setClock((prev) => ({
      ...prev,
      anchorMs: clamp(ms, minMsRef.current, maxMsRef.current),
      anchorWallMs: Date.now(),
    }));
  }, []);

  const play = useCallback(() => {
    setClock((prev) => {
      if (prev.isRunning) return prev;
      // Rebase anchor to NOW so elapsed-while-paused time isn't replayed.
      return { ...prev, anchorMs: prev.anchorMs, anchorWallMs: Date.now(), isRunning: true };
    });
  }, []);

  const pause = useCallback(() => {
    setClock((prev) => {
      if (!prev.isRunning) return prev;
      const frozen = clamp(
        prev.anchorMs + (Date.now() - prev.anchorWallMs) * prev.speed,
        minMsRef.current,
        maxMsRef.current
      );
      return { ...prev, anchorMs: frozen, anchorWallMs: Date.now(), isRunning: false };
    });
  }, []);

  const toggle = useCallback(() => {
    setClock((prev) => {
      if (prev.isRunning) {
        const frozen = clamp(
          prev.anchorMs + (Date.now() - prev.anchorWallMs) * prev.speed,
          minMsRef.current,
          maxMsRef.current
        );
        return { ...prev, anchorMs: frozen, anchorWallMs: Date.now(), isRunning: false };
      }
      return { ...prev, anchorWallMs: Date.now(), isRunning: true };
    });
  }, []);

  const setSpeed = useCallback((nextSpeed: number) => {
    setClock((prev) => {
      // If running, capture the position implied by the OLD speed first,
      // then re-anchor with the new speed so the playhead doesn't jump.
      if (prev.isRunning) {
        const frozen = clamp(
          prev.anchorMs + (Date.now() - prev.anchorWallMs) * prev.speed,
          minMsRef.current,
          maxMsRef.current
        );
        return { ...prev, anchorMs: frozen, anchorWallMs: Date.now(), speed: nextSpeed };
      }
      return { ...prev, speed: nextSpeed };
    });
  }, []);

  return useMemo<UsePlaybackClockResult>(
    () => ({
      currentMs,
      getCurrentMs,
      setCurrent,
      play,
      pause,
      toggle,
      setSpeed,
      isRunning: clock.isRunning,
      speed: clock.speed,
      clock,
    }),
    [currentMs, getCurrentMs, setCurrent, play, pause, toggle, setSpeed, clock]
  );
}
