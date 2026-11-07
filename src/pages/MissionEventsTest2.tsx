import { CSSProperties, JSX, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { loadMissionTimeline } from "../data/missionTimeline.ts";
import type {
  MissionTimeline,
  TimelineActivity,
  TimelineBlock,
  TimelineMilestone,
} from "../types/missionTimeline.ts";
import styles from "./MissionEventsTest2.module.css";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";
const ACTIVITY_LIMIT = 30;
const EVENT_LIMIT = 20;
const COLORS: Record<string, string> = {
  maneuver: "#82b5ff",
  milestone: "#a7c7ee",
  systems: "#5dd9da",
  science: "#c6a0ff",
  photography: "#8cdbac",
  crew_life: "#f2bd7b",
  media_event: "#f09ab8",
  communications: "#82cdd6",
  mission_background: "#9eaec2",
  social: "#d0b0c2",
  unspecified: "#9eaec2",
  pao_commentary: "#9eaec2",
  pao_filler: "#9eaec2",
};

interface Event extends TimelineMilestone {
  id: string;
  ms: number;
}
interface Activity {
  source: TimelineActivity;
  events: Event[];
  start: number;
  end: number;
}
interface Category {
  id: string;
  activities: Activity[];
  events: Event[];
}
interface Block {
  source: TimelineBlock;
  categories: Category[];
  events: Event[];
  start: number;
  end: number;
}
interface Selection {
  blockId: string;
  activityId: string;
  eventId?: string;
}

function label(value: string): string {
  if (value === "pao_commentary") return "PAO commentary";
  if (value === "pao_filler") return "PAO filler";
  return value.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}
function color(category: string): string {
  return COLORS[category] ?? "#a7c7ee";
}
function clock(ms: number): string {
  return new Date(ms).toISOString().slice(11, 19);
}
function utc(ms: number): string {
  return new Date(ms).toISOString().replace("T", " ").slice(0, 19) + " UTC";
}
function met(ms: number, launch: number): string {
  const seconds = Math.floor(Math.abs(ms - launch) / 1000);
  return `T${ms < launch ? "−" : "+"}${Math.floor(seconds / 3600)
    .toString()
    .padStart(3, "0")}:${Math.floor((seconds % 3600) / 60)
    .toString()
    .padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}
function uniqueEvents(events: Event[]): Event[] {
  return [...new Map(events.map((event) => [event.id, event])).values()].sort(
    (a, b) => a.ms - b.ms || a.id.localeCompare(b.id)
  );
}
function rangeStyle(start: number, end: number, from: number, to: number): CSSProperties {
  const left = Math.max(0, Math.min(100, ((start - from) / (to - from)) * 100));
  const right = Math.max(left, Math.min(100, ((end - from) / (to - from)) * 100));
  return { left: `${left}%`, width: `${right - left}%` };
}

export default function MissionEventsTest2(): JSX.Element {
  const [timeline, setTimeline] = useState<MissionTimeline | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [dayId, setDayId] = useState("FD01");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [crew, setCrew] = useState("all");
  const [closedBlocks, setClosedBlocks] = useState<Set<string>>(new Set());
  const [openCategories, setOpenCategories] = useState<Set<string>>(new Set());
  const [limits, setLimits] = useState<Record<string, number>>({});
  const [selection, setSelection] = useState<Selection | null>(null);
  const [eventPage, setEventPage] = useState(0);
  const [window, setWindow] = useState<[number, number] | null>(null);
  const [timeMode, setTimeMode] = useState("utc");

  useEffect(() => {
    let cancelled = false;
    setError("");
    loadMissionTimeline(`${ASSETS_BASE}/artemis-ii/web`)
      .then((data) => {
        if (!cancelled) setTimeline(data);
      })
      .catch((reason: unknown) => {
        if (!cancelled)
          setError(reason instanceof Error ? reason.message : "Unable to load mission events.");
      });
    return () => {
      cancelled = true;
    };
  }, [retry]);

  const day =
    timeline?.flight_days.find((item) => item.flight_day === dayId) ??
    timeline?.flight_days.find((item) => item.kind === "flight_day") ??
    timeline?.flight_days[0];
  const dayStart = day ? Date.parse(day.boundary_start_utc) : 0;
  const dayEnd = day ? Date.parse(day.boundary_end_utc) : 1;
  const [from, to] = window ?? [dayStart, dayEnd];
  const launch = timeline ? Date.parse(timeline.launch_utc) : 0;
  const formatTime = (ms: number): string => (timeMode === "utc" ? clock(ms) : met(ms, launch));

  const blocks = useMemo(() => {
    if (!day || !timeline) return [];
    const needle = query.trim().toLowerCase();
    const result: Block[] = [];
    for (const block of day.blocks) {
      const groups = new Map<string, Activity[]>();
      for (const activity of block.sub_blocks) {
        if (category !== "all" && activity.category !== category) continue;
        const parentMatches =
          `${block.title} ${block.phase} ${activity.title} ${label(activity.category)} ${activity.tags.join(" ")}`
            .toLowerCase()
            .includes(needle);
        const events = uniqueEvents(
          activity.milestone_ids.flatMap((id) => {
            const event = timeline.milestones[id];
            if (!event) return [];
            const ms = Date.parse(event.t);
            if (
              !Number.isFinite(ms) ||
              ms < from ||
              ms > to ||
              (status !== "all" && event.status !== status) ||
              (crew !== "all" &&
                (crew === "unknown" ? event.crew.length > 0 : !event.crew.includes(crew))) ||
              (needle && !parentMatches && !event.title.toLowerCase().includes(needle))
            )
              return [];
            return [{ ...event, id, ms }];
          })
        );
        if (!events.length) continue;
        const start = Math.min(Date.parse(activity.start_utc), events[0].ms);
        const end = Math.max(Date.parse(activity.end_utc), ...events.map((event) => event.ms));
        const row = { source: activity, events, start, end };
        if (!groups.has(activity.category)) groups.set(activity.category, []);
        groups.get(activity.category)!.push(row);
      }
      const categories = [...groups].map(([id, activities]) => ({
        id,
        activities: activities.sort((a, b) => a.start - b.start),
        events: uniqueEvents(activities.flatMap((activity) => activity.events)),
      }));
      if (!categories.length) continue;
      categories.sort((a, b) => a.events[0].ms - b.events[0].ms || a.id.localeCompare(b.id));
      const events = uniqueEvents(categories.flatMap((group) => group.events));
      result.push({
        source: block,
        categories,
        events,
        start: Math.min(Date.parse(block.start_utc), events[0].ms),
        end: Math.max(Date.parse(block.end_utc), events[events.length - 1].ms),
      });
    }
    return result.sort((a, b) => a.start - b.start);
  }, [day, timeline, query, category, status, crew, from, to]);

  const options = useMemo(() => {
    if (!timeline) return { categories: [], statuses: [] };
    return {
      categories: [
        ...new Set(
          timeline.flight_days.flatMap((d) =>
            d.blocks.flatMap((b) => b.sub_blocks.map((a) => a.category))
          )
        ),
      ].sort(),
      statuses: [
        ...new Set(Object.values(timeline.milestones).map((event) => event.status)),
      ].sort(),
    };
  }, [timeline]);
  const selectedBlock = blocks.find((block) => block.source.id === selection?.blockId);
  const selectedActivity = selectedBlock?.categories
    .flatMap((group) => group.activities)
    .find((activity) => activity.source.id === selection?.activityId);
  const activeBlock = selectedActivity ? selectedBlock : blocks[0];
  const activeActivity = selectedActivity ?? activeBlock?.categories[0]?.activities[0];
  const activeEvent =
    activeActivity?.events.find((event) => event.id === selection?.eventId) ??
    activeActivity?.events[0];
  const pageCount = Math.max(1, Math.ceil((activeActivity?.events.length ?? 0) / EVENT_LIMIT));
  const currentPage = Math.min(eventPage, pageCount - 1);
  const eventCount = uniqueEvents(blocks.flatMap((block) => block.events)).length;
  const activityCount = blocks.reduce(
    (total, block) => total + block.categories.reduce((n, group) => n + group.activities.length, 0),
    0
  );

  function resetSelection(): void {
    setSelection(null);
    setEventPage(0);
    setLimits({});
  }
  function chooseDay(id: string): void {
    setDayId(id);
    setWindow(null);
    setClosedBlocks(new Set());
    setOpenCategories(new Set());
    resetSelection();
  }
  function toggle(key: string, setter: typeof setClosedBlocks): void {
    setter((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  function selectActivity(block: Block, activity: Activity, eventId?: string): void {
    setSelection({ blockId: block.source.id, activityId: activity.source.id, eventId });
    const index = Math.max(
      0,
      activity.events.findIndex((event) => event.id === eventId)
    );
    setEventPage(Math.floor(index / EVENT_LIMIT));
  }
  function changeWindow(start: number, end: number): void {
    const duration = Math.min(dayEnd - dayStart, Math.max(60000, end - start));
    const left = Math.max(dayStart, Math.min(dayEnd - duration, start));
    setWindow([left, left + duration]);
    setEventPage(0);
    setLimits({});
  }
  function zoom(factor: number): void {
    const center =
      activeEvent && activeEvent.ms >= from && activeEvent.ms <= to
        ? activeEvent.ms
        : (from + to) / 2;
    const duration = (to - from) * factor;
    changeWindow(center - duration / 2, center + duration / 2);
  }

  function eventMarks(
    events: Event[],
    categoryId: string,
    onSelect: (event: Event) => void
  ): JSX.Element[] {
    const bins = new Map<number, Event[]>();
    for (const event of events) {
      const bin = Math.min(79, Math.max(0, Math.floor(((event.ms - from) / (to - from)) * 80)));
      if (!bins.has(bin)) bins.set(bin, []);
      bins.get(bin)!.push(event);
    }
    return [...bins].map(([bin, items]) => (
      <button
        key={bin}
        className={styles.eventMark}
        style={{
          left: `${(bin / 80) * 100}%`,
          height: `${Math.min(27, 8 + Math.log2(items.length + 1) * 4)}px`,
        }}
        title={`${items.length} event${items.length === 1 ? "" : "s"} · ${clock(items[0].ms)} UTC`}
        aria-label={`${items.length} ${label(categoryId).toLowerCase()} events near ${clock(items[0].ms)} UTC: ${items[0].title}`}
        onClick={() => onSelect(items[0])}
      />
    ));
  }

  if (!timeline || !day) {
    return (
      <main className={styles.page}>
        <h1>Mission events / hierarchy</h1>
        {error ? (
          <div role="alert" className={styles.empty}>
            <p>Couldn’t load mission events.</p>
            <p>{error}</p>
            <button onClick={() => setRetry((value) => value + 1)}>Try again</button>
          </div>
        ) : (
          <p role="status">
            {timeline ? "No mission days available." : "Loading Artemis II mission events…"}
          </p>
        )}
      </main>
    );
  }

  const dayIndex = timeline.flight_days.indexOf(day);
  const dayTitle =
    day.kind === "flight_day"
      ? `Flight day ${Number(day.flight_day.slice(2))}`
      : `Prelaunch · ${day.flight_day.slice(-10)}`;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>ARTEMIS II / MISSION HISTORY / EXPLORER 02</p>
          <h1>
            Mission events <span>/ hierarchy</span>
          </h1>
          <p className={styles.subhead}>Follow the timeline. Unfold the mission.</p>
        </div>
        <Link to="/mission-events-test">Original event explorer ↗</Link>
      </header>

      <nav className={styles.days} aria-label="Mission days">
        {timeline.flight_days.map((item) => (
          <button
            key={item.flight_day}
            aria-pressed={item === day}
            onClick={() => chooseDay(item.flight_day)}
          >
            <strong>
              {item.kind === "flight_day" ? item.flight_day : item.flight_day.slice(-5)}
            </strong>
            <small>
              {item.kind === "prelaunch"
                ? "PRELAUNCH"
                : `${item.milestone_count.toLocaleString()} EVENTS`}
            </small>
          </button>
        ))}
      </nav>

      <section className={styles.dayHeading} aria-label="Selected day">
        <div className={styles.navigation}>
          <button
            aria-label="Previous mission day"
            disabled={dayIndex === 0}
            onClick={() => chooseDay(timeline.flight_days[dayIndex - 1].flight_day)}
          >
            ←
          </button>
          <div>
            <h2>{dayTitle}</h2>
            <p>{day.phases.join(" / ")}</p>
          </div>
          <button
            aria-label="Next mission day"
            disabled={dayIndex === timeline.flight_days.length - 1}
            onClick={() => chooseDay(timeline.flight_days[dayIndex + 1].flight_day)}
          >
            →
          </button>
        </div>
        <p className={styles.summary} aria-live="polite">
          <strong>{blocks.length}</strong> blocks <span>›</span>{" "}
          <strong>{activityCount.toLocaleString()}</strong> activities <span>›</span>{" "}
          <strong>{eventCount.toLocaleString()}</strong> events
        </p>
      </section>

      <section className={styles.filters} aria-label="Filter mission events">
        <label className={styles.search}>
          Search the hierarchy
          <input
            type="search"
            placeholder="Block, activity, event or topic…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              resetSelection();
            }}
          />
        </label>
        <label>
          Category
          <select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              resetSelection();
            }}
          >
            <option value="all">All categories</option>
            {options.categories.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              resetSelection();
            }}
          >
            <option value="all">All statuses</option>
            {options.statuses.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Crew
          <select
            value={crew}
            onChange={(event) => {
              setCrew(event.target.value);
              resetSelection();
            }}
          >
            <option value="all">All crew</option>
            {Object.entries(timeline.crew).map(([slug, info]) => (
              <option key={slug} value={slug}>
                {info.name}
              </option>
            ))}
            <option value="unknown">Unattributed</option>
          </select>
        </label>
        <button
          onClick={() => {
            setQuery("");
            setCategory("all");
            setStatus("all");
            setCrew("all");
            resetSelection();
          }}
        >
          Reset filters
        </button>
      </section>

      <div className={styles.workspace}>
        <section className={styles.explorer} aria-label="Mission hierarchy timeline">
          <div className={styles.toolbar}>
            <div className={styles.segmented} aria-label="Hierarchy detail">
              <button
                onClick={() => {
                  setClosedBlocks(new Set(blocks.map((block) => block.source.id)));
                  setOpenCategories(new Set());
                }}
              >
                Blocks only
              </button>
              <button onClick={() => setClosedBlocks(new Set())}>Show categories</button>
            </div>
            <div className={styles.navigation}>
              <button
                aria-label="Pan earlier"
                disabled={from <= dayStart}
                onClick={() => changeWindow(from - (to - from) / 2, to - (to - from) / 2)}
              >
                ←
              </button>
              <button aria-label="Zoom in" disabled={to - from <= 60000} onClick={() => zoom(0.5)}>
                ＋
              </button>
              <button
                aria-label="Zoom out"
                disabled={to - from >= dayEnd - dayStart}
                onClick={() => zoom(2)}
              >
                −
              </button>
              <button
                aria-label="Pan later"
                disabled={to >= dayEnd}
                onClick={() => changeWindow(from + (to - from) / 2, to + (to - from) / 2)}
              >
                →
              </button>
              <button
                onClick={() => {
                  setWindow(null);
                  resetSelection();
                }}
              >
                Full day
              </button>
              <label className={styles.timeMode}>
                Time axis
                <select
                  aria-label="Time axis"
                  value={timeMode}
                  onChange={(event) => setTimeMode(event.target.value)}
                >
                  <option value="utc">UTC</option>
                  <option value="met">MET</option>
                </select>
              </label>
            </div>
          </div>
          <div className={styles.windowLabel}>
            {utc(from)} <span>→</span> {utc(to)}
          </div>
          <div className={styles.timelineScroll}>
            <div className={styles.timelineInner}>
              <div className={`${styles.row} ${styles.axis}`}>
                <div className={styles.axisLabel}>BLOCK / CATEGORY / ACTIVITY</div>
                <div className={styles.axisTrack}>
                  {Array.from({ length: 5 }, (_, index) => (
                    <span key={index} style={{ left: `${index * 25}%` }}>
                      {formatTime(from + ((to - from) * index) / 4).slice(
                        0,
                        timeMode === "utc" ? 5 : undefined
                      )}
                    </span>
                  ))}
                </div>
              </div>
              {blocks.map((block, blockIndex) => {
                const blockOpen = !closedBlocks.has(block.source.id);
                return (
                  <div key={block.source.id} className={styles.blockGroup}>
                    <div className={`${styles.row} ${styles.blockRow}`}>
                      <button
                        className={styles.treeLabel}
                        aria-expanded={blockOpen}
                        aria-label={`${blockOpen ? "Collapse" : "Expand"} block ${block.source.title}`}
                        onClick={() => toggle(block.source.id, setClosedBlocks)}
                      >
                        <span className={styles.chevron}>{blockOpen ? "▾" : "▸"}</span>
                        <span className={styles.blockNumber}>
                          {String(blockIndex + 1).padStart(2, "0")}
                        </span>
                        <span className={styles.labelText}>
                          <strong>{block.source.title}</strong>
                          <small>
                            {block.source.phase} · {block.events.length} events
                          </small>
                        </span>
                      </button>
                      <div className={styles.track}>
                        <button
                          className={styles.blockBar}
                          style={rangeStyle(block.start, block.end, from, to)}
                          title="Display window; not an execution duration. Click to focus."
                          aria-label={`Focus block ${block.source.title}`}
                          onClick={() => changeWindow(block.start - 60000, block.end + 60000)}
                        >
                          <span>{clock(Math.max(from, block.start)).slice(0, 5)}</span>
                        </button>
                      </div>
                    </div>
                    {blockOpen &&
                      block.categories.map((group) => {
                        const key = `${block.source.id}:${group.id}`;
                        const isOpen = openCategories.has(key);
                        const limit = limits[key] ?? ACTIVITY_LIMIT;
                        return (
                          <div
                            key={key}
                            style={{ "--category-color": color(group.id) } as CSSProperties}
                          >
                            <div className={`${styles.row} ${styles.categoryRow}`}>
                              <button
                                className={`${styles.treeLabel} ${styles.categoryLabel}`}
                                aria-expanded={isOpen}
                                aria-label={`${isOpen ? "Collapse" : "Expand"} ${label(group.id)} in ${block.source.title}`}
                                onClick={() => toggle(key, setOpenCategories)}
                              >
                                <span className={styles.chevron}>{isOpen ? "▾" : "▸"}</span>
                                <i className={styles.swatch} />
                                <span className={styles.labelText}>
                                  <strong>{label(group.id)}</strong>
                                  <small>
                                    {group.activities.length} activities · {group.events.length}{" "}
                                    events
                                  </small>
                                </span>
                              </button>
                              <div className={styles.track}>
                                {eventMarks(group.events, group.id, (event) => {
                                  const activity = group.activities.find((item) =>
                                    item.events.some((item) => item.id === event.id)
                                  )!;
                                  selectActivity(block, activity, event.id);
                                  setOpenCategories((previous) => new Set([...previous, key]));
                                  setLimits((previous) => ({
                                    ...previous,
                                    [key]: Math.max(limit, group.activities.indexOf(activity) + 1),
                                  }));
                                })}
                              </div>
                            </div>
                            {isOpen &&
                              group.activities.slice(0, limit).map((activity) => {
                                const selected = activity === activeActivity;
                                return (
                                  <div
                                    key={activity.source.id}
                                    className={`${styles.row} ${styles.activityRow} ${selected ? styles.selectedRow : ""}`}
                                  >
                                    <button
                                      className={`${styles.treeLabel} ${styles.activityLabel}`}
                                      aria-pressed={selected}
                                      title={activity.source.title}
                                      onClick={() => selectActivity(block, activity)}
                                    >
                                      <span className={styles.branch}>└</span>
                                      <span className={styles.labelText}>
                                        {activity.source.title}
                                        <small>
                                          {activity.events.length} events ·{" "}
                                          {clock(activity.events[0].ms)} UTC
                                        </small>
                                      </span>
                                    </button>
                                    <div className={styles.track}>
                                      <span
                                        className={styles.activityBar}
                                        style={rangeStyle(activity.start, activity.end, from, to)}
                                      />
                                      {eventMarks(activity.events, group.id, (event) =>
                                        selectActivity(block, activity, event.id)
                                      )}
                                      {selected && activeEvent && (
                                        <span
                                          className={styles.selectedTick}
                                          style={{
                                            left: `${((activeEvent.ms - from) / (to - from)) * 100}%`,
                                          }}
                                        />
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            {isOpen && group.activities.length > limit && (
                              <div className={styles.moreRow}>
                                <button
                                  onClick={() =>
                                    setLimits((previous) => ({
                                      ...previous,
                                      [key]: limit + ACTIVITY_LIMIT,
                                    }))
                                  }
                                >
                                  Show {Math.min(ACTIVITY_LIMIT, group.activities.length - limit)}{" "}
                                  more activities ({group.activities.length - limit} remaining)
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                );
              })}
              {!blocks.length && (
                <div className={styles.empty} role="status">
                  No events match this time window and filters. Try Full day or Reset filters.
                </div>
              )}
            </div>
          </div>
          <div className={styles.legend}>
            <span>
              <i className={styles.legendBar} /> Block display window
            </span>
            <span>
              <i className={styles.legendMark} /> Event cluster · click to inspect
            </span>
            <span>Expand a category to reveal activities</span>
          </div>
          <p className={styles.note}>
            Categories group activities inside each block. Only activities with matching mission
            events are shown. Bars show display or evidence windows, not confirmed execution
            durations.
          </p>
        </section>

        <aside className={styles.inspector} aria-label="Selected activity and event">
          {activeBlock && activeActivity && activeEvent ? (
            <>
              <div className={styles.inspectorHeading}>
                <h2>Inside the activity</h2>
                <span>{activeActivity.events.length} EVENTS</span>
              </div>
              <ol className={styles.breadcrumb} aria-label="Event hierarchy">
                <li>
                  <small>01 / MISSION BLOCK</small>
                  <span>{activeBlock.source.title}</span>
                </li>
                <li>
                  <small>02 / CATEGORY</small>
                  <span style={{ color: color(activeActivity.source.category) }}>
                    {label(activeActivity.source.category)}
                  </span>
                </li>
                <li>
                  <small>03 / ACTIVITY</small>
                  <strong>{activeActivity.source.title}</strong>
                </li>
              </ol>
              <section className={styles.eventSection} aria-label="Activity events">
                <div className={styles.inspectorHeading}>
                  <h3>04 / EVENTS</h3>
                  <span>CHRONOLOGICAL</span>
                </div>
                <div
                  className={styles.eventList}
                  key={`${activeBlock.source.id}:${activeActivity.source.id}:${currentPage}`}
                >
                  {activeActivity.events
                    .slice(currentPage * EVENT_LIMIT, (currentPage + 1) * EVENT_LIMIT)
                    .map((event) => (
                      <button
                        key={event.id}
                        aria-pressed={event.id === activeEvent.id}
                        onClick={() =>
                          setSelection({
                            blockId: activeBlock.source.id,
                            activityId: activeActivity.source.id,
                            eventId: event.id,
                          })
                        }
                      >
                        <small>
                          {clock(event.ms)} UTC{" "}
                          <span>{event.time_basis === "report_time" ? "REPORT" : "EVENT"}</span>
                        </small>
                        <span>{event.title}</span>
                      </button>
                    ))}
                </div>
                {pageCount > 1 && (
                  <div className={styles.pagination}>
                    <button
                      disabled={!currentPage}
                      onClick={() =>
                        selectActivity(
                          activeBlock,
                          activeActivity,
                          activeActivity.events[(currentPage - 1) * EVENT_LIMIT].id
                        )
                      }
                    >
                      Previous events
                    </button>
                    <span>
                      {currentPage + 1} / {pageCount}
                    </span>
                    <button
                      disabled={currentPage >= pageCount - 1}
                      onClick={() =>
                        selectActivity(
                          activeBlock,
                          activeActivity,
                          activeActivity.events[(currentPage + 1) * EVENT_LIMIT].id
                        )
                      }
                    >
                      Next events
                    </button>
                  </div>
                )}
              </section>
              <section className={styles.eventDetail} aria-label="Event detail">
                <span className={styles.status}>{label(activeEvent.status)}</span>
                <h3>{activeEvent.title}</h3>
                <p className={styles.met}>{met(activeEvent.ms, launch)}</p>
                <dl>
                  <div>
                    <dt>
                      {activeEvent.time_basis === "report_time" ? "Report time" : "Event time"}
                    </dt>
                    <dd>{utc(activeEvent.ms)}</dd>
                  </div>
                  {activeEvent.time_basis === "explicit_event_time" &&
                    activeEvent.reported_t !== activeEvent.t && (
                      <div>
                        <dt>Reported at</dt>
                        <dd>{utc(Date.parse(activeEvent.reported_t))}</dd>
                      </div>
                    )}
                  <div>
                    <dt>Crew</dt>
                    <dd>
                      {activeEvent.crew.length
                        ? activeEvent.crew
                            .map((slug) => timeline.crew[slug]?.name ?? label(slug))
                            .join(", ")
                        : "Unattributed"}
                    </dd>
                  </div>
                </dl>
                {activeEvent.time_basis === "report_time" && (
                  <p className={styles.timingNote}>
                    Placed at the time of the report. The exact event time has not been established.
                  </p>
                )}
                <details key={activeBlock.source.id}>
                  <summary>Mission block context</summary>
                  <p>{activeBlock.source.description}</p>
                  <p>Block status: {label(activeBlock.source.status)}</p>
                  {activeBlock.source.planned_start_utc && (
                    <p>Planned start: {utc(Date.parse(activeBlock.source.planned_start_utc))}</p>
                  )}
                </details>
              </section>
            </>
          ) : (
            <p className={styles.empty}>
              Choose an activity or an event mark to inspect its place in the mission.
            </p>
          )}
        </aside>
      </div>
    </main>
  );
}
