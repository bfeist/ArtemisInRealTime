import { CSSProperties, JSX, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { loadMissionTimeline } from "../data/missionTimeline.ts";
import type {
  MissionTimeline,
  TimelineBlock,
  TimelineMilestone,
} from "../types/missionTimeline.ts";
import styles from "./MissionEventsTest.module.css";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";
const PAGE_SIZE = 40;
const LANES = [
  { id: "flight", label: "Flight operations", color: "#5b9cf6" },
  { id: "systems", label: "Spacecraft systems", color: "#67e8f9" },
  { id: "science", label: "Science", color: "#c4a0ff" },
  { id: "photography", label: "Photography", color: "#4ade80" },
  { id: "crew", label: "Crew life", color: "#fbbf6a" },
  { id: "updates", label: "Mission updates", color: "#9aaec4" },
];

interface MissionEvent extends TimelineMilestone {
  id: string;
  ms: number;
  dayId: string;
  category: string;
  block: TimelineBlock;
}

interface EventCluster {
  id: string;
  laneId: string;
  start: number;
  events: MissionEvent[];
}

function laneFor(category: string): string {
  if (category === "systems") return "systems";
  if (category === "science") return "science";
  if (category === "photography") return "photography";
  if (category === "crew_life") return "crew";
  if (category === "maneuver" || category === "milestone") return "flight";
  return "updates";
}

function label(value: string): string {
  return value.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}

function dayLabel(id: string): string {
  return id.startsWith("FD") ? `Flight day ${Number(id.slice(2))}` : `Prelaunch · ${id.slice(-5)}`;
}

function utc(ms: number): string {
  return new Date(ms).toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

function clock(ms: number): string {
  return new Date(ms).toISOString().slice(11, 19);
}

function met(ms: number, launch: number): string {
  const delta = Math.floor(Math.abs(ms - launch) / 1000);
  const hours = Math.floor(delta / 3600)
    .toString()
    .padStart(3, "0");
  const minutes = Math.floor((delta % 3600) / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (delta % 60).toString().padStart(2, "0");
  return `T${ms < launch ? "−" : "+"}${hours}:${minutes}:${seconds}`;
}

function eventColor(event: MissionEvent): string {
  return LANES.find((lane) => lane.id === laneFor(event.category))!.color;
}

function statusTone(status: string): string {
  if (status === "anomaly" || status === "cancelled") return styles.alert;
  if (["completed", "confirmed", "resolved", "executed"].includes(status)) return styles.confirmed;
  return styles.neutral;
}

export default function MissionEventsTest(): JSX.Element {
  const [timeline, setTimeline] = useState<MissionTimeline | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [dayId, setDayId] = useState("FD01");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [crew, setCrew] = useState("all");
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [eventId, setEventId] = useState<string | null>(null);
  const [page, setPage] = useState(0);

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

  const events = useMemo(() => {
    if (!timeline) return [];
    const rows: MissionEvent[] = [];
    for (const day of timeline.flight_days) {
      for (const block of day.blocks) {
        for (const activity of block.sub_blocks) {
          for (const id of activity.milestone_ids) {
            const milestone = timeline.milestones[id];
            if (!milestone) continue;
            rows.push({
              ...milestone,
              id,
              ms: Date.parse(milestone.t),
              dayId: day.flight_day,
              category: activity.category,
              block,
            });
          }
        }
      }
    }
    return rows.sort((a, b) => a.ms - b.ms || a.id.localeCompare(b.id));
  }, [timeline]);

  const day =
    timeline?.flight_days.find((item) => item.flight_day === dayId) ??
    timeline?.flight_days.find((item) => item.kind === "flight_day") ??
    timeline?.flight_days[0];
  const dayEvents = useMemo(
    () => events.filter((event) => event.dayId === day?.flight_day),
    [events, day]
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return dayEvents.filter(
      (event) =>
        (category === "all" || event.category === category) &&
        (status === "all" || event.status === status) &&
        (crew === "all" || (crew === "unknown" ? !event.crew.length : event.crew.includes(crew))) &&
        (!needle ||
          `${event.title} ${event.block.title} ${event.category}`.toLowerCase().includes(needle))
    );
  }, [dayEvents, query, category, status, crew]);
  const start = day ? Date.parse(day.boundary_start_utc) : 0;
  const end = day ? Date.parse(day.boundary_end_utc) : 1;
  const duration = Math.max(end - start, 1);
  const clusters = useMemo(() => {
    const groups = new Map<string, EventCluster>();
    const binWidth = duration / 96;
    for (const event of filtered) {
      const bin = Math.max(0, Math.min(95, Math.floor((event.ms - start) / binWidth)));
      const laneId = laneFor(event.category);
      const id = `${laneId}:${bin}`;
      if (!groups.has(id))
        groups.set(id, { id, laneId, start: start + bin * binWidth, events: [] });
      groups.get(id)!.events.push(event);
    }
    return Array.from(groups.values());
  }, [filtered, start, duration]);
  const focusedCluster = clusters.find((item) => item.id === clusterId);
  const visible = focusedCluster?.events ?? filtered;
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const pageEvents = visible.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const selected = visible.find((event) => event.id === eventId) ?? pageEvents[0];
  const categories = useMemo(
    () => [...new Set(events.map((event) => event.category))].sort(),
    [events]
  );
  const statuses = useMemo(
    () => [...new Set(events.map((event) => event.status))].sort(),
    [events]
  );

  function resetSelection(): void {
    setClusterId(null);
    setEventId(null);
    setPage(0);
  }

  function chooseDay(id: string): void {
    setDayId(id);
    resetSelection();
  }

  if (!timeline || !day) {
    return (
      <main className={styles.page}>
        <h1 className={styles.heading}>Mission events</h1>
        {error ? (
          <div role="alert" className={styles.empty}>
            <p>Couldn’t load mission events.</p>
            <p className={styles.muted}>{error}</p>
            <button className={styles.button} onClick={() => setRetry((value) => value + 1)}>
              Try again
            </button>
          </div>
        ) : (
          <p role="status" className={styles.muted}>
            {timeline ? "No mission days available." : "Loading Artemis II mission events…"}
          </p>
        )}
      </main>
    );
  }

  const launch = Date.parse(timeline.launch_utc);
  const dayIndex = timeline.flight_days.indexOf(day);
  const crewName = (slug: string): string => timeline.crew[slug]?.name ?? label(slug);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>ARTEMIS II / MISSION HISTORY</p>
          <h1 className={styles.heading}>Mission events</h1>
          <p className={styles.subhead}>Explore the mission, one flight day at a time.</p>
        </div>
        <div className={styles.headerAside}>
          <span className={styles.prototype}>PROTOTYPE</span>
          <Link className={styles.link} to="/timeline-test2">
            Playback timeline ↗
          </Link>
        </div>
      </header>

      <nav className={styles.days} aria-label="Mission days">
        {timeline.flight_days.map((item) => (
          <button
            key={item.flight_day}
            className={`${styles.day} ${item === day ? styles.daySelected : ""}`}
            aria-pressed={item === day}
            onClick={() => chooseDay(item.flight_day)}
          >
            <span className={styles.dayName}>
              {item.kind === "flight_day" ? item.flight_day : item.flight_day.slice(-5)}
            </span>
            <span className={styles.dayCount}>{item.milestone_count.toLocaleString()}</span>
            <span className={styles.dayCaption}>
              {item.kind === "prelaunch" ? "PRELAUNCH" : "EVENTS"}
            </span>
            <span
              className={styles.dayMeter}
              style={{
                height: `${Math.max(4, (item.milestone_count / Math.max(...timeline.flight_days.map((d) => d.milestone_count), 1)) * 34)}px`,
              }}
            />
          </button>
        ))}
      </nav>

      <section className={styles.dayHeader} aria-label="Selected day">
        <div className={styles.dayNavigation}>
          <button
            className={styles.button}
            aria-label="Previous mission day"
            disabled={dayIndex === 0}
            onClick={() => chooseDay(timeline.flight_days[dayIndex - 1].flight_day)}
          >
            ←
          </button>
          <h2>{dayLabel(day.flight_day)}</h2>
          <button
            className={styles.button}
            aria-label="Next mission day"
            disabled={dayIndex === timeline.flight_days.length - 1}
            onClick={() => chooseDay(timeline.flight_days[dayIndex + 1].flight_day)}
          >
            →
          </button>
        </div>
        <div className={styles.dayTimes}>
          <span>
            {utc(start)} → {utc(end)}
          </span>
          <span>{day.phases.join(" / ")}</span>
        </div>
      </section>

      <section className={styles.filters} aria-label="Filter mission events">
        <label className={styles.search}>
          Search events
          <input
            type="search"
            placeholder="Burn, lunar observation, hatch…"
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
            {categories.map((value) => (
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
            {statuses.map((value) => (
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
          className={styles.reset}
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

      <section className={styles.map} aria-label="Event distribution">
        <div className={styles.sectionHeading}>
          <h3>Event distribution</h3>
          <span>{filtered.length.toLocaleString()} events · select a cluster to explore</span>
        </div>
        <div className={styles.mapScroll}>
          <div className={styles.mapInner}>
            <div className={styles.axis}>
              <span>UTC</span>
              <div>
                {Array.from({ length: 7 }, (_, index) => {
                  const time = start + (duration * index) / 6;
                  return (
                    <span key={index} style={{ left: `${(index / 6) * 100}%` }}>
                      {clock(time).slice(0, 5)}
                    </span>
                  );
                })}
              </div>
            </div>
            {LANES.map((lane) => (
              <div className={styles.lane} key={lane.id}>
                <span className={styles.laneLabel}>
                  <i style={{ background: lane.color }} />
                  {lane.label}
                </span>
                <div className={styles.laneTrack}>
                  {Array.from({ length: 7 }, (_, index) => (
                    <span
                      className={styles.gridLine}
                      key={index}
                      style={{ left: `${(index / 6) * 100}%` }}
                    />
                  ))}
                  {clusters
                    .filter((cluster) => cluster.laneId === lane.id)
                    .map((cluster) => (
                      <button
                        key={cluster.id}
                        aria-label={`${cluster.events.length} ${lane.label.toLowerCase()} events near ${clock(cluster.start)} UTC`}
                        aria-pressed={cluster.id === clusterId}
                        title={`${clock(cluster.start)} UTC · ${cluster.events.length} events`}
                        className={`${styles.cluster} ${cluster.id === clusterId ? styles.clusterSelected : ""}`}
                        style={
                          {
                            left: `${((cluster.start - start) / duration) * 100}%`,
                            "--event-color": lane.color,
                            height: `${Math.min(33, 10 + Math.log2(cluster.events.length + 1) * 5)}px`,
                          } as CSSProperties
                        }
                        onClick={() => {
                          setClusterId(cluster.id === clusterId ? null : cluster.id);
                          setEventId(null);
                          setPage(0);
                        }}
                      >
                        {cluster.events.length > 3 ? <span>{cluster.events.length}</span> : null}
                      </button>
                    ))}
                  {selected && laneFor(selected.category) === lane.id && (
                    <span
                      className={styles.selectedTick}
                      style={{
                        left: `${Math.max(0, Math.min(100, ((selected.ms - start) / duration) * 100))}%`,
                      }}
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
        <p className={styles.mapNote}>
          Times shown in UTC. Events without an established execution time appear at the time of
          their report.
        </p>
      </section>

      <div className={styles.workspace}>
        <section className={styles.eventPanel} aria-label="Mission event list">
          <div className={styles.sectionHeading}>
            <h3>
              {focusedCluster ? `Events near ${clock(focusedCluster.start)} UTC` : "Mission events"}
            </h3>
            {focusedCluster ? (
              <button className={styles.reset} onClick={resetSelection}>
                Show full day ×
              </button>
            ) : (
              <span>{visible.length.toLocaleString()} matching events</span>
            )}
          </div>
          <div
            className={styles.eventList}
            key={[day.flight_day, clusterId, currentPage, query, category, status, crew].join(":")}
          >
            {pageEvents.length ? (
              pageEvents.map((event) => (
                <button
                  key={event.id}
                  className={`${styles.eventRow} ${selected?.id === event.id ? styles.eventSelected : ""}`}
                  aria-pressed={selected?.id === event.id}
                  onClick={() => setEventId(event.id)}
                >
                  <span className={styles.eventTime}>
                    {clock(event.ms)}
                    <small>{met(event.ms, launch)}</small>
                  </span>
                  <span className={styles.eventDot} style={{ background: eventColor(event) }} />
                  <span className={styles.eventText}>
                    <span>{event.title}</span>
                    <small>
                      {label(event.category)} ·{" "}
                      {event.time_basis === "report_time" ? "Report time" : "Event time"}
                    </small>
                  </span>
                  <span className={`${styles.status} ${statusTone(event.status)}`}>
                    {label(event.status)}
                  </span>
                </button>
              ))
            ) : (
              <div className={styles.empty}>
                No events match these filters. Try another day or reset the filters.
              </div>
            )}
          </div>
          <footer className={styles.pagination}>
            <span>
              {visible.length
                ? `${currentPage * PAGE_SIZE + 1}–${Math.min((currentPage + 1) * PAGE_SIZE, visible.length)} of ${visible.length.toLocaleString()}`
                : "0 events"}
            </span>
            <div>
              <button
                className={styles.button}
                disabled={!currentPage}
                onClick={() => {
                  setPage(currentPage - 1);
                  setEventId(null);
                }}
              >
                Previous
              </button>
              <button
                className={styles.button}
                disabled={currentPage >= pageCount - 1}
                onClick={() => {
                  setPage(currentPage + 1);
                  setEventId(null);
                }}
              >
                Next
              </button>
            </div>
          </footer>
        </section>

        <aside className={styles.inspector} aria-label="Selected event">
          {selected ? (
            <>
              <div className={styles.sectionHeading}>
                <h3>Event detail</h3>
                <span className={`${styles.status} ${statusTone(selected.status)}`}>
                  {label(selected.status)}
                </span>
              </div>
              <div className={styles.inspectorBody}>
                <span className={styles.category} style={{ color: eventColor(selected) }}>
                  {label(selected.category)}
                </span>
                <h2>{selected.title}</h2>
                <div className={styles.eventClock}>{met(selected.ms, launch)}</div>
                <dl className={styles.facts}>
                  <div>
                    <dt>{selected.time_basis === "report_time" ? "Report time" : "Event time"}</dt>
                    <dd>{utc(selected.ms)}</dd>
                  </div>
                  {selected.time_basis === "explicit_event_time" &&
                    Date.parse(selected.reported_t) !== selected.ms && (
                      <div>
                        <dt>Reported at</dt>
                        <dd>{utc(Date.parse(selected.reported_t))}</dd>
                      </div>
                    )}
                  <div>
                    <dt>Crew</dt>
                    <dd>
                      {selected.crew.length
                        ? selected.crew.map(crewName).join(", ")
                        : "Unattributed"}
                    </dd>
                  </div>
                </dl>
                {selected.time_basis === "report_time" && (
                  <p className={styles.timingNote}>
                    Placed at the time of the report. The exact event time has not been established.
                  </p>
                )}
                <details className={styles.blockDetail} key={selected.block.id}>
                  <summary>Activity block</summary>
                  <h3>{selected.block.title}</h3>
                  <span className={`${styles.status} ${statusTone(selected.block.status)}`}>
                    {label(selected.block.status)}
                  </span>
                  <p>{selected.block.description}</p>
                  <dl className={styles.facts}>
                    {selected.block.planned_start_utc && (
                      <div>
                        <dt>Planned start</dt>
                        <dd>{utc(Date.parse(selected.block.planned_start_utc))}</dd>
                      </div>
                    )}
                    {selected.block.confirmed_start_utc && (
                      <div>
                        <dt>Confirmed constituent start</dt>
                        <dd>{utc(Date.parse(selected.block.confirmed_start_utc))}</dd>
                      </div>
                    )}
                    {selected.block.confirmed_end_utc && (
                      <div>
                        <dt>Confirmed constituent end</dt>
                        <dd>{utc(Date.parse(selected.block.confirmed_end_utc))}</dd>
                      </div>
                    )}
                  </dl>
                </details>
              </div>
            </>
          ) : (
            <div className={styles.empty}>
              Choose an event to see its mission time, status and crew.
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
