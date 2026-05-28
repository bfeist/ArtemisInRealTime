# Trajectory UI Split Plan

`TrajectoryTest.tsx` and `TrajectoryTest2.tsx` are intentionally large prototype pages. They prove the data pipeline, timeline controls, mission switching, 2D canvas view, and Three.js/R3F scene all work together. When this becomes a real site feature, the useful next step is not a rewrite. It is to extract stable pieces around the `TrajectoryTest2` approach while keeping the prototype route available as a reference.

This guide describes a practical split for the production trajectory UI.

## Goals

- Keep `/trajectory-test2` as the behavioral reference until the production page matches it.
- Move mission data contracts and time/math helpers out of page files first.
- Share one playback/data model between the HUD, timeline, and 3D scene.
- Isolate the R3F scene from route/page layout so it can be embedded elsewhere.
- Keep shader code and visual assets near the scene components, not mixed into page state.
- Avoid making the first extraction too abstract. Split by concrete responsibilities already present in the test files.

## Current Prototype Responsibilities

`src/pages/TrajectoryTest2.tsx` currently contains:

| Concern | Examples in `TrajectoryTest2.tsx` | Production home |
| --- | --- | --- |
| Data contracts | `Trajectory`, `Itinerary`, `MissionEvent`, `Phase` | `src/features/trajectory/types.ts` |
| Constants | `ASSETS_BASE`, `MISSIONS`, `SPEEDS`, `R_EARTH_KM` | `src/features/trajectory/constants.ts` |
| Formatting/time helpers | `parseUtc`, `formatUtc`, `formatMet`, `formatKm`, `findIndex`, `activePhase` | `src/features/trajectory/time.ts` and `math.ts` |
| Data loading | `Promise.all([...trajectory.json, itinerary.json])` | `src/features/trajectory/useTrajectoryData.ts` |
| Playback state | `scrubMs`, `playing`, `speed`, RAF loop | `src/features/trajectory/useTrajectoryPlayback.ts` |
| Derived HUD state | distance, altitude, active phase, last/next event | `src/features/trajectory/useTrajectoryHud.ts` |
| Scene orientation | `tiltQ`, `rotFixed`, orbital normal math | `src/features/trajectory/useTrajectoryOrientation.ts` and `geometry.ts` |
| Timeline controls | play/start/end/speed buttons, range input, event ticks | `src/features/trajectory/components/TrajectoryTimeline.tsx` |
| Stats/event UI | stat cards, last/next event bar | `src/features/trajectory/components/TrajectoryHud.tsx` |
| R3F camera and scene | `SceneCamera`, `TrajectoryScene` | `src/features/trajectory/scene/` |
| Scene bodies | `Earth`, `Moon`, `Atmosphere`, `Orion`, `TrajectoryLines`, `StarField` | `src/features/trajectory/scene/*.tsx` |
| Shader strings | day/night, atmosphere, stars | `src/features/trajectory/scene/shaders.ts` |
| CSS | page layout, HUD, timeline, 3D banner overrides | CSS modules beside components |

## Suggested Folder Layout

```text
src/features/trajectory/
  constants.ts
  types.ts
  time.ts
  math.ts
  useTrajectoryData.ts
  useTrajectoryTimestamps.ts
  useTrajectoryPlayback.ts
  useTrajectoryHud.ts
  useTrajectoryOrientation.ts

  components/
    TrajectoryPage.tsx
    MissionSelector.tsx
    TrajectoryHud.tsx
    TrajectoryTimeline.tsx
    TrajectoryNotes.tsx
    TrajectoryPage.module.css
    TrajectoryHud.module.css
    TrajectoryTimeline.module.css

  scene/
    TrajectoryCanvas.tsx
    TrajectoryScene.tsx
    SceneCamera.tsx
    Earth.tsx
    Moon.tsx
    Atmosphere.tsx
    Orion.tsx
    TrajectoryLines.tsx
    StarField.tsx
    shaders.ts
    astronomy.ts
    geometry.ts
    sceneConstants.ts
    TrajectoryCanvas.module.css
```

The route file can then become thin:

```tsx
import { TrajectoryPage } from "../features/trajectory/components/TrajectoryPage";

export default function TrajectoryRoute() {
  return <TrajectoryPage initialMission="artemis-ii" />;
}
```

## Proposed Public Component Boundary

The production UI should treat the 3D renderer as an embeddable component:

```tsx
<TrajectoryCanvas
  trajectory={data}
  timestamps={timestamps}
  scrubMsRef={scrubMsRef}
  rotFixed={rotFixed}
  tiltQ={tiltQ}
  isArtemisII={data.mission_id === "artemis-ii"}
/>
```

