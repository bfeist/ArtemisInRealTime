import type { CommEntry, EnrichedTranscripts } from "../types/transcripts.ts";

export async function loadEnrichedTranscripts(base: string): Promise<EnrichedTranscripts> {
  const response = await fetch(`${base}/transcripts.json`);
  if (!response.ok) throw new Error(`transcripts.json HTTP ${response.status}`);
  return response.json() as Promise<EnrichedTranscripts>;
}

export async function loadComm(base: string): Promise<CommEntry[]> {
  return (await loadEnrichedTranscripts(base)).comm;
}
