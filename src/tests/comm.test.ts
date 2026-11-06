import { afterEach, describe, expect, it, vi } from "vitest";
import { loadComm, loadEnrichedTranscripts, parseCommCsv } from "../data/comm.ts";
import { transcriptLabel } from "../types/transcripts.ts";

afterEach(() => vi.unstubAllGlobals());

describe("web comm formats", () => {
  it("loads structured speakers separately from text and permits missing speakers", async () => {
    const entries = [
      { t: "2026-04-06T20:03:51Z", s: 0, d: 2, text: "Observation.", speaker: "Koch" },
      { t: "2026-04-06T20:04:00Z", s: 9, d: 1, text: "Copy." },
    ];
    const fetch = vi.fn().mockResolvedValue(Response.json(entries));
    vi.stubGlobal("fetch", fetch);
    const result = await loadComm("/mission/web");
    expect(result).toEqual(entries);
    expect(transcriptLabel(result[0])).toBe("Koch: Observation.");
    expect(transcriptLabel(result[1])).toBe("Copy.");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("falls back to existing CSV assets without interpreting bracketed text as a speaker", async () => {
    const csv = "2026-04-06T20:03:51Z|0|2|loops/oe1.m4a|[CAPCOM] A | B\r\n";
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("", { status: 404 }))
      .mockResolvedValueOnce(new Response(csv));
    vi.stubGlobal("fetch", fetch);
    const result = await loadComm("/mission/web");
    expect(result[0].text).toBe("[CAPCOM] A | B");
    expect(result[0].speaker).toBeUndefined();
    expect(fetch).toHaveBeenLastCalledWith("/mission/web/comm.csv");
    expect(parseCommCsv(`${csv}\ninvalid row`)).toHaveLength(1);
  });

  it("reports server errors instead of silently using stale CSV", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("", { status: 500 }));
    vi.stubGlobal("fetch", fetch);
    await expect(loadComm("/mission/web")).rejects.toThrow("comm.json HTTP 500");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("enriched transcripts", () => {
  it("loads assessments and playback metadata in one request", async () => {
    const transcripts = {
      comm: [{ t: "2026-04-01T18:00:00Z", s: 3, d: 2, category: "science", text: "A | B" }],
      pao: [{ t: "2026-04-01T18:01:00Z", category: "pao_commentary", text: "Commentary" }],
    };
    const fetch = vi.fn().mockResolvedValue(Response.json(transcripts));
    vi.stubGlobal("fetch", fetch);
    expect(await loadEnrichedTranscripts("/web")).toEqual(transcripts);
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/web/transcripts.json");
  });

  it("supports legacy installations with optional PAO and literal pipes", async () => {
    const fetch = vi.fn(async (url: string) => {
      if (url.endsWith("comm.json")) return Response.json([]);
      if (url.endsWith("PAO.csv")) return new Response("2026-04-01T18:00:00Z|A | B\r\n");
      return new Response("", { status: 404 });
    });
    vi.stubGlobal("fetch", fetch);
    expect(await loadEnrichedTranscripts("/web")).toEqual({
      comm: [],
      pao: [{ t: "2026-04-01T18:00:00Z", text: "A | B" }],
    });
  });

  it("does not silently fall back when enriched publication fails", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("", { status: 500 }));
    vi.stubGlobal("fetch", fetch);
    await expect(loadEnrichedTranscripts("/web")).rejects.toThrow("transcripts.json HTTP 500");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
