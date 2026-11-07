import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadEnrichedTranscripts } from "../data/comm.ts";
import { VirtuosoMockContext } from "react-virtuoso";
import CommTest from "../pages/CommTest.tsx";

vi.mock("../data/comm.ts", () => ({ loadEnrichedTranscripts: vi.fn() }));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
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
  it("keeps Comm and PAO category selections independent, including Show all", async () => {
    renderPage();
    const comm = await screen.findByRole("group", { name: "Comm categories" });
    const pao = screen.getByRole("group", { name: "PAO categories" });
    const commScience = within(comm).getByRole("button", { name: "Science (1)" });
    const paoScience = within(pao).getByRole("button", { name: "Science (1)" });
    await screen.findByText("Science observation.");
    expect(screen.getByText("4 shown")).toBeInTheDocument();
    fireEvent.click(commScience);
    expect(commScience).toHaveAttribute("aria-pressed", "false");
    expect(paoScience).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(screen.queryByText("Science observation.")).not.toBeInTheDocument());
    expect(screen.getByText("Science commentary.")).toBeInTheDocument();
    expect(screen.getByText("3 shown")).toBeInTheDocument();

    fireEvent.click(paoScience);
    await waitFor(() => expect(screen.queryByText("Science commentary.")).not.toBeInTheDocument());
    fireEvent.click(within(comm).getByRole("button", { name: "Show all" }));
    expect(await screen.findByText("Science observation.")).toBeInTheDocument();
    expect(screen.queryByText("Science commentary.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Toggle OE-2 off" }));
    await waitFor(() => expect(screen.queryByText("Systems check.")).not.toBeInTheDocument());
    expect(screen.getByText("Koch:")).toBeInTheDocument();
    expect(screen.getByText("2 shown")).toBeInTheDocument();
  });

  it("keeps unassessed transcripts visible and allows filtering them", async () => {
    renderPage();
    await screen.findByText("Awaiting assessment.");
    fireEvent.click(screen.getByRole("button", { name: "Uncategorized (1)" }));
    await waitFor(() => expect(screen.queryByText("Awaiting assessment.")).not.toBeInTheDocument());
    expect(screen.getByText("3 shown")).toBeInTheDocument();
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
    const types = await screen.findByRole("group", { name: "PAO types" });
    const subjects = screen.getByRole("group", { name: "PAO categories" });
    expect(within(types).getByRole("button", { name: "PAO filler (1)" })).toBeInTheDocument();
    fireEvent.click(within(types).getByRole("button", { name: "PAO filler (1)" }));
    await waitFor(() =>
      expect(screen.queryByText("Coverage introduction.")).not.toBeInTheDocument()
    );
    expect(screen.getByText("Technical reply.")).toBeInTheDocument();
    expect(screen.getByText("Mission explanation.")).toBeInTheDocument();
    fireEvent.click(within(subjects).getByRole("button", { name: "Mission background (2)" }));
    await waitFor(() => expect(screen.queryByText("Mission explanation.")).not.toBeInTheDocument());
    expect(screen.getByText("Systems explanation.")).toBeInTheDocument();
    fireEvent.click(within(types).getByRole("button", { name: "Show all" }));
    expect(screen.queryByText("Coverage introduction.")).not.toBeInTheDocument();
    fireEvent.click(within(subjects).getByRole("button", { name: "Show all" }));
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
