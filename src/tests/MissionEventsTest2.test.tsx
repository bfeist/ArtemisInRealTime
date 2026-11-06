import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadMissionTimeline } from "../data/missionTimeline.ts";
import MissionEventsTest2 from "../pages/MissionEventsTest2.tsx";
import type {
  MissionTimeline,
  TimelineActivity,
  TimelineBlock,
  TimelineMilestone,
} from "../types/missionTimeline.ts";

vi.mock("../data/missionTimeline.ts", () => ({ loadMissionTimeline: vi.fn() }));

function activity(
  id: string,
  title: string,
  category: string,
  ids: string[],
  start = "2026-04-01T23:00:00Z"
): TimelineActivity {
  return {
    id,
    title,
    category,
    milestone_ids: ids,
    start_utc: start,
    end_utc: start,
    crew: [],
    tags: [],
    context_only: false,
  };
}
function block(
  id: string,
  title: string,
  activities: TimelineActivity[],
  start: string,
  end: string
): TimelineBlock {
  return {
    id,
    title,
    sub_blocks: activities,
    start_utc: start,
    end_utc: end,
    phase: "Outbound",
    category: "maneuver",
    status: "partially_executed",
    description: "Planned outbound activities.",
    planned_start_utc: start,
  } as TimelineBlock;
}
function milestone(
  title: string,
  t: string,
  overrides: Partial<TimelineMilestone> = {}
): TimelineMilestone {
  return {
    title,
    t,
    reported_t: t,
    status: "completed",
    time_basis: "report_time",
    crew: [],
    ...overrides,
  };
}
function fixture(): MissionTimeline {
  return {
    schema_version: 1,
    launch_utc: "2026-04-01T22:35:12Z",
    crew: { glover: { name: "Victor Glover" }, koch: { name: "Christina Koch" } },
    milestones: {
      burn: milestone("Burn ignition confirmed", "2026-04-01T23:00:00Z", {
        time_basis: "explicit_event_time",
        reported_t: "2026-04-01T23:02:00Z",
        status: "started",
        crew: ["glover"],
      }),
      cooling: milestone("Cooling check complete", "2026-04-02T01:00:00Z"),
      lunar: milestone("Lunar observation underway", "2026-04-02T10:00:00Z", { crew: ["koch"] }),
      sleep: milestone("Crew sleep confirmed", "2026-04-03T00:00:00Z"),
    },
    flight_days: [
      {
        flight_day: "FD01",
        kind: "flight_day",
        boundary_start_utc: "2026-04-01T22:35:12Z",
        boundary_end_utc: "2026-04-02T22:35:12Z",
        milestone_count: 3,
        phases: ["Outbound"],
        blocks: [
          block(
            "outbound",
            "Outbound checkout",
            [
              activity("burn", "Engine checkout", "maneuver", ["burn", "burn"]),
              activity(
                "cooling",
                "Cooling activity",
                "systems",
                ["cooling"],
                "2026-04-02T01:00:00Z"
              ),
            ],
            "2026-04-01T22:35:12Z",
            "2026-04-02T02:00:00Z"
          ),
          block(
            "science",
            "Lunar science",
            [
              activity(
                "lunar",
                "Window observations",
                "science",
                ["lunar"],
                "2026-04-02T10:00:00Z"
              ),
            ],
            "2026-04-02T09:00:00Z",
            "2026-04-02T11:00:00Z"
          ),
        ],
      },
      {
        flight_day: "FD02",
        kind: "flight_day",
        boundary_start_utc: "2026-04-02T22:35:12Z",
        boundary_end_utc: "2026-04-03T22:35:12Z",
        milestone_count: 1,
        phases: ["Outbound"],
        blocks: [
          block(
            "rest",
            "Crew rest",
            [activity("sleep", "Sleep period", "crew_life", ["sleep"], "2026-04-03T00:00:00Z")],
            "2026-04-03T00:00:00Z",
            "2026-04-03T04:00:00Z"
          ),
        ],
      },
    ],
  } as unknown as MissionTimeline;
}
function open(): void {
  render(
    <MemoryRouter>
      <MissionEventsTest2 />
    </MemoryRouter>
  );
}
function detail(): ReturnType<typeof within> {
  return within(screen.getByRole("region", { name: "Event detail" }));
}

beforeEach(() => {
  vi.mocked(loadMissionTimeline).mockReset().mockResolvedValue(fixture());
});

