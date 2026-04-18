import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import ComingSoon from "../pages/ComingSoon.tsx";
import { MemoryRouter } from "react-router-dom";

describe("ComingSoon", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00-05:00"));
  });

  it("renders the coming soon text", () => {
    render(
      <MemoryRouter>
        <ComingSoon />
      </MemoryRouter>
    );
    expect(screen.getByText("Coming Soon")).toBeInTheDocument();
  });

  it("renders countdown segments", () => {
    render(
      <MemoryRouter>
        <ComingSoon />
      </MemoryRouter>
    );
    expect(screen.getByText("Days")).toBeInTheDocument();
    expect(screen.getByText("Hours")).toBeInTheDocument();
    expect(screen.getByText("Minutes")).toBeInTheDocument();
    expect(screen.getByText("Seconds")).toBeInTheDocument();
  });
});
