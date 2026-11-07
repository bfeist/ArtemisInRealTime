import { resolveMissionRelease } from "./missionRelease.ts";

export interface VideoPart {
  partNum: number;
  videoId: string;
  durationS: number;
  startOffsetS: number;
}

export interface VideoPartCatalog {
  schemaVersion: 1;
  streamStartUtc: string;
  timingStatus: "candidate" | "verified";
  parts: VideoPart[];
}

export interface VideoSyncMapping {
  id: string;
  feed: string;
  startUtc: string;
  endUtc: string;
  videoId: string;
  videoStartSeconds: number;
  rate: number;
  recordingId: string;
  recordingStartSeconds: number;
}

export interface VideoSync {
  schemaVersion: 1;
  mappings: VideoSyncMapping[];
}

export function validateVideoParts(value: VideoPartCatalog): VideoPartCatalog {
  if (
    value?.schemaVersion !== 1 ||
    !Number.isFinite(Date.parse(value.streamStartUtc)) ||
    !["candidate", "verified"].includes(value.timingStatus) ||
    !value.parts?.length
  ) {
    throw new Error("Invalid video part catalog");
  }
  const ids = new Set<string>();
  let previousEnd = 0;
  value.parts.forEach((part, index) => {
    if (
      part.partNum !== index + 1 ||
      !/^[A-Za-z0-9_-]{11}$/.test(part.videoId) ||
      ids.has(part.videoId) ||
      !Number.isFinite(part.durationS) ||
      part.durationS <= 0 ||
      !Number.isFinite(part.startOffsetS) ||
      part.startOffsetS < previousEnd - 0.001
    ) {
      throw new Error("Invalid or overlapping video part");
    }
    ids.add(part.videoId);
    previousEnd = part.startOffsetS + part.durationS;
  });
  return value;
}

export async function loadVideoParts(
  base: string,
  legacy?: VideoPartCatalog
): Promise<VideoPartCatalog> {
  const release = await resolveMissionRelease(base);
  const response = await fetch(`${release}/video-parts.json`);
  if (response.status === 404 && release === base.replace(/\/+$/, "") && legacy) {
    return validateVideoParts(legacy);
  }
  if (!response.ok) throw new Error(`video-parts.json HTTP ${response.status}`);
  return validateVideoParts(await response.json());
}

/** Half-open intervals avoid selecting two parts at a cut. Never invent mappings for gaps. */
export function findVideoPart(parts: VideoPart[], offset: number): VideoPart | null {
  return (
    parts.find(
      (part) => offset >= part.startOffsetS && offset < part.startOffsetS + part.durationS
    ) ?? null
  );
}

export function validateVideoSync(value: VideoSync): VideoSync {
  if (value?.schemaVersion !== 1 || !Array.isArray(value.mappings))
    throw new Error("Invalid video synchronization map");
  const ids = new Set<string>();
  for (const row of value.mappings) {
    const start = Date.parse(row.startUtc),
      end = Date.parse(row.endUtc);
    if (
      !row.id ||
      ids.has(row.id) ||
      !row.feed ||
      !row.recordingId ||
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      end <= start ||
      !/^[A-Za-z0-9_-]{11}$/.test(row.videoId) ||
      !Number.isFinite(row.videoStartSeconds) ||
      row.videoStartSeconds < 0 ||
      !Number.isFinite(row.rate) ||
      row.rate <= 0 ||
      !Number.isFinite(row.recordingStartSeconds) ||
      row.recordingStartSeconds < 0
    ) {
      throw new Error("Invalid video synchronization interval");
    }
    ids.add(row.id);
  }
  return value;
}

export async function loadVideoSync(base: string): Promise<VideoSync> {
  const release = await resolveMissionRelease(base);
  const response = await fetch(`${release}/pao-sync.json`);
  if (response.status === 404) return { schemaVersion: 1, mappings: [] };
  if (!response.ok) throw new Error(`pao-sync.json HTTP ${response.status}`);
  return validateVideoSync(await response.json());
}

/** Return all verified alternatives rather than arbitrarily choosing a broadcast. */
export function videoPositions(
  sync: VideoSync,
  utc: string,
  feed?: string
): { mapping: VideoSyncMapping; seconds: number }[] {
  const time = Date.parse(utc);
  return sync.mappings
    .filter(
      (row) =>
        (!feed || row.feed === feed) &&
        time >= Date.parse(row.startUtc) &&
        time < Date.parse(row.endUtc)
    )
    .map((row) => ({
      mapping: row,
      seconds: row.videoStartSeconds + ((time - Date.parse(row.startUtc)) / 1000) * row.rate,
    }));
}
