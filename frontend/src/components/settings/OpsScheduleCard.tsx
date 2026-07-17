/**
 * OpsScheduleCard.tsx — "Pianificazione lavori / Job scheduling" card (A5 / R12-7).
 *
 * Two-row card inside Settings › Advanced:
 *   • Lint   — "Scansione lint: trova problemi; le correzioni restano manuali"
 *   • Backfill — "Classificazione domini: tagga le pagine nuove/non classificate"
 *
 * Each row:
 *   - Frequency <select> (off/hourly/daily/weekly) → save-on-change via putAppConfig
 *   - "Esegui ora" button (POST run-now; disabled while in_flight)
 *   - Last-run line ("Ultima esecuzione: <time> — <status>" or "Mai eseguita")
 *   - "In esecuzione…" badge when in_flight
 *
 * Data flow:
 *   - GET /ops/schedules on mount (via getOpsSchedules).
 *   - Returns null → 404 older backend → card is hidden entirely (no crash).
 *   - Frequency change → putAppConfig("lint_schedule"|"backfill_schedule", value).
 *   - Run-now → runOpNow(op) then refetch.
 *   - Manual refresh icon provided (no polling loop — I3).
 *
 * Error handling:
 *   - 409 in-flight → showToast info
 *   - 400 dormant (empty vocabulary) → inline hint about vocabulary
 *   - 404 unknown op → generic error toast
 *
 * INVARIANT I3: no polling loop; no heavy work on token stream.
 * INVARIANT I6: no hardcoded provider IDs.
 * INVARIANT I7: 409/400 surfaced, not silenced.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import {
  getOpsSchedules,
  runOpNow,
  RunOpNowError,
  type OpsScheduleEntry,
  type OpsScheduleFrequency,
  type OpsScheduleOp,
} from "../../api/opsScheduleClient";
import { putAppConfig, type AppConfigKey } from "../../api/appConfigClient";
import { showToast } from "../common/Toast";
import { formatRelativeTime } from "../ingest/IngestRunList";

// ─── Frequency options ────────────────────────────────────────────────────────

const FREQUENCY_OPTIONS: OpsScheduleFrequency[] = ["off", "hourly", "daily", "weekly"];

// ─── Icon: refresh ────────────────────────────────────────────────────────────

function IconRefresh({ spinning }: { spinning?: boolean }) {
  return (
    <RefreshCw
      size={13}
      aria-hidden="true"
      style={spinning ? { animation: "synapse-spin 0.8s linear infinite" } : undefined}
    />
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function OpsScheduleCard() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language ?? "en";

  const [loading, setLoading]     = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  /**
   * null  → 404 / older backend → hide card entirely.
   * array → normal state.
   */
  const [ops, setOps] = useState<OpsScheduleEntry[] | null>([]);

  /** Per-op local frequency (tracks the <select> before/after PUT). */
  const [localFreqs, setLocalFreqs] = useState<Record<OpsScheduleOp, OpsScheduleFrequency>>({
    lint: "off",
    backfill: "off",
    schema_review: "off",
    reclassify: "off",
  });

  /** Per-op saving state (while PUT is in-flight). */
  const [saving, setSaving] = useState<Record<OpsScheduleOp, boolean>>({
    lint: false,
    backfill: false,
    schema_review: false,
    reclassify: false,
  });

  /** Per-op run-now busy state. */
  const [runningNow, setRunningNow] = useState<Record<OpsScheduleOp, boolean>>({
    lint: false,
    backfill: false,
    schema_review: false,
    reclassify: false,
  });

  /** When 400-dormant is returned, show the vocabulary hint for that op. */
  const [dormantHint, setDormantHint] = useState<Record<OpsScheduleOp, boolean>>({
    lint: false,
    backfill: false,
    schema_review: false,
    reclassify: false,
  });

  // ── Fetch ───────────────────────────────────────────────────────────────────

  const loadSchedules = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await getOpsSchedules(signal);
      if (data === null) {
        // 404 → older backend → null sentinel to hide card
        setOps(null);
        setFetchError(null);
        return;
      }
      setOps(data.ops);
      setFetchError(null);
      // Seed local frequency state from server
      const freqs: Record<OpsScheduleOp, OpsScheduleFrequency> = {
        lint: "off",
        backfill: "off",
        schema_review: "off",
        reclassify: "off",
      };
      for (const entry of data.ops) {
        freqs[entry.op] = entry.schedule;
      }
      setLocalFreqs(freqs);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setFetchError(t("settings.opsSchedule.fetchError"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    const ctrl = new AbortController();
    void loadSchedules(ctrl.signal);
    return () => ctrl.abort();
  }, [loadSchedules]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadSchedules();
  }, [loadSchedules]);

  // ── Frequency change → PUT /config/app ─────────────────────────────────────

  const handleFrequencyChange = useCallback(async (op: OpsScheduleOp, value: OpsScheduleFrequency) => {
    setLocalFreqs((prev) => ({ ...prev, [op]: value }));
    setSaving((prev) => ({ ...prev, [op]: true }));

    // Map op name → AppConfigKey (S10/S11/S12/S13 — R12-7/A5/R12-8/R12-9)
    const scheduleKeyMap: Record<OpsScheduleOp, AppConfigKey> = {
      lint: "lint_schedule",
      backfill: "backfill_schedule",
      schema_review: "schema_review_schedule",
      reclassify: "reclassify_schedule",
    };
    const key: AppConfigKey = scheduleKeyMap[op];
    try {
      await putAppConfig(key, value);
      // Reload so server-authoritative state is shown
      await loadSchedules();
    } catch {
      showToast(t("settings.opsSchedule.saveError"), "error");
    } finally {
      setSaving((prev) => ({ ...prev, [op]: false }));
    }
  }, [t, loadSchedules]);

  // ── Run now → POST /ops/schedules/{op}/run-now ─────────────────────────────

  const handleRunNow = useCallback(async (op: OpsScheduleOp) => {
    setRunningNow((prev) => ({ ...prev, [op]: true }));
    setDormantHint((prev) => ({ ...prev, [op]: false }));
    try {
      await runOpNow(op);
      showToast(t("settings.opsSchedule.runNowTriggered", { op: t(`settings.opsSchedule.opLabel.${op}`) }), "success");
      // Refresh to update in_flight / last_run_at
      await loadSchedules();
    } catch (e: unknown) {
      if (e instanceof RunOpNowError) {
        if (e.httpStatus === 409) {
          showToast(t("settings.opsSchedule.alreadyRunning"), "error");
        } else if (e.httpStatus === 400) {
          // Dormant op — show vocabulary hint
          setDormantHint((prev) => ({ ...prev, [op]: true }));
        }
      } else {
        const detail = e instanceof Error ? e.message : t("common.unknown");
        showToast(t("settings.opsSchedule.runNowError", { detail }), "error");
      }
    } finally {
      setRunningNow((prev) => ({ ...prev, [op]: false }));
    }
  }, [t, loadSchedules]);

  // ── Render guards ───────────────────────────────────────────────────────────

  // 404 / older backend → hide the card entirely (no placeholder, no error)
  if (!loading && ops === null) return null;

  // ── Status helpers ──────────────────────────────────────────────────────────

  /**
   * Normalise the backend's raw last_status into a coarse kind. The backend sends
   * "ok" | "dormant" | "error:<msg>" | "skipped"; the "error:" carries a detail suffix
   * so we match on the prefix rather than an exact string (R13-12).
   */
  function statusKind(raw: string | null): "ok" | "dormant" | "error" | "skipped" {
    if (!raw) return "ok";
    if (raw === "dormant") return "dormant";
    if (raw.startsWith("error")) return "error";
    if (raw === "skipped") return "skipped";
    return "ok";
  }

  function statusLabel(entry: OpsScheduleEntry): string {
    if (entry.in_flight) return t("settings.opsSchedule.statusRunning");
    if (!entry.last_run_at) return t("settings.opsSchedule.never");
    switch (statusKind(entry.last_status)) {
      case "dormant":  return t("settings.opsSchedule.statusDormant");
      case "error":    return t("settings.opsSchedule.statusError");
      case "skipped":  return t("settings.opsSchedule.statusSkipped");
      default:         return t("settings.opsSchedule.statusOk");
    }
  }

  function statusColor(entry: OpsScheduleEntry): string {
    if (entry.in_flight) return "var(--syn-accent)";
    switch (statusKind(entry.last_status)) {
      case "ok":       return "var(--syn-green)";
      case "error":    return "var(--syn-red)";
      case "dormant":  return "var(--syn-amber)";
      default:         return "var(--syn-text-dim)";
    }
  }

  // ── Row renderer ────────────────────────────────────────────────────────────

  function OpRow({ entry }: { entry: OpsScheduleEntry }) {
    const op = entry.op;
    const isSaving    = saving[op];
    const isRunning   = runningNow[op];
    const isInFlight  = entry.in_flight;
    const runDisabled = isRunning || isInFlight || isSaving;

    return (
      <div
        data-testid={`ops-schedule-row-${op}`}
        style={{
          padding: "14px 16px",
          background: "var(--syn-surface)",
          border: "1px solid var(--syn-border)",
          borderRadius: 8,
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {/* Header: op label + in-flight badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text)" }}>
            {t(`settings.opsSchedule.opLabel.${op}`)}
          </span>
          {isInFlight && (
            <span
              data-testid={`ops-in-flight-badge-${op}`}
              style={{
                padding: "1px 7px",
                borderRadius: 4,
                fontSize: 10,
                fontWeight: 600,
                background: "color-mix(in srgb, var(--syn-accent) 12%, var(--syn-mix-base) 88%)",
                color: "var(--syn-accent)",
                border: "1px solid color-mix(in srgb, var(--syn-accent) 30%, transparent 70%)",
              }}
            >
              {t("settings.opsSchedule.statusRunning")}
            </span>
          )}
        </div>

        {/* Description */}
        <p style={{ margin: 0, fontSize: 11, color: "var(--syn-text-dim)", lineHeight: 1.5 }}>
          {t(`settings.opsSchedule.opDesc.${op}`)}
        </p>

        {/* Controls row: frequency + run-now */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          {/* Frequency select */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <label
              htmlFor={`ops-freq-${op}`}
              style={{ fontSize: 11, color: "var(--syn-text-muted)", whiteSpace: "nowrap" }}
            >
              {t("settings.opsSchedule.frequencyLabel")}
            </label>
            <select
              id={`ops-freq-${op}`}
              data-testid={`ops-freq-select-${op}`}
              value={localFreqs[op]}
              disabled={isSaving}
              onChange={(e) => {
                void handleFrequencyChange(op, e.target.value as OpsScheduleFrequency);
              }}
              style={{
                padding: "4px 8px",
                background: "var(--syn-bg)",
                border: "1px solid var(--syn-border)",
                borderRadius: 5,
                color: "var(--syn-text)",
                fontSize: 12,
                cursor: isSaving ? "not-allowed" : "pointer",
                opacity: isSaving ? 0.5 : 1,
              }}
            >
              {FREQUENCY_OPTIONS.map((freq) => (
                <option key={freq} value={freq}>
                  {t(`settings.opsSchedule.freq.${freq}`)}
                </option>
              ))}
            </select>
            {isSaving && (
              <span style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
                {t("settings.opsSchedule.saving")}
              </span>
            )}
          </div>

          {/* Run now button */}
          <button
            data-testid={`ops-run-now-${op}`}
            disabled={runDisabled}
            onClick={() => { void handleRunNow(op); }}
            aria-label={t("settings.opsSchedule.runNow")}
            style={{
              padding: "4px 12px",
              border: "1px solid var(--syn-border)",
              borderRadius: 5,
              background: "transparent",
              color: runDisabled ? "var(--syn-text-dim)" : "var(--syn-accent)",
              fontSize: 12,
              fontWeight: 500,
              cursor: runDisabled ? "not-allowed" : "pointer",
              opacity: runDisabled ? 0.5 : 1,
              whiteSpace: "nowrap",
            }}
          >
            {isRunning ? t("common.loading") : t("settings.opsSchedule.runNow")}
          </button>
        </div>

        {/* Last-run line */}
        {entry.last_run_at ? (
          <div
            data-testid={`ops-last-run-${op}`}
            style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}
          >
            <span style={{ color: "var(--syn-text-dim)" }}>
              {t("settings.opsSchedule.lastRun")}:
            </span>
            <span
              style={{
                padding: "1px 5px",
                borderRadius: 3,
                background: `${statusColor(entry)}22`,
                color: statusColor(entry),
                fontSize: 10,
                fontWeight: 600,
              }}
            >
              {statusLabel(entry)}
            </span>
            <span style={{ color: "var(--syn-text-muted)" }}>
              {formatRelativeTime(entry.last_run_at, lang)}
            </span>
          </div>
        ) : (
          <span
            data-testid={`ops-never-run-${op}`}
            style={{ fontSize: 11, color: "var(--syn-text-dim)" }}
          >
            {t("settings.opsSchedule.never")}
          </span>
        )}

        {/* Outcome detail — why the run produced what it did (R13-12). */}
        {entry.last_run_at && !isInFlight && entry.last_detail && (
          <span
            data-testid={`ops-last-detail-${op}`}
            style={{ fontSize: 10, color: "var(--syn-text-dim)", lineHeight: 1.5 }}
          >
            {entry.last_detail}
          </span>
        )}

        {/* 400-dormant hint */}
        {dormantHint[op] && (
          <p
            role="alert"
            data-testid={`ops-dormant-hint-${op}`}
            style={{ margin: 0, fontSize: 11, color: "var(--syn-amber)", lineHeight: 1.5 }}
          >
            {t("settings.opsSchedule.dormantHint")}
          </p>
        )}
      </div>
    );
  }

  // ── Main render ─────────────────────────────────────────────────────────────

  return (
    <div data-testid="ops-schedule-card">
      {/* Card header with title + manual refresh */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
        <p style={{ margin: 0, fontSize: 12, color: "var(--syn-text-muted)", flex: 1 }}>
          {t("settings.opsSchedule.desc")}
        </p>
        <button
          data-testid="ops-schedule-refresh"
          onClick={() => { void handleRefresh(); }}
          disabled={loading || refreshing}
          aria-label={t("settings.opsSchedule.refresh")}
          title={t("settings.opsSchedule.refresh")}
          style={{
            padding: 4,
            border: "none",
            background: "transparent",
            color: (loading || refreshing) ? "var(--syn-text-dim)" : "var(--syn-text-muted)",
            cursor: (loading || refreshing) ? "default" : "pointer",
            borderRadius: 4,
            display: "flex",
            alignItems: "center",
          }}
        >
          <IconRefresh spinning={refreshing} />
        </button>
      </div>

      {/* Loading skeleton */}
      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("common.loading")}</p>
      )}

      {/* Fetch error */}
      {fetchError && !loading && (
        <p role="alert" style={{ fontSize: 12, color: "var(--syn-red)", margin: "0 0 12px" }}>
          {fetchError}
        </p>
      )}

      {/* Op rows */}
      {!loading && ops !== null && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {ops.map((entry) => (
            <OpRow key={entry.op} entry={entry} />
          ))}
          {ops.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--syn-text-dim)", margin: 0 }}>
              {t("settings.opsSchedule.noOps")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
