/**
 * ActivityBar.tsx — bottom status bar + expandable Activity Panel (F1).
 *
 * COLLAPSED STATE (28px footer):
 *   Vault id · data_version · uptime · connectivity dot · active provider
 *   + queue status icon + statusText + chevron toggle.
 *
 * EXPANDED PANEL (upward, above the 28px bar):
 *   Progress bar · Pause/Resume · Cancel All · Retry Failed
 *   Per-task rows grouped processing → pending → failed.
 *
 * Polling strategy: delegates to activityStore.startPolling() — single
 * setTimeout chain, fast (1500ms) while active, slow (5000ms) when idle (I3).
 *
 * INVARIANT I3: subscribes via typed selectors + useShallow.
 * INVARIANT I2: never runs any layout; no graph-store coupling here.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  AlertCircle,
  CheckCircle2,
  ChevronUp,
  ChevronDown,
  Clock,
  X,
  RotateCcw,
  PauseCircle,
  PlayCircle,
} from "lucide-react";

import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { useGraphMeta } from "../../store/graphStore";
import { fetchStatus } from "../../api/pagesClient";
import { useProviderStore, selectActiveProvider } from "../../store/providerStore";
import {
  useActivityStore,
  useActivityCounts,
  useActivityTasks,
  selectStartPolling,
  selectCancelRun,
  selectRetryRun,
  selectTogglePause,
  MAX_VISIBLE_FAILED,
} from "../../store/activityStore";
import { MaxRetriesExceededError } from "../../api/ingestClient";
import type { QueueTask } from "../../api/types";

// ─── Status-poll constants ─────────────────────────────────────────────────────

const STATUS_POLL_MS = 30_000;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatUptime(s: number | null): string {
  if (s === null) return "–";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function progressPercent(
  completed: number,
  pending: number,
  processing: number,
): number {
  const done = completed;
  const denominator = completed + pending + processing;
  if (denominator === 0) return 0;
  return Math.min(100, Math.round((done / denominator) * 100));
}

// ─── Sub-component: TaskRow ────────────────────────────────────────────────────

interface TaskRowProps {
  task: QueueTask;
  isCancelling: boolean;
  onCancel: (runId: string) => void;
  onRetry: (runId: string) => void;
}

function TaskRow({ task, isCancelling, onCancel, onRetry }: TaskRowProps) {
  const { t } = useTranslation();
  const [retryError, setRetryError] = useState<string | null>(null);

  const handleRetry = useCallback(() => {
    if (!task.run_id) return;
    setRetryError(null);
    useActivityStore
      .getState()
      .retryRun(task.run_id)
      .catch((err: unknown) => {
        if (err instanceof MaxRetriesExceededError) {
          setRetryError(t("activity.maxRetriesReached"));
        }
      });
    onRetry(task.run_id);
  }, [task.run_id, t, onRetry]);

  const handleCancel = useCallback(() => {
    if (!task.run_id) return;
    onCancel(task.run_id);
  }, [task.run_id, onCancel]);

  const isMaxRetries = task.retry_count >= 3;

  return (
    <div
      data-testid="activity-task-row"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "6px 0",
        borderBottom: "1px solid var(--syn-border)",
      }}
    >
      {/* Status icon */}
      <span style={{ flexShrink: 0, paddingTop: 1 }}>
        {task.status === "processing" ? (
          <Loader2
            size={13}
            style={{ animation: "spin 1s linear infinite", color: "var(--syn-accent)" }}
          />
        ) : task.status === "failed" ? (
          <AlertCircle size={13} style={{ color: "var(--syn-red)" }} />
        ) : (
          <Clock size={13} style={{ color: "var(--syn-text-dim)" }} />
        )}
      </span>

      {/* File info */}
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: "block",
            fontSize: 12,
            color: "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={task.filename}
        >
          {isCancelling ? (
            <em style={{ color: "var(--syn-text-dim)" }}>{t("activity.cancelling")}</em>
          ) : (
            task.filename
          )}
        </span>
        {(task.error ?? task.source_path) && (
          <span
            style={{
              display: "block",
              fontSize: 10,
              color: "var(--syn-text-dim)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              marginTop: 1,
            }}
            title={task.error ?? task.source_path}
          >
            {task.error ?? task.source_path}
          </span>
        )}
        {retryError && (
          <span
            style={{
              display: "block",
              fontSize: 10,
              color: "var(--syn-red)",
              marginTop: 1,
            }}
          >
            {retryError}
          </span>
        )}
      </span>

      {/* Actions */}
      <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
        {(task.status === "processing" || task.status === "pending") && task.run_id && (
          <button
            data-testid="activity-cancel"
            title={t("activity.cancel")}
            onClick={handleCancel}
            disabled={isCancelling}
            style={iconButtonStyle}
            aria-label={t("activity.cancel")}
          >
            <X size={11} />
          </button>
        )}
        {task.status === "failed" && task.run_id && (
          <button
            data-testid="activity-retry"
            title={isMaxRetries ? t("activity.maxRetriesReached") : t("activity.retry")}
            onClick={handleRetry}
            disabled={isMaxRetries}
            style={{
              ...iconButtonStyle,
              opacity: isMaxRetries ? 0.4 : 1,
              cursor: isMaxRetries ? "not-allowed" : "pointer",
            }}
            aria-label={
              isMaxRetries ? t("activity.maxRetriesReached") : t("activity.retry")
            }
          >
            <RotateCcw size={11} />
          </button>
        )}
      </span>
    </div>
  );
}

