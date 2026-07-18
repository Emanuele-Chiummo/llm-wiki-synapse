/**
 * ActiveJobsBlock.tsx — "LAVORI ATTIVI" section for HomeDashboard (A4 + WS-C) [F18].
 * Visible ONLY when at least one job is active. Fetches deep-research runs and
 * backfill-domain status ONCE on mount. Ingest counts come from activityStore via props.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 *
 * INVARIANT I3: no new polling intervals; existing activityStore is the sole ingest source.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import { fetchResearchRuns } from "../../api/researchClient";
import {
  getBackfillDomainStatus,
  type BackfillDomainStatus,
} from "../../api/statsClient";
import type { ResearchRunSummary } from "../../api/types";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface BatchProgress {
  running: boolean;
  done: number;
  total: number;
  eta_seconds: number | null;
}

export interface IngestTaskProgress {
  phase?: string | null;
  progress?: number | null;
  eta_seconds?: number | null;
}

export interface ActiveJobsBlockProps {
  /** Ingest counts come from activityStore already polled by ActivityBar — no new poller. */
  ingestProcessing: number;
  ingestPending: number;
  /**
   * WS-C [F3/F16]: Batch progress from activityStore (bulk "index all").
   * Null when no batch is running (single-file or idle mode).
   */
  ingestBatch: BatchProgress | null;
  /**
   * WS-C [F3/F16]: Processing tasks from activityStore for single-file aggregate.
   */
  ingestTasks: IngestTaskProgress[];
  /** v1.5.3: true while a synthesize run is in flight. */
  synthesizeRunning: boolean;
  onNavigateIngest: () => void;
  onNavigateResearch: () => void;
  onNavigateBackfill: () => void;
  onNavigateSynthesize: () => void;
}

// ─── JobRow ───────────────────────────────────────────────────────────────────

interface JobRowProps {
  icon: import("react").ReactNode;
  label: string;
  meta?: string | undefined;
  onClick: () => void;
  testId?: string | undefined;
}

function JobRow({ icon, label, meta, onClick, testId }: JobRowProps) {
  return (
    <button
      data-testid={testId}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        width: "100%",
        padding: "7px 10px",
        borderRadius: "var(--syn-radius-md)",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        textAlign: "left",
        transition: "background 0.1s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      <span
        style={{ flexShrink: 0, color: "var(--syn-accent)", display: "flex", alignItems: "center" }}
      >
        {icon}
      </span>
      <span
        style={{
          flex: 1,
          fontSize: 13,
          color: "var(--syn-text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      {meta && (
        <span style={{ fontSize: 11, color: "var(--syn-text-dim)", flexShrink: 0 }}>{meta}</span>
      )}
      <span style={{ fontSize: 11, color: "var(--syn-accent)", flexShrink: 0 }}>→</span>
    </button>
  );
}

// ─── ActiveJobsBlock ──────────────────────────────────────────────────────────

