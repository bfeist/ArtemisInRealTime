# Three.js Trajectory Visualization — Implementation Brief

**Project**: Artemis in Real Time  
**Task**: Implement a new `trajectory-test2` route that replaces the 2D Canvas trajectory banner with a Three.js perspective 3D scene.  
**Preserve**: The existing `trajectory-test` route must remain completely unchanged.  
**Stack**: React 19 + TypeScript + Vite, react-router-dom v7

---

## 1. What Exists Today (`trajectory-test`)

The current page lives in `src/pages/TrajectoryTest.tsx`. It is a ~780-line file that renders a **200 px-tall full-bleed canvas banner** using the raw Canvas 2D API (no WebGL). Key characteristics:

### Layout
- The page has a monospace dark-space UI (`#050810` background)
- Banner: `canvasWrap` CSS class — `height: 200px`, `width: calc(100% + 3rem)` (bleeds past page padding), `overflow: hidden`
- Below the banner: timeline scrubber, speed buttons, event timeline with tick marks

### Coordinate System
The trajectory JSON is in **EME2000** (Earth Mean Equator and Equinox of J2000.0):
- Origin = Earth's center
- X-Y plane = Earth's mean equatorial plane at J2000
- +Z = J2000 north pole (~23.4° from ecliptic normal)
- **Units = km**
- The existing view is a pure **top-down X-Y projection** (Z is ignored for drawing)

The data looks like:
```ts
interface Trajectory {
  mission_id: string;        // "artemis-i" | "artemis-ii"
  earth_radius_km: number;   // 6371
  moon_radius_km: number;    // 1737
  max_distance_km: number;   // ~438600 (Artemis I), ~413000 (Artemis II)
  points: {
    t:  string[];   // UTC ISO strings, one per step (~5 min intervals)
    ox: number[];   // Orion X, km, EME2000
    oy: number[];   // Orion Y, km, EME2000
    oz: number[];   // Orion Z, km, EME2000
    mx: number[];   // Moon X, km, EME2000
    my: number[];   // Moon Y, km, EME2000
    mz: number[];   // Moon Z, km, EME2000
    speed_earth: number[];  // km/s
    speed_moon:  number[];  // km/s
  };
}
```
Fetched from: `${ASSETS_BASE}/${mission}/web/ephemeris/trajectory.json`  
(`ASSETS_BASE` = `/artemis-assets` in dev, `https://media.artemisinrealtime.org` in prod)

### What the 2D Canvas draws (per frame)
1. **Background** `#03060c` fill
2. **Star field** — Hipparcos catalog from `src/data/bright-stars.json`, pre-rendered to an offscreen D×D canvas (D = 2 × max distance from Earth pivot to any canvas corner), rotated each frame via `ctx.rotate(-rot)` centered on Earth's screen position
3. **Moon dashed trail** — full mission arc in gray
4. **Orion future trail** — from current position onward, dim blue
5. **Orion past trail** — from launch to current, bright white
6. **Earth** — blue `#3a7bd5` circle with radius `max(3*dpr, earth_radius_km * scale)` plus a radial glow gradient
7. **Moon** — gray circle, same scale
8. **Orion** — orange dot (4px) with white outer ring (5px)

### Rotation logic
```
scale = (W * 0.76) / data.max_distance_km   // Orion at max dist → 88% of canvas width
earthSX = W * 0.12                           // Earth 12% from left
earthSY = H * 0.5                            // Earth vertically centered

psi = atan2(orionCurY, orionCurX)            // world angle of Orion from Earth

// Artemis I — always keep Orion pointing to the right:
rot = -psi

// Artemis II — lock to max-distance direction; clamp only if Orion leaves canvas:
rot = rotFixed (precomputed from max-distance trajectory point)
      with vertical sinMax clamp if needed
```

Each frame: world coords are projected via `rx = kmX*cosR - kmY*sinR`, screen coords via `sx = earthSX + rx*scale`.

