import type { CommEntry } from "../types/transcripts.ts";

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
