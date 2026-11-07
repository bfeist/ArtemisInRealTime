import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadVideoParts, type VideoPartCatalog } from "../data/videoSync.ts";
import VideoTest from "../pages/VideoTest.tsx";

vi.mock("../data/videoSync.ts", async (original) => ({
  ...(await original<typeof import("../data/videoSync.ts")>()),
  loadVideoParts: vi.fn(),
}));

const catalog: VideoPartCatalog = {
  schemaVersion: 1,
  streamStartUtc: "2026-04-01T23:59:50Z",
  timingStatus: "verified",
  parts: [
    { partNum: 1, videoId: "aaaaaaaaaaa", startOffsetS: 0, durationS: 10 },
    { partNum: 2, videoId: "bbbbbbbbbbb", startOffsetS: 15, durationS: 10 },
  ],
};
const player = {
  loadVideoById: vi.fn(),
  cueVideoById: vi.fn(),
  seekTo: vi.fn(),
  getCurrentTime: vi.fn(() => 0),
  getPlayerState: vi.fn(() => 2),
  destroy: vi.fn(),
};
let ready: () => void;
let initialVideo: string;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadVideoParts).mockResolvedValue(catalog);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  window.YT = {
    Player: class {
      constructor(
        _element: HTMLElement,
        config: { videoId: string; events: { onReady: () => void } }
      ) {
        initialVideo = config.videoId;
        ready = config.events.onReady;
        return player;
      }
    } as unknown as NonNullable<typeof window.YT>["Player"],
    PlayerState: { PLAYING: 1, PAUSED: 2, ENDED: 0 },
  };
});
afterEach(() => {
  delete window.YT;
  vi.unstubAllGlobals();
});

describe("video part playback", () => {
  it("loads uploaded identities, seeks across UTC midnight and leaves gaps unmapped", async () => {
    render(<VideoTest />);
    await screen.findByRole("link", { name: "aaaaaaaaaaa" });
    expect(initialVideo).toBe("aaaaaaaaaaa");
    act(() => ready());
    const slider = screen.getByRole("slider");
    const start = Date.parse(catalog.streamStartUtc);
    fireEvent.change(slider, { target: { value: start + 16000 } });
    expect(player.loadVideoById).toHaveBeenLastCalledWith({
      videoId: "bbbbbbbbbbb",
      startSeconds: 1,
    });
    expect(screen.getByRole("link", { name: "bbbbbbbbbbb" })).toHaveAttribute(
      "href",
      "https://youtu.be/bbbbbbbbbbb"
    );
    fireEvent.change(slider, { target: { value: start + 12000 } });
    expect(screen.getByText("No mapped video")).toBeInTheDocument();
    expect(player.loadVideoById).toHaveBeenCalledTimes(1);
    expect(player.seekTo).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /End/ }));
    expect(player.seekTo.mock.calls.at(-1)?.[0]).toBeCloseTo(9.999);
  });

  it("reports catalog failures without inventing a replacement video", async () => {
    vi.mocked(loadVideoParts).mockRejectedValue(new Error("Missing uploaded part"));
    render(<VideoTest />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Missing uploaded part");
    await waitFor(() => expect(screen.queryByRole("slider")).not.toBeInTheDocument());
  });
});
