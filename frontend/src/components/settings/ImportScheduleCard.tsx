/**
 * ImportScheduleCard.tsx — Feature S: scheduled folder import settings (ADR-0020 §4 / §5).
 *
 * Displays and edits:
 *   - Enabled toggle
 *   - Source directory text input (container-visible path hint)
 *   - Frequency select (15m / 1h / 6h / daily)
 *   - Last-run status line (relative time + badge + count)
 *   - Save button (PUT /import-schedule)
 *   - Run now button (POST /import-schedule/run-now)
 *   - dir_ok:false inline warning
 *
 * INVARIANT I3: reads importScheduleStore only via typed selectors.
 * INVARIANT I7: runNow 409/400 errors are surfaced, not silenced.
 * INVARIANT I6: no hardcoded provider/model IDs.
 *
 * Polling: starts when last_status === "running" (bounded — stops on non-running).
 */

import { useEffect, useState, useCallback, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  useImportScheduleStore,
  selectImportSchedule,
  selectImportLoading,
  selectImportSaving,
  selectImportRunning,
  selectImportError,
  selectImportSaveError,
  selectDirOk,
  selectDirMessage,
  selectFetchSchedule,
  selectSaveSchedule,
  selectRunNow,
  selectStartPollingIfRunning,
} from "../../store/importScheduleStore";
import type { ImportFrequency } from "../../api/types";
import { showToast } from "../common/Toast";
import { formatRelativeTime } from "../ingest/IngestRunList";

// ─── Frequency options ────────────────────────────────────────────────────────

const FREQUENCY_OPTIONS: ImportFrequency[] = ["15m", "1h", "6h", "daily"];

// ─── Component ────────────────────────────────────────────────────────────────

