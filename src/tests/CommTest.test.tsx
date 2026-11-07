import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadEnrichedTranscripts } from "../data/comm.ts";
import { VirtuosoMockContext } from "react-virtuoso";
import CommTest from "../pages/CommTest.tsx";
import { loadVideoSync } from "../data/videoSync.ts";

vi.mock("../data/comm.ts", () => ({ loadEnrichedTranscripts: vi.fn() }));
vi.mock("../data/videoSync.ts", async (original) => ({
  ...(await original<typeof import("../data/videoSync.ts")>()),
  loadVideoSync: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  vi.mocked(loadVideoSync).mockResolvedValue({ schemaVersion: 1, mappings: [] });
  vi.mocked(loadEnrichedTranscripts).mockResolvedValue({
    comm: [
      {
        t: "2026-04-01T18:00:00Z",
        s: 3,
        d: 2,
        src: "loops/flight_oe1.m4a",
        text: "Science observation.",
        speaker: "Koch",
        category: "science",
      },
      {
        t: "2026-04-01T18:01:00Z",
        d: 2,
        src: "loops/flight_oe2.m4a",
        text: "Systems check.",
        category: "systems",
      },
      { t: "2026-04-01T18:02:00Z", d: 2, text: "Awaiting assessment." },
    ],
    pao: [{ t: "2026-04-01T18:03:00Z", text: "Science commentary.", category: "science" }],
  });
});

function renderPage() {
  return render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 600, itemHeight: 48 }}>
      <CommTest />
    </VirtuosoMockContext.Provider>
  );
}

