import type { CommEntry, EnrichedTranscripts } from "../types/transcripts.ts";
import { resolveMissionRelease } from "./missionRelease.ts";

export async function loadEnrichedTranscripts(base: string): Promise<EnrichedTranscripts> {
  const assetBase = await resolveMissionRelease(base);
  const response = await fetch(`${assetBase}/transcripts.json`);
  if (!response.ok) throw new Error(`transcripts.json HTTP ${response.status}`);
  const data = (await response.json()) as EnrichedTranscripts;
  if (!Array.isArray(data?.comm) || !Array.isArray(data?.pao)) {
    throw new Error("Invalid enriched transcript publication");
  }
  for (const entry of data.pao) {
    const playback = entry.playback;
    if (!playback) continue;
    const { audio, video } = playback;
    if (
      !playback.feed ||
      !playback.recordingId ||
      (!audio && !video) ||
      (audio &&
        (!/^comm\/.+\.(aac|m4a)$/.test(audio.src) ||
          /[\\:%?#]/.test(audio.src) ||
          audio.src.split("/").some((part) => !part || part === "." || part === "..") ||
          !Number.isFinite(audio.s) ||
          audio.s < 0 ||
          !Number.isFinite(audio.d) ||
          audio.d <= 0)) ||
      (video &&
        (!/^[A-Za-z0-9_-]{11}$/.test(video.id) ||
          !Number.isFinite(video.s) ||
          video.s < 0 ||
          !Number.isFinite(video.d) ||
          video.d <= 0))
    ) {
      throw new Error("Invalid PAO playback selection");
    }
  }
  return { ...data, assetBase };
}

export async function loadComm(base: string): Promise<CommEntry[]> {
  return (await loadEnrichedTranscripts(base)).comm;
}