export function ImportScheduleCard() {
  const { t, i18n } = useTranslation();

  const schedule = useImportScheduleStore(selectImportSchedule);
  const loading = useImportScheduleStore(selectImportLoading);
  const saving = useImportScheduleStore(selectImportSaving);
  const running = useImportScheduleStore(selectImportRunning);
  const fetchError = useImportScheduleStore(selectImportError);
  const saveError = useImportScheduleStore(selectImportSaveError);
  const dirOk = useImportScheduleStore(selectDirOk);
  const dirMessage = useImportScheduleStore(selectDirMessage);

  const fetchSchedule = useImportScheduleStore(selectFetchSchedule);
  const saveSchedule = useImportScheduleStore(selectSaveSchedule);
  const runNow = useImportScheduleStore(selectRunNow);
  const startPollingIfRunning = useImportScheduleStore(selectStartPollingIfRunning);

  // Local form state — mirrors server state but is editable before Save
  const [enabled, setEnabled] = useState(false);
  const [sourceDir, setSourceDir] = useState("");
  const [frequency, setFrequency] = useState<ImportFrequency>("1h");

  // Sync form state when server data arrives
  useEffect(() => {
    if (schedule) {
      setEnabled(schedule.enabled);
      setSourceDir(schedule.source_dir ?? "");
      setFrequency(schedule.frequency);
    }
  }, [schedule]);

  // Fetch on mount
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchSchedule(ctrl.signal);
    return () => ctrl.abort();
  }, [fetchSchedule]);

  // Start polling when status is running
  useEffect(() => {
    if (schedule?.last_status === "running") {
      return startPollingIfRunning();
    }
    return undefined;
  }, [schedule?.last_status, startPollingIfRunning]);

  const handleSave = useCallback(async () => {
    const body = {
      enabled,
      source_dir: sourceDir.trim() || null,
      frequency,
    };
    const res = await saveSchedule(body);
    if (res) {
      showToast(t("settings.import.runNowToast"), "success");
    } else {
      // saveError is already set in store; show toast too
      const detail = saveError ?? t("common.unknown");
      showToast(t("settings.import.saveError", { detail }), "error");
    }
  }, [enabled, sourceDir, frequency, saveSchedule, saveError, t]);

  const handleRunNow = useCallback(async () => {
    try {
      await runNow();
      showToast(t("settings.import.runNowToast"), "success");
      // Polling will start via the useEffect when last_status transitions to "running"
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : t("common.unknown");
      showToast(t("settings.import.runNowError", { detail }), "error");
    }
  }, [runNow, t]);

  const handleFrequencyChange = useCallback((e: ChangeEvent<HTMLSelectElement>) => {
    setFrequency(e.target.value as ImportFrequency);
  }, []);

  const handleSourceDirChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setSourceDir(e.target.value);
  }, []);

  // ── Status badge for last run ────────────────────────────────────────────

  function statusLabel(status: string | null): string {
    switch (status) {
      case "ok": return t("settings.import.statusOk");
      case "error": return t("settings.import.statusError");
      case "running": return t("settings.import.statusRunning");
      case "dir_missing": return t("settings.import.statusDirMissing");
      case "skipped_disabled": return t("settings.import.statusDisabled");
      default: return t("settings.import.never");
    }
  }

  function statusColor(status: string | null): string {
    switch (status) {
      case "ok": return "#2ea043";
      case "running": return "#1f6feb";
      case "error":
      case "dir_missing": return "#f85149";
      default: return "#484f58";
    }
  }

  const lang = i18n.language ?? "en";

  return (
    <div data-testid="import-schedule-card">
      {/* Title */}
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "#6e7681" }}>
        {t("settings.import.title")}
      </p>

      {loading && (
        <p style={{ fontSize: 12, color: "#484f58" }}>{t("common.loading")}</p>
      )}

      {fetchError && !loading && (
        <p role="alert" style={{ fontSize: 12, color: "#f85149", margin: "0 0 12px" }}>
          {fetchError}
        </p>
      )}

      {!loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Enabled toggle */}
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              cursor: "pointer",
              fontSize: 12,
              color: "#e6edf3",
            }}
          >
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ width: 14, height: 14, cursor: "pointer", accentColor: "#1f6feb" }}
              aria-label={t("settings.import.enabled")}
            />
            {t("settings.import.enabled")}
          </label>

          {/* Source directory */}
          <div>
            <label
              htmlFor="import-source-dir"
              style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#8b949e", marginBottom: 4 }}
            >
              {t("settings.import.sourceDir")}
            </label>
            <input
              id="import-source-dir"
              type="text"
              value={sourceDir}
              onChange={handleSourceDirChange}
              placeholder="/data/sources"
              disabled={saving}
              style={{
                width: "100%",
                padding: "6px 10px",
                background: "#161b22",
                border: `1px solid ${dirOk === false ? "#f85149" : "#21262d"}`,
                borderRadius: 6,
                color: "#e6edf3",
                fontSize: 12,
                fontFamily: "monospace",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <p style={{ margin: "3px 0 0", fontSize: 11, color: "#484f58" }}>
              {t("settings.import.dirHint")}
            </p>

            {/* dir_ok:false warning */}
            {dirOk === false && dirMessage && (
              <p
                role="alert"
                style={{ margin: "4px 0 0", fontSize: 11, color: "#f85149" }}
              >
                {t("settings.import.dirWarning", { dir: dirMessage })}
              </p>
            )}
          </div>

          {/* Frequency select */}
          <div>
            <label
              htmlFor="import-frequency"
              style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#8b949e", marginBottom: 4 }}
            >
              {t("settings.import.frequency")}
            </label>
            <select
              id="import-frequency"
              value={frequency}
              onChange={handleFrequencyChange}
              disabled={saving}
              style={{
                padding: "6px 10px",
                background: "#161b22",
                border: "1px solid #21262d",
                borderRadius: 6,
                color: "#e6edf3",
                fontSize: 12,
                cursor: saving ? "not-allowed" : "pointer",
              }}
            >
              {FREQUENCY_OPTIONS.map((freq) => (
                <option key={freq} value={freq}>
                  {t(`settings.import.freq${freq === "15m" ? "15m" : freq === "1h" ? "1h" : freq === "6h" ? "6h" : "Daily"}`)}
                </option>
              ))}
            </select>
          </div>

          {/* Last run status */}
          {schedule && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
              <span style={{ color: "#484f58" }}>{t("settings.import.lastRun")}:</span>
              <span
                style={{
                  padding: "1px 6px",
                  borderRadius: 4,
                  background: `${statusColor(schedule.last_status)}22`,
                  color: statusColor(schedule.last_status),
                  fontSize: 10,
                  fontWeight: 600,
                }}
              >
                {statusLabel(schedule.last_status)}
              </span>
              {schedule.last_run_at && (
                <span style={{ color: "#6e7681" }}>
                  {formatRelativeTime(schedule.last_run_at, lang)}
                </span>
              )}
              {schedule.last_imported_count > 0 && (
                <span style={{ color: "#484f58" }}>
                  — {schedule.last_imported_count} {t("settings.import.imported")}
                </span>
              )}
            </div>
          )}

          {/* Save error */}
          {saveError && (
            <p role="alert" style={{ margin: 0, fontSize: 11, color: "#f85149" }}>
              {saveError}
            </p>
          )}

          {/* Action buttons */}
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button
              onClick={() => void handleSave()}
              disabled={saving}
              aria-label={t("settings.import.save")}
              style={{
                padding: "6px 16px",
                border: "none",
                borderRadius: 6,
                background: saving ? "#21262d" : "#1f6feb",
                color: saving ? "#484f58" : "#e6edf3",
                fontSize: 12,
                fontWeight: 600,
                cursor: saving ? "not-allowed" : "pointer",
              }}
            >
              {saving ? t("common.loading") : t("settings.import.save")}
            </button>

            <button
              onClick={() => void handleRunNow()}
              disabled={running || saving || !schedule?.enabled}
              aria-label={t("settings.import.runNow")}
              data-testid="import-run-now"
              title={
                !schedule?.enabled
                  ? t("settings.import.statusDisabled")
                  : undefined
              }
              style={{
                padding: "6px 16px",
                border: "1px solid #21262d",
                borderRadius: 6,
                background: "transparent",
                color: running || saving || !schedule?.enabled ? "#484f58" : "#58a6ff",
                fontSize: 12,
                fontWeight: 500,
                cursor: running || saving || !schedule?.enabled ? "not-allowed" : "pointer",
              }}
            >
              {running ? t("common.loading") : t("settings.import.runNow")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
