/**
 * DeepSearchView.tsx — Deep Research section (F10, ADR-0024 §8, AC-F10-8).
 *
 * Layout:
 *   - Header with title
 *   - Topic input + "Start Research" button → POST /research/start → 202 {run_id}
 *   - Past runs list (TanStack Virtual, I4) — clicking a row shows its detail
 *   - Run detail panel (right side): status, iterations, cost, synthesis, sources
 *
 * INVARIANT I3: polling is a setTimeout chain (not setInterval), cleaned up on
 *   unmount and on terminal status. No per-frame heavy work. Zustand selectors +
 *   shallow equality. Markdown/synthesis NOT parsed during stream — displayed as
 *   pre-formatted text (no streaming here; polling yields finished text only).
 * INVARIANT I4: run list virtualised with TanStack Virtual.
 * INVARIANT I7: total_cost_usd shown at 4dp. Polling stops when terminal status
 *   is reached (converged / max_iter_reached / budget_exhausted / error).
 */

import {
  useEffect,
  useRef,
  useState,
  useCallback,
  type KeyboardEvent,
  type CSSProperties,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useResearchStore,
  selectResearchRuns,
  selectResearchTotal,
  selectResearchListLoading,
  selectResearchListError,
  selectSelectedRunId,
  selectResearchDetail,
  selectDetailLoading,
  selectDetailError,
  selectStarting,
  selectStartError,
  selectResearchRunningCount,
  selectFetchFreshResearch,
  selectFetchMoreResearch,
  selectSelectRun,
  selectStartRun,
  selectStartPollingDetail,
  selectClearStartError,
  isTerminal,
} from "../../store/researchStore";
import { selectVaultId, useAppStore } from "../../store/appStore";
import { useStatusStore, selectStatusDataVersion } from "../../store/statusStore";
import { Skeleton } from "../ui/Skeleton";
import { EmptyState } from "../common/EmptyState";
import { formatCost, formatRelativeTime } from "../ingest/IngestRunList";
import type { ResearchRunSummary, ResearchRunDetail, ResearchSource } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

const ROW_HEIGHT = 84;

// ─── Research status badge ────────────────────────────────────────────────────

const RESEARCH_STATUS_COLOR: Record<string, string> = {
  running: "var(--syn-accent)",
  converged: "var(--syn-green)",
  max_iter_reached: "var(--syn-amber)",
  budget_exhausted: "var(--syn-amber)",
  error: "var(--syn-red)",
};

// UXB-2 AC-UXB2-5: literal "white" replaced with var(--syn-mix-base) (dark-mode safe).
const RESEARCH_STATUS_BG: Record<string, string> = {
  running: "var(--syn-accent-soft)",
  converged: "color-mix(in srgb, var(--syn-green) 10%, var(--syn-mix-base) 90%)",
  max_iter_reached: "color-mix(in srgb, var(--syn-amber) 10%, var(--syn-mix-base) 90%)",
  budget_exhausted: "color-mix(in srgb, var(--syn-amber) 10%, var(--syn-mix-base) 90%)",
  error: "color-mix(in srgb, var(--syn-red) 10%, var(--syn-mix-base) 90%)",
};

interface ResearchStatusBadgeProps {
  status: string;
}

function ResearchStatusBadge({ status }: ResearchStatusBadgeProps) {
  const { t } = useTranslation();
  const labelKey = `research.status.${status}` as const;
  const label = t(labelKey as string, { defaultValue: status });
  const color = RESEARCH_STATUS_COLOR[status] ?? "var(--syn-text-dim)";
  const bg = RESEARCH_STATUS_BG[status] ?? "var(--syn-surface-hover)";

  const reducedMotion =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <span
      aria-label={label}
      data-status={status}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: 11,
        fontWeight: 600,
        color,
        background: bg,
        border: `1px solid color-mix(in srgb, ${color} 40%, transparent 60%)`,
        borderRadius: 10,
        padding: "2px 7px",
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          animation:
            status === "running" && !reducedMotion
              ? "synapse-pulse 1.4s ease-in-out infinite"
              : "none",
        }}
      />
      {label}
      {/* UXA-28: @keyframes synapse-pulse is declared globally in theme.css — no inline <style> needed */}
    </span>
  );
}