This is close to the existing `TrajectoryScene` call. The main change is that page layout does not know about individual scene objects. It only passes mission data, current time, and precomputed orientation values.

For a less Three-specific boundary, wrap those derived values in a view model:

```ts
interface TrajectorySceneModel {
  trajectory: Trajectory;
  timestamps: number[];
  scrubMsRef: React.MutableRefObject<number>;
  orientation: {
    tiltQ: THREE.Quaternion;
    rotFixed: number;
    lockMaxDistanceDirection: boolean;
  };
}
```

Use this only if more than one renderer or page needs the same scene inputs.

## Extraction Order

Do this in small steps so each move is easy to verify.

1. Extract `types.ts`.
   Move `Phase`, `MissionEvent`, `Itinerary`, and `Trajectory`. Import them back into both test pages. This removes the duplicated data contract first.

2. Extract `time.ts` and `math.ts`.
   Move `parseUtc`, `formatUtc`, `formatMet`, `formatKm`, `findIndex`, and `activePhase`. These are low-risk and shared by the HUD, timeline, and both prototype pages.

3. Extract `constants.ts`.
   Move `ASSETS_BASE`, `MISSIONS`, `SPEEDS`, `Speed`, and `R_EARTH_KM`. If `ASSETS_BASE` later becomes app-wide, move it to the existing app config instead.

4. Extract `useTrajectoryData.ts`.
   The hook should accept `mission` and return `{ data, itinerary, loading, error }`. Keep fetch URLs identical at first.

   ```ts
   function useTrajectoryData(mission: string) {
     return { data, itinerary, loading, error };
   }
   ```

5. Extract `useTrajectoryPlayback.ts`.
   Move `scrubMs`, `playing`, `speed`, `scrubMsRef`, and the RAF loop. The hook should accept `startMs`, `endMs`, and reset when a mission changes.

   ```ts
   function useTrajectoryPlayback(params: {
     startMs: number;
     endMs: number;
     initialScrubMs: number;
   }) {
     return { scrubMs, setScrubMs, scrubMsRef, playing, setPlaying, speed, setSpeed };
   }
   ```

6. Extract `useTrajectoryHud.ts`.
   Move the interpolation and derived stats. Return the current index, distances, speeds, altitude, active phase, events in range, last event, and next event.

7. Extract `useTrajectoryOrientation.ts`.
   Move `tiltQ` and `rotFixed` computation. Put the pure vector math in `geometry.ts` so it can be tested without React.

8. Extract presentational components.
   Move mission buttons, stats strip, event bar, timeline, and notes. These should receive props and contain little or no mission math.

9. Extract `TrajectoryCanvas.tsx`.
   Move the `<Canvas>` wrapper, badge, hint, `SceneCamera`, and `TrajectoryScene` call. Keep the scene implementation in the same file briefly if that makes the first move easier.

10. Split scene objects.
   Move `Earth`, `Moon`, `Atmosphere`, `Orion`, `TrajectoryLines`, and `StarField` into separate files. Move shader strings to `shaders.ts`.

11. Replace the prototype internals.
    Once the feature components exist, `TrajectoryTest2.tsx` can either render the new `TrajectoryPage` or remain frozen as the known-good prototype.

## Hook Shape

The real UI will be easier to reason about if only one layer owns each kind of state:

| State | Owner | Notes |
| --- | --- | --- |
| Selected mission | Page or route-level component | Drives data fetches and URL state later. |
| Fetched trajectory/itinerary | `useTrajectoryData` | No rendering logic. |
| Playback time and speed | `useTrajectoryPlayback` | Owns RAF and `scrubMsRef`. |
| Derived current sample | `useTrajectoryHud` or `useTrajectorySample` | Uses `findIndex` and interpolation. |
| Scene orientation | `useTrajectoryOrientation` | Computes `tiltQ`, `rotFixed`, and lock/clamp flags. |
| 3D object positions | R3F scene components | Uses refs and `useFrame`, avoids React state per frame. |
| UI display state | Presentational components | Buttons, labels, timeline tick rendering. |

Keep `scrubMsRef` for the R3F scene. It avoids forcing React renders for every WebGL frame. Keep `scrubMs` state for the HUD and timeline so normal React UI updates still happen.

## Scene Split Details

### `TrajectoryCanvas.tsx`

Owns only DOM/R3F shell:

- `Canvas` props
- background color
- badge and max-distance hint
- CSS module for the 200 px banner or future production height
- calls `SceneCamera` and `TrajectoryScene`

