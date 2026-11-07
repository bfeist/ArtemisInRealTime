import { describe, expect, it } from "vitest";
import {
  findVideoPart,
  validateVideoParts,
  validateVideoSync,
  videoPositions,
  type VideoSync,
} from "../data/videoSync.ts";

const parts = [
  { partNum: 1, videoId: "HOMxD7rFO8o", startOffsetS: 0, durationS: 10 },
  { partNum: 2, videoId: "_AgVLxn1_xs", startOffsetS: 12, durationS: 10 },
];
describe("video coverage and synchronization", () => {
  it("does not seek through an uncovered interval or beyond the last frame", () => {
    expect(findVideoPart(parts, 9)).toBe(parts[0]);
    expect(findVideoPart(parts, 10)).toBeNull();
    expect(findVideoPart(parts, 12)).toBe(parts[1]);
    expect(findVideoPart(parts, 22)).toBeNull();
  });

  it("rejects missing parts and duplicate uploaded identities", () => {
    const catalog = {
      schemaVersion: 1 as const,
      streamStartUtc: "2026-04-01T00:00:00Z",
      timingStatus: "candidate" as const,
      parts,
    };
    expect(validateVideoParts(catalog)).toBe(catalog);
    expect(() => validateVideoParts({ ...catalog, parts: [parts[1]] })).toThrow();
    expect(() =>
      validateVideoParts({
        ...catalog,
        parts: [parts[0], { ...parts[1], videoId: parts[0].videoId }],
      })
    ).toThrow();
  });

  it("uses piecewise offsets/rates and retains alternate video mappings", () => {
    const mapping = {
      id: "a",
      feed: "pao",
      startUtc: "2026-04-01T00:00:00Z",
      endUtc: "2026-04-01T00:00:10Z",
      videoId: parts[0].videoId,
      videoStartSeconds: 100,
      rate: 1.01,
      recordingId: "ia-1",
      recordingStartSeconds: 5,
    };
    const sync: VideoSync = {
      schemaVersion: 1,
      mappings: [
        mapping,
        { ...mapping, id: "b", videoId: parts[1].videoId, videoStartSeconds: 200 },
      ],
    };
    validateVideoSync(sync);
    expect(videoPositions(sync, "2026-04-01T00:00:05Z").map((p) => p.seconds)).toEqual([
      105.05, 205.05,
    ]);
    expect(videoPositions(sync, mapping.endUtc)).toEqual([]);
    expect(videoPositions(sync, mapping.startUtc, "other-feed")).toEqual([]);
  });
});
