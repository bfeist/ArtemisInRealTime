import type { CommEntry, EnrichedTranscripts, PaoEntry } from "../types/transcripts.ts";

/** Read older assets while preserving literal pipes in the transcript text. */
export function parseCommCsv(csv: string): CommEntry[] {
  const entries: CommEntry[] = [];
  for (const line of csv.split("\n")) {
    const trimmed = line.replace(/\r$/, "");
    const fields = trimmed.split("|");
    if (fields.length < 5) continue;
    const [t, start, duration, src, ...textParts] = fields;
    entries.push({ t, s: Number(start), d: Number(duration), src, text: textParts.join("|") });
  }
  return entries;
}

export async function loadComm(base: string): Promise<CommEntry[]> {
  const response = await fetch(`${base}/comm.json`);
  if (response.ok) return response.json() as Promise<CommEntry[]>;
  if (response.status !== 404) throw new Error(`comm.json HTTP ${response.status}`);
  const legacy = await fetch(`${base}/comm.csv`);
  if (!legacy.ok) throw new Error(`comm.csv HTTP ${legacy.status}`);
  return parseCommCsv(await legacy.text());
}

export async function loadEnrichedTranscripts(base: string): Promise<EnrichedTranscripts> {
  const response = await fetch(`${base}/transcripts.json`);
  if (response.ok) return response.json() as Promise<EnrichedTranscripts>;
  if (response.status !== 404) throw new Error(`transcripts.json HTTP ${response.status}`);
  const [comm, paoResponse] = await Promise.all([loadComm(base), fetch(`${base}/PAO.csv`)]);
  if (!paoResponse.ok && paoResponse.status !== 404) {
    throw new Error(`PAO.csv HTTP ${paoResponse.status}`);
  }
  const pao: PaoEntry[] = [];
  for (const line of paoResponse.ok ? (await paoResponse.text()).split("\n") : []) {
    const trimmed = line.replace(/\r$/, "");
    const index = trimmed.indexOf("|");
    if (index !== -1) pao.push({ t: trimmed.slice(0, index), text: trimmed.slice(index + 1) });
  }
  return { comm, pao };
}