### `TrajectoryScene.tsx`

Owns scene composition and per-frame orchestration:

- `groupRef`, `skyGroupRef`, `starGroupRef`
- current index refs
- in-plane rotation
- sun direction updates
- passing current refs into child objects

Keep `useFrame` orchestration here unless a child clearly owns the behavior. For example, `TrajectoryLines` owns its own `drawRange` buffer updates, but the parent owns the mission-wide rotation.

### `astronomy.ts`

Good home for:

- `sunDirectionEME2000`
- `earthRotationAngle`
- `J2000_EPOCH_MS`
- future higher-accuracy solar/lunar helpers

### `geometry.ts`

Good home for:

- `computeOrbitalTiltQuaternion`
- `computeRotFixed`
- shared interpolation helpers
- km-to-Earth-radius conversion helpers

These functions are worth unit testing because small sign mistakes can make the scene look plausible but wrong.

## CSS Split

`TrajectoryTest.module.css` currently mixes page layout, mission controls, stats, canvas wrapper, playback controls, and event ticks. A production split could be:

```text
TrajectoryPage.module.css      page, heading, subhead, mission row
TrajectoryHud.module.css       stats strip, stat cards, phase chip, event bar
TrajectoryTimeline.module.css  controls, speed buttons, slider, event ticks
TrajectoryCanvas.module.css    3D banner wrapper, badge, hint
TrajectoryNotes.module.css     notes and warnings
```

Start by copying existing class names into the new modules, then rename only when the feature is stable. CSS churn is easy to create and hard to review.

## What To Keep Out Of The Split

- Do not extract a generic "space renderer" yet. The current renderer is mission-trajectory-specific.
- Do not make `TrajectoryScene` fetch data. It should stay a pure renderer of already-loaded mission data.
- Do not move shader strings into CSS. Keep GLSL in TypeScript modules so imports and uniforms stay near the scene code.
- Do not replace refs with React state inside `useFrame`. That would make playback noisier and slower.
- Do not merge 2D and 3D renderers too early. They share data and helpers, but their rendering loops are intentionally different.

## Verification Checklist After Each Extraction

- `/trajectory-test2` still loads both Artemis I and Artemis II.
- Play, pause, start, end, speed buttons, and slider still update the scene.
- The HUD values change while playback runs.
- Last/next event labels and event ticks still appear.
- Earth remains at roughly 12% from the left edge.
- Max trajectory distance still fits near 88% of the banner width.
- Artemis II keeps the max-distance direction stable unless clamping is needed.
- Artemis I still rotates so Orion points to the right.
- Stars remain behind the scene and rotate with the view.
- No new per-frame allocations are introduced in `useFrame` paths.

## Suggested Tests

Unit tests are most useful for extracted pure helpers:

- `parseUtc` handles strings with and without trailing `Z`.
- `formatMet` clamps pre-launch values to `T+0d`.
- `findIndex` works when moving forward, backward, and at both ends.
- `activePhase` returns the latest phase before the current time.
- `computeOrbitalTiltQuaternion` maps the cumulative orbital normal toward `+Z`.
- `computeRotFixed` returns a stable angle for the max-distance point.

Integration/manual tests are enough for the WebGL rendering at first. If screenshot testing is added later, test framing rather than exact pixels.

## Migration End State

The real page should read roughly like this:

```tsx
export function TrajectoryPage({ initialMission }: { initialMission: string }) {
  const [mission, setMission] = useState(initialMission);
  const { data, itinerary, loading, error } = useTrajectoryData(mission);
  const timestamps = useTrajectoryTimestamps(data);
  const playback = useTrajectoryPlayback(getPlaybackBounds(data));
  const hud = useTrajectoryHud({ data, itinerary, timestamps, scrubMs: playback.scrubMs });
  const orientation = useTrajectoryOrientation(data);

  return (
    <main>
      <MissionSelector mission={mission} onMissionChange={setMission} />
      <TrajectoryHud hud={hud} loading={loading} error={error} />
      {data && timestamps && orientation && (
        <TrajectoryCanvas
          trajectory={data}
          timestamps={timestamps}
          scrubMsRef={playback.scrubMsRef}
          orientation={orientation}
        />
      )}
      <TrajectoryTimeline playback={playback} events={hud.eventsInRange} />
      <TrajectoryNotes notes={data?.notes ?? []} />
    </main>
  );
}
```

That shape keeps the route readable, keeps the 3D renderer reusable, and leaves the prototype files available as a working example while the production UI takes form.