describe("mission hierarchy explorer", () => {
  it("reveals categories and activities under their own blocks and preserves the event ancestry", async () => {
    open();
    await screen.findByRole("heading", { name: "Flight day 1" });
    const hierarchy = within(screen.getByRole("region", { name: "Mission hierarchy timeline" }));
    expect(hierarchy.queryByRole("button", { name: /Engine checkout/ })).not.toBeInTheDocument();
    fireEvent.click(
      hierarchy.getByRole("button", { name: "Expand Maneuver in Outbound checkout" })
    );
    expect(hierarchy.getByRole("button", { name: /Engine checkout/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(hierarchy.queryByRole("button", { name: /Cooling activity/ })).not.toBeInTheDocument();
    const ancestry = within(screen.getByRole("list", { name: "Event hierarchy" }));
    expect(ancestry.getByText("Outbound checkout")).toBeInTheDocument();
    expect(ancestry.getByText("Maneuver")).toBeInTheDocument();
    expect(ancestry.getByText("Engine checkout")).toBeInTheDocument();
    expect(detail().getByText("2026-04-01 23:00:00 UTC")).toBeInTheDocument();
    expect(detail().getByText("2026-04-01 23:02:00 UTC")).toBeInTheDocument();
    expect(screen.getByLabelText("Selected day")).toHaveTextContent("3 events");
    fireEvent.click(hierarchy.getByRole("button", { name: "Blocks only" }));
    expect(hierarchy.queryByRole("button", { name: /Expand Maneuver/ })).not.toBeInTheDocument();
    fireEvent.click(hierarchy.getByRole("button", { name: "Show categories" }));
    fireEvent.click(hierarchy.getByRole("button", { name: /systems events near/ }));
    expect(detail().getByRole("heading", { name: "Cooling check complete" })).toBeInTheDocument();
    expect(detail().getByText(/exact event time has not been established/)).toBeInTheDocument();
    expect(hierarchy.getByRole("button", { name: /Cooling activity/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("searches ancestor titles and filters event status, crew and category without refetching", async () => {
    open();
    await screen.findByRole("heading", { name: "Flight day 1" });
    const search = screen.getByRole("searchbox", { name: "Search the hierarchy" });
    fireEvent.change(search, { target: { value: "Outbound checkout" } });
    expect(
      screen.queryByRole("button", { name: "Collapse block Lunar science" })
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Selected day")).toHaveTextContent("2 events");
    fireEvent.change(search, { target: { value: "Engine checkout" } });
    expect(screen.getByLabelText("Selected day")).toHaveTextContent("1 events");
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Crew" }), { target: { value: "koch" } });
    expect(
      detail().getByRole("heading", { name: "Lunar observation underway" })
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
      target: { value: "started" },
    });
    expect(screen.getByText(/No events match this time window/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Category" }), {
      target: { value: "systems" },
    });
    expect(detail().getByRole("heading", { name: "Cooling check complete" })).toBeInTheDocument();
    expect(loadMissionTimeline).toHaveBeenCalledTimes(1);
  });

  it("focuses time windows, resets the view and navigates days with a MET axis", async () => {
    open();
    await screen.findByRole("heading", { name: "Flight day 1" });
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Focus block Outbound checkout" }));
    expect(
      screen.queryByRole("button", { name: "Collapse block Lunar science" })
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Full day" }));
    expect(
      screen.getByRole("button", { name: "Collapse block Lunar science" })
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Time axis" }), {
      target: { value: "met" },
    });
    expect(screen.getByRole("region", { name: "Mission hierarchy timeline" })).toHaveTextContent(
      "T+000:00:00"
    );
    fireEvent.click(screen.getByRole("button", { name: "Next mission day" }));
    expect(screen.getByRole("heading", { name: "Flight day 2" })).toBeInTheDocument();
    expect(detail().getByRole("heading", { name: "Crew sleep confirmed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    expect(loadMissionTimeline).toHaveBeenCalledTimes(1);
  });

  it("reveals large activity groups in batches and keeps selected events on their list page", async () => {
    const data = fixture();
    const extraEvents = Array.from({ length: 24 }, (_, index) => {
      const id = `extra-${index}`;
      data.milestones[id] = milestone(
        `Telemetry event ${index}`,
        `2026-04-01T23:${String(index + 1).padStart(2, "0")}:00Z`
      );
      return id;
    });
    data.flight_days[0].blocks[0].sub_blocks[0].milestone_ids.push(...extraEvents);
    data.flight_days[0].blocks[0].sub_blocks.push(
      ...Array.from({ length: 32 }, (_, index) =>
        activity(`row-${index}`, `Additional activity ${index}`, "maneuver", ["burn"])
      )
    );
    vi.mocked(loadMissionTimeline).mockResolvedValue(data);
    open();
    await screen.findByRole("heading", { name: "Flight day 1" });
    fireEvent.click(screen.getByRole("button", { name: "Expand Maneuver in Outbound checkout" }));
    const hierarchy = within(screen.getByRole("region", { name: "Mission hierarchy timeline" }));
    expect(
      hierarchy.queryByRole("button", { name: /Additional activity 31/ })
    ).not.toBeInTheDocument();
    fireEvent.click(hierarchy.getByRole("button", { name: /Show 3 more activities/ }));
    expect(hierarchy.getByRole("button", { name: /Additional activity 31/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next events" }));
    expect(detail().getByRole("heading", { name: "Telemetry event 19" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Activity events" })).getByRole("button", {
        name: /Telemetry event 19/,
      })
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Previous events" }));
    expect(detail().getByRole("heading", { name: "Burn ignition confirmed" })).toBeInTheDocument();
  });

  it("recovers from a failed load", async () => {
    vi.mocked(loadMissionTimeline).mockRejectedValueOnce(new Error("timeline.json HTTP 503"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load mission events.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Flight day 1" })).toBeInTheDocument();
    expect(loadMissionTimeline).toHaveBeenCalledTimes(2);
  });
});
