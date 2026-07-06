/**
 * researchTopicDialog.test.tsx — unit tests for ResearchTopicDialog (B5/D3, F10).
 *
 * Coverage:
 *   T1. Dialog renders with the seed topic before optimization completes.
 *   T2. Dialog shows the "optimizing" loading state while the API call is in-flight.
 *   T3. After optimization, topic textarea and query rows reflect the mocked response.
 *   T4. User can edit the topic textarea.
 *   T5. User can add a new query row.
 *   T6. User can remove a query row.
 *   T7. Confirm button calls onConfirm with the edited topic + queries.
 *   T8. Cancel button calls onCancel.
 *   T9. Escape key calls onCancel.
 *   T10. Confirm button is disabled while optimization is in-flight.
 *   T11. On optimize API error the dialog shows a graceful-degradation note.
 *   T12. No per-render API calls — optimize is called exactly once (I3).
 *
 * All API calls are mocked via vi.mock — no real network.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ResearchTopicDialog } from "../components/research/ResearchTopicDialog";

// ─── i18n stub ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: "en" },
  }),
}));

// ─── optimizeResearchTopic mock ───────────────────────────────────────────────

vi.mock("../api/researchClient", () => ({
  optimizeResearchTopic: vi.fn(),
}));

import { optimizeResearchTopic } from "../api/researchClient";
const mockOptimize = optimizeResearchTopic as ReturnType<typeof vi.fn>;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderDialog(
  overrides: Partial<{
    seedTopic: string;
    onConfirm: (topic: string, queries: string[]) => void;
    onCancel: () => void;
  }> = {},
) {
  const onConfirm = overrides.onConfirm ?? vi.fn();
  const onCancel = overrides.onCancel ?? vi.fn();
  const seedTopic = overrides.seedTopic ?? "Kubernetes networking";

  render(
    <ResearchTopicDialog
      seedTopic={seedTopic}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );

  return { onConfirm, onCancel };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ─── T1: renders with seed topic ─────────────────────────────────────────────

describe("ResearchTopicDialog", () => {
  it("T1: dialog element is present", async () => {
    mockOptimize.mockReturnValue(new Promise(() => {})); // never resolves

    renderDialog();

    expect(screen.getByTestId("research-topic-dialog")).toBeDefined();
  });

  // ─── T2: loading state ─────────────────────────────────────────────────────

  it("T2: shows optimizing indicator while API call is in-flight", () => {
    mockOptimize.mockReturnValue(new Promise(() => {})); // never resolves

    renderDialog();

    expect(screen.getByTestId("research-topic-dialog-optimizing")).toBeDefined();
  });

  // ─── T3: prefills from optimize response ──────────────────────────────────

  it("T3: prefills topic and query rows from the optimize response", async () => {
    mockOptimize.mockResolvedValueOnce({
      optimized_topic: "Kubernetes CNI deep dive",
      queries: ["Calico vs Cilium", "eBPF networking performance"],
    });

    renderDialog({ seedTopic: "Kubernetes networking" });

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    const topicTextarea = screen.getByTestId("research-topic-dialog-topic") as HTMLTextAreaElement;
    expect(topicTextarea.value).toBe("Kubernetes CNI deep dive");

    expect(screen.getByTestId("research-topic-dialog-query-0")).toBeDefined();
    expect(screen.getByTestId("research-topic-dialog-query-1")).toBeDefined();
    const q0 = screen.getByTestId("research-topic-dialog-query-0") as HTMLInputElement;
    expect(q0.value).toBe("Calico vs Cilium");
  });

  // ─── T4: editable topic ────────────────────────────────────────────────────

  it("T4: user can edit the topic textarea", async () => {
    mockOptimize.mockResolvedValueOnce({
      optimized_topic: "Kubernetes CNI",
      queries: [],
    });

    renderDialog();

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    const topicTextarea = screen.getByTestId("research-topic-dialog-topic") as HTMLTextAreaElement;
    fireEvent.change(topicTextarea, { target: { value: "My custom topic" } });
    expect(topicTextarea.value).toBe("My custom topic");
  });

  // ─── T5: add query ────────────────────────────────────────────────────────

  it("T5: clicking Add query appends a new query row", async () => {
    mockOptimize.mockResolvedValueOnce({ optimized_topic: "Test", queries: ["Q1"] });

    renderDialog();

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    expect(screen.queryByTestId("research-topic-dialog-query-1")).toBeNull();
    fireEvent.click(screen.getByTestId("research-topic-dialog-add-query"));
    expect(screen.getByTestId("research-topic-dialog-query-1")).toBeDefined();
  });

  // ─── T6: remove query ────────────────────────────────────────────────────

  it("T6: clicking the remove button removes that query row", async () => {
    mockOptimize.mockResolvedValueOnce({
      optimized_topic: "Test",
      queries: ["Q1", "Q2"],
    });

    renderDialog();

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    expect(screen.getByTestId("research-topic-dialog-query-0")).toBeDefined();
    expect(screen.getByTestId("research-topic-dialog-query-1")).toBeDefined();

    fireEvent.click(screen.getByTestId("research-topic-dialog-remove-query-0"));

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-query-1")).toBeNull();
    });
  });

  // ─── T7: confirm calls onConfirm with edited topic + queries ─────────────

  it("T7: confirm button calls onConfirm with edited topic and queries", async () => {
    mockOptimize.mockResolvedValueOnce({
      optimized_topic: "Kubernetes CNI",
      queries: ["Calico vs Cilium"],
    });

    const onConfirm = vi.fn();
    renderDialog({ onConfirm });

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    // Edit the topic
    fireEvent.change(screen.getByTestId("research-topic-dialog-topic"), {
      target: { value: "Edited topic" },
    });

    fireEvent.click(screen.getByTestId("research-topic-dialog-confirm"));

    expect(onConfirm).toHaveBeenCalledOnce();
    const [calledTopic, calledQueries] = onConfirm.mock.calls[0] as [string, string[]];
    expect(calledTopic).toBe("Edited topic");
    expect(calledQueries).toEqual(["Calico vs Cilium"]);
  });

  // ─── T8: cancel calls onCancel ────────────────────────────────────────────

  it("T8: cancel button calls onCancel", async () => {
    mockOptimize.mockResolvedValueOnce({ optimized_topic: "T", queries: [] });
    const onCancel = vi.fn();
    renderDialog({ onCancel });

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    fireEvent.click(screen.getByTestId("research-topic-dialog-cancel"));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  // ─── T9: Escape key cancels ────────────────────────────────────────────────

  it("T9: pressing Escape calls onCancel", async () => {
    mockOptimize.mockResolvedValueOnce({ optimized_topic: "T", queries: [] });
    const onCancel = vi.fn();
    renderDialog({ onCancel });

    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });

    expect(onCancel).toHaveBeenCalledOnce();
  });

  // ─── T10: confirm disabled while optimizing ───────────────────────────────

  it("T10: confirm button is disabled while optimization is in-flight", () => {
    mockOptimize.mockReturnValue(new Promise(() => {}));

    renderDialog();

    const confirmBtn = screen.getByTestId("research-topic-dialog-confirm") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);
  });

  // ─── T11: graceful degradation on optimize error ──────────────────────────

  it("T11: shows graceful degradation note when optimize API errors", async () => {
    mockOptimize.mockRejectedValueOnce(new Error("503 Provider not configured"));

    renderDialog({ seedTopic: "My topic" });

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    // The degradation note key should appear (i18n returns the key in tests)
    expect(
      screen.getByText("research.topicDialog.optimizeUnavailable"),
    ).toBeDefined();

    // Topic textarea should fall back to the seed topic
    const topicTextarea = screen.getByTestId("research-topic-dialog-topic") as HTMLTextAreaElement;
    expect(topicTextarea.value).toBe("My topic");
  });

  // ─── T12: optimize called exactly once (I3) ───────────────────────────────

  it("T12: optimizeResearchTopic called exactly once on mount (I3)", async () => {
    mockOptimize.mockResolvedValueOnce({ optimized_topic: "T", queries: [] });

    renderDialog({ seedTopic: "Test topic" });

    await waitFor(() => {
      expect(screen.queryByTestId("research-topic-dialog-optimizing")).toBeNull();
    });

    expect(mockOptimize).toHaveBeenCalledOnce();
    expect(mockOptimize).toHaveBeenCalledWith("Test topic", expect.any(AbortSignal));
  });
});
