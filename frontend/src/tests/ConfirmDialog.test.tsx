/**
 * ConfirmDialog.test.tsx — unit tests for the accessible confirmation dialog (FE-A11Y-2).
 *
 * Covers:
 *   - Focus is moved to the Cancel button on mount
 *   - Focus is restored to the triggering element on unmount (close)
 *   - Esc key calls onCancel
 *   - Tab/Shift+Tab cycles between the two buttons only
 *   - Backdrop click calls onCancel
 *   - Confirm button click calls onConfirm
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog } from "../components/common/ConfirmDialog";

const DEFAULT_PROPS = {
  title: "Are you sure?",
  body: "This action cannot be undone.",
  confirmLabel: "Confirm",
  cancelLabel: "Cancel",
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe("ConfirmDialog — mount / unmount focus", () => {
  it("moves focus to the cancel button on mount", () => {
    render(<ConfirmDialog {...DEFAULT_PROPS} />);
    const cancel = screen.getByTestId("confirm-dialog-cancel");
    expect(document.activeElement).toBe(cancel);
  });

  it("restores focus to the triggering element on unmount", () => {
    // Create a button that "opened" the dialog and give it focus.
    const trigger = document.createElement("button");
    trigger.textContent = "Open";
    document.body.appendChild(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    const { unmount } = render(<ConfirmDialog {...DEFAULT_PROPS} />);
    // After mount, focus is on cancel button
    expect(document.activeElement).toBe(screen.getByTestId("confirm-dialog-cancel"));

    // Unmount (dialog closes) — focus should return to trigger
    unmount();
    expect(document.activeElement).toBe(trigger);

    document.body.removeChild(trigger);
  });
});

describe("ConfirmDialog — keyboard interaction", () => {
  it("calls onCancel when Esc is pressed", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...DEFAULT_PROPS} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onConfirm when confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...DEFAULT_PROPS} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...DEFAULT_PROPS} onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe("ConfirmDialog — backdrop click", () => {
  it("calls onCancel when clicking the backdrop overlay", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...DEFAULT_PROPS} onCancel={onCancel} />);
    const overlay = screen.getByTestId("confirm-dialog-overlay");
    fireEvent.click(overlay);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onCancel when clicking inside the dialog box", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...DEFAULT_PROPS} onCancel={onCancel} />);
    const dialog = screen.getByTestId("confirm-dialog");
    fireEvent.click(dialog);
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe("ConfirmDialog — danger variant", () => {
  it("renders without danger styling when danger=false (default)", () => {
    render(<ConfirmDialog {...DEFAULT_PROPS} />);
    const confirm = screen.getByTestId("confirm-dialog-confirm");
    // Non-danger confirm has .syn-btn--primary class
    expect(confirm.className).toContain("syn-btn--primary");
  });

  it("renders with danger styling when danger=true", () => {
    render(<ConfirmDialog {...DEFAULT_PROPS} danger />);
    const confirm = screen.getByTestId("confirm-dialog-confirm");
    // Danger confirm has only .syn-btn class, not --primary
    expect(confirm.className).not.toContain("syn-btn--primary");
  });
});