### React structure
- `useState` for mission slug, data, scrubMs, playing, speed
- `useRef(drawRef)` — imperative draw function rebuilt when `data` changes; called from RAF tick and scrub effect
- RAF loop — increments scrubMs by `dt * speed * 1000` each frame
- `useMemo(hud)` — recomputes distance/speed stats from scrubMs
- Route: `<Route path="trajectory-test" element={<TrajectoryTest />} />`

### Files involved
| File | Role |
|---|---|
| `src/pages/TrajectoryTest.tsx` | Main component (~780 lines) |
| `src/pages/TrajectoryTest.module.css` | Styles |
| `src/data/bright-stars.json` | Hipparcos star catalog |
| `src/index.tsx` | Router (where to add the new route) |
| `src/App.tsx` | Just renders `<Outlet />` |

---

## 2. Goals for the 3D Version (`trajectory-test2`)

The new page should be a **new file** `src/pages/TrajectoryTest2.tsx` registered at `/trajectory-test2`. It should have the same HUD (stats strip, event bar, timeline scrubber) but replace the 2D canvas banner with a **Three.js WebGL scene**.

### Visual features to implement
| Feature | Notes |
|---|---|
| **Earth globe with continents** | Day-side texture map; no rotation needed for the top-down trajectory view, but Earth's spin relative to EME2000 is optional enhancement |
| **Earth terminator (day/night line)** | GLSL shader blending day and night textures based on computed sun position vector at current timestamp |
| **Moon globe** | Simple gray sphere with crater texture |
| **Moon terminator** | Same day/night shader approach applied to Moon |
| **Trajectory lines** | Orion past (bright) / future (dim), Moon arc (dashed), as 3D `THREE.Line` geometry |
| **Orion spacecraft marker** | Billboard sprite or `THREE.Sprite` scaled to fixed visible size; orange with white outline |
| **Real star field** | `THREE.Points` geometry using Hipparcos RA/Dec catalog — already available in `src/data/bright-stars.json` |
| **Atmosphere** | Translucent sphere slightly larger than Earth with Fresnel shader |
| **Camera** | Perspective camera above Earth's north pole (+Z axis), looking down; rotates with trajectory (same rotation logic as existing) |

---

## 3. Library Research

### Three.js (core renderer)
- **npm**: `three` + `@types/three`
- **Version**: r184 (latest as of May 2026)
- **License**: MIT | **Stars**: 113k
- **Why**: The project already uses Vite + React 19 TypeScript. Three.js is the industry standard for WebGL. No size concern — tree-shaking works well with modern bundlers.
- **Install**: `npm install three @types/three`
- **Docs**: https://threejs.org/docs/

### @react-three/fiber (R3F) — **Recommended renderer**
- **npm**: `@react-three/fiber`
- **Version**: v9.6.1 (latest) — pairs with **React 19** ✓
- **License**: MIT | **Stars**: 30.9k
- **What it is**: React renderer for Three.js. `<mesh />` → `new THREE.Mesh()`. No overhead over raw Three.js; components render outside React. The project already uses React 19.
- **Install**: `npm install @react-three/fiber`
- **Key hooks**: `useFrame(({ clock, camera }) => { ... })` runs every RAF tick, `useThree()` accesses renderer/camera/scene
- **Canvas**: `<Canvas camera={{ position: [0,0,d], fov: 45 }}>` creates the WebGL context and handles resize automatically
- **Why not raw Three.js**: R3F integrates naturally with the existing React component structure. The scrubber/HUD can remain plain React; only the canvas subtree uses R3F.
- **Docs**: https://docs.pmnd.rs/react-three-fiber

### @react-three/drei — **Helpers**
- **npm**: `@react-three/drei`
- **Version**: v10.7.7 | **License**: MIT | **Stars**: 9.7k
- **Install**: `npm install @react-three/drei`
- **Key exports for this project**:
  - `<Stars radius count factor saturation fade>` — GPU point cloud of 5000+ stars with RA/Dec-based positioning, configurable density. Can be used instead of the custom Hipparcos catalog, OR the custom catalog can be fed into a raw `THREE.Points` for exact match with the existing star set.
  - `<PerspectiveCamera makeDefault position fov>` — declarative camera
  - `<OrbitControls>` — optional user camera interaction (may conflict with auto-rotation; disable or use carefully)
  - `<shaderMaterial>` — JSX wrapper for custom GLSL materials
  - `<Html>` — overlay DOM elements at 3D positions (useful for Orion label)
  - `<Line>` (from drei, uses `LineGeometry`) — fat lines with configurable linewidth
