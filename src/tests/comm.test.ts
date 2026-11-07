import { afterEach, describe, expect, it, vi } from "vitest";
import { loadComm, loadEnrichedTranscripts } from "../data/comm.ts";
import { transcriptLabel } from "../types/transcripts.ts";

vi.mock("../data/missionRelease.ts", () => ({
  resolveMissionRelease: async (base: string) => base.replace(/\/+$/, ""),
}));

afterEach(() => vi.unstubAllGlobals());

describe("enriched transcripts", () => {
  it.each(["comm/../private.m4a", "comm/raw.wav", "https://example.test/audio.m4a"])(
    "rejects an invalid PAO playback path %s",
    async (src) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          Response.json({
            comm: [],
            pao: [
              {
                t: "2026-04-01T18:00:00Z",
                text: "Commentary",
                playback: {
                  source: "ia",
                  recordingId: "ia-1",
                  feed: "pao",
                  audio: { src, s: 0, d: 8 },
                },
              },
            ],
          })
        )
      );
      await expect(loadEnrichedTranscripts("/web")).rejects.toThrow(
        "Invalid PAO playback selection"
      );
    }
  );

  it("loads assessments and playback metadata in one request", async () => {
    const transcripts = {
      comm: [{ t: "2026-04-01T18:00:00Z", s: 3, d: 2, category: "science", text: "A | B" }],
      pao: [{ t: "2026-04-01T18:01:00Z", category: "mission_background", text: "Commentary" }],
    };
    const fetch = vi.fn().mockResolvedValue(Response.json(transcripts));
    vi.stubGlobal("fetch", fetch);
    expect(await loadEnrichedTranscripts("/web")).toEqual({ ...transcripts, assetBase: "/web" });
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/web/transcripts.json");
  });

  it("loads timeline comm from the same enriched publication with separate speakers", async () => {
    const entries = [
      { t: "2026-04-06T20:03:51Z", s: 0, d: 2, text: "Observation.", speaker: "Koch" },
      { t: "2026-04-06T20:04:00Z", s: 9, d: 1, text: "Copy." },
    ];
    const fetch = vi.fn().mockResolvedValue(Response.json({ comm: entries, pao: [] }));
    vi.stubGlobal("fetch", fetch);
    const result = await loadComm("/web");
    expect(result).toEqual(entries);
    expect(transcriptLabel(result[0])).toBe("Koch: Observation.");
    expect(transcriptLabel(result[1])).toBe("Copy.");
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/web/transcripts.json");
  });

  it.each([404, 500])("reports HTTP %i without loading plain transcript assets", async (status) => {
    const fetch = vi.fn().mockResolvedValue(new Response("", { status }));
    vi.stubGlobal("fetch", fetch);
    await expect(loadEnrichedTranscripts("/web")).rejects.toThrow(
      `transcripts.json HTTP ${status}`
    );
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/web/transcripts.json");
  });
});
