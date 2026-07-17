/**
 * ReviewDeepResearchPanel.tsx — Persistent Deep Research right-panel for the Review view (R4, F10).
 *
 * Matches llm_wiki 0.6.0 review page: topic input + live task list on the right side.
 * REUSES researchStore + researchClient — no new store logic (INVARIANT I9 reuse).
 *
 * Behaviour:
 *   - Fetches the research run list (from researchStore) on mount.
 *   - Topic input + Enter/Start button → researchStore.startRun → appears in list.
 *   - lastResearchRunId prop: bumped when a per-item "Ricerca Profonda" in the review
 *     queue completes; triggers a refresh so the new run appears immediately in the list.
 *   - Empty state: "No research tasks yet" with instructions matching llm_wiki text.
 *   - Run rows: topic + status badge + relative time.
 *
 * INVARIANT I3: Zustand selectors + shallow equality. No per-token work here.
 * INVARIANT I7: startRun is bounded server-side; this panel just passes the topic.
 * INVARIANT I9: researchStore.startRun routes through the pluggable InferenceProvider.
 */

import { useState, useEffect, useCallback, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useResearchStore,
  selectResearchRuns,
  selectStartRun,
  selectStarting,
  selectFetchFreshResearch,
} from "../../store/researchStore";
import type { ResearchRunSummary } from "../../api/types";

// ─── Props ────────────────────────────────────────────────────────────────────

export interface ReviewDeepResearchPanelProps {
  vaultId: string;
  /**
   * The run_id of the most recently completed per-item deep-research action.
   * When this value changes the panel refreshes the run list so the new run
   * appears without the user having to navigate away.
   */
  lastResearchRunId: string | null;
  /** Present when the panel is hosted inside the shared responsive drawer. */
  onClose?: () => void;
}

// ─── Status colour map ────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  running: "var(--syn-accent)",
  converged: "var(--syn-green)",
  max_iter_reached: "var(--syn-amber)",
  budget_exhausted: "var(--syn-amber)",
  error: "var(--syn-red)",
};

// ─── Run row ──────────────────────────────────────────────────────────────────

interface RunRowProps {
  run: ResearchRunSummary;
  lang: string;
}

function RunRow({ run, lang }: RunRowProps) {
  const { t } = useTranslation();
  const relTime = (() => {
    try {
      const date = new Date(run.started_at);
      const diff = date.getTime() - Date.now();
      const formatter = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
      const mins = Math.round(diff / 60_000);
      if (Math.abs(mins) < 60) return formatter.format(mins, "minute");
      const hrs = Math.round(diff / 3_600_000);
      return Math.abs(hrs) < 24 ? formatter.format(hrs, "hour") : date.toLocaleDateString(lang);
    } catch {
      return "";
    }
  })();

  return (
    <div
      data-testid="review-dr-run-row"
      style={{
        padding: "8px 12px",
        borderBottom: "1px solid var(--syn-border)",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 500,
          color: "var(--syn-text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={run.topic}
      >
        {run.topic}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
        <span style={{ color: STATUS_COLOR[run.status] ?? "var(--syn-text-dim)", fontWeight: 500 }}>
          {t(`research.status.${run.status}`, { defaultValue: run.status })}
        </span>
        <span style={{ color: "var(--syn-text-dim)" }}>{relTime}</span>
      </div>
    </div>
  );
}

// ─── Panel ────────────────────────────────────────────────────────────────────

export function ReviewDeepResearchPanel({
  vaultId,
  lastResearchRunId,
  onClose,
}: ReviewDeepResearchPanelProps) {
  const { t, i18n } = useTranslation();
  const [topic, setTopic] = useState("");

  const runs = useResearchStore(useShallow(selectResearchRuns));
  const starting = useResearchStore(selectStarting);
  const startRun = useResearchStore(selectStartRun);
  const fetchFresh = useResearchStore(selectFetchFreshResearch);

  // Load research runs on mount
  useEffect(() => {
    void fetchFresh(vaultId);
    // fetchFresh is stable (store action); vaultId drives the query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vaultId]);

  // Refresh the run list whenever a per-item deep-research action completes.
  // lastResearchRunId is updated in reviewStore.lastDeepResearch — we receive
  // it as a prop to keep the panel decoupled from reviewStore directly.
  useEffect(() => {
    if (lastResearchRunId != null) {
      void fetchFresh(vaultId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastResearchRunId]);

  const handleSubmit = useCallback(() => {
    const trimmed = topic.trim();
    if (!trimmed || starting) return;
    void startRun({ vault_id: vaultId, topic: trimmed }).then(() => {
      setTopic("");
    });
  }, [topic, starting, startRun, vaultId]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") handleSubmit();
    },
    [handleSubmit],
  );

  return (
    <div
      data-testid="review-dr-panel"
      style={{
        width: onClose ? "100%" : 264,
        minWidth: onClose ? 0 : 180,
        borderLeft: onClose ? 0 : "1px solid var(--syn-border)",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        background: "var(--syn-bg-soft)",
        overflow: "hidden",
      }}
    >
      {/* ── Panel header ────────────────────────────────────────────────── */}
      <div
        style={{
          padding: "10px 12px 6px",
          borderBottom: "1px solid var(--syn-border)",
          fontSize: 13,
          fontWeight: 600,
          color: "var(--syn-text)",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span>{t("review.deepResearchPanel.panelTitle")}</span>
        {onClose && (
          <button
            type="button"
            className="syn-btn syn-btn--ghost syn-btn--sm"
            aria-label={t("review.deepResearchPanel.close")}
            onClick={onClose}
          >
            <X size={16} aria-hidden="true" />
          </button>
        )}
      </div>

      {/* ── Topic input + start button ───────────────────────────────────── */}
      <div
        style={{
          padding: "8px 10px",
          borderBottom: "1px solid var(--syn-border)",
          display: "flex",
          gap: 6,
          flexShrink: 0,
        }}
      >
        <input
          data-testid="review-dr-topic-input"
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t("review.deepResearchPanel.topicPlaceholder")}
          disabled={starting}
          aria-label={t("review.deepResearchPanel.topicPlaceholder")}
          style={{
            flex: 1,
            fontSize: 11,
            padding: "4px 6px",
            border: "1px solid var(--syn-border)",
            borderRadius: "var(--syn-radius-sm)",
            background: "var(--syn-bg)",
            color: "var(--syn-text)",
            outline: "none",
            minWidth: 0,
          }}
        />
        <button
          data-testid="review-dr-start-btn"
          onClick={handleSubmit}
          disabled={!topic.trim() || starting}
          aria-label={t("review.deepResearchPanel.startBtn")}
          aria-busy={starting}
          className="syn-btn syn-btn--secondary syn-btn--sm"
          style={{ flexShrink: 0, padding: "4px 8px", fontSize: 11 }}
        >
          {starting
            ? t("review.deepResearchPanel.starting")
            : t("review.deepResearchPanel.startBtn")}
        </button>
      </div>

      {/* ── Run list / empty state ───────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          minHeight: 0,
        }}
      >
        {runs.length === 0 ? (
          <div
            data-testid="review-dr-empty"
            style={{
              padding: "24px 16px",
              textAlign: "center",
              fontSize: 11,
              color: "var(--syn-text-dim)",
              lineHeight: 1.7,
            }}
          >
            {t("review.deepResearchPanel.noTasks")}
          </div>
        ) : (
          runs.map((run) => <RunRow key={run.id} run={run} lang={i18n.language} />)
        )}
      </div>
    </div>
  );
}
