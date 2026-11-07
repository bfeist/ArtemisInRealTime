export interface TranscriptAssessment {
  importance?: number;
  category?: string;
  tags?: string[];
  topic?: string;
  event_id?: string;
  source?: "PAO" | "OE-1" | "OE-2" | "PCD" | "unknown";
  statement_kinds?: string[];
}

export interface PaoEntry extends TranscriptAssessment {
  t: string;
  text: string;
  pao_type?: "commentary" | "filler" | "other";
  playback?: {
    source: string;
    recordingId: string;
    feed: string;
    audio?: { src: string; s: number; d: number };
    video?: { id: string; s: number; d: number; mappingId: string };
  };
}

export interface CommEntry extends PaoEntry {
  s?: number;
  d: number;
  src?: string;
  speaker?: string;
  lang?: string;
}

export interface EnrichedTranscripts {
  comm: CommEntry[];
  pao: PaoEntry[];
  /** Resolved once by the loader so playback uses the same immutable release as its text. */
  assetBase?: string;
}

export function transcriptLabel(entry: CommEntry): string {
  return entry.speaker ? `${entry.speaker}: ${entry.text}` : entry.text;
}