// ─── Run card (virtualised row) ───────────────────────────────────────────────

// t prop typed as key-only (no opts) — matches TFunction's simplest overload and
// avoids exactOptionalPropertyTypes incompatibility (opts?: Record is not assignable).
interface RunCardProps {
  run: ResearchRunSummary;
  selected: boolean;
  lang: string;
  style: CSSProperties;
  onClick: () => void;
  t: (key: string) => string;
}

function RunCard({ run, selected, lang, style, onClick, t }: RunCardProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-selected={selected}
      aria-label={`${t("research.runLabel")}: ${run.topic.slice(0, 60)}`}
      data-run-id={run.id}
      data-testid="research-run-card"
      style={{
        ...style,
        height: ROW_HEIGHT,
        padding: "8px 12px",
        background: selected ? "var(--syn-accent-soft)" : "transparent",
        borderBottom: "1px solid var(--syn-border)",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        outline: selected ? "1px solid var(--syn-accent)" : "none",
        outlineOffset: -1,
      }}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
    >
      {/* Row 1: status badge + cost */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <ResearchStatusBadge status={run.status} />
        <span style={{ fontSize: 12, color: "var(--syn-text-dim)", marginLeft: "auto" }}>
          {t("research.cost")}:{" "}
          <span style={{ fontFamily: "var(--syn-font-mono)", color: "var(--syn-text)" }}>
            {formatCost(run.total_cost_usd)}
          </span>
        </span>
      </div>

      {/* Row 2: topic (truncated) */}
      <div
        style={{
          fontSize: 12,
          color: "var(--syn-text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={run.topic}
      >
        {run.topic}
      </div>

      {/* Row 3: iterations + relative time */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("research.iterations")}: {run.iterations_used}
          {" · "}
          {t("research.sources")}: {run.sources_fetched}
        </span>
        <span
          style={{ fontSize: 11, color: "var(--syn-text-dim)", marginLeft: "auto", opacity: 0.7 }}
          title={run.started_at}
        >
          {formatRelativeTime(run.started_at, lang)}
        </span>
      </div>
    </div>
  );
}

// ─── Run detail panel ─────────────────────────────────────────────────────────

interface RunDetailPanelProps {
  runId: string | null;
  detail: ResearchRunDetail | null;
  loading: boolean;
  error: string | null;
}

function RunDetailPanel({ runId, detail, loading, error }: RunDetailPanelProps) {
  const { t } = useTranslation();

  if (runId === null) {
    return (
      <div
        data-testid="research-detail-empty"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--syn-text-dim)",
          fontSize: 13,
          padding: 24,
          textAlign: "center",
        }}
      >
        {t("research.noRunSelected")}
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <Skeleton height={24} width="60%" />
        <Skeleton height={16} width="40%" />
        <Skeleton height={80} />
        <Skeleton height={80} />
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" style={{ padding: 16, color: "var(--syn-red)", fontSize: 12 }}>
        {error}
      </div>
    );
  }

  if (!detail) return null;

  return (
    <div
      data-testid="research-run-detail"
      style={{
        height: "100%",
        overflow: "auto",
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {/* Topic */}
      <div>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
            wordBreak: "break-word",
          }}
        >
          {detail.topic}
        </p>
      </div>

      {/* Status + meta row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <ResearchStatusBadge status={detail.status} />
        <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("research.iterations")}:{" "}
          <strong style={{ color: "var(--syn-text)" }}>{detail.iterations_used}</strong>/
          {detail.max_iter}
        </span>
        <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("research.cost")}:{" "}
          <span style={{ fontFamily: "var(--syn-font-mono)", color: "var(--syn-text)" }}>
            {formatCost(detail.total_cost_usd)}
          </span>
        </span>
        <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("research.sources")}:{" "}
          <strong style={{ color: "var(--syn-text)" }}>{detail.sources_fetched}</strong>
        </span>
      </div>

      {/* Error message */}
      {detail.error_message && (
        <div role="alert" className="syn-section-notice syn-section-notice--danger">
          {detail.error_message}
        </div>
      )}

      {/* Synthesis page link */}
      {detail.synthesis_page_id && (
        <div className="syn-section-notice syn-section-notice--success">
          {t("research.wikiPageCreated")}
        </div>
      )}

      {/* Synthesis text */}
      {detail.synthesis_text && (
        <div>
          <p
            style={{
              margin: "0 0 6px",
              fontSize: 11,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            {t("research.synthesis")}
          </p>
          <pre
            data-testid="research-synthesis-text"
            style={{
              margin: 0,
              padding: "10px 12px",
              background: "var(--syn-surface-sunken)",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              fontSize: 12,
              color: "var(--syn-text)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 300,
              overflow: "auto",
              fontFamily: "var(--syn-font-mono)",
            }}
          >
            {detail.synthesis_text}
          </pre>
        </div>
      )}

      {/* Queries used */}
      {detail.queries_used.length > 0 && (
        <div>
          <p
            style={{
              margin: "0 0 6px",
              fontSize: 11,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            {t("research.queriesUsed")}
          </p>
          <ul
            style={{
              margin: 0,
              padding: "0 0 0 16px",
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {detail.queries_used.map((q, i) => (
              <li key={i} style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Sources list */}
      {detail.sources.length > 0 && (
        <div>
          <p
            style={{
              margin: "0 0 6px",
              fontSize: 11,
              fontWeight: 600,
              color: "var(--syn-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            {t("research.sourcesFetched")}
          </p>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              maxHeight: 240,
              overflow: "auto",
            }}
          >
            {detail.sources.map((src, i) => (
              <SourceRow key={i} source={src} t={t} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

interface SourceRowProps {
  source: ResearchSource;
  t: (key: string) => string;
}

function SourceRow({ source, t }: SourceRowProps) {
  return (
    <div
      style={{
        padding: "6px 10px",
        background: "var(--syn-surface-sunken)",
        border: "1px solid var(--syn-border)",
        borderRadius: 6,
        fontSize: 11,
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <a
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          color: "var(--syn-accent)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          display: "block",
        }}
        title={source.url}
      >
        {source.title ?? source.url}
      </a>
      <div style={{ display: "flex", gap: 8, color: "var(--syn-text-dim)" }}>
        <span>
          {t("research.iteration")}: {source.iteration}
        </span>
        {source.relevance_score !== null && (
          <span>
            {t("research.relevance")}: {source.relevance_score.toFixed(2)}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Run list (virtualised) ───────────────────────────────────────────────────

interface RunListProps {
  vaultId?: string;
}

function ResearchRunList({ vaultId }: RunListProps) {
  const { t, i18n } = useTranslation();
  const runs = useResearchStore(useShallow(selectResearchRuns));
  const total = useResearchStore(selectResearchTotal);
  const loading = useResearchStore(selectResearchListLoading);
  const selectedRunId = useResearchStore(selectSelectedRunId);
  const selectRun = useResearchStore(selectSelectRun);
  const fetchMore = useResearchStore(selectFetchMoreResearch);

  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: runs.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 5,
  });

  if (runs.length === 0 && loading) {
    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <Skeleton height={ROW_HEIGHT} />
        <Skeleton height={ROW_HEIGHT} />
        <Skeleton height={ROW_HEIGHT} />
      </div>
    );
  }

  if (runs.length === 0 && !loading) {
    return <EmptyState testId="research-run-list-empty" title={t("research.empty")} />;
  }

  const totalHeight = virtualizer.getTotalSize();
  const items = virtualizer.getVirtualItems();
  const hasMore = runs.length < total;

  return (
    <div
      ref={scrollRef}
      style={{ overflow: "auto", height: "100%", flex: 1, minHeight: 0 }}
      data-testid="research-run-list"
    >
      <div style={{ height: totalHeight + (hasMore ? 48 : 0), position: "relative" }}>
        {items.map((vRow) => {
          const run = runs[vRow.index];
          if (!run) return null;
          return (
            <RunCard
              key={run.id}
              run={run}
              selected={run.id === selectedRunId}
              lang={i18n.language}
              style={{ position: "absolute", top: vRow.start, width: "100%" }}
              onClick={() => void selectRun(run.id)}
              t={t}
            />
          );
        })}

        {hasMore && (
          <button
            onClick={() => void fetchMore(vaultId)}
            disabled={loading}
            style={{
              position: "absolute",
              top: totalHeight,
              left: 0,
              right: 0,
              height: 40,
              margin: "4px 12px",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              background: "var(--syn-bg-soft)",
              color: "var(--syn-text-muted)",
              fontSize: 12,
              cursor: loading ? "wait" : "pointer",
            }}
          >
            {loading ? t("common.loading") : t("research.loadMore")}
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Main DeepSearchView ─────────────────────────────────────────────────────

export function DeepSearchView() {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);

  // Store actions
  const fetchFresh = useResearchStore(selectFetchFreshResearch);
  const startRun = useResearchStore(selectStartRun);
  const startPollingDetail = useResearchStore(selectStartPollingDetail);
  const clearStartError = useResearchStore(selectClearStartError);

  // Store state
  const starting = useResearchStore(selectStarting);
  const startError = useResearchStore(selectStartError);
  const listError = useResearchStore(selectResearchListError);
  const listLoading = useResearchStore(selectResearchListLoading);
  const runningCount = useResearchStore(selectResearchRunningCount);
  const selectedRunId = useResearchStore(selectSelectedRunId);
  const detail = useResearchStore(selectResearchDetail);
  const detailLoading = useResearchStore(selectDetailLoading);
  const detailError = useResearchStore(selectDetailError);

  // Topic input state
  const [topic, setTopic] = useState("");

  // Polling cleanup ref
  const stopPollRef = useRef<(() => void) | null>(null);
  const topicInputRef = useRef<HTMLInputElement>(null);

  // SSE data version for fetch-on-event
  const dataVersion = useStatusStore(selectStatusDataVersion);

  // Initial fetch on mount
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchFresh(vaultId, ctrl.signal);
    return () => ctrl.abort();
  }, [vaultId, fetchFresh]);

  // Fetch-on-event: re-fetch the run list whenever a data_version bump arrives over SSE.
  // dataVersion is null on vault switch (resetForVault) — skip that tick.
  useEffect(() => {
    if (dataVersion === null) return;
    void fetchFresh(vaultId);
  }, [dataVersion, vaultId, fetchFresh]);

  // Start polling whenever selected run is "running"
  useEffect(() => {
    if (selectedRunId !== null && detail !== null && !isTerminal(detail.status)) {
      if (!stopPollRef.current) {
        stopPollRef.current = startPollingDetail(selectedRunId);
      }
    }
    if (detail !== null && isTerminal(detail.status) && stopPollRef.current) {
      stopPollRef.current();
      stopPollRef.current = null;
    }
  }, [selectedRunId, detail, startPollingDetail]);

  // Cleanup polling on unmount (I3)
  useEffect(() => {
    return () => {
      if (stopPollRef.current) {
        stopPollRef.current();
        stopPollRef.current = null;
      }
    };
  }, []);

  const handleStart = useCallback(async () => {
    const trimmed = topic.trim();
    if (!trimmed || starting) return;
    clearStartError();
    try {
      const runId = await startRun({ vault_id: vaultId ?? "default", topic: trimmed });
      setTopic("");
      // Start polling the new run
      stopPollRef.current?.();
      stopPollRef.current = startPollingDetail(runId);
    } catch {
      // error is in store (startError)
    }
  }, [topic, starting, vaultId, startRun, startPollingDetail, clearStartError]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") void handleStart();
    },
    [handleStart],
  );

  return (
    <div
      className="deep-search-view"
      data-testid="deep-search-view"
      style={{
        display: "flex",
        flex: 1,
        overflow: "hidden",
        width: "100%",
        height: "100%",
      }}
    >
      {/* ── Left pane: input + run list ────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
          background: "var(--syn-bg)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 16px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
            background: "var(--syn-bg-soft)",
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: 13,
              fontWeight: 600,
              color: "var(--syn-text)",
              flex: 1,
            }}
          >
            {t("research.title")}
            {runningCount > 0 && (
              <span
                aria-label={`${runningCount} running`}
                style={{
                  marginLeft: 8,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  minWidth: 18,
                  height: 18,
                  padding: "0 5px",
                  borderRadius: 9,
                  background: "var(--syn-accent)",
                  color: "#ffffff",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                {runningCount}
              </span>
            )}
          </h2>
        </div>

        {/* Topic input area */}
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
            background: "var(--syn-bg)",
          }}
        >
          <label
            htmlFor="research-topic-input"
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 500,
              color: "var(--syn-text-muted)",
              marginBottom: 4,
            }}
          >
            {t("research.topicLabel")}
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              ref={topicInputRef}
              id="research-topic-input"
              data-testid="research-topic-input"
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t("research.topicPlaceholder")}
              disabled={starting}
              style={{
                flex: 1,
                padding: "6px 10px",
                background: "var(--syn-surface)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                color: "var(--syn-text)",
                fontSize: 12,
                outline: "none",
              }}
            />
            <button
              data-testid="research-start-btn"
              onClick={() => void handleStart()}
              disabled={!topic.trim() || starting}
              aria-label={t("research.startButton")}
              style={{
                padding: "6px 16px",
                border: "none",
                borderRadius: 6,
                background:
                  !topic.trim() || starting ? "var(--syn-surface-hover)" : "var(--syn-accent)",
                color: !topic.trim() || starting ? "var(--syn-text-dim)" : "#ffffff",
                fontSize: 12,
                fontWeight: 600,
                cursor: !topic.trim() || starting ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {starting ? t("common.loading") : t("research.startButton")}
            </button>
          </div>
          <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
            {t("research.topicHint")}
          </p>

          {/* Start error */}
          {startError && (
            <div
              role="alert"
              style={{
                marginTop: 6,
                fontSize: 12,
                color: "var(--syn-red)",
              }}
            >
              {startError}
            </div>
          )}
        </div>

        {/* List error */}
        {listError && !listLoading && (
          <div
            role="alert"
            style={{
              padding: "8px 16px",
              borderBottom: "1px solid var(--syn-border)",
              flexShrink: 0,
              fontSize: 12,
              color: "var(--syn-red)",
              background: "color-mix(in srgb, var(--syn-red) 6%, var(--syn-mix-base) 94%)",
            }}
          >
            {listError}
            <button
              onClick={() => void fetchFresh(vaultId)}
              style={{
                marginLeft: 8,
                fontSize: 12,
                color: "var(--syn-text-muted)",
                background: "none",
                border: "none",
                cursor: "pointer",
                textDecoration: "underline",
                padding: 0,
              }}
            >
              {t("common.retry")}
            </button>
          </div>
        )}

        {/* Virtualised run list */}
        <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          <ResearchRunList vaultId={vaultId} />
        </div>
      </div>

      {/* ── Right pane: run detail ─────────────────────────────────────────── */}
      <div
        className="deep-search-view__detail"
        style={{
          width: 360,
          flexShrink: 0,
          overflow: "hidden",
          background: "var(--syn-bg-soft)",
          borderLeft: "1px solid var(--syn-border)",
        }}
      >
        <RunDetailPanel
          runId={selectedRunId}
          detail={detail}
          loading={detailLoading}
          error={detailError}
        />
      </div>
    </div>
  );
}
