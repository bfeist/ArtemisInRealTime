import { JSX, useEffect, useMemo, useRef, useState } from "react";
import brightStarsRaw from "../data/bright-stars.json";
import styles from "./TrajectoryTest.module.css";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

// ── Data shapes ─────────────────────────────────────────────────────────────

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
  source_preflight_pdf: string | null;
  source_asflown: string;
  events: MissionEvent[];
}

interface Trajectory {
  mission_id: string;
  mission_name: string;
  frame: string;
  frame_note?: string;
  earth_radius_km: number;
  moon_radius_km: number;
  launch_utc: string | null;
  splashdown_utc: string | null;
  coverage_start_utc: string;
  coverage_end_utc: string;
  phases: Phase[];
  notes: string[];
  max_distance_km: number;
  n_points: number;
  points: {
    t: string[];
    ox: number[];
    oy: number[];
    oz: number[];
    speed_earth: number[];
    speed_moon: number[];
    mx: number[];
    my: number[];
    mz: number[];
  };
}

// ── Missions available ───────────────────────────────────────────────────────

const MISSIONS: { slug: string; label: string }[] = [
  { slug: "artemis-ii", label: "Artemis II" },
  { slug: "artemis-i", label: "Artemis I" },
];

const SPEEDS = [12, 24, 60, 600, 3600] as const;
type Speed = (typeof SPEEDS)[number];

// ── Helpers ──────────────────────────────────────────────────────────────────

function parseUtc(iso: string): number {
  return Date.parse(iso.endsWith("Z") ? iso : iso + "Z");
}

function formatUtc(ms: number): string {
  const d = new Date(ms);
  return d.toISOString().replace(".000", "").replace("T", " ").replace("Z", "Z");
}

function formatMet(launchMs: number, nowMs: number): string {
  const dt = Math.max(0, nowMs - launchMs);
  const days = Math.floor(dt / 86_400_000);
  const hrs = Math.floor((dt % 86_400_000) / 3_600_000);
  const mins = Math.floor((dt % 3_600_000) / 60_000);
  const secs = Math.floor((dt % 60_000) / 1000);
  return `T+${days}d ${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function formatKm(v: number): string {
  if (v >= 10_000) return `${(v / 1000).toFixed(1).replace(/\.0$/, "")} ×10³ km`;
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 0 })} km`;
}

/** Linear-search the largest index whose t ≤ target. Cached previous index. */
function findIndex(ts: number[], target: number, hint: number): number {
  if (target <= ts[0]) return 0;
  if (target >= ts[ts.length - 1]) return ts.length - 1;
  let i = Math.max(0, Math.min(hint, ts.length - 2));
  if (ts[i] > target) {
    while (i > 0 && ts[i] > target) i--;
  } else {
    while (i < ts.length - 1 && ts[i + 1] <= target) i++;
  }
  return i;
}

function activePhase(phases: Phase[], nowMs: number): Phase | null {
  let active: Phase | null = null;
  for (const p of phases) {
    if (parseUtc(p.t) <= nowMs) active = p;
    else break;
  }
  return active;
}

// ── Star catalog (real astronomical data) ────────────────────────────────────

interface StarCatalogRaw {
  n: number;
  ra: number[];
  dec: number[];
  mag: number[];
  bv: number[];
}

/** Pre-processed for fast per-frame rendering: projections and colours baked in. */
interface StarCatalog {
  n: number;
  x: number[]; // ra / 360 → 0–1 (multiply by canvas W)
  y: number[]; // (90 - dec) / 180 → 0–1 (multiply by canvas H)
  r: number[]; // visual radius in CSS pixels (multiply by devicePixelRatio)
  color: string[]; // pre-built rgba() strings
}

// Approximate star colours from B-V colour index — heavily desaturated so
// they read as near-white background pinpoints, not vivid coloured blobs.
const _STAR_COLORS = [
  "rgba(210,220,255,", // 0: O/B pale blue-white
  "rgba(235,242,255,", // 1: A   near-white
  "rgba(255,255,252,", // 2: F/G white
  "rgba(255,245,228,", // 3: K   very pale warm
  "rgba(255,232,215,", // 4: M   very pale warm-orange
] as const;

