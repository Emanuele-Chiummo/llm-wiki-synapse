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

// ─── P3-c: importable file-type groups (must mirror backend known set) ──────────
// text (_ALLOWED_EXTENSIONS) + extractable (_EXTRACTABLE_EXTENSIONS). Placeholder
// image/AV types are intentionally NOT auto-imported by the scheduler.
const EXT_GROUPS: { labelKey: string; exts: string[] }[] = [
  { labelKey: "settings.import.typeGroupText", exts: [".md", ".txt", ".markdown", ".mdx"] },
  { labelKey: "settings.import.typeGroupDocs", exts: [".pdf", ".docx", ".rtf", ".odt"] },
  { labelKey: "settings.import.typeGroupSheets", exts: [".xlsx", ".csv", ".ods"] },
  { labelKey: "settings.import.typeGroupSlides", exts: [".pptx", ".odp"] },
  { labelKey: "settings.import.typeGroupWeb", exts: [".html"] },
];
const ALL_KNOWN_EXTS: string[] = EXT_GROUPS.flatMap((g) => g.exts);

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
  // P3-c: wider Source-Watch types. allowed = null → all known checked (default).
  const [allowedExts, setAllowedExts] = useState<Set<string>>(new Set(ALL_KNOWN_EXTS));
  const [excludedFolders, setExcludedFolders] = useState("");
  const [maxSizeMb, setMaxSizeMb] = useState(0);

  // Sync form state when server data arrives
  useEffect(() => {
    if (schedule) {
      setEnabled(schedule.enabled);
      setSourceDir(schedule.source_dir ?? "");
      setFrequency(schedule.frequency);
      const ae = schedule.allowed_extensions;
      setAllowedExts(
        ae
          ? new Set(
              ae
                .split(",")
                .map((s) => s.trim().toLowerCase())
                .filter(Boolean),
            )
          : new Set(ALL_KNOWN_EXTS),
      );
      setExcludedFolders(schedule.excluded_folders ?? "");
      setMaxSizeMb(schedule.max_size_mb ?? 0);
    }
  }, [schedule]);

  const toggleExt = useCallback((ext: string) => {
    setAllowedExts((prev) => {
      const next = new Set(prev);
      if (next.has(ext)) next.delete(ext);
      else next.add(ext);
      return next;
    });
  }, []);

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
      // P3-c: send the explicit list of checked types; "" → backend default wider set
      allowed_extensions: Array.from(allowedExts).join(","),
      excluded_folders: excludedFolders.trim(),
      max_size_mb: maxSizeMb,
    };
    const res = await saveSchedule(body);
    if (res) {
      showToast(t("settings.import.runNowToast"), "success");
    } else {
      // saveError is already set in store; show toast too
      const detail = saveError ?? t("common.unknown");
      showToast(t("settings.import.saveError", { detail }), "error");
    }
  }, [
    enabled,
    sourceDir,
    frequency,
    allowedExts,
    excludedFolders,
    maxSizeMb,
    saveSchedule,
    saveError,
    t,
  ]);

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
      case "ok": return "var(--syn-green)";
      case "running": return "var(--syn-accent)";
      case "error":
      case "dir_missing": return "var(--syn-red)";
      default: return "var(--syn-text-dim)";
    }
  }

  const lang = i18n.language ?? "en";

  return (
    <div data-testid="import-schedule-card">
      {/* Title */}
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--syn-text-muted)" }}>
        {t("settings.import.title")}
      </p>

      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("common.loading")}</p>
      )}

      {fetchError && !loading && (
        <p role="alert" style={{ fontSize: 12, color: "var(--syn-red)", margin: "0 0 12px" }}>
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
              color: "var(--syn-text)",
            }}
          >
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ width: 14, height: 14, cursor: "pointer", accentColor: "var(--syn-accent)" }}
              aria-label={t("settings.import.enabled")}
            />
            {t("settings.import.enabled")}
          </label>

          {/* Source directory */}
          <div>
            <label
              htmlFor="import-source-dir"
              style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--syn-text-muted)", marginBottom: 4 }}
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
                background: "var(--syn-bg)",
                border: `1px solid ${dirOk === false ? "var(--syn-red)" : "var(--syn-border)"}`,
                borderRadius: 6,
                color: "var(--syn-text)",
                fontSize: 12,
                fontFamily: "monospace",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("settings.import.dirHint")}
            </p>

            {/* dir_ok:false warning */}
            {dirOk === false && dirMessage && (
              <p
                role="alert"
                style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-red)" }}
              >
                {t("settings.import.dirWarning", { dir: dirMessage })}
              </p>
            )}
          </div>

          {/* Frequency select */}
          <div>
            <label
              htmlFor="import-frequency"
              style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--syn-text-muted)", marginBottom: 4 }}
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
                background: "var(--syn-bg)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                color: "var(--syn-text)",
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

          {/* P3-c: Allowed file types — grouped checkboxes */}
          <div data-testid="import-allowed-types">
            <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--syn-text-muted)", marginBottom: 6 }}>
              {t("settings.import.allowedTypes")}
            </label>
            <p style={{ margin: "0 0 8px", fontSize: 11, color: "var(--syn-text-dim)", lineHeight: 1.5 }}>
              {t("settings.import.allowedTypesHint")}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {EXT_GROUPS.map((group) => (
                <div key={group.labelKey}>
                  <p style={{ margin: "0 0 4px", fontSize: 11, fontWeight: 600, color: "var(--syn-text-dim)" }}>
                    {t(group.labelKey)}
                  </p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {group.exts.map((ext) => {
                      const on = allowedExts.has(ext);
                      return (
                        <label
                          key={ext}
                          data-testid={`import-type-${ext}`}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 5,
                            padding: "3px 9px",
                            border: `1px solid ${on ? "var(--syn-accent)" : "var(--syn-border)"}`,
                            borderRadius: 6,
                            background: on ? "var(--syn-accent-soft)" : "var(--syn-surface)",
                            color: on ? "var(--syn-accent)" : "var(--syn-text-muted)",
                            fontSize: 11,
                            fontFamily: "monospace",
                            cursor: saving ? "not-allowed" : "pointer",
                            userSelect: "none",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={on}
                            disabled={saving}
                            onChange={() => toggleExt(ext)}
                            style={{ width: 12, height: 12, cursor: "pointer", accentColor: "var(--syn-accent)" }}
                          />
                          {ext}
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* P3-c: Excluded folders */}
          <div>
            <label
              htmlFor="import-excluded-folders"
              style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--syn-text-muted)", marginBottom: 4 }}
            >
              {t("settings.import.excludedFolders")}
            </label>
            <input
              id="import-excluded-folders"
              data-testid="import-excluded-folders"
              type="text"
              value={excludedFolders}
              onChange={(e) => setExcludedFolders(e.target.value)}
              placeholder="node_modules, .git, archive"
              disabled={saving}
              style={{
                width: "100%",
                padding: "6px 10px",
                background: "var(--syn-bg)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                color: "var(--syn-text)",
                fontSize: 12,
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("settings.import.excludedFoldersHint")}
            </p>
          </div>

          {/* P3-c: Max file size */}
          <div>
            <label
              htmlFor="import-max-size"
              style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--syn-text-muted)", marginBottom: 4 }}
            >
              {t("settings.import.maxSize")}
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                id="import-max-size"
                data-testid="import-max-size"
                type="number"
                min={0}
                value={maxSizeMb}
                onChange={(e) => setMaxSizeMb(Math.max(0, Number(e.target.value) || 0))}
                disabled={saving}
                style={{
                  width: 90,
                  padding: "6px 10px",
                  background: "var(--syn-bg)",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 6,
                  color: "var(--syn-text)",
                  fontSize: 12,
                  outline: "none",
                }}
              />
              <span style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("settings.import.maxSizeUnit")}</span>
            </div>
            <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("settings.import.maxSizeHint")}
            </p>
          </div>

          {/* Last run status */}
          {schedule && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
              <span style={{ color: "var(--syn-text-dim)" }}>{t("settings.import.lastRun")}:</span>
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
                <span style={{ color: "var(--syn-text-muted)" }}>
                  {formatRelativeTime(schedule.last_run_at, lang)}
                </span>
              )}
              {schedule.last_imported_count > 0 && (
                <span style={{ color: "var(--syn-text-dim)" }}>
                  — {schedule.last_imported_count} {t("settings.import.imported")}
                </span>
              )}
            </div>
          )}

          {/* Save error */}
          {saveError && (
            <p role="alert" style={{ margin: 0, fontSize: 11, color: "var(--syn-red)" }}>
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
                border: "1px solid var(--syn-accent-strong)",
                borderRadius: 6,
                background: saving ? "var(--syn-surface-hover)" : "var(--syn-accent)",
                color: saving ? "var(--syn-text-dim)" : "#ffffff",
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
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                background: "transparent",
                color: running || saving || !schedule?.enabled ? "var(--syn-text-dim)" : "var(--syn-accent)",
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