const iconButtonStyle: import("react").CSSProperties = {
  background: "none",
  border: "1px solid var(--syn-border)",
  borderRadius: 3,
  padding: "2px 4px",
  cursor: "pointer",
  color: "var(--syn-text-dim)",
  display: "flex",
  alignItems: "center",
  lineHeight: 1,
};

// ─── Main component ───────────────────────────────────────────────────────────

export function ActivityBar() {
  const { t } = useTranslation();
  const vaultId = useGraphStore(selectVaultId);
  const { dataVersion: storeVersion } = useGraphMeta();
  const activeProvider = useProviderStore(selectActiveProvider);

  // Status-bar polling (GET /status every 30s)
  const [status, setStatus] = useState<{ dataVersion: number | null; uptimeSeconds: number | null }>({
    dataVersion: null,
    uptimeSeconds: null,
  });
  const [pollError, setPollError] = useState(false);
  const statusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    async function pollStatus() {
      try {
        const res = await fetchStatus(ctrl.signal);
        if (!ctrl.signal.aborted) {
          setStatus({ dataVersion: res.data_version, uptimeSeconds: res.uptime_seconds });
          setPollError(false);
        }
      } catch {
        if (!ctrl.signal.aborted) setPollError(true);
      }
      if (!ctrl.signal.aborted) {
        statusTimerRef.current = setTimeout(pollStatus, STATUS_POLL_MS);
      }
    }
    void pollStatus();
    return () => {
      ctrl.abort();
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
    };
  }, []);

  // Queue store
  const counts = useActivityCounts();
  const tasks = useActivityTasks();
  const cancellingIds = useActivityStore((s) => s.cancellingIds);
  const startPolling = useActivityStore(selectStartPolling);
  const cancelRun = useActivityStore(selectCancelRun);
  const retryRun = useActivityStore(selectRetryRun);
  const togglePause = useActivityStore(selectTogglePause);

  // Start the queue poll loop on mount
  useEffect(() => {
    const stop = startPolling();
    return stop;
  }, [startPolling]);

  // Auto-expand when processing transitions 0 → >0
  const prevProcessingRef = useRef(0);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (prevProcessingRef.current === 0 && counts.processing > 0) {
      setExpanded(true);
    }
    prevProcessingRef.current = counts.processing;
  }, [counts.processing]);

  // ── Computed display values ──────────────────────────────────────────────────

  const displayVersion = storeVersion ?? status.dataVersion;
  const isActive = counts.processing > 0;
  const hasFailed = counts.failed > 0;

  /** Compact status text for the collapsed bar. */
  const statusText: string = (() => {
    if (counts.paused) return t("activity.paused");
    if (isActive) {
      const proc = tasks.find((tk) => tk.status === "processing");
      return proc ? proc.filename : t("activity.processing");
    }
    if (hasFailed) return t("activity.failed", { count: counts.failed });
    if (counts.total > 0) {
      return t("activity.completedCount", {
        completed: counts.completed_since_idle,
        total: counts.total,
      });
    }
    return t("activity.emptyQueue");
  })();

  // ── Task grouping (processing → pending → failed; failed capped at MAX_VISIBLE_FAILED) ──
  const processingTasks = tasks.filter((tk) => tk.status === "processing");
  const pendingTasks = tasks.filter((tk) => tk.status === "pending");
  const failedTasks = tasks
    .filter((tk) => tk.status === "failed")
    .slice(0, MAX_VISIBLE_FAILED);

  const pct = progressPercent(counts.completed_since_idle, counts.pending, counts.processing);
  const hasActiveTasks = counts.pending + counts.processing >= 2;

  // ── Cancel-all handler ───────────────────────────────────────────────────────
  const handleCancelAll = useCallback(() => {
    if (!window.confirm(t("activity.cancelAllConfirm"))) return;
    const activeIds = [...processingTasks, ...pendingTasks]
      .filter((tk) => tk.run_id !== undefined)
      .map((tk) => tk.run_id as string);
    for (const id of activeIds) void cancelRun(id);
  }, [processingTasks, pendingTasks, cancelRun, t]);

  // ── Retry-failed handler ─────────────────────────────────────────────────────
  const handleRetryFailed = useCallback(() => {
    for (const tk of failedTasks) {
      if (tk.run_id !== undefined && tk.retry_count < 3) void retryRun(tk.run_id);
    }
  }, [failedTasks, retryRun]);

  const handleTaskCancel = useCallback(
    (runId: string) => void cancelRun(runId),
    [cancelRun],
  );
  const handleTaskRetry = useCallback((_runId: string) => {
    // retryRun already called inside TaskRow; this callback is a no-op hook for future extensions
  }, []);

  return (
    <div style={{ position: "relative" }}>
      {/* ── Expanded panel (upward) ──────────────────────────────────────────── */}
      {expanded && (
        <div
          data-testid="activity-panel"
          style={{
            position: "absolute",
            bottom: "100%",
            right: 0,
            width: "min(420px, 100vw)",
            maxHeight: "50vh",
            overflowY: "auto",
            background: "var(--syn-bg-card, var(--syn-bg-soft))",
            border: "1px solid var(--syn-border)",
            borderRadius: "6px 6px 0 0",
            boxShadow: "0 -4px 16px rgba(0,0,0,0.25)",
            zIndex: 200,
            padding: "10px 12px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          {/* Header row */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "var(--syn-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                flex: 1,
              }}
            >
              {t("activity.queueStatus")}
            </span>

            {/* Pause / Resume */}
            <button
              data-testid="activity-pause-toggle"
              title={counts.paused ? t("activity.resumeQueue") : t("activity.pauseQueue")}
              onClick={() => void togglePause()}
              style={{ ...iconButtonStyle, fontSize: 11, gap: 4, padding: "3px 6px" }}
              aria-label={counts.paused ? t("activity.resumeQueue") : t("activity.pauseQueue")}
            >
              {counts.paused ? (
                <PlayCircle size={12} />
              ) : (
                <PauseCircle size={12} />
              )}
              {counts.paused ? t("activity.resumeQueue") : t("activity.pauseQueue")}
            </button>

            {/* Cancel All (only when ≥2 active tasks) */}
            {hasActiveTasks && (
              <button
                data-testid="activity-cancel-all"
                title={t("activity.cancelAll")}
                onClick={handleCancelAll}
                style={{ ...iconButtonStyle, fontSize: 11, gap: 4, padding: "3px 6px" }}
                aria-label={t("activity.cancelAll")}
              >
                <X size={12} />
                {t("activity.cancelAll")}
              </button>
            )}

            {/* Retry Failed */}
            {hasFailed && (
              <button
                data-testid="activity-retry-failed"
                title={t("activity.retryFailed")}
                onClick={handleRetryFailed}
                style={{ ...iconButtonStyle, fontSize: 11, gap: 4, padding: "3px 6px" }}
                aria-label={t("activity.retryFailed")}
              >
                <RotateCcw size={12} />
                {t("activity.retryFailed")}
              </button>
            )}
          </div>

          {/* Progress bar */}
          <div
            data-testid="activity-progress"
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <div
              style={{
                flex: 1,
                height: 4,
                borderRadius: 2,
                background: "var(--syn-border)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${pct}%`,
                  background: hasFailed ? "var(--syn-red)" : "var(--syn-accent)",
                  borderRadius: 2,
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <span style={{ fontSize: 10, color: "var(--syn-text-dim)", whiteSpace: "nowrap" }}>
              {t("activity.completedCount", {
                completed: counts.completed_since_idle,
                total: Math.max(1, counts.completed_since_idle + counts.pending + counts.processing),
              })}
            </span>
          </div>

          {/* Task list — empty state */}
          {tasks.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--syn-text-dim)", margin: 0, textAlign: "center", padding: "8px 0" }}>
              {t("activity.emptyQueue")}
            </p>
          )}

          {/* Processing tasks */}
          {processingTasks.map((tk) => (
            <TaskRow
              key={tk.run_id ?? tk.source_path}
              task={tk}
              isCancelling={tk.run_id !== undefined && cancellingIds.has(tk.run_id)}
              onCancel={handleTaskCancel}
              onRetry={handleTaskRetry}
            />
          ))}

          {/* Pending tasks */}
          {pendingTasks.map((tk, i) => (
            <TaskRow
              key={tk.run_id ?? `pending-${i}`}
              task={tk}
              isCancelling={tk.run_id !== undefined && cancellingIds.has(tk.run_id)}
              onCancel={handleTaskCancel}
              onRetry={handleTaskRetry}
            />
          ))}

          {/* Failed tasks (capped at MAX_VISIBLE_FAILED) */}
          {failedTasks.map((tk) => (
            <TaskRow
              key={tk.run_id ?? tk.source_path}
              task={tk}
              isCancelling={false}
              onCancel={handleTaskCancel}
              onRetry={handleTaskRetry}
            />
          ))}
          {counts.failed > MAX_VISIBLE_FAILED && (
            <p style={{ fontSize: 10, color: "var(--syn-text-dim)", margin: 0 }}>
              +{counts.failed - MAX_VISIBLE_FAILED} more failed tasks
            </p>
          )}
        </div>
      )}

      {/* ── Collapsed bar (28px) ─────────────────────────────────────────────── */}
      <footer
        className="activity-bar"
        aria-label="Activity bar"
        data-testid="activity-bar"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "0 12px",
          height: 28,
          background: "var(--syn-bg-soft)",
          borderTop: "1px solid var(--syn-border)",
          fontSize: 11,
          color: "var(--syn-text-dim)",
          flexShrink: 0,
          overflow: "hidden",
        }}
      >
        {/* Vault id */}
        <span
          aria-label={`Vault: ${vaultId}`}
          style={{ display: "flex", alignItems: "center", gap: 4 }}
        >
          <span aria-hidden="true" style={{ opacity: 0.5 }}>&#128193;</span>
          <span style={{ color: "var(--syn-text-muted)" }}>{vaultId}</span>
        </span>

        {/* Data version */}
        <span
          aria-label={`Data version: ${displayVersion ?? "unknown"}`}
          style={{ display: "flex", alignItems: "center", gap: 4 }}
        >
          <span aria-hidden="true" style={{ opacity: 0.5 }}>v</span>
          <span style={{ fontFamily: "monospace", color: "var(--syn-text-muted)" }}>
            {displayVersion ?? "–"}
          </span>
        </span>

        {/* Uptime */}
        {status.uptimeSeconds !== null && (
          <span
            aria-label={`Uptime: ${formatUptime(status.uptimeSeconds)}`}
            style={{ display: "flex", alignItems: "center", gap: 4 }}
          >
            <span aria-hidden="true" style={{ opacity: 0.5 }}>&#8679;</span>
            <span style={{ color: "var(--syn-text-dim)" }}>{formatUptime(status.uptimeSeconds)}</span>
          </span>
        )}

        {/* Connectivity indicator */}
        <span
          aria-label={pollError ? "Backend unreachable" : "Backend connected"}
          style={{ display: "flex", alignItems: "center", gap: 4 }}
        >
          <span
            aria-hidden="true"
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: pollError ? "var(--syn-red)" : "var(--syn-green)",
              display: "inline-block",
            }}
          />
        </span>

        {/* Spacer */}
        <span style={{ flex: 1 }} />

        {/* Queue status summary + toggle — left of provider indicator */}
        <button
          data-testid="activity-panel-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? t("activity.collapse") : t("activity.expand")}
          aria-expanded={expanded}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 5,
            color: hasFailed
              ? "var(--syn-red)"
              : isActive
              ? "var(--syn-accent)"
              : "var(--syn-text-dim)",
            fontSize: 11,
            padding: "0 4px",
          }}
        >
          {/* Status icon */}
          {isActive ? (
            <Loader2
              size={11}
              style={{ animation: "spin 1s linear infinite" }}
              aria-hidden="true"
            />
          ) : hasFailed ? (
            <AlertCircle size={11} aria-hidden="true" />
          ) : (
            <CheckCircle2 size={11} aria-hidden="true" />
          )}

          {/* Status text (only show when non-empty queue or paused) */}
          {(counts.total > 0 || counts.paused) && (
            <span
              style={{
                maxWidth: 160,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {statusText}
            </span>
          )}

          {/* Chevron */}
          {expanded ? (
            <ChevronDown size={11} aria-hidden="true" />
          ) : (
            <ChevronUp size={11} aria-hidden="true" />
          )}
        </button>

        {/* Active provider indicator (F17) */}
        <span
          aria-label={`Active provider: ${activeProvider?.provider_type ?? "none"}`}
          style={{
            color: activeProvider ? "var(--syn-text-muted)" : "var(--syn-text-dim)",
            cursor: "default",
          }}
        >
          {activeProvider
            ? `${activeProvider.provider_type}${activeProvider.model_id ? ` / ${activeProvider.model_id}` : ""}`
            : "–"}
        </span>
      </footer>

      {/* Keyframe for spinner (injected once via style tag) */}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
