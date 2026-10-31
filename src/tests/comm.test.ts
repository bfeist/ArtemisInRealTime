import { afterEach, describe, expect, it, vi } from "vitest";
import { loadComm, parseCommCsv } from "../data/comm.ts";
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
