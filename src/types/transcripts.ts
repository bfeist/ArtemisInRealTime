export interface CommEntry {
  t: string;
  s?: number;
  d: number;
  src?: string;
  text: string;
  speaker?: string;
  lang?: string;
}

export function transcriptLabel(entry: CommEntry): string {
  return entry.speaker ? `${entry.speaker}: ${entry.text}` : entry.text;
}
