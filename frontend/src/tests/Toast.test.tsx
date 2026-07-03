/**
 * Toast.test.tsx — unit tests for Toast.tsx (UXA-16 role fix).
 *
 * Covers:
 *   UXA-16-1: error toast uses role="alert"
 *   UXA-16-2: success toast uses role="status"
 *
 * PROJECT GOTCHA: vi.clearAllMocks() wipes implementations — re-set in beforeEach.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ToastHost, showToast } from "../components/common/Toast";

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// ─── UXA-16: role attribute on ToastItem ─────────────────────────────────────

describe("Toast — UXA-16: ARIA role per variant", () => {
  it("UXA-16-1: error toast renders with role='alert'", () => {
    render(<ToastHost />);
    act(() => {
      showToast("Something failed", "error");
    });
    const alerts = screen.getAllByRole("alert");
    expect(alerts.length).toBeGreaterThanOrEqual(1);
    expect(alerts[0]!.textContent).toContain("Something failed");
  });

  it("UXA-16-2: success toast renders with role='status'", () => {
    render(<ToastHost />);
    act(() => {
      showToast("Saved successfully", "success");
    });
    // role="status" elements
    const statuses = screen.getAllByRole("status");
    expect(statuses.length).toBeGreaterThanOrEqual(1);
    expect(statuses[0]!.textContent).toContain("Saved successfully");
  });

  it("UXA-16-3: mixed toasts get independent roles", () => {
    render(<ToastHost />);
    act(() => {
      showToast("OK", "success");
      showToast("Error!", "error");
    });
    expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("status").length).toBeGreaterThanOrEqual(1);
  });

  it("UXA-16-4: toast auto-dismisses after 4 seconds", () => {
    render(<ToastHost />);
    act(() => {
      showToast("Temporary", "success");
    });
    expect(screen.getAllByRole("status").length).toBeGreaterThanOrEqual(1);
    act(() => {
      vi.advanceTimersByTime(4001);
    });
    expect(screen.queryAllByRole("status")).toHaveLength(0);
  });
});
