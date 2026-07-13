/**
 * ErrorState.test.tsx — unit tests for the reusable ErrorState component (audit #1b).
 *
 * Covers:
 *   A. Renders a friendly title (custom or default via i18n fallback).
 *   B. Shows a Retry button only when onRetry is provided; calls it on click.
 *   C. Technical details section is present and contains the raw error text.
 *   D. No Retry button rendered when onRetry is omitted.
 *   E. No details section rendered when error prop is omitted.
 *   F. Copy button is present inside the details section.
 *
 * i18n: mocked with minimal key map; uses the same pattern as ChatEmptyState.test.tsx.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorState } from "../components/common/ErrorState";

// ─── Mocks ────────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "errors.genericTitle": "Something went wrong",
        "errors.technicalDetails": "Technical details",
        "errors.copyDetails": "Copy details",
        "common.retry": "Retry",
      };
      return map[key] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

// ─── A. Title rendering ───────────────────────────────────────────────────────

describe("ErrorState — title rendering", () => {
  it("renders a custom title when title prop is provided", () => {
    render(<ErrorState title="Couldn't load projects" />);
    expect(screen.getByTestId("error-state-title").textContent).toBe(
      "Couldn't load projects",
    );
  });

  it("renders the default i18n title when title prop is omitted", () => {
    render(<ErrorState />);
    expect(screen.getByTestId("error-state-title").textContent).toBe(
      "Something went wrong",
    );
  });

  it("renders the error-state container with role='alert'", () => {
    render(<ErrorState title="Oops" />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});

// ─── B. Retry button ─────────────────────────────────────────────────────────

describe("ErrorState — Retry button", () => {
  it("renders a Retry button when onRetry is provided", () => {
    render(<ErrorState onRetry={vi.fn()} />);
    expect(screen.getByTestId("error-state-retry")).toBeTruthy();
  });

  it("calls onRetry when the Retry button is clicked", () => {
    const onRetry = vi.fn();
    render(<ErrorState onRetry={onRetry} />);
    fireEvent.click(screen.getByTestId("error-state-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("Retry button label matches common.retry i18n key", () => {
    render(<ErrorState onRetry={vi.fn()} />);
    expect(screen.getByTestId("error-state-retry").textContent).toContain("Retry");
  });
});

// ─── C. Technical details ─────────────────────────────────────────────────────

describe("ErrorState — technical details section", () => {
  it("renders a details element when error prop is provided", () => {
    render(<ErrorState error={new Error("boom")} />);
    expect(screen.getByTestId("error-state-details")).toBeTruthy();
  });

  it("shows the raw error message in the detail text block", () => {
    const err = new Error("Unexpected token '<'");
    render(<ErrorState error={err} />);
    const pre = screen.getByTestId("error-state-detail-text");
    expect(pre.textContent).toContain("Unexpected token '<'");
  });

  it("shows the error stack when available", () => {
    const err = new Error("stack test");
    // Error.stack always contains the message too
    render(<ErrorState error={err} />);
    const pre = screen.getByTestId("error-state-detail-text");
    expect(pre.textContent).toContain("stack test");
  });

  it("shows a plain string error as-is", () => {
    render(<ErrorState error="500 Internal Server Error" />);
    expect(screen.getByTestId("error-state-detail-text").textContent).toContain(
      "500 Internal Server Error",
    );
  });

  it("details summary label uses errors.technicalDetails key", () => {
    render(<ErrorState error="any" />);
    const summary = screen.getByTestId("error-state-details").querySelector("summary");
    expect(summary?.textContent).toBe("Technical details");
  });
});

// ─── D. No Retry button when onRetry omitted ─────────────────────────────────

describe("ErrorState — no Retry button when onRetry omitted", () => {
  it("does NOT render a Retry button when onRetry is not provided", () => {
    render(<ErrorState title="Oops" error="raw error" />);
    expect(screen.queryByTestId("error-state-retry")).toBeNull();
  });
});

// ─── E. No details section when error omitted ────────────────────────────────

describe("ErrorState — no details section when error omitted", () => {
  it("does NOT render a details block when error prop is omitted", () => {
    render(<ErrorState title="Oops" onRetry={vi.fn()} />);
    expect(screen.queryByTestId("error-state-details")).toBeNull();
  });

  it("does NOT render a details block when error prop is null", () => {
    // error?: unknown — null means "no error available"
    render(<ErrorState title="Oops" error={null} />);
    expect(screen.queryByTestId("error-state-details")).toBeNull();
  });
});

// ─── F. Copy button inside details ───────────────────────────────────────────

describe("ErrorState — copy button", () => {
  it("renders a copy button inside the details block", () => {
    render(<ErrorState error="raw text" />);
    expect(screen.getByTestId("error-state-copy")).toBeTruthy();
  });

  it("copy button label uses errors.copyDetails key", () => {
    render(<ErrorState error="raw text" />);
    expect(screen.getByTestId("error-state-copy").textContent).toContain("Copy details");
  });
});
