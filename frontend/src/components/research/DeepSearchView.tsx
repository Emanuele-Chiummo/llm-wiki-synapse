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
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { formatCost, formatRelativeTime } from "../ingest/IngestRunList";
import type { ResearchRunSummary, ResearchRunDetail, ResearchSource } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

const ROW_HEIGHT = 84;

// ─── Research status badge ────────────────────────────────────────────────────

const RESEARCH_STATUS_COLOR: Record<string, string> = {
  running: "#1f6feb",
  converged: "#3fb950",
  max_iter_reached: "#d29922",
  budget_exhausted: "#d29922",
  error: "#f85149",
};

const RESEARCH_STATUS_BG: Record<string, string> = {
  running: "#1f6feb22",
  converged: "#3fb95022",
  max_iter_reached: "#d2992222",
  budget_exhausted: "#d2992222",
  error: "#f8514922",
};

interface ResearchStatusBadgeProps {
  status: string;
}

function ResearchStatusBadge({ status }: ResearchStatusBadgeProps) {
  const { t } = useTranslation();
  const labelKey = `research.status.${status}` as const;
  const label = t(labelKey as string, { defaultValue: status });
  const color = RESEARCH_STATUS_COLOR[status] ?? "#8b949e";
  const bg = RESEARCH_STATUS_BG[status] ?? "#8b949e22";

  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
        border: `1px solid ${color}4d`,
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
      <style>{`
        @keyframes synapse-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
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
        background: selected ? "#1f2937" : "transparent",
        borderBottom: "1px solid #21262d",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        outline: selected ? "1px solid #1f6feb" : "none",
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
        <span style={{ fontSize: 12, color: "#6e7681", marginLeft: "auto" }}>
          {t("research.cost")}:{" "}
          <span style={{ fontFamily: "monospace", color: "#e6edf3" }}>
            {formatCost(run.total_cost_usd)}
          </span>
        </span>
      </div>

      {/* Row 2: topic (truncated) */}
      <div
        style={{
          fontSize: 12,
          color: "#e6edf3",
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
        <span style={{ fontSize: 11, color: "#6e7681" }}>
          {t("research.iterations")}: {run.iterations_used}
          {" · "}
          {t("research.sources")}: {run.sources_fetched}
        </span>
        <span
          style={{ fontSize: 11, color: "#484f58", marginLeft: "auto" }}
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
          color: "#484f58",
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
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "#6e7681",
          fontSize: 13,
        }}
      >
        {t("common.loading")}
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        style={{ padding: 16, color: "#f85149", fontSize: 12 }}
      >
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
            color: "#e6edf3",
            wordBreak: "break-word",
          }}
        >
          {detail.topic}
        </p>
      </div>

      {/* Status + meta row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <ResearchStatusBadge status={detail.status} />
        <span style={{ fontSize: 12, color: "#6e7681" }}>
          {t("research.iterations")}: <strong style={{ color: "#e6edf3" }}>{detail.iterations_used}</strong>/{detail.max_iter}
        </span>
        <span style={{ fontSize: 12, color: "#6e7681" }}>
          {t("research.cost")}:{" "}
          <span style={{ fontFamily: "monospace", color: "#e6edf3" }}>
            {formatCost(detail.total_cost_usd)}
          </span>
        </span>
        <span style={{ fontSize: 12, color: "#6e7681" }}>
          {t("research.sources")}: <strong style={{ color: "#e6edf3" }}>{detail.sources_fetched}</strong>
        </span>
      </div>

      {/* Error message */}
      {detail.error_message && (
        <div
          role="alert"
          style={{
            padding: "8px 12px",
            background: "#1a0f0f",
            border: "1px solid #f8514933",
            borderRadius: 6,
            fontSize: 12,
            color: "#f85149",
          }}
        >
          {detail.error_message}
        </div>
      )}

      {/* Synthesis page link */}
      {detail.synthesis_page_id && (
        <div
          style={{
            padding: "6px 10px",
            background: "#122d1f",
            border: "1px solid #3fb95033",
            borderRadius: 6,
            fontSize: 12,
            color: "#3fb950",
          }}
        >
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
              color: "#8b949e",
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
              background: "#0d1117",
              border: "1px solid #21262d",
              borderRadius: 6,
              fontSize: 12,
              color: "#c9d1d9",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 300,
              overflow: "auto",
              fontFamily: "monospace",
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
              color: "#8b949e",
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
              <li key={i} style={{ fontSize: 12, color: "#8b949e" }}>
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
              color: "#8b949e",
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
        background: "#0d1117",
        border: "1px solid #21262d",
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
          color: "#58a6ff",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          display: "block",
        }}
        title={source.url}
      >
        {source.title ?? source.url}
      </a>
      <div style={{ display: "flex", gap: 8, color: "#484f58" }}>
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

  if (runs.length === 0 && !loading) {
    return (
      <div
        data-testid="research-run-list-empty"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "#484f58",
          fontSize: 13,
          padding: 24,
          textAlign: "center",
        }}
      >
        {t("research.empty")}
      </div>
    );
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
              border: "1px solid #21262d",
              borderRadius: 6,
              background: "#161b22",
              color: "#8b949e",
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
  const vaultId = useGraphStore(selectVaultId);

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

  // Initial fetch on mount
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchFresh(vaultId, ctrl.signal);
    return () => ctrl.abort();
  }, [vaultId, fetchFresh]);

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
          background: "#0d1117",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 16px",
            borderBottom: "1px solid #21262d",
            flexShrink: 0,
            background: "#161b22",
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: 13,
              fontWeight: 600,
              color: "#e6edf3",
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
                  background: "#1f6feb",
                  color: "#e6edf3",
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
            borderBottom: "1px solid #21262d",
            flexShrink: 0,
            background: "#0d1117",
          }}
        >
          <label
            htmlFor="research-topic-input"
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 500,
              color: "#8b949e",
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
                background: "#161b22",
                border: "1px solid #21262d",
                borderRadius: 6,
                color: "#e6edf3",
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
                background: !topic.trim() || starting ? "#21262d" : "#1f6feb",
                color: !topic.trim() || starting ? "#484f58" : "#e6edf3",
                fontSize: 12,
                fontWeight: 600,
                cursor: !topic.trim() || starting ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {starting ? t("common.loading") : t("research.startButton")}
            </button>
          </div>
          <p style={{ margin: "4px 0 0", fontSize: 11, color: "#484f58" }}>
            {t("research.topicHint")}
          </p>

          {/* Start error */}
          {startError && (
            <div
              role="alert"
              style={{
                marginTop: 6,
                fontSize: 12,
                color: "#f85149",
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
              borderBottom: "1px solid #21262d",
              flexShrink: 0,
              fontSize: 12,
              color: "#f85149",
              background: "#1a0f0f",
            }}
          >
            {listError}
            <button
              onClick={() => void fetchFresh(vaultId)}
              style={{
                marginLeft: 8,
                fontSize: 12,
                color: "#8b949e",
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
        style={{
          width: 360,
          flexShrink: 0,
          overflow: "hidden",
          background: "#161b22",
          borderLeft: "1px solid #21262d",
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
