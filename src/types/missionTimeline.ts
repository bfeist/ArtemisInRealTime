/** Static presentation data exported by server-batch step 1h. All times are UTC. */
export interface TimelineAssetRef {
  /** Relative to the mission web root. */
  path: string;
  bytes: number;
  /** Estimate for hosts that enable HTTP gzip; the file itself is ordinary JSON. */
  gzip_bytes: number;
}

export interface TimelineMilestone {
  title: string;
  status: string;
  /** Event time when explicitly established; otherwise the report time. */
  t: string;
  reported_t: string;
  end_utc?: string;
  last_reported_t?: string;
  /** A report timestamp must not be displayed as an established execution time. */
  time_basis: "explicit_event_time" | "report_time";
  crew: string[];
  related_planned_block_ids?: string[];
}

export interface TimelineActivity {
  /** Scoped to its containing block. */
  id: string;
  title: string;
  category: string;
  start_utc: string;
  end_utc: string;
  crew: string[];
  tags: string[];
  context_only: boolean;
  reference_only?: boolean;
  status?: string;
  planned_start_utc?: string;
  /** Keys in MissionTimeline.milestones. */
  milestone_ids: string[];
}

export interface TimelineCrewActivity {
  block_id: string;
  flight_day: string;
  start_utc: string;
  end_utc: string;
  activity: string;
  category: string;
  status: string;
  source_type: "investigated_milestone" | "reviewed_utterance";
  milestone_ids: string[];
  reported_start_utc: string;
  reported_end_utc: string;
  event_time: string | null;
  time_basis: "explicit_event_time" | "report_time";
  shared_with_all: boolean;
  topics?: string[];
  tags?: string[];
}

export interface TimelineBlock {
  id: string;
  flight_day: string;
  planned_block_id: string | null;
  planned_flight_day: string | null;
  phase: string;
  title: string;
  category: string;
  status: string;
  status_scope: "planned_activity_group" | "observed_collection";
  time_source: "planned_and_evidence_window" | "event_and_report_times";
  /** Display bounds for this day segment; these are not execution bounds. */
  start_utc: string;
  end_utc: string;
  planned_start_utc: string | null;
  planned_end_utc: string | null;
  confirmed_start_utc: string | null;
  confirmed_end_utc: string | null;
  confirmed_time_scope: "constituent_activity_evidence";
  schedule_delta_min: number | null;
  crew: string[];
  utterance_count: number;
  milestone_count: number;
  photo_count: number;
  description: string;
  reference_events: { id: string; kind: string; t: string; label: string }[];
  constituent_milestone_ids: string[];
  sub_blocks: TimelineActivity[];
}

export interface TimelineDay {
  flight_day: string;
  kind: "prelaunch" | "flight_day";
  boundary_start_utc: string;
  boundary_end_utc: string;
  start_utc: string;
  end_utc: string;
  phases: string[];
  block_count: number;
  executed_block_count: number;
  partially_executed_block_count: number;
  milestone_count: number;
  utterance_count: number;
  blocks: TimelineBlock[];
}

export interface MissionTimeline {
  schema_version: 1;
  mission_id: string;
  mission_name: string;
  launch_utc: string;
  splashdown_utc: string | null;
  summary: Record<string, number>;
  crew: Record<
    string,
    { name: string; role: string; activity_count: number; timeline: TimelineAssetRef }
  >;
  /** Stored once; sub-blocks, constituents and crew tracks reference these keys. */
  milestones: Record<string, TimelineMilestone>;
  flight_days: TimelineDay[];
}

export interface TimelineCrewTrack {
  schema_version: 1;
  crew: string;
  items: TimelineCrewActivity[];
}
