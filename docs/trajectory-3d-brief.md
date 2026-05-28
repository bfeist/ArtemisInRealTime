# Three.js Trajectory Visualization — Implementation Notes

**Project**: Artemis in Real Time  
**Status**: Implemented — `trajectory-test2` route live at `src/pages/TrajectoryTest2.tsx`  
**Preserved**: The existing `trajectory-test` route was left completely unchanged.  
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
  mission_id: string; // "artemis-i" | "artemis-ii"
  earth_radius_km: number; // 6371
  moon_radius_km: number; // 1737
  max_distance_km: number; // ~438600 (Artemis I), ~413000 (Artemis II)
  points: {
    t: string[]; // UTC ISO strings, one per step (~5 min intervals)
    ox: number[]; // Orion X, km, EME2000
    oy: number[]; // Orion Y, km, EME2000
    oz: number[]; // Orion Z, km, EME2000
    mx: number[]; // Moon X, km, EME2000
    my: number[]; // Moon Y, km, EME2000
    mz: number[]; // Moon Z, km, EME2000
    speed_earth: number[]; // km/s
    speed_moon: number[]; // km/s
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

| File                                  | Role                                |
| ------------------------------------- | ----------------------------------- |
| `src/pages/TrajectoryTest.tsx`        | Main component (~780 lines)         |
| `src/pages/TrajectoryTest.module.css` | Styles                              |
| `src/data/bright-stars.json`          | Hipparcos star catalog              |
| `src/index.tsx`                       | Router (where to add the new route) |
| `src/App.tsx`                         | Just renders `<Outlet />`           |

---

## 2. What Was Implemented (`trajectory-test2`)

`src/pages/TrajectoryTest2.tsx` is registered at `/trajectory-test2`. It shares the same HUD (stats strip, event bar, timeline scrubber) as the 2D version and replaces the canvas banner with a Three.js WebGL **orthographic** scene.

### Visual features implemented

| Feature                          | Status         | Notes                                                                                                                                                                                                                                  |
| -------------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Earth globe with continents**  | ✓ Implemented  | Procedural GLSL shader driven by a grayscale water mask (`earth-water.png`). Land colors computed from latitude bands (tropical green, temperate brown, polar ice). No photograph texture.                                             |
| **Earth terminator (day/night)** | ✓ Implemented  | Custom GLSL fragment shader; `smoothstep(-0.15, 0.15, dot(normal, sunDirection))` blends between ambient (0.22) and full daylight.                                                                                                     |
| **Earth rotation (GAST)**        | ✓ Implemented  | Full sidereal rotation: `earthRotationAngle()` applied as `meshRef.current.rotation.z` each frame.                                                                                                                                     |
| **Moon globe**                   | ✓ Implemented  | Procedural gray shader (`baseColor: #cfcfcf`), same day/night terminator as Earth. No texture.                                                                                                                                         |
| **Moon terminator**              | ✓ Implemented  | Same shader approach as Earth.                                                                                                                                                                                                         |
| **Trajectory lines**             | ✓ Implemented  | Moon arc: `<Line>` (drei, dashed). Orion past/future: raw `<line>` + `BufferGeometry` with imperative `drawRange` updates each frame.                                                                                                  |
| **Orion spacecraft marker**      | ✓ Implemented  | `THREE.Sprite` with a canvas-drawn orange circle + 4 px white stroke baked into a `CanvasTexture`. `sizeAttenuation={true}`.                                                                                                           |
| **Real star field**              | ✓ Implemented  | `THREE.Points` with custom GLSL gaussian soft-disk shaders. Stars colored by B-V index, sized by magnitude. Projected onto a flat plane at `z = −1900` via orthographic celestial projection (not a spherical shell).                  |
| **Atmosphere halo**              | ✓ Implemented  | Custom sun-side GLSL shader on a `scale={1.06}` sphere, `THREE.BackSide`, additive blending. Blue glow follows the sun direction.                                                                                                      |
| **Camera**                       | ✓ Orthographic | `THREE.OrthographicCamera` — frustum calibrated to the 2D formula: Earth at 12% from left, max trajectory at 88%.                                                                                                                      |
| **Orbital plane tilt**           | ✓ New feature  | Quaternion `tiltQ` computed from the mission's cumulative orbital angular momentum (cross products of consecutive position vectors). Tilts the entire scene so the orbital plane faces the camera face-on, eliminating foreshortening. |

**Items from the original spec that were not implemented:**

- No CDN-hosted photograph textures (Earth/Moon are fully procedural)
- `three-globe` package was not installed (only its CDN water-mask asset is fetched)
- `Stars`, `Html`, `PerspectiveCamera` from `@react-three/drei` are not used; only `Line` was imported from drei

---

## 3. Libraries Used

| Package              | Version installed | Usage in `TrajectoryTest2.tsx`                                                |
| -------------------- | ----------------- | ----------------------------------------------------------------------------- |
| `three`              | `^0.184.0`        | Core renderer, geometry, materials, shaders                                   |
| `@types/three`       | `^0.184.1`        | TypeScript types                                                              |
| `@react-three/fiber` | `^9.6.1`          | `Canvas` (orthographic), `useFrame`, `useLoader`, `useThree`                  |
| `@react-three/drei`  | `^10.7.7`         | `Line` only — fat dashed Moon arc                                             |
| `solar-calculator`   | `^0.3.0`          | `solar.century()`, `solar.apparentLongitude()`, `solar.obliquityOfEcliptic()` |

`three-globe` was **not** installed. Only its CDN water-mask texture is fetched:

```
https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-water.png
```

Install command actually run:

```bash
npm install three @types/three @react-three/fiber @react-three/drei solar-calculator
```

---

## 4. Architecture: Coordinate Handling

### Scale

1 Three.js unit = 1 Earth radius = 6371 km (`R_EARTH_KM = 6371`). Moon radius ≈ 0.273 R⊕.

### Orbital plane tilt (`tiltQ`) — key new feature

The EME2000 trajectory is inclined relative to the EME2000 XY plane. Without correction, the orbit appears foreshortened when projected. A quaternion `tiltQ` is precomputed that maps the mission's orbital angular momentum vector **L** to the world +Z axis (i.e., toward the camera):

```ts
// Cumulative cross product of consecutive position vectors
for (let i = 0; i < n - 1; i++) {
  Lx += oy[i] * oz[i + 1] - oz[i] * oy[i + 1];
  Ly += oz[i] * ox[i + 1] - ox[i] * oz[i + 1];
  Lz += ox[i] * oy[i + 1] - oy[i] * ox[i + 1];
}
// Ensure normal points toward the camera (Lz > 0)
if (Lz < 0) {
  Lx = -Lx;
  Ly = -Ly;
  Lz = -Lz;
}
tiltQ = new THREE.Quaternion().setFromUnitVectors(L_normalized, new THREE.Vector3(0, 0, 1));
```

The outer `<group quaternion={tiltQ}>` tilts the entire scene (Earth, Moon, Orion, trajectory) so the orbital plane is perpendicular to the camera, giving a true face-on view.

### Camera

`THREE.OrthographicCamera` via `<Canvas orthographic>`. The frustum is calibrated by `SceneCamera` to exactly replicate the 2D canvas scaling:

```ts
maxDistRearth = max_distance_km / 6371;
totalWidth = maxDistRearth / 0.76;
cam.left = -totalWidth * 0.12; // Earth at 12% from left
cam.right = +totalWidth * 0.88; // max trajectory at 88%
cam.top = totalWidth / aspect / 2;
cam.bottom = -totalWidth / aspect / 2;
cam.position.set(0, 0, 10); // depth irrelevant for ortho
```

### Scene group hierarchy

```
<Canvas orthographic>
  <SceneCamera />                         ← calibrates ortho frustum each resize
  <group ref={skyGroupRef}>               ← rotation.z = rot (stars only, no tilt)
    <StarField />
  </group>
  <group quaternion={tiltQ}>              ← orbital-plane tilt (static per mission)
    <group ref={starGroupRef}>            ← setRotationFromQuaternion(_tmpQuat)
      <Atmosphere />                      ← Earth+atm rotate together in-plane
      <Earth />
    </group>
    <group ref={groupRef}>               ← setRotationFromQuaternion(_tmpQuat)
      <Moon />                            ← trajectory objects rotate in-plane
      <Orion />
      <TrajectoryLines />
    </group>
  </group>
</Canvas>
```

### Rotation logic (per frame)

`rot` is computed in tilt-corrected world space so Orion stays on screen +X:

```ts
_tmpVec.set(ocx, ocy, ocz).applyQuaternion(tiltQ);  // project to viewing plane
const psi = Math.atan2(_tmpVec.y, _tmpVec.x);

// Artemis I: always align Orion to screen +X
rot = -psi;

// Artemis II: hold max-distance direction; clamp only if Orion leaves canvas
rot = rotFixed  (with vertical sinMax clamp using actual canvas height)
```

`_tmpQuat.setFromAxisAngle(orbNormal, rot)` is then applied via `setRotationFromQuaternion` to both the trajectory group and the Earth/atmosphere group.

The sun direction is also rotated into screen space: `rawSun.applyQuaternion(_tmpQuat).applyQuaternion(tiltQ)`.

### `rotFixed` for Artemis II

Precomputed from the max-distance trajectory point in tilt-corrected space:

```ts
const v = new THREE.Vector3(ox[maxIdx], oy[maxIdx], oz[maxIdx]).applyQuaternion(tiltQ);
rotFixed = Math.atan2(-v.y, v.x);
```

---

## 5. Key Implementation Details

### 5.1 Earth shader (procedural, water-mask only)

No photograph textures. A single grayscale water-mask (`earth-water.png`, `1 = ocean, 0 = land`) drives land/ocean classification. Colors are procedural uniforms:

```glsl
float water = texture2D(waterMask, vUv).r;
float lat   = abs(vUv.y - 0.5) * 2.0;        // 0 = equator, 1 = pole
float ice   = smoothstep(0.78, 0.92, lat);
vec3 land   = mix(uLandTropical, uLandTemperate, smoothstep(0.15, 0.55, lat));
vec3 base   = mix(land, uOcean, water);
vec3 surface = mix(base, uIce, ice);
float sun   = dot(normalize(vWorldNormal), sunDirection);
float light = 0.22 + 0.78 * smoothstep(-0.15, 0.15, sun);
gl_FragColor = vec4(surface * light, 1.0);
```

Color uniforms: `uOcean: #1e96e8`, `uLandTropical: #3d7828`, `uLandTemperate: #7a5e22`, `uIce: #f0f6ff`.

The sphere geometry is pre-rotated `rotateX(Math.PI / 2)` so its geographic Y-pole aligns with EME2000 +Z.

### 5.2 Earth rotation (GAST)

```ts
const J2000_EPOCH_MS = Date.UTC(2000, 0, 1, 12, 0, 0);

function earthRotationAngle(utcMs: number): number {
  const T = (utcMs - J2000_EPOCH_MS) / 86_400_000;
  const deg = 280.46061837 + 360.98564736629 * T;
  return ((deg % 360) * Math.PI) / 180;
}
```

Applied as `meshRef.current.rotation.z = earthRotationAngle(scrubMsRef.current)` in `useFrame`.

### 5.3 Atmosphere (sun-side GLSL halo)

Custom GLSL shader on a `scale={1.06}` sphere, `THREE.BackSide`, `THREE.AdditiveBlending`, no depth write:

```glsl
vec3 norm = -normalize(vWorldNormal);  // BackSide normals point inward
float sun = dot(norm, sunDirection);
float glow = smoothstep(-0.25, 0.25, sun);
gl_FragColor = vec4(0.3, 0.65, 1.0, 0.18 * glow);
```

### 5.4 Star field (flat-plane orthographic projection)

Stars are placed on a flat plane at `z = −1900` (behind all scene geometry). Each star's RA/Dec is projected orthographically onto this plane — the astronomically correct approach for an orthographic camera:

```ts
const dx = Math.cos(decRad) * Math.cos(raRad); // unit celestial sphere X
const dy = Math.sin(decRad); // unit celestial sphere Y
// Scale to circumscribed-square radius so stars fill the frustum at all rotations
pos[i * 3] = dx * maxDist;
pos[i * 3 + 1] = dy * maxDist;
pos[i * 3 + 2] = -1900;
```

Stars receive B-V color mapping (5 bands: blue hot → white → yellow → orange → red), magnitude-based sprite size (1.5–9.0 px), and a custom GLSL gaussian soft-disk shader (sharp bright core + diffuse halo terms):

```glsl
float r    = length(gl_PointCoord - vec2(0.5)) * 2.0;
float core = exp(-r * r * 8.0);
float halo = exp(-r * r * 1.8) * 0.50;
float alpha = min(1.0, core + halo);
if (alpha < 0.008) discard;
gl_FragColor = vec4(vColor, alpha);
```

The star group (`skyGroupRef`) receives `rotation.z = rot` only — no `tiltQ` — so stars stay at the correct world-space `z = −1900` depth behind the scene.

### 5.5 Trajectory lines

- **Moon arc** (static, full mission): `<Line>` from `@react-three/drei` — `color="#a0a0c8"`, `lineWidth={1}`, `dashed`, `dashSize={0.3}`, `gapSize={0.3}`, `opacity={0.35}`
- **Orion past / future** (dynamic): raw `<line>` with `<bufferGeometry>` backed by a pre-allocated `Float32Array` (full mission length). `setDrawRange` is updated imperatively in `useFrame`. The current interpolated position is written into the buffer at slot `idx+1` (past) or `idx` (future) each frame — no per-frame allocation.

### 5.6 Orion marker

Canvas-drawn circle texture (64×64, orange `#ff8a3c` fill + 4 px white stroke) baked once into a `THREE.CanvasTexture`, rendered as `<sprite scale={[1.2, 1.2, 1]}>` with `sizeAttenuation={true}` and `depthTest={false}`.

### 5.7 Moon (procedural gray shader)

No texture. Base color `#cfcfcf`, same `smoothstep(-0.05, 0.05, intensity)` day/night terminator. Radius: `data.moon_radius_km / R_EARTH_KM ≈ 0.273`.

### 5.8 Sun direction

Computed in EME2000 from `solar-calculator`, then rotated into screen space each frame:

```ts
function sunDirectionEME2000(date: Date): THREE.Vector3 {
  const t = solar.century(date);
  const lng = solar.apparentLongitude(t); // ecliptic longitude, deg
  const eps = solar.obliquityOfEcliptic(t); // deg
  return new THREE.Vector3(
    Math.cos(lngRad),
    Math.sin(lngRad) * Math.cos(epsRad),
    Math.sin(lngRad) * Math.sin(epsRad)
  ).normalize();
}

// Applied in useFrame:
sunDirRef.current.copy(rawSun).applyQuaternion(_tmpQuat).applyQuaternion(tiltQ);
```

---

## 6. File Structure (as implemented)

```
src/pages/
  TrajectoryTest.tsx           ← UNCHANGED (existing 2D version)
  TrajectoryTest.module.css    ← UNCHANGED (imported by both pages)
  TrajectoryTest2.tsx          ← 3D version (~1,220 lines)
  TrajectoryTest2.module.css   ← Overrides: canvasWrap3d, canvasBadge, canvasHint
src/data/
  bright-stars.json            ← Hipparcos catalog (shared, unchanged)
```

`TrajectoryTest2.tsx` imports both CSS modules:

- `styles` from `TrajectoryTest.module.css` — all HUD classes (heading, stats strip, timeline, buttons, etc.)
- `styles2` from `TrajectoryTest2.module.css` — `canvasWrap3d` (200 px banner), `canvasBadge`, `canvasHint`

Canvas height: `canvasWrap3d` uses `height: 200px` — same as the 2D banner. The full-bleed style (`width: calc(100% + 3rem)`, `margin-left: -1.5rem`) is preserved.

Route added to `src/index.tsx`:

```tsx
import TrajectoryTest2 from "./pages/TrajectoryTest2.tsx";
<Route path="trajectory-test2" element={<TrajectoryTest2 />} />;
```

---

## 7. What Was Copied / Reused from the Existing Code

| Element                                                                      | Disposition                                                                                                   |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `Trajectory` / `Itinerary` interfaces                                        | Duplicated verbatim in `TrajectoryTest2.tsx`                                                                  |
| `parseUtc`, `formatUtc`, `formatMet`, `formatKm`, `findIndex`, `activePhase` | Duplicated verbatim                                                                                           |
| `MISSIONS`, `SPEEDS` constants                                               | Duplicated verbatim                                                                                           |
| `useState` / `useMemo` for HUD, events, playback                             | Copied and adapted                                                                                            |
| Timeline scrubber + event ticks JSX                                          | Near-identical copy                                                                                           |
| CSS for stats strip, event bar, timeline, buttons                            | Imported from `TrajectoryTest.module.css`                                                                     |
| `rotFixed` precomputation                                                    | Adapted for tilt space: applies `tiltQ` before `atan2`                                                        |
| `rot` / `psi` rotation math                                                  | Adapted — computed in tilt-corrected space; applied via `setRotationFromQuaternion` instead of `ctx.rotate()` |
| Hipparcos star data (`bright-stars.json`)                                    | Reused; different projection (flat plane, not spherical shell)                                                |

---

## 8. Design Decisions Made

1. **Orthographic camera (not perspective)**: Exactly matches the 2D scale formula (`scale = W * 0.76 / max_distance_km`), preventing perspective foreshortening that would make Earth dominate when Orion is far away.

2. **Orbital plane tilt (`tiltQ`)**: The EME2000 Z-axis is not the orbital angular momentum axis for either mission. Without tilt the trajectory appears foreshortened. Tilt is computed per-mission from the trajectory data itself via cumulative cross products.

3. **Procedural Earth shader (no photo textures)**: Only the three-globe water mask is fetched. The visualization reads as a schematic diagram rather than a photo-realistic globe, consistent with the 2D version's aesthetic. The procedural 4-color palette (ocean / tropical / temperate / ice) renders clearly at 200 px banner height.

4. **Stars on flat plane (not spherical shell)**: Placing stars on a `THREE.Points` sphere and projecting with an orthographic camera would distort positions away from center. The orthographic celestial projection onto `z = −1900` is the correct analogue of the 2D offscreen-canvas star field and preserves angular positions accurately.

5. **`three-globe` not installed**: Earth terminator handled entirely by the custom GLSL shader. Only the CDN water-mask asset from three-globe's distribution is fetched.

6. **Canvas height stays 200 px**: The brief suggested 350–500 px for 3D. Kept at 200 px to preserve the page layout. The orthographic + face-on tilt means the orbital plane fills the banner width even at 200 px.

7. **Trajectory line approach (zwei strategies)**: Moon arc uses `<Line>` from drei (static, dashed, fat line). Orion past/future use raw `<line>` + `BufferGeometry` with `drawRange` for zero-allocation per-frame updates — matching the imperative pattern of the existing 2D `drawRef`.

8. **No `three-globe`, no `react-globe.gl`**: Both evaluated and rejected. `three-globe` manages its own camera/controls; `react-globe.gl` requires ECEF coordinates. The custom procedural shaders are simpler and more controllable.

---

## 9. Follow-up Refactor Guide

See [`trajectory-ui-split-plan.md`](./trajectory-ui-split-plan.md) for a production-oriented split plan. It covers the proposed `src/features/trajectory/` layout, extraction order, hook/component boundaries, scene module split, CSS split, and verification checklist for turning `TrajectoryTest2.tsx` into a real reusable UI.
