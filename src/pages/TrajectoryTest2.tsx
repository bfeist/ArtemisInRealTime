/* eslint-disable react/no-unknown-property */
import { JSX, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import { Line } from "@react-three/drei";
import * as THREE from "three";
// solar-calculator has no types — declare what we use.
import * as solar from "solar-calculator";
import brightStarsRaw from "../data/bright-stars.json";
// eslint-disable-next-line css-modules/no-unused-class
import styles from "./TrajectoryTest.module.css";
import styles2 from "./TrajectoryTest2.module.css";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

// ── Data shapes (duplicated from TrajectoryTest.tsx) ────────────────────────

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

// ── Star catalog (Hipparcos) ────────────────────────────────────────────────

interface StarCatalogRaw {
  n: number;
  ra: number[];
  dec: number[];
  mag: number[];
  bv: number[];
}

// ── Sun direction in EME2000 ────────────────────────────────────────────────

function sunDirectionEME2000(date: Date): THREE.Vector3 {
  const t = solar.century(date);
  const lng = solar.apparentLongitude(t); // ecliptic longitude (deg)
  const eps = solar.obliquityOfEcliptic(t); // deg
  const lngRad = (lng * Math.PI) / 180;
  const epsRad = (eps * Math.PI) / 180;
  return new THREE.Vector3(
    Math.cos(lngRad),
    Math.sin(lngRad) * Math.cos(epsRad),
    Math.sin(lngRad) * Math.sin(epsRad)
  ).normalize();
}

// ── Earth rotation angle (GAST, simplified) ─────────────────────────────────

const J2000_EPOCH_MS = Date.UTC(2000, 0, 1, 12, 0, 0);
const MS_PER_DAY = 86_400_000;

function earthRotationAngle(utcMs: number): number {
  const T = (utcMs - J2000_EPOCH_MS) / MS_PER_DAY;
  const deg = 280.46061837 + 360.98564736629 * T;
  return ((deg % 360) * Math.PI) / 180;
}

// ── Constants ───────────────────────────────────────────────────────────────

const R_EARTH_KM = 6371;
// Earth and Moon are rendered at true EME2000 scale (1 unit = 1 R⊕).
// The orthographic camera frustum is set so that Earth appears at 12% from
// the left edge and the full trajectory fits to 88%, exactly matching the 2D
// canvas scale formula: scale = W * 0.76 / max_distance_km.

// ── Orthographic camera (matches 2D scale formula) ─────────────────────────

// Sets the orthographic frustum so Earth sits at 12% from left and the max
// trajectory distance lands at 88% — identical to the 2D drawRef scaling.
function SceneCamera({ maxDistKm }: { maxDistKm: number }): null {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  useEffect(() => {
    const cam = camera as THREE.OrthographicCamera;
    const maxDistRearth = maxDistKm / R_EARTH_KM;
    const totalWidth = maxDistRearth / 0.76; // world units (R⊕)
    const aspect = size.width / size.height;
    cam.left = -totalWidth * 0.12;
    cam.right = totalWidth * 0.88;
    cam.top = totalWidth / aspect / 2;
    cam.bottom = -totalWidth / aspect / 2;
    cam.near = 0.01;
    cam.far = 2000;
    cam.position.set(0, 0, 10);
    cam.updateProjectionMatrix();
  }, [camera, size, maxDistKm]);
  return null;
}

// ── Texture URLs ────────────────────────────────────────────────────────────

// Grayscale water mask (white = ocean, dark = land) from the three-globe package.
const TEX_EARTH_WATER = "https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-water.png";

// ── Shaders ─────────────────────────────────────────────────────────────────

const dayNightVertex = /* glsl */ `
varying vec3 vWorldNormal;
varying vec2 vUv;
void main() {
  vWorldNormal = normalize(mat3(modelMatrix) * normal);
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

// waterMask.r: 1 = ocean, 0 = land. Land blends from tropical green → earthy
// brown with latitude, plus white polar ice caps.
const dayNightFragment = /* glsl */ `
uniform sampler2D waterMask;
uniform vec3 sunDirection;
uniform vec3 uOcean;
uniform vec3 uLandTropical;
uniform vec3 uLandTemperate;
uniform vec3 uIce;
varying vec3 vWorldNormal;
varying vec2 vUv;
void main() {
  float water = texture2D(waterMask, vUv).r;
  float lat   = abs(vUv.y - 0.5) * 2.0; // 0 = equator, 1 = pole
  float ice   = smoothstep(0.78, 0.92, lat);
  vec3 land   = mix(uLandTropical, uLandTemperate, smoothstep(0.15, 0.55, lat));
  vec3 base   = mix(land, uOcean, water);
  vec3 surface = mix(base, uIce, ice);
  float sun   = dot(normalize(vWorldNormal), sunDirection);
  float light = 0.22 + 0.78 * smoothstep(-0.15, 0.15, sun);
  gl_FragColor = vec4(surface * light, 1.0);
}
`;

const moonVertex = dayNightVertex;
const moonFragment = /* glsl */ `
uniform vec3 sunDirection;
uniform vec3 baseColor;
varying vec3 vWorldNormal;
varying vec2 vUv;
void main() {
  float intensity = dot(normalize(vWorldNormal), sunDirection);
  float blend = smoothstep(-0.05, 0.05, intensity);
  vec3 lit = baseColor * (0.08 + 0.92 * blend);
  gl_FragColor = vec4(lit, 1.0);
}
`;

// Atmosphere: sun-side only halo using BackSide normals.
const atmosphereVertex = /* glsl */ `
varying vec3 vWorldNormal;
void main() {
  vWorldNormal = normalize(mat3(modelMatrix) * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const atmosphereFragment = /* glsl */ `
uniform vec3 sunDirection;
varying vec3 vWorldNormal;
void main() {
  vec3 norm = -normalize(vWorldNormal); // BackSide normals point inward
  float sun = dot(norm, sunDirection);
  float glow = smoothstep(-0.25, 0.25, sun);
  gl_FragColor = vec4(0.3, 0.65, 1.0, 0.18 * glow);
}
`;

// ── Earth (flat 4-color sphere + GAST rotation) ─────────────────────────────

function Earth({
  sunDirRef,
  scrubMsRef,
}: {
  sunDirRef: React.MutableRefObject<THREE.Vector3>;
  scrubMsRef: React.MutableRefObject<number>;
}): JSX.Element {
  const waterTex = useLoader(THREE.TextureLoader, TEX_EARTH_WATER);
  const meshRef = useRef<THREE.Mesh>(null);

  // Pre-rotate sphere so its Y-pole becomes the +Z (EME2000) pole.
  const geom = useMemo(() => {
    const g = new THREE.SphereGeometry(1, 64, 64);
    g.rotateX(Math.PI / 2);
    return g;
  }, []);

  const uniforms = useMemo(
    () => ({
      waterMask: { value: waterTex },
      sunDirection: { value: new THREE.Vector3(1, 0, 0) },
      uOcean: { value: new THREE.Color(0x1e96e8) },
      uLandTropical: { value: new THREE.Color(0x3d7828) },
      uLandTemperate: { value: new THREE.Color(0x7a5e22) },
      uIce: { value: new THREE.Color(0xf0f6ff) },
    }),
    [waterTex]
  );

  useFrame(() => {
    uniforms.sunDirection.value.copy(sunDirRef.current);
    // GAST Earth rotation: applies sidereal rotation about the EME2000 Z axis.
    if (meshRef.current) {
      meshRef.current.rotation.z = earthRotationAngle(scrubMsRef.current);
    }
  });

  return (
    <mesh ref={meshRef} geometry={geom}>
      <shaderMaterial
        vertexShader={dayNightVertex}
        fragmentShader={dayNightFragment}
        uniforms={uniforms}
      />
    </mesh>
  );
}

// ── Atmosphere halo (sun-side only) ─────────────────────────────────────────

function Atmosphere({
  sunDirRef,
}: {
  sunDirRef: React.MutableRefObject<THREE.Vector3>;
}): JSX.Element {
  const uniforms = useMemo(() => ({ sunDirection: { value: new THREE.Vector3(1, 0, 0) } }), []);
  useFrame(() => {
    uniforms.sunDirection.value.copy(sunDirRef.current);
  });
  return (
    <mesh scale={1.06}>
      <sphereGeometry args={[1, 48, 48]} />
      <shaderMaterial
        vertexShader={atmosphereVertex}
        fragmentShader={atmosphereFragment}
        uniforms={uniforms}
        transparent
        side={THREE.BackSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}

// ── Moon ────────────────────────────────────────────────────────────────────

function Moon({
  positionRef,
  sunDirRef,
  radius,
}: {
  positionRef: React.MutableRefObject<THREE.Vector3>;
  sunDirRef: React.MutableRefObject<THREE.Vector3>;
  radius: number;
}): JSX.Element {
  const meshRef = useRef<THREE.Mesh>(null);
  const uniforms = useMemo(
    () => ({
      baseColor: { value: new THREE.Color("#cfcfcf") },
      sunDirection: { value: new THREE.Vector3(1, 0, 0) },
    }),
    []
  );
  useFrame(() => {
    if (meshRef.current) {
      meshRef.current.position.copy(positionRef.current);
    }
    uniforms.sunDirection.value.copy(sunDirRef.current);
  });
  return (
    <mesh ref={meshRef}>
      <sphereGeometry args={[radius, 48, 48]} />
      <shaderMaterial vertexShader={moonVertex} fragmentShader={moonFragment} uniforms={uniforms} />
    </mesh>
  );
}

// ── Orion marker (sprite, fixed pixel size) ─────────────────────────────────

function Orion({
  positionRef,
}: {
  positionRef: React.MutableRefObject<THREE.Vector3>;
}): JSX.Element {
  const spriteRef = useRef<THREE.Sprite>(null);
  const ringRef = useRef<THREE.Sprite>(null);
  useFrame(() => {
    if (spriteRef.current) spriteRef.current.position.copy(positionRef.current);
    if (ringRef.current) ringRef.current.position.copy(positionRef.current);
  });
  // Build a circle texture once
  const dotTex = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 64;
    c.height = 64;
    const g = c.getContext("2d")!;
    g.beginPath();
    g.arc(32, 32, 22, 0, Math.PI * 2);
    g.fillStyle = "#ff8a3c";
    g.fill();
    g.lineWidth = 4;
    g.strokeStyle = "#ffffff";
    g.stroke();
    const tex = new THREE.CanvasTexture(c);
    tex.needsUpdate = true;
    return tex;
  }, []);
  return (
    <sprite ref={spriteRef} scale={[1.2, 1.2, 1]}>
      <spriteMaterial map={dotTex} depthTest={false} sizeAttenuation={true} />
    </sprite>
  );
}

// ── Trajectory lines (imperative updates each frame) ────────────────────────

function TrajectoryLines({
  data,
  idxRef,
  orionPosRef,
}: {
  data: Trajectory;
  idxRef: React.MutableRefObject<number>;
  orionPosRef: React.MutableRefObject<THREE.Vector3>;
}): JSX.Element {
  const { ox, oy, oz, mx, my, mz } = data.points;
  const n = ox.length;

  // Full Moon path (static)
  const moonPoints = useMemo(() => {
    const arr: THREE.Vector3[] = new Array(n);
    for (let i = 0; i < n; i++)
      arr[i] = new THREE.Vector3(mx[i] / R_EARTH_KM, my[i] / R_EARTH_KM, mz[i] / R_EARTH_KM);
    return arr;
  }, [mx, my, mz, n]);

  // Past / future buffers — we keep two BufferGeometries with full capacity
  // and use drawRange to slice them imperatively in useFrame.
  const pastGeomRef = useRef<THREE.BufferGeometry>(null);
  const futureGeomRef = useRef<THREE.BufferGeometry>(null);

  const positions = useMemo(() => {
    const arr = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      arr[i * 3] = ox[i] / R_EARTH_KM;
      arr[i * 3 + 1] = oy[i] / R_EARTH_KM;
      arr[i * 3 + 2] = oz[i] / R_EARTH_KM;
    }
    return arr;
  }, [ox, oy, oz, n]);

  useEffect(() => {
    if (pastGeomRef.current) {
      pastGeomRef.current.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      pastGeomRef.current.setDrawRange(0, 1);
    }
    if (futureGeomRef.current) {
      futureGeomRef.current.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      futureGeomRef.current.setDrawRange(0, n);
    }
  }, [positions, n]);

  useFrame(() => {
    const idx = idxRef.current;
    // Past: indices 0..idx, plus current interpolated position at the end.
    if (pastGeomRef.current) {
      const attr = pastGeomRef.current.getAttribute("position") as THREE.BufferAttribute;
      const arr = attr.array as Float32Array;
      // Write the interpolated current position into slot (idx+1)
      const slot = Math.min(idx + 1, n - 1);
      arr[slot * 3] = orionPosRef.current.x;
      arr[slot * 3 + 1] = orionPosRef.current.y;
      arr[slot * 3 + 2] = orionPosRef.current.z;
      attr.needsUpdate = true;
      pastGeomRef.current.setDrawRange(0, idx + 2);
    }
    if (futureGeomRef.current) {
      const attr = futureGeomRef.current.getAttribute("position") as THREE.BufferAttribute;
      const arr = attr.array as Float32Array;
      // Slot 0 of the future line = current interpolated position; restore the
      // original launch position via copy from positions (but we don't draw it).
      // Simpler: draw from idx+1 .. n-1, and overwrite point at idx with the
      // current interpolated position so the line starts smoothly.
      const slot = Math.max(0, idx);
      arr[slot * 3] = orionPosRef.current.x;
      arr[slot * 3 + 1] = orionPosRef.current.y;
      arr[slot * 3 + 2] = orionPosRef.current.z;
      attr.needsUpdate = true;
      futureGeomRef.current.setDrawRange(slot, n - slot);
    }
  });

  return (
    <>
      {/* Moon trail (full, static, dashed via Line from drei) */}
      <Line
        points={moonPoints}
        color="#a0a0c8"
        lineWidth={1}
        transparent
        opacity={0.35}
        dashed
        dashSize={0.3}
        gapSize={0.3}
      />
      {/* Future trail */}
      <line>
        <bufferGeometry ref={futureGeomRef} />
        <lineBasicMaterial color="#a0c8ff" transparent opacity={0.22} />
      </line>
      {/* Past trail (bright) */}
      <line>
        <bufferGeometry ref={pastGeomRef} />
        <lineBasicMaterial color="#ffffff" />
      </line>
    </>
  );
}

// ── Star shaders (gaussian soft disk, per-star size from magnitude) ─────────

const starVertex = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
varying vec3 vColor;
uniform float uPixelRatio;
void main() {
  vColor = aColor;
  gl_PointSize = aSize * uPixelRatio;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

// Gaussian falloff: soft disk that fades to transparent at the sprite edge.
// gl_PointCoord goes (0,0)→(1,1) across the gl_PointSize square.
// Two-term gaussian: sharp bright core + wider diffuse halo, matching how
// bright stars look to the eye (or on long-exposure film).
const starFragment = /* glsl */ `
varying vec3 vColor;
void main() {
  vec2 coord = gl_PointCoord - vec2(0.5);
  float r = length(coord) * 2.0;
  float core = exp(-r * r * 8.0);
  float halo = exp(-r * r * 1.8) * 0.50;
  float alpha = min(1.0, core + halo);
  if (alpha < 0.008) discard;
  gl_FragColor = vec4(vColor, alpha);
}
`;

// ── Star field (Hipparcos, celestial sphere → orthographic projection) ─────

// Stars are placed on a flat plane far behind all scene geometry. Each star's
// (RA, Dec) is treated as a direction on the celestial sphere and projected
// onto the back plane via an orthographic celestial projection:
//   dx =  cos(dec) · cos(ra)
//   dy =  sin(dec)
//   dz =  cos(dec) · sin(ra)
// Stars in the back hemisphere (dz ≤ 0) are visible; their (dx, dy) is scaled
// to the circumscribed-square radius of the camera frustum so the field stays
// filled at any rotation angle. This is the correct astronomical analogue of
// the d3-celestial flat-sky projection (and matches what the human eye sees
// when looking out from behind Earth).
function StarField({ maxDistKm }: { maxDistKm: number }): JSX.Element {
  const size = useThree((s) => s.size);
  const dpr = useThree((s) => s.gl.getPixelRatio());

  const { positions, colors, sizes } = useMemo(() => {
    const raw = brightStarsRaw as StarCatalogRaw;
    const { n, ra, dec, mag, bv } = raw;

    // Circumscribed radius: farthest frustum corner from Earth (the pivot).
    const maxDistRearth = maxDistKm / R_EARTH_KM;
    const totalWidth = maxDistRearth / 0.76;
    const aspect = size.width > 0 && size.height > 0 ? size.width / size.height : 4.5;
    const R = totalWidth * 0.88;
    const T = totalWidth / (2 * aspect);
    const maxDist = Math.hypot(R, T);

    const pos = new Float32Array(n * 3);
    const col = new Float32Array(n * 3);
    const siz = new Float32Array(n);
    const DEG = Math.PI / 180;

    for (let i = 0; i < n; i++) {
      const raRad = ra[i] * DEG;
      const decRad = dec[i] * DEG;
      const cd = Math.cos(decRad);
      // Direction vector on the unit celestial sphere.
      const dx = cd * Math.cos(raRad);
      const dy = Math.sin(decRad);
      // Orthographic projection of the celestial sphere onto the back plane.
      // Scale by maxDist so the unit disk fills the circumscribed square,
      // guaranteeing star coverage of the full frustum at any rotation.
      pos[i * 3] = dx * maxDist;
      pos[i * 3 + 1] = dy * maxDist;
      pos[i * 3 + 2] = -1900;

      // Per-star sprite size in CSS pixels: bright (low mag) → larger glow disk.
      // Catalog range: mag ≈ -1.44 (Sirius) to 6.0. Generous sizes so the
      // gaussian softness is clearly visible even on non-retina screens.
      siz[i] = Math.max(1.5, Math.min(9.0, 7.5 - mag[i] * 1.0));

      // B-V → approximate stellar colour (slightly saturated to look vivid)
      const b = bv[i];
      let r = 1,
        g = 1,
        bl = 1;
      if (b < 0) {
        r = 0.55;
        g = 0.75;
        bl = 1.0;
      } else if (b < 0.3) {
        r = 0.82;
        g = 0.91;
        bl = 1.0;
      } else if (b < 0.6) {
        r = 1.0;
        g = 1.0;
        bl = 0.9;
      } else if (b < 1.0) {
        r = 1.0;
        g = 0.88;
        bl = 0.65;
      } else {
        r = 1.0;
        g = 0.65;
        bl = 0.35;
      }

      const brightness = Math.max(0.22, Math.min(1.0, 1.05 - mag[i] * 0.13));
      col[i * 3] = r * brightness;
      col[i * 3 + 1] = g * brightness;
      col[i * 3 + 2] = bl * brightness;
    }
    return { positions: pos, colors: col, sizes: siz };
  }, [maxDistKm, size]);

  const uniforms = useMemo(() => ({ uPixelRatio: { value: dpr } }), [dpr]);

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-aColor" args={[colors, 3]} />
        <bufferAttribute attach="attributes-aSize" args={[sizes, 1]} />
      </bufferGeometry>
      <shaderMaterial
        vertexShader={starVertex}
        fragmentShader={starFragment}
        uniforms={uniforms}
        transparent
        depthTest={true}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}

// ── Inner 3D scene ──────────────────────────────────────────────────────────

interface SceneProps {
  data: Trajectory;
  ts: number[];
  scrubMsRef: React.MutableRefObject<number>;
  rotFixed: number;
  isArtemisII: boolean;
  tiltQ: THREE.Quaternion;
}

function TrajectoryScene({
  data,
  ts,
  scrubMsRef,
  rotFixed,
  isArtemisII,
  tiltQ,
}: SceneProps): JSX.Element {
  const groupRef = useRef<THREE.Group>(null);
  const starGroupRef = useRef<THREE.Group>(null);
  const skyGroupRef = useRef<THREE.Group>(null);
  const lastIdxRef = useRef(0);
  const idxRef = useRef(0);
  const orionPosRef = useRef(new THREE.Vector3());
  const moonPosRef = useRef(new THREE.Vector3());
  const sunDirRef = useRef(new THREE.Vector3(1, 0, 0));
  // Pre-allocated temporaries to avoid per-frame GC pressure.
  const _tmpVec = useRef(new THREE.Vector3());
  const _tmpQuat = useRef(new THREE.Quaternion());
  // Orbital normal in EME2000/local space = direction tiltQ maps to world +Z.
  const orbNormal = useMemo(
    () => new THREE.Vector3(0, 0, 1).applyQuaternion(tiltQ.clone().invert()),
    [tiltQ]
  );

  const { ox, oy, oz, mx, my, mz } = data.points;
  // True orbital scale: 1 unit = 1 R⊕, same as the trajectory coordinate system.
  const moonRadius = data.moon_radius_km / R_EARTH_KM;

  useFrame((state) => {
    const now = scrubMsRef.current;
    const idx = findIndex(ts, now, lastIdxRef.current);
    lastIdxRef.current = idx;
    idxRef.current = idx;
    const nextI = Math.min(idx + 1, ts.length - 1);
    const tGap = ts[nextI] - ts[idx];
    const alpha = tGap > 0 ? Math.min(1, (now - ts[idx]) / tGap) : 0;
    const lerp = (a: number, b: number) => a + (b - a) * alpha;

    const ocx = lerp(ox[idx], ox[nextI]);
    const ocy = lerp(oy[idx], oy[nextI]);
    const ocz = lerp(oz[idx], oz[nextI]);
    const mcx = lerp(mx[idx], mx[nextI]);
    const mcy = lerp(my[idx], my[nextI]);
    const mcz = lerp(mz[idx], mz[nextI]);

    orionPosRef.current.set(ocx / R_EARTH_KM, ocy / R_EARTH_KM, ocz / R_EARTH_KM);
    moonPosRef.current.set(mcx / R_EARTH_KM, mcy / R_EARTH_KM, mcz / R_EARTH_KM);

    // Sun direction: compute in EME2000 then rotate into screen space with `rot`.
    // (computed after rot is known — see below)

    // Project Orion into world space (after orbital-plane tilt) so `rot` keeps
    // Orion on screen +X regardless of the viewing tilt.
    // Artemis I: always align.  Artemis II: hold max-distance with clamping.
    _tmpVec.current.set(ocx, ocy, ocz).applyQuaternion(tiltQ);
    const psi = Math.atan2(_tmpVec.current.y, _tmpVec.current.x);
    let rot: number;
    if (!isArtemisII) {
      rot = -psi;
    } else {
      const { width, height } = state.size;
      const aspect = width > 0 && height > 0 ? width / height : 4.5;
      const halfHeight = data.max_distance_km / R_EARTH_KM / 0.76 / (2 * aspect);
      const orionR = Math.hypot(_tmpVec.current.x, _tmpVec.current.y) / R_EARTH_KM;
      if (orionR < 1) {
        rot = rotFixed;
      } else {
        const theta = psi + rotFixed;
        // 12 CSS-px padding, expressed in world units (R⊕)
        const pad = halfHeight * (24 / height);
        const sinMax = Math.min(1, (halfHeight - pad) / orionR);
        const sinTheta = Math.sin(theta);
        if (Math.abs(sinTheta) <= sinMax) {
          rot = rotFixed;
        } else {
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
    // Build in-plane rotation quaternion around the orbital normal, then combine
    // with the tilt so shaders receive the correct world-space sun direction.
    _tmpQuat.current.setFromAxisAngle(orbNormal, rot);
    const rawSun = sunDirectionEME2000(new Date(now));
    sunDirRef.current.copy(rawSun).applyQuaternion(_tmpQuat.current).applyQuaternion(tiltQ);

    if (groupRef.current) {
      groupRef.current.setRotationFromQuaternion(_tmpQuat.current);
    }
    // Earth/Atmosphere rotate with the trajectory group (orbNormal tilt).
    if (starGroupRef.current) {
      starGroupRef.current.setRotationFromQuaternion(_tmpQuat.current);
    }
    // Star field lives in world space outside tiltQ — simple Z rotation only
    // so it stays at z=-1900 world and covers the frustum correctly.
    if (skyGroupRef.current) {
      skyGroupRef.current.rotation.z = rot;
    }
  });

  return (
    <>
      {/* Star field in world space — Z rotation only so z=-1900 is preserved
          and stars stay within the camera frustum regardless of orbital tilt. */}
      <group ref={skyGroupRef}>
        <StarField maxDistKm={data.max_distance_km} />
      </group>
      {/* Orbital-plane tilt: rotates the whole scene so the mission's orbital
          normal faces the camera, giving a face-on view with minimal foreshortening. */}
      <group quaternion={tiltQ}>
        {/* Earth and Atmosphere rotate with the trajectory (orbNormal tilt). */}
        <group ref={starGroupRef}>
          <Atmosphere sunDirRef={sunDirRef} />
          <Earth sunDirRef={sunDirRef} scrubMsRef={scrubMsRef} />
        </group>
        {/* Trajectory group: rotates by `rot` to keep Orion on the right */}
        <group ref={groupRef}>
          <Moon positionRef={moonPosRef} sunDirRef={sunDirRef} radius={moonRadius} />
          <Orion positionRef={orionPosRef} />
          <TrajectoryLines data={data} idxRef={idxRef} orionPosRef={orionPosRef} />
        </group>
      </group>
    </>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

function TrajectoryTest2(): JSX.Element {
  const [mission, setMission] = useState("artemis-ii");
  const [data, setData] = useState<Trajectory | null>(null);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [scrubMs, setScrubMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(60);

  const lastIdxRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const lastFrameRef = useRef<number>(0);
  const scrubMsRef = useRef(0);

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
        if (!r.ok) return null;
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

  const ts = useMemo(() => {
    if (!data) return null;
    return data.points.t.map(parseUtc);
  }, [data]);

  // Compute the orbital angular momentum direction (L) via cumulative cross
  // product of consecutive position vectors, then build a quaternion that maps
  // L → world +Z so the orbital plane faces the camera.
  const tiltQ = useMemo((): THREE.Quaternion => {
    if (!data) return new THREE.Quaternion();
    const { ox, oy, oz } = data.points;
    const n = ox.length;
    let Lx = 0,
      Ly = 0,
      Lz = 0;
    for (let i = 0; i < n - 1; i++) {
      const ax = ox[i],
        ay = oy[i],
        az = oz[i];
      const bx = ox[i + 1],
        by = oy[i + 1],
        bz = oz[i + 1];
      Lx += ay * bz - az * by;
      Ly += az * bx - ax * bz;
      Lz += ax * by - ay * bx;
    }
    const mag = Math.sqrt(Lx * Lx + Ly * Ly + Lz * Lz);
    Lx /= mag;
    Ly /= mag;
    Lz /= mag;
    // Ensure the normal points toward the camera's side (Lz > 0 in EME2000).
    if (Lz < 0) {
      Lx = -Lx;
      Ly = -Ly;
      Lz = -Lz;
    }
    return new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(Lx, Ly, Lz),
      new THREE.Vector3(0, 0, 1)
    );
  }, [data]);

  const startMs = data ? parseUtc(data.coverage_start_utc) : 0;
  const endMs = data ? parseUtc(data.coverage_end_utc) : 0;
  const launchMs = data?.launch_utc ? parseUtc(data.launch_utc) : startMs;

  // Precompute rotFixed for Artemis II: the in-plane angle (in world/tilt space)
  // that aligns the 3D max-distance point with screen +X.
  const rotFixed = useMemo(() => {
    if (!data || data.mission_id !== "artemis-ii") return 0;
    const { ox, oy, oz } = data.points;
    let maxDistSq = 0;
    let maxIdx = 0;
    for (let i = 0; i < ox.length; i++) {
      const d2 = ox[i] * ox[i] + oy[i] * oy[i] + oz[i] * oz[i];
      if (d2 > maxDistSq) {
        maxDistSq = d2;
        maxIdx = i;
      }
    }
    const v = new THREE.Vector3(ox[maxIdx], oy[maxIdx], oz[maxIdx]).applyQuaternion(tiltQ);
    return Math.atan2(-v.y, v.x);
  }, [data, tiltQ]);

  useEffect(() => {
    if (!playing || !data) return;
    scrubMsRef.current = scrubMs;
    const tick = (frameTime: number) => {
      const dt = (frameTime - lastFrameRef.current) / 1000;
      lastFrameRef.current = frameTime;
      const next = scrubMsRef.current + dt * speed * 1000;
      if (next >= endMs) {
        scrubMsRef.current = endMs;
        setScrubMs(endMs);
        setPlaying(false);
        return;
      }
      scrubMsRef.current = next;
      setScrubMs(next);
      rafRef.current = requestAnimationFrame(tick);
    };
    lastFrameRef.current = performance.now();
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [playing, data, speed, endMs, scrubMs]);

  useEffect(() => {
    scrubMsRef.current = scrubMs;
  }, [scrubMs]);

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

  const isArtemisII = data?.mission_id === "artemis-ii";

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Trajectory Test 2 — 3D</h1>
      <div className={styles.subhead}>
        WebGL perspective view (Three.js / R3F) with textured Earth, day/night terminator,
        atmosphere halo, Moon, and Hipparcos star field. Same EME2000 ephemeris source as{" "}
        <a href="/trajectory-test">trajectory-test</a>.
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

      {data && ts && hud && (
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

          <div className={styles2.canvasWrap3d}>
            <div className={styles2.canvasBadge}>WebGL · 3D</div>
            <Canvas
              orthographic
              camera={{ position: [0, 0, 10], near: 0.01, far: 2000 }}
              dpr={[1, 2]}
              gl={{ antialias: true, alpha: false }}
            >
              <color attach="background" args={["#03060c"]} />
              <SceneCamera maxDistKm={data.max_distance_km} />
              <TrajectoryScene
                data={data}
                ts={ts}
                scrubMsRef={scrubMsRef}
                rotFixed={rotFixed}
                isArtemisII={!!isArtemisII}
                tiltQ={tiltQ}
              />
            </Canvas>
            <div className={styles2.canvasHint}>
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

export default TrajectoryTest2;