- **Docs**: https://drei.pmnd.rs/

### three-globe (Earth sphere with data layers)
- **npm**: `three-globe` (underlying plugin of globe.gl)
- **Version**: ~2.x | **License**: MIT | **Stars**: part of globe.gl (3k)
- **What it is**: A plain `THREE.Object3D` plugin that wraps a textured Earth sphere. Can be added to any Three.js scene directly.
- **Why consider it**: Has built-in atmosphere shader, day/night GLSL shader, bump maps. Is a `THREE.Object3D` that can be added to an R3F scene via `<primitive object={globeObj} />`.
- **Caveat**: Primarily designed for lat/lng surface data. The trajectory is in EME2000 inertial space, so its data layers (arcs, paths) are NOT directly usable for the trajectory — those would need to be separate Three.js objects.
- **Decision**: Use `three-globe` only for the Earth sphere + atmosphere/terminator rendering. Draw trajectory as separate `THREE.Line`/`THREE.Points` objects.
- **Install**: `npm install three-globe`
- **Earth textures** available from its CDN: `https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-day.jpg`, `earth-night.jpg`, `earth-water.png` (specular), `earth-topology.png` (bump map)

### solar-calculator (sun position)
- **npm**: `solar-calculator`
- **What it is**: Computes solar position from a JS Date. Used in the globe.gl day/night example (MIT license).
- **Key functions**: `solar.century(date)`, `solar.declination(t)`, `solar.equationOfTime(t)` — gives solar longitude/latitude in degrees → convert to a 3D unit vector for the terminator shader uniform.
- **Alternative**: `suncalc` npm package (older, more widely known) provides `SunCalc.getPosition(date, lat, lng)` → azimuth + altitude. For a sun direction vector in EME2000 you need the ecliptic longitude, which `solar-calculator` provides more directly.
- **Install**: `npm install solar-calculator`
- **The day/night GLSL shader** from globe.gl's example is reproduced below in section 5 and is the recommended approach.

### Summary of installs needed
```bash
npm install three @types/three @react-three/fiber @react-three/drei three-globe solar-calculator
```

---

## 4. Architecture Decision: Coordinate Handling

### The key complication: EME2000 is inertial, Earth rotates
The trajectory data is in EME2000, an **inertial frame fixed to J2000.0**. The Earth's surface **rotates** relative to this frame at ~15°/hour (one full revolution per sidereal day ≈ 86164 seconds).

This matters for: **which side of Earth faces the Sun** (terminator) and **which continents are visible** from above.

#### Option A: Ignore Earth rotation (simplest — recommended for V1)
- Treat the Earth as non-rotating in the scene; the continents don't move
- The day/night terminator is still computed correctly relative to the Sun direction vector
- The Sun direction in EME2000 at a given UTC can be computed from `solar-calculator`
- Earth's texture just stays "locked" in the J2000 orientation (prime meridian roughly toward X-axis at J2000 epoch)
- Result: continents are visible and plausible but not geographically correct

#### Option B: Full Earth rotation correction (accurate — optional enhancement)
- At each frame, compute Greenwich Apparent Sidereal Time (GAST) from the current `scrubMs`
- GAST at J2000 epoch (2000-01-01 12:00 TT) = 280.46061837°
- Earth rotation rate ≈ 360.985647°/day
- Apply `earthMesh.rotation.z = GAST_degrees_at_scrubMs * (Math.PI/180)` 
- This rotates the Earth texture so the correct continents face the Sun / are visible from above
- The GAST formula: `GAST = 280.46061837 + 360.98564736629 * (julianDate - 2451545.0)`
- This is a simple ~5-line calculation

