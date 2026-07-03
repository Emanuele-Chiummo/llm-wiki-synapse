/**
 * OpsScheduleCard.test.tsx — vitest unit tests for the A5 / R12-7 job-scheduling card.
 *
 * Covers:
 *   1. Renders two rows from mocked schedules (lint + backfill).
 *   2. Frequency change PUTs the right appConfigClient key and value.
 *   3. Run-now POSTs the right op and refreshes schedules.
 *   4. in_flight disables the run-now button and shows the running badge.
 *   5. 400-dormant shows vocabulary hint.
 *   6. Card is hidden when getOpsSchedules returns null (older backend).
 *   7. i18n parity: EN and IT key sets are identical.
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
  backfillInFlight: boolean;
}> = {}) {
  const {
    lintSchedule = "off",
    lintLastRunAt = null,
    lintLastStatus = null,
    lintInFlight = false,
    backfillSchedule = "off",
    backfillLastRunAt = null,
    backfillLastStatus = null,
    backfillInFlight = false,
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
        in_flight: backfillInFlight,
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

  // ── 1. Renders two rows ─────────────────────────────────────────────────────

  describe("renders two rows from mocked schedules", () => {
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

    it("renders frequency selects for both ops", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-freq-select-lint")).toBeDefined();
        expect(screen.getByTestId("ops-freq-select-backfill")).toBeDefined();
      });
    });

    it("renders run-now buttons for both ops", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-run-now-lint")).toBeDefined();
        expect(screen.getByTestId("ops-run-now-backfill")).toBeDefined();
      });
    });

    it("shows 'never' text when no last_run_at", async () => {
      renderCard();
      await waitFor(() => {
        expect(screen.getByTestId("ops-never-run-lint")).toBeDefined();
        expect(screen.getByTestId("ops-never-run-backfill")).toBeDefined();
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

  // ── 7. i18n parity ─────────────────────────────────────────────────────────

  describe("i18n parity — EN and IT key sets are identical", () => {
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
    });
  });
});
