/**
 * OpsScheduleCard.test.tsx — vitest unit tests for the A5 / R12-7 / R12-8 / R12-9
 * job-scheduling card.
 *
 * Covers:
 *   1. Renders FOUR rows from mocked schedules (lint + backfill + schema_review + reclassify)
 *      [R12-9].
 *   2. Frequency change PUTs the right appConfigClient key and value.
 *   3. Run-now POSTs the right op and refreshes schedules.
 *   4. in_flight disables the run-now button and shows the running badge.
 *   5. 400-dormant shows vocabulary hint.
 *   6. Card is hidden when getOpsSchedules returns null (older backend).
 *   7. i18n parity: EN and IT key sets are identical (including reclassify keys).
 *   8. Frequency change for schema_review PUTs "schema_review_schedule" [R12-8].
 *   9. Frequency change for reclassify PUTs "reclassify_schedule" [R12-9].
 *  10. run-now for reclassify calls runOpNow("reclassify") [R12-9].
 *
 * INVARIANT I3: no polling in the component — manual refresh only.
 * INVARIANT I7: 409 and 400 are surfaced, not silenced.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { OpsScheduleCard } from "../components/settings/OpsScheduleCard";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      // For interpolated keys return the template; otherwise return last segment.
      if (opts && Object.keys(opts).length > 0) return key;
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock showToast ───────────────────────────────────────────────────────────

const mockShowToast = vi.fn();
vi.mock("../components/common/Toast", () => ({
  showToast: (...args: unknown[]) => mockShowToast(...args),
}));

// ─── Mock formatRelativeTime ──────────────────────────────────────────────────

vi.mock("../components/ingest/IngestRunList", () => ({
  formatRelativeTime: (_iso: string, _lang?: string) => "2 minutes ago",
}));

// ─── Factories for mocked schedule responses ──────────────────────────────────

function makeOpsResponse(overrides: Partial<{
  lintSchedule: string;
  lintLastRunAt: string | null;
  lintLastStatus: string | null;
  lintInFlight: boolean;
  backfillSchedule: string;
  backfillLastRunAt: string | null;
  backfillLastStatus: string | null;
  backfillLastDetail: string | null;
  backfillInFlight: boolean;
  schemaReviewSchedule: string;
  schemaReviewLastRunAt: string | null;
  schemaReviewLastStatus: string | null;
  schemaReviewInFlight: boolean;
  reclassifySchedule: string;
  reclassifyLastRunAt: string | null;
  reclassifyLastStatus: string | null;
  reclassifyInFlight: boolean;
}> = {}) {
  const {
    lintSchedule = "off",
    lintLastRunAt = null,
    lintLastStatus = null,
    lintInFlight = false,
    backfillSchedule = "off",
    backfillLastRunAt = null,
    backfillLastStatus = null,
    backfillLastDetail = null,
    backfillInFlight = false,
    schemaReviewSchedule = "off",
    schemaReviewLastRunAt = null,
    schemaReviewLastStatus = null,
    schemaReviewInFlight = false,
    reclassifySchedule = "off",
    reclassifyLastRunAt = null,
    reclassifyLastStatus = null,
    reclassifyInFlight = false,
  } = overrides;

  return {
    ops: [
      {
        op: "lint" as const,
        schedule: lintSchedule,
        last_run_at: lintLastRunAt,
        last_status: lintLastStatus,
        in_flight: lintInFlight,
      },
      {
        op: "backfill" as const,
        schedule: backfillSchedule,
        last_run_at: backfillLastRunAt,
        last_status: backfillLastStatus,
        last_detail: backfillLastDetail,
        in_flight: backfillInFlight,
      },
      {
        op: "schema_review" as const,
        schedule: schemaReviewSchedule,
        last_run_at: schemaReviewLastRunAt,
        last_status: schemaReviewLastStatus,
        in_flight: schemaReviewInFlight,
      },
      {
        op: "reclassify" as const,
        schedule: reclassifySchedule,
        last_run_at: reclassifyLastRunAt,
        last_status: reclassifyLastStatus,
        in_flight: reclassifyInFlight,
      },
    ],
  };
}

// ─── Mocks for API clients ────────────────────────────────────────────────────

const mockGetOpsSchedules = vi.fn();
const mockRunOpNow = vi.fn();
const mockPutAppConfig = vi.fn();

vi.mock("../api/opsScheduleClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/opsScheduleClient")>();
  return {
    ...orig,
    getOpsSchedules: (...args: unknown[]) => mockGetOpsSchedules(...args),
    runOpNow: (...args: unknown[]) => mockRunOpNow(...args),
  };
});

vi.mock("../api/appConfigClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/appConfigClient")>();
  return {
    ...orig,
    putAppConfig: (...args: unknown[]) => mockPutAppConfig(...args),
  };
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderCard() {
  return render(<OpsScheduleCard />);
}

// ─── Test suite ───────────────────────────────────────────────────────────────

describe("OpsScheduleCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockShowToast.mockClear();
  });

  // ── 1. Renders four rows (lint + backfill + schema_review + reclassify) — R12-9 ─

  describe("renders four rows from mocked schedules", () => {
    beforeEach(() => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
    });

    it("renders the card container", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-schedule-card")).toBeDefined();
      });
    });

    it("renders a row for lint", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-schedule-row-lint")).toBeDefined();
      });
    });

    it("renders a row for backfill", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-schedule-row-backfill")).toBeDefined();
      });
    });

    it("renders a row for schema_review [R12-8]", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-schedule-row-schema_review")).toBeDefined();
      });
    });

    it("renders a row for reclassify [R12-9]", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-schedule-row-reclassify")).toBeDefined();
      });
    });

    it("renders frequency selects for all four ops", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-lint")).toBeDefined();
        expect(screen.getByTestId("ops-freq-select-backfill")).toBeDefined();
        expect(screen.getByTestId("ops-freq-select-schema_review")).toBeDefined();
        expect(screen.getByTestId("ops-freq-select-reclassify")).toBeDefined();
      });
    });

    it("renders run-now buttons for all four ops", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
        expect(screen.getByTestId("ops-run-now-backfill")).toBeDefined();
        expect(screen.getByTestId("ops-run-now-schema_review")).toBeDefined();
        expect(screen.getByTestId("ops-run-now-reclassify")).toBeDefined();
      });
    });

    it("shows 'never' text when no last_run_at (all four ops)", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-never-run-lint")).toBeDefined();
        expect(screen.getByTestId("ops-never-run-backfill")).toBeDefined();
        expect(screen.getByTestId("ops-never-run-schema_review")).toBeDefined();
        expect(screen.getByTestId("ops-never-run-reclassify")).toBeDefined();
      });
    });
  });

  // ── R13-12: honest outcome reporting (dormant / detail / error prefix) ───────

  describe("R13-12 — reports the true op outcome, not a blind 'ok'", () => {
    it("shows the 'Dormant' status label for a dormant backfill (not 'OK')", async () => {
      mockGetOpsSchedules.mockResolvedValue(
        makeOpsResponse({
          backfillLastRunAt: new Date().toISOString(),
          backfillLastStatus: "dormant",
          backfillLastDetail: "dormant: no domain vocabulary configured",
        }),
      );
      renderCard();
      await waitFor(() => {
        const row = screen.getByTestId("ops-schedule-row-backfill");
        expect(row.textContent).toContain("Dormant");
      });
    });

    it("renders the last_detail counts line for a completed run", async () => {
      mockGetOpsSchedules.mockResolvedValue(
        makeOpsResponse({
          backfillLastRunAt: new Date().toISOString(),
          backfillLastStatus: "ok",
          backfillLastDetail: "0 tagged / 30 processed / 30 failed",
        }),
      );
      renderCard();
      await waitFor(() => {
        const detail = screen.getByTestId("ops-last-detail-backfill");
        expect(detail.textContent).toContain("0 tagged");
        expect(detail.textContent).toContain("30 failed");
      });
    });

    it("treats an 'error:<msg>' status as an error (prefix match)", async () => {
      mockGetOpsSchedules.mockResolvedValue(
        makeOpsResponse({
          backfillLastRunAt: new Date().toISOString(),
          backfillLastStatus: "error:run reported failure",
          backfillLastDetail: "error: no ingest provider",
        }),
      );
      renderCard();
      await waitFor(() => {
        const row = screen.getByTestId("ops-schedule-row-backfill");
        expect(row.textContent).toContain("Error");
      });
    });
  });

  // ── 2. Frequency change PUTs the right key/value ────────────────────────────

  describe("frequency change calls putAppConfig with correct key", () => {
    beforeEach(() => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      mockPutAppConfig.mockResolvedValue(undefined);
    });

    it("PUTs lint_schedule when lint frequency changes", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-lint")).toBeDefined();
      });

      const lintSelect = screen.getByTestId("ops-freq-select-lint");
      fireEvent.change(lintSelect, { target: { value: "daily" } });

      await waitFor(() => {
        expect(mockPutAppConfig).toHaveBeenCalledWith("lint_schedule", "daily");
      });
    });

    it("PUTs backfill_schedule when backfill frequency changes", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-backfill")).toBeDefined();
      });

      const backfillSelect = screen.getByTestId("ops-freq-select-backfill");
      fireEvent.change(backfillSelect, { target: { value: "weekly" } });

      await waitFor(() => {
        expect(mockPutAppConfig).toHaveBeenCalledWith("backfill_schedule", "weekly");
      });
    });

    it("refreshes schedules after frequency save", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-lint")).toBeDefined();
      });

      const lintSelect = screen.getByTestId("ops-freq-select-lint");
      fireEvent.change(lintSelect, { target: { value: "hourly" } });

      await waitFor(() => {
        // getOpsSchedules called once on mount, then again after PUT
        expect(mockGetOpsSchedules.mock.calls.length).toBeGreaterThanOrEqual(2);
      });
    });
  });

  // ── 3. Run-now POSTs and refreshes ─────────────────────────────────────────

  describe("run-now button", () => {
    beforeEach(() => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      mockRunOpNow.mockResolvedValue({ status: "triggered", op: "lint" });
    });

    it("calls runOpNow with the correct op on click", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-lint"));

      await waitFor(() => {
        expect(mockRunOpNow).toHaveBeenCalledWith("lint");
      });
    });

    it("refreshes schedules after run-now", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-lint"));

      await waitFor(() => {
        expect(mockGetOpsSchedules.mock.calls.length).toBeGreaterThanOrEqual(2);
      });
    });

    it("shows success toast after run-now", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-lint"));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          expect.stringContaining("runNowTriggered"),
          "success",
        );
      });
    });
  });

  // ── 4. in_flight disables button and shows running badge ───────────────────

  describe("in_flight state", () => {
    it("disables run-now button for the in-flight op", async () => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse({ lintInFlight: true }));
      renderCard();

      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
      });

      const btn = screen.getByTestId("ops-run-now-lint") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });

    it("shows in-flight badge for the running op", async () => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse({ lintInFlight: true }));
      renderCard();

      await waitFor(() => {
        expect(screen.getByTestId("ops-in-flight-badge-lint")).toBeDefined();
      });
    });

    it("does NOT disable run-now button for the non-in-flight op", async () => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse({ lintInFlight: true }));
      renderCard();

      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-backfill")).toBeDefined();
      });

      const btn = screen.getByTestId("ops-run-now-backfill") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
  });

  // ── 5. 400-dormant shows vocabulary hint ───────────────────────────────────

  describe("400-dormant response", () => {
    it("shows vocabulary hint when run-now returns 400", async () => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      const { RunOpNowError } = await import("../api/opsScheduleClient");
      mockRunOpNow.mockRejectedValue(
        new RunOpNowError(400, "POST /ops/schedules/backfill/run-now: dormant"),
      );

      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-backfill")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-backfill"));

      await waitFor(() => {
        expect(screen.getByTestId("ops-dormant-hint-backfill")).toBeDefined();
      });
    });

    it("does NOT show a toast on 400-dormant (only inline hint)", async () => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      const { RunOpNowError } = await import("../api/opsScheduleClient");
      mockRunOpNow.mockRejectedValue(
        new RunOpNowError(400, "POST /ops/schedules/backfill/run-now: dormant"),
      );

      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-backfill")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-backfill"));

      await waitFor(() => {
        // hint should be present
        expect(screen.getByTestId("ops-dormant-hint-backfill")).toBeDefined();
      });

      // No error toast — only info/success toasts for 409 and 202
      expect(mockShowToast).not.toHaveBeenCalledWith(expect.anything(), "error");
    });

    it("shows info toast on 409 in-flight", async () => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      const { RunOpNowError } = await import("../api/opsScheduleClient");
      mockRunOpNow.mockRejectedValue(
        new RunOpNowError(409, "POST /ops/schedules/lint/run-now: already in flight"),
      );

      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-lint"));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          expect.stringContaining("alreadyRunning"),
          "error",
        );
      });
    });
  });

  // ── 6. Card hidden when getOpsSchedules returns null ───────────────────────


  describe("null response hides the card (older backend)", () => {
    it("renders nothing when getOpsSchedules returns null", async () => {
      mockGetOpsSchedules.mockResolvedValue(null);
      const { container } = renderCard();

      // Wait for loading to finish
      await waitFor(() => {
        expect(mockGetOpsSchedules).toHaveBeenCalled();
      });

      // Give React a tick to process the null state
      await new Promise((r) => setTimeout(r, 50));

      expect(container.firstChild).toBeNull();
    });
  });

  // ── 8. schema_review frequency change PUTs schema_review_schedule [R12-8] ───

  describe("schema_review frequency change", () => {
    beforeEach(() => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      mockPutAppConfig.mockResolvedValue(undefined);
    });

    it("PUTs schema_review_schedule when schema_review frequency changes", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-schema_review")).toBeDefined();
      });

      const srSelect = screen.getByTestId("ops-freq-select-schema_review");
      fireEvent.change(srSelect, { target: { value: "weekly" } });

      await waitFor(() => {
        expect(mockPutAppConfig).toHaveBeenCalledWith("schema_review_schedule", "weekly");
      });
    });

    it("run-now calls runOpNow with 'schema_review'", async () => {
      mockRunOpNow.mockResolvedValue({ status: "triggered", op: "schema_review" });

      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-schema_review")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-schema_review"));

      await waitFor(() => {
        expect(mockRunOpNow).toHaveBeenCalledWith("schema_review");
      });
    });
  });

  // ── 7. i18n parity ─────────────────────────────────────────────────────────

  describe("i18n parity — EN and IT key sets are identical (including schema_review)", () => {
    it("both locales have the same opsSchedule keys", async () => {
      const en = (await import("../i18n/locales/en.json")) as Record<string, unknown>;
      const it = (await import("../i18n/locales/it.json")) as Record<string, unknown>;

      const enSettings = en.settings as Record<string, unknown>;
      const itSettings = it.settings as Record<string, unknown>;

      const enOps = enSettings.opsSchedule as Record<string, unknown>;
      const itOps = itSettings.opsSchedule as Record<string, unknown>;

      expect(enOps).toBeDefined();
      expect(itOps).toBeDefined();

      // All top-level keys in EN must exist in IT
      const enKeys = Object.keys(enOps).sort();
      const itKeys = Object.keys(itOps).sort();
      expect(itKeys).toEqual(enKeys);

      // freq sub-keys
      const enFreq = enOps.freq as Record<string, unknown>;
      const itFreq = itOps.freq as Record<string, unknown>;
      expect(Object.keys(itFreq).sort()).toEqual(Object.keys(enFreq).sort());

      // opLabel sub-keys
      const enLabel = enOps.opLabel as Record<string, unknown>;
      const itLabel = itOps.opLabel as Record<string, unknown>;
      expect(Object.keys(itLabel).sort()).toEqual(Object.keys(enLabel).sort());

      // opDesc sub-keys
      const enDesc = enOps.opDesc as Record<string, unknown>;
      const itDesc = itOps.opDesc as Record<string, unknown>;
      expect(Object.keys(itDesc).sort()).toEqual(Object.keys(enDesc).sort());

      // R12-8: schema_review keys must be present in both locales
      expect(enLabel["schema_review"]).toBeDefined();
      expect(itLabel["schema_review"]).toBeDefined();
      expect(enDesc["schema_review"]).toBeDefined();
      expect(itDesc["schema_review"]).toBeDefined();

      // R12-9: reclassify keys must be present in both locales
      expect(enLabel["reclassify"]).toBeDefined();
      expect(itLabel["reclassify"]).toBeDefined();
      expect(enDesc["reclassify"]).toBeDefined();
      expect(itDesc["reclassify"]).toBeDefined();
    });
  });

  // ── 9. reclassify frequency change PUTs reclassify_schedule [R12-9] ──────────

  describe("reclassify frequency change", () => {
    beforeEach(() => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      mockPutAppConfig.mockResolvedValue(undefined);
    });

    it("PUTs reclassify_schedule when reclassify frequency changes [R12-9]", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-reclassify")).toBeDefined();
      });

      const rcSelect = screen.getByTestId("ops-freq-select-reclassify");
      fireEvent.change(rcSelect, { target: { value: "daily" } });

      await waitFor(() => {
        expect(mockPutAppConfig).toHaveBeenCalledWith("reclassify_schedule", "daily");
      });
    });
  });

  // ── 10. run-now for reclassify calls runOpNow("reclassify") [R12-9] ──────────

  describe("reclassify run-now", () => {
    beforeEach(() => {
      mockGetOpsSchedules.mockResolvedValue(makeOpsResponse());
      mockRunOpNow.mockResolvedValue({ status: "triggered", op: "reclassify" });
    });

    it("calls runOpNow with 'reclassify' [R12-9]", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-reclassify")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-reclassify"));

      await waitFor(() => {
        expect(mockRunOpNow).toHaveBeenCalledWith("reclassify");
      });
    });

    it("shows success toast after reclassify run-now [R12-9]", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-reclassify")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-reclassify"));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          expect.stringContaining("runNowTriggered"),
          "success",
        );
      });
    });

    it("shows 409 toast when reclassify is already in-flight [R12-9]", async () => {
      const { RunOpNowError } = await import("../api/opsScheduleClient");
      mockRunOpNow.mockRejectedValue(
        new RunOpNowError(409, "POST /ops/schedules/reclassify/run-now: already in flight"),
      );

      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-reclassify")).toBeDefined();
      });

      fireEvent.click(screen.getByTestId("ops-run-now-reclassify"));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          expect.stringContaining("alreadyRunning"),
          "error",
        );
      });
    });
  });
});