**Recommendation**: Implement Option B — it's not much code and makes the visualization accurate.

### Scale
Use a normalized scale where 1 Three.js unit = 1 Earth radius = 6371 km.

```
EARTH_R = 1.0
MOON_R = data.moon_radius_km / 6371  ≈ 0.273
orionPos = new THREE.Vector3(ox/6371, oy/6371, oz/6371)
moonPos  = new THREE.Vector3(mx/6371, my/6371, mz/6371)
```

Max trajectory distance: ~438,600 km / 6371 = ~68.8 Earth radii from Earth center.

### Camera placement
- Position: `(0, 0, 80)` in Earth-radii units — slightly outside max trajectory distance, looking down the -Z axis toward Earth
- The existing rotation logic (psi, rotFixed) can be applied by rotating a `THREE.Group` containing all scene objects around the Z axis, OR by animating the camera's `.up` vector (which controls which way is "right" on screen)
- **Recommended**: Keep a `sceneGroup` at the origin and `sceneGroup.rotation.z = rot` each frame. Camera stays fixed at `(0, 0, 80)` looking at `(0, 0, 0)`. This replicates the existing 2D rotation behavior exactly.

### Banner vs. full-height canvas
The current banner is only 200px tall. For 3D this is very cramped for a sphere. **Consider making the 3D canvas taller**: 350–500px. The CSS class can set a different height while keeping the same full-bleed style.

---

## 5. Key Implementation Details

### 5.1 Earth with day/night terminator

Use the GLSL shader from the globe.gl day/night example (Apache/MIT licensed). The shader takes:
- `dayTexture` — equirectangular day texture
- `nightTexture` — equirectangular night (city lights) texture
- `sunDirection` — `THREE.Vector3` uniform: unit vector toward the Sun in EME2000 space

```glsl
// Fragment shader
uniform sampler2D dayTexture;
uniform sampler2D nightTexture;
uniform vec3 sunDirection;  // EME2000 unit vector toward Sun
varying vec3 vNormal;
varying vec2 vUv;

void main() {
  float intensity = dot(normalize(vNormal), sunDirection);
  float blendFactor = smoothstep(-0.1, 0.1, intensity);
  vec4 dayColor   = texture2D(dayTexture,   vUv);
  vec4 nightColor = texture2D(nightTexture, vUv);
  gl_FragColor    = mix(nightColor, dayColor, blendFactor);
}
```

Sun direction in EME2000 from `solar-calculator`:
```ts
import * as solar from 'solar-calculator';

function sunDirectionEME2000(date: Date): THREE.Vector3 {
  const t = solar.century(date);
  const lng = solar.apparentLongitude(t);  // ecliptic longitude, degrees
  const eps = 23.439291111 - 0.013004167 * t;  // obliquity of ecliptic
  const lngRad = lng * Math.PI / 180;
  const epsRad = eps * Math.PI / 180;
  // Convert ecliptic (lambda, 0) → equatorial (EME2000)
  return new THREE.Vector3(
    Math.cos(lngRad),
    Math.sin(lngRad) * Math.cos(epsRad),
    Math.sin(lngRad) * Math.sin(epsRad)
  ).normalize();
}
```

### 5.2 Earth rotation (GAST)
```ts
const J2000_JD = 2451545.0;
const MS_PER_DAY = 86400000;

function earthRotationAngle(utcMs: number): number {
  const jd = J2000_JD + (utcMs - Date.UTC(2000, 0, 1, 12, 0, 0)) / MS_PER_DAY;
  const T = jd - J2000_JD;
  // GAST in degrees (simplified, good to ~0.1°)
  const gast = 280.46061837 + 360.98564736629 * T;
  return (gast % 360) * Math.PI / 180;
}
```
Apply as `earthMesh.rotation.z = earthRotationAngle(scrubMs)` each frame.