export function ActiveJobsBlock({
  ingestProcessing,
  ingestPending,
  ingestBatch,
  ingestTasks,
  synthesizeRunning,
  onNavigateIngest,
  onNavigateResearch,
  onNavigateBackfill,
  onNavigateSynthesize,
}: ActiveJobsBlockProps) {
  const { t } = useTranslation();

  const [runningResearch, setRunningResearch] = useState<ResearchRunSummary[]>([]);
  const [backfillStatus, setBackfillStatus] = useState<BackfillDomainStatus | null>(null);
  const [jobsLoading, setJobsLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

  const fetchJobStatus = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setJobsLoading(true);

    void (async () => {
      try {
        const [runsResult, backfillResult] = await Promise.all([
          fetchResearchRuns({ limit: 50 }, ac.signal).catch(() => null),
          getBackfillDomainStatus(ac.signal).catch(() => null),
        ]);
        if (ac.signal.aborted) return;

        const running = (runsResult?.items ?? []).filter((r) => r.status === "running");
        setRunningResearch(running);
        setBackfillStatus(backfillResult);
      } catch {
        if (ac.signal.aborted) return;
        setRunningResearch([]);
        setBackfillStatus(null);
      } finally {
        if (!ac.signal.aborted) setJobsLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    fetchJobStatus();
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [fetchJobStatus]);

  const hasIngest = ingestProcessing > 0 || ingestPending > 0;
  const hasResearch = runningResearch.length > 0;
  const hasBackfill = backfillStatus?.running === true;
  const hasSynthesize = synthesizeRunning;

  // ── WS-C [F3/F16]: Compute ingest progress values ──────────────────────────
  let ingestPct: number | null = null;
  let ingestEtaSeconds: number | null = null;
  let ingestDone: number | null = null;
  let ingestTotal: number | null = null;

  const clampPct = (n: number) => Math.min(100, Math.max(0, Math.round(n)));

  if (ingestBatch !== null && ingestBatch.total > 0) {
    ingestPct = clampPct((ingestBatch.done / ingestBatch.total) * 100);
    ingestDone = ingestBatch.done;
    ingestTotal = ingestBatch.total;
    ingestEtaSeconds = ingestBatch.eta_seconds;
  } else if (ingestTasks.length > 0) {
    const withProgress = ingestTasks.filter((tk) => tk.progress != null);
    if (withProgress.length > 0) {
      const avg =
        withProgress.reduce((sum, tk) => sum + (tk.progress ?? 0), 0) / withProgress.length;
      ingestPct = clampPct(avg * 100);
    }
    const etas = ingestTasks
      .filter((tk) => tk.eta_seconds != null)
      .map((tk) => tk.eta_seconds as number);
    ingestEtaSeconds = etas.length > 0 ? Math.min(...etas) : null;
  }

  const hasIngestProgress = ingestPct !== null;

  if (jobsLoading && !hasIngest) return null;
  if (!hasIngest && !hasResearch && !hasBackfill && !hasSynthesize) return null;

  return (
    <section
      aria-label={t("home.activeJobs.ariaLabel")}
      data-testid="home-active-jobs"
      style={{
        padding: "12px 14px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid color-mix(in srgb, var(--syn-accent) 25%, var(--syn-border) 75%)",
        background: "var(--syn-bg-soft)",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 2,
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={12} style={{ color: "var(--syn-accent)" }} aria-hidden="true" />
          <span className="syn-eyebrow">{t("home.activeJobs.title")}</span>
        </div>
        <button
          data-testid="home-active-jobs-refresh"
          onClick={fetchJobStatus}
          title={t("home.activeJobs.refresh")}
          aria-label={t("home.activeJobs.refresh")}
          style={{
            padding: 4,
            border: "none",
            background: "transparent",
            cursor: "pointer",
            color: "var(--syn-text-dim)",
            display: "flex",
            alignItems: "center",
          }}
        >
          <RefreshCw size={11} aria-hidden="true" />
        </button>
      </div>

      {/* Ingest row — WS-C: progress bar + ETA + done/total */}
      {hasIngest && (
        <div data-testid="home-active-jobs-ingest-wrapper">
          <JobRow
            testId="home-active-jobs-ingest"
            icon={<Loader2 size={12} />}
            label={t("home.activeJobs.ingest")}
            meta={
              ingestBatch !== null && ingestDone !== null && ingestTotal !== null
                ? t("home.activeJobs.ingestBatchCount", { done: ingestDone, total: ingestTotal })
                : ingestProcessing > 0 && ingestPending > 0
                  ? `${ingestProcessing} ${t("home.activeJobs.ingestProcessing")} · ${ingestPending} ${t("home.activeJobs.ingestPending")}`
                  : ingestProcessing > 0
                    ? `${ingestProcessing} ${t("home.activeJobs.ingestProcessing")}`
                    : `${ingestPending} ${t("home.activeJobs.ingestPending")}`
            }
            onClick={onNavigateIngest}
          />
          {hasIngestProgress && (
            <div style={{ padding: "0 10px 4px" }}>
              <div
                data-testid="home-active-jobs-ingest-progress-bar"
                style={{
                  height: 4,
                  borderRadius: 2,
                  background: "var(--syn-border)",
                  overflow: "hidden",
                }}
                role="progressbar"
                aria-valuenow={ingestPct ?? 0}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={t("home.activeJobs.ingestProgressLabel", { pct: ingestPct ?? 0 })}
              >
                <div
                  style={{
                    height: "100%",
                    width: `${ingestPct ?? 0}%`,
                    background: "var(--syn-accent)",
                    borderRadius: 2,
                    transition: "width 0.4s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 3 }}>
                <span
                  data-testid="home-active-jobs-ingest-pct"
                  style={{
                    fontSize: 10,
                    color: "var(--syn-text-muted)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {ingestPct}%
                </span>
                {ingestEtaSeconds !== null && (
                  <span
                    data-testid="home-active-jobs-ingest-eta"
                    style={{ fontSize: 10, color: "var(--syn-text-dim)" }}
                  >
                    {t("home.activeJobs.ingestEta", { eta: ingestEtaSeconds })}
                  </span>
                )}
              </div>
            </div>
          )}
          {ingestBatch === null && ingestTasks.length > 0 && (
            <div
              data-testid="home-active-jobs-ingest-phases"
              style={{ padding: "0 10px 4px", display: "flex", flexDirection: "column", gap: 1 }}
            >
              {ingestTasks.slice(0, 3).map((tk, idx) =>
                tk.phase ? (
                  <span key={idx} style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
                    {t(`activity.phase.${tk.phase}`, { defaultValue: tk.phase })}
                  </span>
                ) : null,
              )}
            </div>
          )}
        </div>
      )}

      {/* Deep Research running rows */}
      {runningResearch.map((run) => (
        <JobRow
          key={run.id}
          testId={`home-active-jobs-research-${run.id}`}
          icon={<Loader2 size={12} />}
          label={`${t("home.activeJobs.research")}: ${run.topic}`}
          onClick={onNavigateResearch}
        />
      ))}

      {/* Backfill domini row */}
      {hasBackfill && (
        <JobRow
          testId="home-active-jobs-backfill"
          icon={<Loader2 size={12} />}
          label={t("home.activeJobs.backfill")}
          meta={
            backfillStatus?.last_summary
              ? t("home.activeJobs.backfillTagged", {
                  count: backfillStatus.last_summary.tagged ?? 0,
                })
              : undefined
          }
          onClick={onNavigateBackfill}
        />
      )}

      {/* Sintesi/confronti row (v1.5.3) */}
      {hasSynthesize && (
        <JobRow
          testId="home-active-jobs-synthesize"
          icon={<Loader2 size={12} />}
          label={t("home.activeJobs.synthesize")}
          onClick={onNavigateSynthesize}
        />
      )}
    </section>
  );
}