describe("comm test category filters", () => {
  it("uses the pinned release for reviewed PAO audio and the selected uploaded video", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.mocked(loadEnrichedTranscripts).mockResolvedValue({
      assetBase: "/assets/releases/baseline",
      comm: [],
      pao: [
        {
          t: "2026-04-01T18:03:00Z",
          text: "Reviewed commentary.",
          playback: {
            source: "ia-pao",
            feed: "pao",
            recordingId: "ia-recording",
            audio: { src: "comm/pao/recording.m4a", s: 12.5, d: 8 },
            video: { id: "aaaaaaaaaaa", s: 102.5, d: 8, mappingId: "verified-map" },
          },
        },
      ],
    });
    const { container } = renderPage();
    const row = await screen.findByRole("button", { name: /Reviewed commentary/ });
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-pressed", "true");
    const audio = container.querySelector("audio");
    expect(audio).toHaveAttribute("src", "/assets/releases/baseline/comm/pao/recording.m4a");
    fireEvent.loadedMetadata(audio);
    expect(audio.currentTime).toBe(12.5);
    expect(screen.getByRole("link", { name: "Watch video" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=aaaaaaaaaaa&t=102s"
    );
    audio.currentTime = 20.5;
    fireEvent.timeUpdate(audio);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    fireEvent.click(row);
    expect(container.querySelector("audio")).toBeNull();
    expect(row).toHaveAttribute("aria-pressed", "false");
  });

  it("shares category selections across COMM and PAO, with All, None, and Only", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Categories: All" }));
    const categories = screen.getByRole("group", { name: "Categories" });
    const science = within(categories).getByRole("checkbox", { name: "Science, 2 entries" });
    await screen.findByText("Science observation.");
    fireEvent.click(science);
    expect(science).not.toBeChecked();
    await waitFor(() => expect(screen.queryByText("Science observation.")).not.toBeInTheDocument());
    expect(screen.queryByText("Science commentary.")).not.toBeInTheDocument();
    expect(screen.getByText("2 shown")).toBeInTheDocument();

    fireEvent.click(within(categories).getByRole("button", { name: "All" }));
    expect(await screen.findByText("Science observation.")).toBeInTheDocument();
    expect(screen.getByText("Science commentary.")).toBeInTheDocument();
    fireEvent.click(within(categories).getByRole("button", { name: "Only Science" }));
    expect(screen.getByText("2 shown")).toBeInTheDocument();
    expect(screen.queryByText("Systems check.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Categories: Science" })).toBeInTheDocument();
    fireEvent.click(within(categories).getByRole("button", { name: "None" }));
    expect(await screen.findByText("No transmissions in this view")).toBeInTheDocument();
    fireEvent.click(within(categories).getByRole("button", { name: "Done" }));
    fireEvent.click(
      within(screen.getByText("No transmissions in this view").parentElement).getByRole("button", {
        name: "Reset filters",
      })
    );
    expect(await screen.findByText("4 shown")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Categories: All" })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  it("keeps unassessed transcripts visible and allows filtering them", async () => {
    renderPage();
    await screen.findByText("Awaiting assessment.");
    fireEvent.click(screen.getByRole("button", { name: "Categories: All" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Uncategorized, 1 entries" }));
    await waitFor(() => expect(screen.queryByText("Awaiting assessment.")).not.toBeInTheDocument());
    expect(screen.getByText("3 shown")).toBeInTheDocument();
  });

  it("dismisses pickers with Escape and outside clicks and restores keyboard focus", async () => {
    renderPage();
    const trigger = await screen.findByRole("button", { name: "Categories: All" });
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("exposes unknown channels and resets channel selections", async () => {
    renderPage();
    const unknown = await screen.findByRole("switch", { name: "Unknown channel" });
    fireEvent.click(unknown);
    expect(screen.queryByText("Awaiting assessment.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "OE-2" }));
    expect(screen.queryByText("Systems check.")).not.toBeInTheDocument();
    expect(screen.getByText("2 shown")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(unknown).toBeChecked();
    expect(await screen.findByText("4 shown")).toBeInTheDocument();
  });

  it("filters PAO broadcast types independently of subjects and COMM", async () => {
    vi.mocked(loadEnrichedTranscripts).mockResolvedValue({
      comm: [{ t: "2026-04-01T18:00:00Z", d: 1, text: "Technical reply.", category: "systems" }],
      pao: [
        {
          t: "2026-04-01T18:01:00Z",
          text: "Coverage introduction.",
          category: "mission_background",
          pao_type: "filler",
        },
        {
          t: "2026-04-01T18:02:00Z",
          text: "Mission explanation.",
          category: "mission_background",
          pao_type: "commentary",
        },
        {
          t: "2026-04-01T18:03:00Z",
          text: "Systems explanation.",
          category: "systems",
          pao_type: "commentary",
        },
      ],
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "PAO types: All" }));
    const types = screen.getByRole("group", { name: "PAO types" });
    fireEvent.click(within(types).getByRole("checkbox", { name: "PAO filler, 1 entries" }));
    await waitFor(() =>
      expect(screen.queryByText("Coverage introduction.")).not.toBeInTheDocument()
    );
    expect(screen.getByText("Technical reply.")).toBeInTheDocument();
    expect(screen.getByText("Mission explanation.")).toBeInTheDocument();
    fireEvent.click(within(types).getByRole("button", { name: "None" }));
    expect(screen.getByText("1 shown")).toBeInTheDocument();
    expect(screen.getByText("Technical reply.")).toBeInTheDocument();
    fireEvent.click(within(types).getByRole("button", { name: "Only PAO commentary" }));
    fireEvent.click(within(types).getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("button", { name: "Categories: All" }));
    const subjects = screen.getByRole("group", { name: "Categories" });
    fireEvent.click(
      within(subjects).getByRole("checkbox", { name: "Mission background, 2 entries" })
    );
    expect(screen.queryByText("Mission explanation.")).not.toBeInTheDocument();
    expect(screen.getByText("Systems explanation.")).toBeInTheDocument();
    fireEvent.click(within(subjects).getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("switch", { name: "PAO" }));
    expect(screen.getByRole("button", { name: "PAO types: PAO off" })).toBeDisabled();
    expect(screen.queryByText("Systems explanation.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "PAO" }));
    expect(screen.getByRole("button", { name: "PAO types: PAO commentary" })).toBeEnabled();
    expect(await screen.findByText("Systems explanation.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(await screen.findByText("Coverage introduction.")).toBeInTheDocument();
    expect(screen.getByText("4 shown")).toBeInTheDocument();
  });

  it("renders a bounded set of DOM rows for 50,000 transcripts and keeps date navigation", async () => {
    vi.mocked(loadEnrichedTranscripts).mockResolvedValue({
      comm: Array.from({ length: 50_000 }, (_, index) => ({
        t: new Date(Date.UTC(2026, 3, 1) + index * 1000).toISOString(),
        s: index,
        d: 1,
        src: "loops/flight_oe1.m4a",
        text: `Transmission ${index}.`,
        category: "science",
      })),
      pao: [],
    });
    const { container } = renderPage();
    expect(await screen.findByText("50,000 shown")).toBeInTheDocument();
    expect(await screen.findByText("Transmission 0.")).toBeInTheDocument();
    expect(screen.queryByText("Transmission 49999.")).not.toBeInTheDocument();
    expect(container.querySelectorAll("[data-item-index]").length).toBeLessThan(100);
    expect(container.querySelectorAll("*").length).toBeLessThan(1_000);
    expect(screen.getByRole("combobox", { name: "Jump to date" })).toHaveTextContent(
      "April 1, 2026 (50,000)"
    );
  });

  it("provides UTC date offsets and sends the selected date to the virtual scroller", async () => {
    vi.spyOn(document.documentElement, "scrollHeight", "get").mockReturnValue(10_000);
    vi.spyOn(window, "scrollTo").mockImplementation((options: ScrollToOptions | number) => {
      if (options && typeof options === "object") {
        vi.stubGlobal("scrollY", options.top ?? 0);
        fireEvent.scroll(window);
      }
    });
    vi.mocked(loadEnrichedTranscripts).mockResolvedValue({
      comm: Array.from({ length: 100 }, (_, index) => ({
        t: new Date(Date.UTC(2026, 3, 1) + index * 1800_000).toISOString(),
        s: index,
        d: 1,
        src: "loops/flight_oe1.m4a",
        text: `Transmission ${index}.`,
        category: "science",
      })),
      pao: [],
    });
    renderPage();
    await screen.findByText("Transmission 0.");
    const dates = screen.getByRole("combobox", { name: "Jump to date" });
    expect(
      within(dates)
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value)
    ).toEqual(["", "0", "48", "96"]);
    fireEvent.change(dates, { target: { value: "96" } });
    await waitFor(() => expect(window.scrollTo).toHaveBeenCalled());
    expect(window.scrollTo).toHaveBeenCalledWith({ behavior: "auto", top: expect.any(Number) });
    const target = vi.mocked(window.scrollTo).mock.calls[0][0] as ScrollToOptions;
    expect(target.top).toBeGreaterThan(4_000);
  });
});
