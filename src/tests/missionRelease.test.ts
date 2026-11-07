import { afterEach, describe, expect, it, vi } from "vitest";
import { createReleaseResolver } from "../data/missionRelease.ts";

afterEach(() => vi.unstubAllGlobals());

const pointer = (id = "baseline-01") => ({
  schema_version: 1,
  release_id: id,
  base_url: `releases/${id}/`,
  manifest: "release-manifest.json",
});

describe("mission release selection", () => {
  it("pins simultaneous and later requests to the same release", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json(pointer()));
    vi.stubGlobal("fetch", fetch);
    const resolve = createReleaseResolver();
    expect(await Promise.all([resolve("/web/"), resolve("/web")])).toEqual([
      "/web/releases/baseline-01",
      "/web/releases/baseline-01",
    ]);
    fetch.mockResolvedValue(Response.json(pointer("next")));
    expect(await resolve("/web")).toBe("/web/releases/baseline-01");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("uses legacy enriched assets only when the pointer is absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 404 })));
    expect(await createReleaseResolver()("/web/")).toBe("/web");
  });

  it("fails closed on transport failures and allows retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("", { status: 503 }))
        .mockResolvedValueOnce(Response.json(pointer()))
    );
    const resolve = createReleaseResolver();
    await expect(resolve("/web")).rejects.toThrow("HTTP 503");
    expect(await resolve("/web")).toBe("/web/releases/baseline-01");
  });

  it.each([
    { ...pointer(), base_url: "../private/" },
    { ...pointer(), base_url: "https://elsewhere.example/release/" },
    { ...pointer(), release_id: "../../private" },
    { ...pointer(), schema_version: 2 },
  ])("rejects an invalid pointer instead of mixing publications", async (value) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(value)));
    await expect(createReleaseResolver()("/web")).rejects.toThrow("Invalid mission release");
  });
});
