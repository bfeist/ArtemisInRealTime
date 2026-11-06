import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadMissionTimeline } from "../data/missionTimeline.ts";
import MissionEventsTest from "../pages/MissionEventsTest.tsx";
import type { MissionTimeline, TimelineBlock } from "../types/missionTimeline.ts";

vi.mock("../data/missionTimeline.ts", () => ({ loadMissionTimeline: vi.fn() }));

const block = (id: string, category: string, milestoneId: string): TimelineBlock =>
  ({
    id,
    title: "Outbound activities",
    description: "Activities planned during outbound flight.",
    category,
    status: "planned_unconfirmed",
    planned_start_utc: "2026-04-01T22:35:12Z",
    confirmed_start_utc: null,
    confirmed_end_utc: null,
    sub_blocks: [{ id: "sub-1", title: "Activity", category, milestone_ids: [milestoneId] }],
  }) as TimelineBlock;

const fixture = {
  schema_version: 1,
  launch_utc: "2026-04-01T22:35:12Z",
  crew: { koch: { name: "Christina Koch" }, glover: { name: "Victor Glover" } },
  milestones: {
    burn: {
      title: "Burn ignition confirmed",
      status: "started",
      t: "2026-04-01T23:00:00Z",
      reported_t: "2026-04-01T23:02:00Z",
      time_basis: "explicit_event_time",
      crew: ["glover"],
    },
    science: {
      title: "Lunar observation underway",
      status: "observed",
      t: "2026-04-02T00:00:00Z",
      reported_t: "2026-04-02T00:00:00Z",
      time_basis: "report_time",
      crew: ["koch"],
    },
    cooling: {
      title: "Cooling check complete",
      status: "completed",
      t: "2026-04-03T00:00:00Z",
      reported_t: "2026-04-03T00:00:00Z",
      time_basis: "report_time",
      crew: [],
    },
  },
  flight_days: [
    {
      flight_day: "FD01",
      kind: "flight_day",
      boundary_start_utc: "2026-04-01T22:35:12Z",
      boundary_end_utc: "2026-04-02T22:35:12Z",
      milestone_count: 2,
      phases: ["Outbound"],
      blocks: [
        block("burn-block", "maneuver", "burn"),
        block("science-block", "science", "science"),
      ],
    },
    {
      flight_day: "FD02",
      kind: "flight_day",
      boundary_start_utc: "2026-04-02T22:35:12Z",
      boundary_end_utc: "2026-04-03T22:35:12Z",
      milestone_count: 1,
      phases: ["Outbound"],
      blocks: [block("cooling-block", "systems", "cooling")],
    },
  ],
} as unknown as MissionTimeline;

function open(): void {
  render(
    <MemoryRouter>
      <MissionEventsTest />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.mocked(loadMissionTimeline).mockReset().mockResolvedValue(fixture);
});

describe("mission event visualization", () => {
  it("loads the timeline once and distinguishes event and report times", async () => {
    open();
    await screen.findByRole("heading", { name: "Flight day 1" });
    const detail = within(screen.getByRole("complementary", { name: "Selected event" }));
    expect(detail.getByRole("heading", { name: "Burn ignition confirmed" })).toBeInTheDocument();
    expect(detail.getByText("2026-04-01 23:00:00 UTC")).toBeInTheDocument();
    expect(detail.getByText("2026-04-01 23:02:00 UTC")).toBeInTheDocument();
    const list = within(screen.getByRole("region", { name: "Mission event list" }));
    fireEvent.click(list.getByRole("button", { name: /Lunar observation underway/ }));
    expect(detail.getByRole("heading", { name: "Lunar observation underway" })).toBeInTheDocument();
    expect(detail.getByText(/exact event time has not been established/)).toBeInTheDocument();
    expect(loadMissionTimeline).toHaveBeenCalledTimes(1);
  });

  it("filters events, explores a cluster, resets and navigates days without refetching", async () => {
    open();
    await screen.findByRole("heading", { name: "Flight day 1" });
    const list = within(screen.getByRole("region", { name: "Mission event list" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search events" }), {
      target: { value: "lunar" },
    });
    expect(list.queryByRole("button", { name: /Burn ignition/ })).not.toBeInTheDocument();
    expect(list.getByRole("button", { name: /Lunar observation/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Crew" }), {
      target: { value: "glover" },
    });
    expect(list.queryByRole("button", { name: /Lunar observation/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    fireEvent.click(screen.getByRole("button", { name: /1 science events near/ }));
    expect(list.queryByRole("button", { name: /Burn ignition/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show full day ×" }));
    expect(list.getByRole("button", { name: /Burn ignition/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next mission day" }));
    expect(screen.getByRole("heading", { name: "Flight day 2" })).toBeInTheDocument();
    expect(list.getByRole("button", { name: /Cooling check complete/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
      target: { value: "started" },
    });
    expect(list.getByText(/No events match/)).toBeInTheDocument();
    expect(loadMissionTimeline).toHaveBeenCalledTimes(1);
  });

  it("allows a failed data load to be retried", async () => {
    vi.mocked(loadMissionTimeline).mockRejectedValueOnce(new Error("timeline.json HTTP 503"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load mission events.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Flight day 1" })).toBeInTheDocument();
    expect(loadMissionTimeline).toHaveBeenCalledTimes(2);
  });
});
