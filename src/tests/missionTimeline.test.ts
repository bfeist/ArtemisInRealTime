import { afterEach, describe, expect, it, vi } from "vitest";
import { loadCrewTimeline, loadMissionTimeline } from "../data/missionTimeline.ts";
import type { TimelineAssetRef } from "../types/missionTimeline.ts";

afterEach(() => vi.unstubAllGlobals());

const crewRef: TimelineAssetRef = {
  path: "timeline/crew/koch.json",
  bytes: 100,
  gzip_bytes: 50,
};

describe("mission timeline loading", () => {
  it("loads the full mission in one request and a crew track only when requested", async () => {
    const timeline = {
      schema_version: 1,
      flight_days: [
        { blocks: [{ sub_blocks: [{ title: "Lunar science", milestone_ids: ["m1"] }] }] },
      ],
      milestones: { m1: { title: "Observation complete" } },
      crew: { koch: { timeline: crewRef } },
    };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json(timeline))
      .mockResolvedValueOnce(Response.json({ schema_version: 1, crew: "koch", items: [] }));
    vi.stubGlobal("fetch", fetch);
    const result = await loadMissionTimeline("/mission/web/");
    expect(result.flight_days[0].blocks[0].sub_blocks[0].title).toBe("Lunar science");
    expect(result.milestones.m1.title).toBe("Observation complete");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenLastCalledWith("/mission/web/timeline.json");
    const crew = await loadCrewTimeline("/mission/web", result.crew.koch.timeline);
    expect(crew.crew).toBe("koch");
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenLastCalledWith("/mission/web/timeline/crew/koch.json");
  });

  it("reports failures and permits another load to retry", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("", { status: 503 }))
      .mockResolvedValueOnce(Response.json({ schema_version: 1, flight_days: [] }));
    vi.stubGlobal("fetch", fetch);
    await expect(loadMissionTimeline("/web")).rejects.toThrow("HTTP 503");
    await expect(loadMissionTimeline("/web")).resolves.toMatchObject({ schema_version: 1 });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("rejects unsupported schemas for the timeline and crew files", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => Response.json({ schema_version: 2 }))
    );
    await expect(loadMissionTimeline("/web")).rejects.toThrow("Unsupported timeline schema");
    await expect(loadCrewTimeline("/web", crewRef)).rejects.toThrow("Unsupported timeline schema");
  });
});