const _starCatalog: StarCatalog = (() => {
  const raw = brightStarsRaw as StarCatalogRaw;
  const { n, ra, dec, mag, bv } = raw;
  const x: number[] = new Array(n);
  const y: number[] = new Array(n);
  const r: number[] = new Array(n);
  const color: string[] = new Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = ra[i] / 360;
    y[i] = (90 - dec[i]) / 180;
    r[i] = Math.max(0.3, 1.4 - mag[i] * 0.22);
    const alpha = Math.max(0.04, Math.min(0.55, 0.72 - mag[i] * 0.1));
    const bvi = bv[i];
    const bucket = bvi < 0 ? 0 : bvi < 0.3 ? 1 : bvi < 0.6 ? 2 : bvi < 1.0 ? 3 : 4;
    color[i] = _STAR_COLORS[bucket] + alpha.toFixed(2) + ")";
  }
  return { n, x, y, r, color };
})();

// ── Component ────────────────────────────────────────────────────────────────

function TrajectoryTest(): JSX.Element {
  const [mission, setMission] = useState("artemis-ii");
  const [data, setData] = useState<Trajectory | null>(null);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [scrubMs, setScrubMs] = useState(0); // current UTC instant in ms
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(60);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const lastIdxRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const lastFrameRef = useRef<number>(0);
  // Imperative refs so the canvas can draw without waiting for React commits
  const scrubMsRef = useRef(0);
  const drawRef = useRef<() => void>(() => {});

  // ── Load trajectory + itinerary when mission changes ───────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    setItinerary(null);
    const base = `${ASSETS_BASE}/${mission}/web/ephemeris`;
    Promise.all([
      fetch(`${base}/trajectory.json`).then((r) => {
        if (!r.ok) throw new Error(`trajectory.json HTTP ${r.status}`);
        return r.json() as Promise<Trajectory>;
      }),
      fetch(`${base}/itinerary.json`).then((r) => {
        if (!r.ok) return null; // itinerary is optional
        return r.json() as Promise<Itinerary>;
      }),
    ])
      .then(([traj, itin]) => {
        if (cancelled) return;
        setData(traj);
        setItinerary(itin);
        setScrubMs(parseUtc(traj.coverage_start_utc));
        setLoading(false);
        setPlaying(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mission]);

  // ── Numeric ts array (memoized) ─────────────────────────────────────────────
  const ts = useMemo(() => {
    if (!data) return null;
    return data.points.t.map(parseUtc);
  }, [data]);

  const startMs = data ? parseUtc(data.coverage_start_utc) : 0;
  const endMs = data ? parseUtc(data.coverage_end_utc) : 0;
  const launchMs = data?.launch_utc ? parseUtc(data.launch_utc) : startMs;

  // ── Animation loop ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!playing || !data) return;
    scrubMsRef.current = scrubMs; // sync ref when playback starts
    const tick = (frameTime: number) => {
      const dt = (frameTime - lastFrameRef.current) / 1000; // s
      lastFrameRef.current = frameTime;
      const next = scrubMsRef.current + dt * speed * 1000;
      if (next >= endMs) {
        scrubMsRef.current = endMs;
        setScrubMs(endMs);
        setPlaying(false);
        drawRef.current();
        return;
      }
      scrubMsRef.current = next;
      setScrubMs(next); // updates slider + HUD via React
      drawRef.current(); // draws canvas immediately, no React roundtrip
      rafRef.current = requestAnimationFrame(tick);
    };
    lastFrameRef.current = performance.now();
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, data, speed, endMs]);

  // ── Canvas draw function — rebuilt when mission data changes ──────────────
  // scrubMsRef is read at draw-time so the RAF can skip React render cycles.
  // Artemis I: view rotates each frame to keep Orion pointing right.
  // Artemis II: view is locked to the max-distance orientation; only a minimal
  //             rotation correction is applied when Orion would leave the canvas.
  useEffect(() => {
    if (!data || !ts) {
      drawRef.current = () => {};
      return;
    }
    const { ox, oy, mx, my } = data.points;

    // Artemis II: find the max-distance trajectory point and compute the fixed
    // view rotation that puts that direction to the right of Earth.
    let rotFixed = 0;
    if (data.mission_id === "artemis-ii") {
      let maxDistSq = 0;
      let maxDistIdx = 0;
      for (let i = 0; i < ox.length; i++) {
        const dsq = ox[i] * ox[i] + oy[i] * oy[i];
        if (dsq > maxDistSq) {
          maxDistSq = dsq;
          maxDistIdx = i;
        }
      }
      rotFixed = Math.atan2(-oy[maxDistIdx], ox[maxDistIdx]);
    }

    drawRef.current = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const W = Math.floor(rect.width * dpr);
      const H = Math.floor(rect.height * dpr);
      if (canvas.width !== W || canvas.height !== H) {
        canvas.width = W;
        canvas.height = H;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      // Interpolate current positions first — needed to derive rotation
      const idx = findIndex(ts, scrubMsRef.current, lastIdxRef.current);
      lastIdxRef.current = idx;
      const nextI = Math.min(idx + 1, ts.length - 1);
      const tGap = ts[nextI] - ts[idx];
      const alpha = tGap > 0 ? Math.min(1, (scrubMsRef.current - ts[idx]) / tGap) : 0;
      const lerp = (a: number, b: number) => a + (b - a) * alpha;
      const orionCurX = lerp(ox[idx], ox[nextI]);
      const orionCurY = lerp(oy[idx], oy[nextI]);
      const moonCurX = lerp(mx[idx], mx[nextI]);
      const moonCurY = lerp(my[idx], my[nextI]);

      // Fixed layout: Earth at 12% from left; scale so max_distance_km lands at 88%
      const earthSX = W * 0.12;
      const earthSY = H * 0.5;
      const scale = (W * 0.76) / data.max_distance_km;

      // Rotation:
      // Artemis I  — always align Orion's current direction with screen +X.
      // Artemis II — hold the max-distance orientation fixed; only rotate by the
      //              minimum amount needed to keep Orion inside the canvas.
      const psi = Math.atan2(orionCurY, orionCurX); // world angle of Orion from Earth
      let rot: number;
      if (data.mission_id !== "artemis-ii") {
        rot = -psi;
      } else {
        const orionScreenR = Math.hypot(orionCurX, orionCurY) * scale;
        if (orionScreenR < 1) {
          rot = rotFixed;
        } else {
          // theta = Orion's angular offset from the fixed view direction
          const theta = psi + rotFixed;
          const pad = 12 * dpr;
          const sinMax = Math.min(1, (earthSY - pad) / orionScreenR);
          const sinTheta = Math.sin(theta);
          if (Math.abs(sinTheta) <= sinMax) {
            rot = rotFixed;
          } else {
            // Nearest angle where |sin(theta)| == sinMax — minimum rotation delta
            const asinMax = Math.asin(sinMax);
            const thetaN = (((theta % (2 * Math.PI)) + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
            let thetaNew: number;
            if (sinTheta > sinMax) {
              thetaNew = thetaN <= Math.PI / 2 ? asinMax : Math.PI - asinMax;
            } else {
              thetaNew = thetaN >= -Math.PI / 2 ? -asinMax : -(Math.PI - asinMax);
            }
            rot = thetaNew - psi;
          }
        }
      }
      const cosR = Math.cos(rot);
      const sinR = Math.sin(rot);

      const project = (kmX: number, kmY: number): [number, number] => {
        const rx = kmX * cosR - kmY * sinR;
        const ry = kmX * sinR + kmY * cosR;
        return [earthSX + rx * scale, earthSY - ry * scale];
      };

      ctx.fillStyle = "#03060c";
      ctx.fillRect(0, 0, W, H);
      drawStars(ctx, W, H, rot);

      // Moon trail (dashed)
      ctx.strokeStyle = "rgba(160, 160, 200, 0.35)";
      ctx.lineWidth = 1 * dpr;
      ctx.setLineDash([4 * dpr, 4 * dpr]);
      ctx.beginPath();
      for (let i = 0; i < mx.length; i++) {
        const [px, py] = project(mx[i], my[i]);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.setLineDash([]);

      // Orion future trail (dimmed) — starts from interpolated current position
      ctx.lineWidth = 1.5 * dpr;
      ctx.strokeStyle = "rgba(160, 200, 255, 0.18)";
      ctx.beginPath();
      const [futX, futY] = project(orionCurX, orionCurY);
      ctx.moveTo(futX, futY);
      for (let i = nextI; i < ox.length; i++) {
        const [px, py] = project(ox[i], oy[i]);
        ctx.lineTo(px, py);
      }
      ctx.stroke();

      // Orion past trail (bright) — ends at interpolated current position
      ctx.strokeStyle = "#ffffff";
      ctx.beginPath();
      for (let i = 0; i <= idx; i++) {
        const [px, py] = project(ox[i], oy[i]);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.lineTo(futX, futY);
      ctx.stroke();

      // Earth with glow
      const [ex, ey] = project(0, 0);
      const earthR = Math.max(3 * dpr, data.earth_radius_km * scale);
      const grd = ctx.createRadialGradient(ex, ey, earthR * 0.6, ex, ey, earthR * 4);
      grd.addColorStop(0, "rgba(120, 180, 255, 0.35)");
      grd.addColorStop(1, "rgba(120, 180, 255, 0)");
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(ex, ey, earthR * 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#3a7bd5";
      ctx.beginPath();
      ctx.arc(ex, ey, earthR, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#9fd0ff";
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.arc(ex, ey, earthR, 0, Math.PI * 2);
      ctx.stroke();

      // Moon
      const [mxp, myp] = project(moonCurX, moonCurY);
      const moonR = Math.max(2 * dpr, data.moon_radius_km * scale);
      ctx.fillStyle = "#dadada";
      ctx.beginPath();
      ctx.arc(mxp, myp, moonR, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#888";
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.arc(mxp, myp, moonR, 0, Math.PI * 2);
      ctx.stroke();

      // Orion dot
      const [oxp, oyp] = project(orionCurX, orionCurY);
      ctx.fillStyle = "#ff8a3c";
      ctx.beginPath();
      ctx.arc(oxp, oyp, 4 * dpr, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.arc(oxp, oyp, 5 * dpr, 0, Math.PI * 2);
      ctx.stroke();
    };

    drawRef.current();
  }, [data, ts]);

  // ── Redraw on scrub (not during playback — RAF handles that) ───────────────
  useEffect(() => {
    scrubMsRef.current = scrubMs;
    if (!playing) drawRef.current();
  }, [scrubMs, playing]);

  // ── Derived values for HUD ─────────────────────────────────────────────────
  const hud = useMemo(() => {
    if (!data || !ts) return null;
    const idx = findIndex(ts, scrubMs, lastIdxRef.current);
    const nextI = Math.min(idx + 1, ts.length - 1);
    const tGap = ts[nextI] - ts[idx];
    const alpha = tGap > 0 ? Math.min(1, (scrubMs - ts[idx]) / tGap) : 0;
    const lerp = (a: number, b: number) => a + (b - a) * alpha;
    const { ox, oy, oz, speed_earth, speed_moon, mx, my, mz } = data.points;
    const orionX = lerp(ox[idx], ox[nextI]);
    const orionY = lerp(oy[idx], oy[nextI]);
    const orionZ = lerp(oz[idx], oz[nextI]);
    const moonX = lerp(mx[idx], mx[nextI]);
    const moonY = lerp(my[idx], my[nextI]);
    const moonZ = lerp(mz[idx], mz[nextI]);
    const distEarth = Math.hypot(orionX, orionY, orionZ);
    const distMoon = Math.hypot(orionX - moonX, orionY - moonY, orionZ - moonZ);
    const alt = distEarth - data.earth_radius_km;
    const phase = activePhase(data.phases, scrubMs);
    return {
      idx,
      distEarth,
      distMoon,
      speedEarth: lerp(speed_earth[idx], speed_earth[nextI]),
      speedMoon: lerp(speed_moon[idx], speed_moon[nextI]),
      alt,
      phase,
    };
  }, [data, ts, scrubMs]);
  // ── Itinerary-derived values ─────────────────────────────────────────
  const eventsInRange = useMemo(() => {
    if (!itinerary || !data) return [];
    const s = parseUtc(data.coverage_start_utc);
    const e = parseUtc(data.coverage_end_utc);
    return itinerary.events.filter((ev) => {
      const t = parseUtc(ev.t);
      return t >= s && t <= e;
    });
  }, [itinerary, data]);

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

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Trajectory Test</h1>
      <div className={styles.subhead}>
        Top-down view from Earth&rsquo;s north pole (EME2000 X–Y projection; +Z is the J2000
        equatorial pole, tilted ~23.4° from the ecliptic). Source ephemeris: NASA/JSC FOD OEM
        (Artemis II) and JPL Horizons (Artemis I). Inspired by{" "}
        <a href="https://issinfo.net/artemis" target="_blank" rel="noreferrer">
          issinfo.net/artemis
        </a>
        .
      </div>

      <div className={styles.missionRow}>
        {MISSIONS.map((m) => (
          <button
            key={m.slug}
            type="button"
            className={`${styles.missionBtn} ${m.slug === mission ? styles.missionBtnActive : ""}`}
            onClick={() => setMission(m.slug)}
          >
            {m.label}
          </button>
        ))}
      </div>

      {loading && <div className={styles.loading}>Loading {mission}…</div>}
      {error && <div className={styles.error}>Error: {error}</div>}

      {data && hud && (
        <>
          <div className={styles.statsStrip}>
            <div className={`${styles.statsRow} ${styles.statsRow3}`}>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>MET</div>
                <div className={styles.statBig}>{formatMet(launchMs, scrubMs)}</div>
              </div>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>UTC</div>
                <div className={styles.statBig}>{formatUtc(scrubMs)}</div>
              </div>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>Phase</div>
                <div className={styles.statBig} style={{ color: hud.phase?.color ?? "#888" }}>
                  <span className={styles.phaseChip}>{hud.phase?.name ?? "—"}</span>
                </div>
              </div>
            </div>
            <div className={`${styles.statsRow} ${styles.statsRow4}`}>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>Earth Dist</div>
                <div className={styles.statBig}>{formatKm(hud.distEarth)}</div>
              </div>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>Moon Dist</div>
                <div className={styles.statBig}>{formatKm(hud.distMoon)}</div>
              </div>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>Vel (Earth)</div>
                <div className={styles.statBig}>{hud.speedEarth.toFixed(2)} km/s</div>
              </div>
              <div className={styles.statCard}>
                <div className={styles.statLabel}>Vel (Moon)</div>
                <div className={styles.statBig}>{hud.speedMoon.toFixed(2)} km/s</div>
              </div>
            </div>
          </div>

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
                    {lastEvent.confidence === "confirmed" ? "✓ " : ""}
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

          <div className={styles.canvasWrap}>
            <canvas ref={canvasRef} className={styles.canvas} />
            <div className={styles.canvasHint}>
              Max Earth distance: {formatKm(data.max_distance_km)}
            </div>
          </div>

          <div className={styles.timelineBar}>
            <div className={styles.controlsRow}>
              <button
                type="button"
                className={styles.ctrlBtn}
                onClick={() => setPlaying((p) => !p)}
              >
                {playing ? "⏸ Pause" : "▶ Play"}
              </button>
              <button type="button" className={styles.ctrlBtn} onClick={() => setScrubMs(startMs)}>
                ⏮ Start
              </button>
              <button type="button" className={styles.ctrlBtn} onClick={() => setScrubMs(endMs)}>
                ⏭ End
              </button>
              <span className={styles.speedLabel}>Speed</span>
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`${styles.ctrlBtn} ${s === speed ? styles.ctrlBtnActive : ""}`}
                  onClick={() => setSpeed(s)}
                >
                  ×{s.toLocaleString()}
                </button>
              ))}
            </div>
            {eventsInRange.length > 0 && (
              <div className={styles.eventTicks}>
                {eventsInRange.map((ev) => {
                  const pct = ((parseUtc(ev.t) - startMs) / (endMs - startMs)) * 100;
                  return (
                    <div
                      key={ev.id}
                      className={styles.eventTick}
                      style={{
                        left: `${pct}%`,
                        borderColor: ev.color,
                        opacity: ev.confidence === "confirmed" ? 1 : 0.6,
                      }}
                      title={`${ev.confidence === "confirmed" ? "✓ " : ""}${ev.name}\n${formatUtc(parseUtc(ev.t))}\nMET ${formatMet(launchMs, parseUtc(ev.t))}`}
                    />
                  );
                })}
              </div>
            )}
            <input
              type="range"
              className={styles.slider}
              min={startMs}
              max={endMs}
              step={1000}
              value={scrubMs}
              onChange={(e) => {
                setScrubMs(Number(e.target.value));
                setPlaying(false);
              }}
            />
            <div className={styles.timelineLabels}>
              <span>{formatUtc(startMs)}</span>
              <span>{formatUtc(endMs)}</span>
            </div>
          </div>

          {data.notes.length > 0 && (
            <ul className={styles.notes}>
              {data.notes.map((n, i) => (
                <li key={i}>⚠︎ {n}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

// ── Background star field ────────────────────────────────────────────────────
// Stars are pre-rendered once onto a square offscreen canvas whose side equals
// the viewport diagonal (√(W²+H²)).  That guarantees the square, when centred
// on the viewport and rotated by any angle, still fully covers the visible area
// — solving the "missing stars in corners during rotation" problem.  The canvas
// is rebuilt only when viewport dimensions change, so per-frame cost is a
// single drawImage call.

let _starOff: HTMLCanvasElement | null = null;
let _starOffSide = 0;
let _starOffW = 0;
let _starOffH = 0;
let _starOffDpr = 0;

// Stars are laid out at (x*W, y*H) — matching the original viewport mapping —
// but offset into a D×D canvas (D = viewport diagonal) so any rotation angle
// keeps the full W×H viewport covered.  The rotation pivot is Earth's screen
// position (earthSX, earthSY) so stars and trajectory co-rotate exactly.
function ensureStarOffscreen(W: number, H: number, dpr: number): [HTMLCanvasElement, number] {
  const earthSX = W * 0.12;
  const earthSY = H * 0.5;
  // The canvas must fully cover the viewport at any rotation angle around the
  // Earth pivot.  size = 2 × (max distance from pivot to any viewport corner).
  const maxDist = Math.max(
    Math.hypot(earthSX, earthSY),
    Math.hypot(W - earthSX, earthSY),
    Math.hypot(earthSX, H - earthSY),
    Math.hypot(W - earthSX, H - earthSY)
  );
  const side = Math.ceil(maxDist * 2);
  if (
    _starOff &&
    _starOffSide === side &&
    _starOffW === W &&
    _starOffH === H &&
    _starOffDpr === dpr
  ) {
    return [_starOff, side];
  }
  const c = document.createElement("canvas");
  c.width = side;
  c.height = side;
  const octx = c.getContext("2d")!;
  const half = side / 2;
  const { n, x, y, r, color } = _starCatalog;
  for (let i = 0; i < n; i++) {
    const px = half + x[i] * W - earthSX;
    const py = half + y[i] * H - earthSY;
    const pr = r[i] * dpr;
    octx.fillStyle = color[i];
    if (pr < 0.8) {
      octx.fillRect(Math.round(px), Math.round(py), 1, 1);
    } else {
      octx.beginPath();
      octx.arc(px, py, pr, 0, Math.PI * 2);
      octx.fill();
    }
  }
  _starOff = c;
  _starOffSide = side;
  _starOffW = W;
  _starOffH = H;
  _starOffDpr = dpr;
  return [c, side];
}

function drawStars(ctx: CanvasRenderingContext2D, W: number, H: number, rot: number): void {
  const dpr = window.devicePixelRatio || 1;
  const [starCanvas, side] = ensureStarOffscreen(W, H, dpr);
  const half = side / 2;
  const earthSX = W * 0.12;
  const earthSY = H * 0.5;
  ctx.save();
  ctx.translate(earthSX, earthSY);
  ctx.rotate(-rot);
  ctx.drawImage(starCanvas, -half, -half);
  ctx.restore();
}

export default TrajectoryTest;