### 5.3 Atmosphere
A slightly larger sphere (radius 1.05) with a `THREE.MeshPhongMaterial` or custom Fresnel shader:
```tsx
<mesh scale={1.05}>
  <sphereGeometry args={[1, 64, 32]} />
  <meshStandardMaterial
    color="#4da6ff"
    transparent
    opacity={0.12}
    side={THREE.BackSide}
    depthWrite={false}
  />
</mesh>
```
Or use drei's `<MeshDistortMaterial>` / a proper atmosphere Fresnel shader for a more realistic glow.

### 5.4 Star field (using existing catalog)
The existing `src/data/bright-stars.json` has Hipparcos stars with RA/Dec/magnitude/BV. In Three.js, map RA/Dec to a unit sphere at large radius (e.g., 1000 Earth radii):

```ts
const phi   = (90 - dec) * Math.PI / 180;  // polar angle from +Z
const theta = ra          * Math.PI / 180;  // azimuth from +X
const R = 1000;
x = R * Math.sin(phi) * Math.cos(theta);
y = R * Math.sin(phi) * Math.sin(theta);
z = R * Math.cos(phi);
```

Put all stars into a `THREE.BufferGeometry` / `THREE.Points` at these fixed positions. They will automatically appear to rotate with the camera view rotation (since they're in the same scene group that rotates, or in world space if you want them fixed). 

**Decision**: Stars should be in **world space** (not the rotating scene group) since they represent fixed inertial directions — they don't rotate with Earth. The existing 2D implementation co-rotates stars with the trajectory view. Choose which behavior to keep.

### 5.5 Trajectory lines
```tsx
// Past trail
const pastPoints = ox.slice(0, idx+1).map((x, i) =>
  new THREE.Vector3(x/R_EARTH_KM, oy[i]/R_EARTH_KM, oz[i]/R_EARTH_KM)
);
<Line points={pastPoints} color="white" lineWidth={1} />

// Future trail (dim)
<Line points={futurePoints} color="rgba(160,200,255,0.18)" lineWidth={1} />

// Moon trail (dashed)
<Line points={moonPoints} color="rgba(160,160,200,0.35)" dashed dashSize={0.1} />
```
`<Line>` from `@react-three/drei` handles fat lines cleanly. For simple hairlines, `<primitive object={new THREE.LineSegments(...)} />` is fine.

### 5.6 Orion marker
At ~430,000 km from Earth, a realistically-sized Orion capsule (~5m) would be invisible. Use a sprite or `<Html>` overlay:
```tsx
// Sprite (always faces camera, fixed pixel size)
<sprite position={orionPos} scale={[0.5, 0.5, 1]}>
  <spriteMaterial color="#ff8a3c" />
</sprite>

// OR: Html overlay (same as existing orange dot + label)
<Html position={orionPos} center>
  <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#ff8a3c', border: '2px solid white' }} />
</Html>
```

### 5.7 Moon with terminator
Same shader as Earth, smaller sphere, with Moon day/night textures. Moon texture available from Three.js examples CDN.

### 5.8 Camera and rotation
```tsx
// In the R3F Canvas
<PerspectiveCamera makeDefault position={[0, 0, CAMERA_Z]} fov={30} near={0.01} far={2000} />

// Scene group that rotates (replicates 2D canvas rotation)
const sceneGroupRef = useRef<THREE.Group>(null);
useFrame(() => {
  if (sceneGroupRef.current) {
    sceneGroupRef.current.rotation.z = rot;  // same rot computed as in 2D version
  }
});
<group ref={sceneGroupRef}>
  {/* Earth, Moon, Orion, trajectory lines */}
</group>
```

The `rot` value is computed from `scrubMsRef.current` using the exact same logic as `TrajectoryTest.tsx`:
- Artemis I: `rot = -atan2(orionCurY, orionCurX)`
- Artemis II: `rot = rotFixed` with vertical clamp

---

## 6. Suggested File Structure

```
src/pages/
  TrajectoryTest.tsx           ← UNCHANGED (existing 2D version)
  TrajectoryTest.module.css    ← UNCHANGED
  TrajectoryTest2.tsx          ← NEW 3D version
  TrajectoryTest2.module.css   ← NEW (can share/extend existing CSS for HUD parts)
```

In `src/index.tsx`, add alongside the existing route:
```tsx
import TrajectoryTest2 from "./pages/TrajectoryTest2.tsx";
// ...
<Route path="trajectory-test2" element={<TrajectoryTest2 />} />
```

---

## 7. What to Keep from the Existing Code

The 3D file should **duplicate or import** (do not modify the originals):

| Element | Recommendation |
|---|---|
| `Trajectory` / `Itinerary` interfaces | Duplicate or move to a shared `types/trajectory.ts` |
| `parseUtc`, `formatUtc`, `formatMet`, `formatKm`, `findIndex`, `activePhase` helpers | Duplicate or extract to `src/utils/trajectoryHelpers.ts` |
| `MISSIONS`, `SPEEDS` constants | Duplicate |
| All `useState` / `useMemo` logic for HUD, events, playback | Nearly identical — copy and adapt |
| Timeline scrubber JSX | Near-identical — copy |
| `_starCatalog` preprocessing from `bright-stars.json` | Re-use for `THREE.Points` star field |
| CSS for stats strip, event bar, timeline, buttons | Can import the same `TrajectoryTest.module.css` if class names overlap, or duplicate the relevant rules |
| **`rotFixed` precomputation** for Artemis II | Copy verbatim — same logic applies |
| **`rot` / `psi` rotation math** | Copy verbatim — just applied to `sceneGroup.rotation.z` instead of `ctx.rotate()` |

---

## 8. Non-Trivial Design Decisions for the Implementer

1. **Canvas height**: Currently 200px. For 3D this feels too cramped. Suggest 350–500px for the new route. The layout can be the same full-bleed style with a taller canvas area.

2. **Stars in rotating group or world space**: 
   - In the 2D version, stars co-rotate with the trajectory (simulating a viewport camera rotating over a fixed scene). 
   - In 3D, the natural behavior is to put stars in world space (they don't move as the camera/scene rotates). This is actually more physically correct since EME2000 stars don't rotate.
   - However, if you put them in world space, as `sceneGroup.rotation.z` changes, stars won't move — which looks fine.
   - If you put them inside `sceneGroup`, they rotate with the scene — which matches the existing 2D behavior.
   - **Recommend**: World space (outside sceneGroup) for physical correctness.

3. **Camera Z distance**: The scene group contains objects up to ~70 Earth radii from origin. Camera at Z=80 with FOV=30 gives a frustum view. For a 200px banner, the perspective effect is very subtle — consider FOV=15–20 for a more "telephoto" look that emphasizes the flat top-down view. For a taller canvas, a wider FOV is fine.

4. **Earth texture source**: The globe.gl example textures at `cdn.jsdelivr.net/npm/three-globe/example/img/` are pre-proven to work with Three.js ShaderMaterial. Use these for Earth day, Earth night, and Moon. Alternatively, NASA Visible Earth textures (https://visibleearth.nasa.gov/) are higher resolution but require hosting.

5. **Lighting**: With the day/night shader, the `sunDirection` uniform handles illumination; no Three.js lights are needed for Earth/Moon. For the Orion marker and trajectory lines (which use standard materials), a simple `<ambientLight intensity={0.5} />` is sufficient.

6. **Performance**: The scrubber runs at up to ×3600 speed. R3F's `useFrame` runs every RAF. Trajectory line geometry needs to be updated imperatively each frame (not declaratively through state) to avoid React overhead — use `useRef` on the geometry's `position` attribute and call `setAttribute` + `needsUpdate = true` each frame, same pattern as the existing `drawRef`.

7. **`react-globe.gl`** (React wrapper over globe.gl) was also evaluated. It is NOT recommended here because:
   - It manages its own camera and controls, which conflict with programmatic rotation
   - It uses lat/lng/altitude for object placement, requiring EME2000→ECEF coordinate transforms for trajectory points
   - It adds a significant layer of abstraction that makes the existing rotation logic hard to replicate
   - The only benefit would be the built-in Earth sphere, which can be achieved more simply with `three-globe` as a plain `Object3D`.

---

## 9. Complete npm Install Command

```bash
npm install three @types/three @react-three/fiber @react-three/drei solar-calculator
```

Optional (for Earth sphere with built-in atmosphere/day-night, add alongside custom trajectory objects):
```bash
npm install three-globe
```

---

## 10. Minimal Skeleton to Start From

```tsx
// src/pages/TrajectoryTest2.tsx
import { JSX, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Stars, Line, Html, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import brightStarsRaw from "../data/bright-stars.json";
import styles from "./TrajectoryTest.module.css";   // reuse CSS for HUD
// ... copy Trajectory/Itinerary interfaces and helper functions from TrajectoryTest.tsx

const R_EARTH_KM = 6371;
const CAMERA_Z = 90;   // Earth radii, slightly beyond max trajectory distance

// 3D scene inner component (inside <Canvas>)
function TrajectoryScene({ data, ts, scrubMsRef, rot }: SceneProps) {
  const groupRef = useRef<THREE.Group>(null);
  const orionRef = useRef<THREE.Mesh>(null);
  // ... geometry refs for trajectory lines

  useFrame(() => {
    if (!groupRef.current) return;
    groupRef.current.rotation.z = rot.current;  // apply same rotation as 2D version
    // ... update trajectory geometry positions
  });

  return (
    <>
      <ambientLight intensity={0.3} />
      <Stars radius={500} depth={50} count={5000} factor={4} saturation={0} fade />
      <group ref={groupRef}>
        {/* Earth */}
        <mesh>
          <sphereGeometry args={[1, 64, 64]} />
          <meshStandardMaterial color="#3a7bd5" />
          {/* TODO: replace with day/night ShaderMaterial */}
        </mesh>
        {/* Atmosphere */}
        <mesh scale={1.05}>
          <sphereGeometry args={[1, 32, 32]} />
          <meshStandardMaterial color="#4da6ff" transparent opacity={0.1} side={THREE.BackSide} />
        </mesh>
        {/* Moon, trajectory lines, Orion marker... */}
      </group>
    </>
  );
}

// Outer React component (HUD + Canvas)
function TrajectoryTest2(): JSX.Element {
  // ... same useState/useEffect/useMemo as TrajectoryTest.tsx
  return (
    <div className={styles.page}>
      {/* ... same heading, mission buttons, stats strip, event bar */}
      <div className={styles.canvasWrap} style={{ height: 400 }}>
        <Canvas>
          <PerspectiveCamera makeDefault position={[0, 0, CAMERA_Z]} fov={20} />
          <TrajectoryScene data={data} ts={ts} scrubMsRef={scrubMsRef} rot={rotRef} />
        </Canvas>
      </div>
      {/* ... same timeline bar */}
    </div>
  );
}

export default TrajectoryTest2;
```

---

## 11. Earth Texture URLs (ready to use, no hosting required)

```
Earth day:   https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-day.jpg
Earth night: https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-night.jpg
Earth bump:  https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-topology.png
Earth water: https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-water.png
Moon:        https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-dark.jpg  (placeholder)
Night sky bg: https://cdn.jsdelivr.net/npm/three-globe/example/img/night-sky.png
```

For Moon, a proper texture is available from NASA: `https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_poles_1k.jpg` (public domain).

---

## 12. Priority Order (suggested implementation sequence)

1. Scaffold the route and file — router entry, empty component, canvas renders a black scene
2. Port all HUD logic (React state, data fetching, scrubber, stats, events) — no 3D yet
3. Add Earth sphere (solid color first, texture map second)
4. Add trajectory lines (Orion past/future/Moon) as `THREE.Line` objects
5. Add Orion marker (Sprite or Html)
6. Add Moon sphere
7. Implement `sceneGroup.rotation.z = rot` with the existing rotation logic
8. Add star field (`<Stars>` from drei OR custom `THREE.Points` from the Hipparcos catalog)
9. Add atmosphere halo
10. Implement day/night terminator shader (Earth first, Moon second)
11. Implement Earth rotation (GAST) for accurate continent orientation
12. Polish: label annotations, canvas height, camera FOV tuning
