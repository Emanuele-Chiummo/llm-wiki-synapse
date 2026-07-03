/**
 * SectionErrorBoundary.test.tsx — the boundary contains section crashes
 * (regression guard: a GraphViewer mount error used to white-screen the app).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SectionErrorBoundary } from "../components/common/SectionErrorBoundary";

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error("boom di prova");
  return <div data-testid="safe-content">ok</div>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SectionErrorBoundary", () => {
  it("renders children when no error", () => {
    render(
      <SectionErrorBoundary sectionId="chat">
        <Bomb explode={false} />
      </SectionErrorBoundary>,
    );
    expect(screen.getByTestId("safe-content")).toBeTruthy();
  });

  it("catches a child render error and shows the fallback with the message", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <SectionErrorBoundary sectionId="graph">
        <Bomb explode />
      </SectionErrorBoundary>,
    );
    const fallback = screen.getByTestId("section-error-boundary");
    expect(fallback).toBeTruthy();
    expect(fallback.textContent).toContain("boom di prova");
  });

  it("retry button clears the error state", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    let explode = true;
    function Wrapper() {
      return (
        <SectionErrorBoundary sectionId="graph">
          <Bomb explode={explode} />
        </SectionErrorBoundary>
      );
    }
    const { rerender } = render(<Wrapper />);
    expect(screen.getByTestId("section-error-boundary")).toBeTruthy();
    // Update the child so it no longer throws, THEN retry: the boundary
    // re-renders the (now safe) children.
    explode = false;
    rerender(<Wrapper />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("safe-content")).toBeTruthy();
  });
});
