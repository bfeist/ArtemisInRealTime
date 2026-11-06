import type {
  MissionTimeline,
  TimelineAssetRef,
  TimelineCrewTrack,
} from "../types/missionTimeline.ts";

async function read<T extends { schema_version: number }>(base: string, path: string): Promise<T> {
  const response = await fetch(`${base.replace(/\/+$/, "")}/${path}`);
  if (!response.ok) throw new Error(`${path} HTTP ${response.status}`);
  const value = (await response.json()) as T;
  if (value?.schema_version !== 1) throw new Error(`Unsupported timeline schema: ${path}`);
  return value;
}

/** Load once into mission/global state; includes every block, activity and milestone. */
export function loadMissionTimeline(base: string): Promise<MissionTimeline> {
  return read<MissionTimeline>(base, "timeline.json");
}

/** Load a complete person's track when enabled, then retain it in application state. */
export function loadCrewTimeline(base: string, ref: TimelineAssetRef): Promise<TimelineCrewTrack> {
  return read<TimelineCrewTrack>(base, ref.path);
}
