/**
 * IngestRunList.tsx — TanStack-Virtual virtualised list of ingest run cards (ADR-0018 §3).
 *
 * INVARIANT I4: at most ~40 DOM rows mounted regardless of history length.
 *               Uses @tanstack/react-virtual useVirtualizer.
 * INVARIANT I7: total_cost_usd shown on every row at exactly 4 decimal places ($0.0000).
 *
 * Each row shows: status badge, provider_type, pages_created, cost (4dp), relative time, truncated error.
 * Clicking a row calls setSelectedRunId — detail shows in IngestRunDetail (right pane).
 * "Load more" at the bottom triggers fetchMore for offset paging.
 */

import { useRef, useState, type CSSProperties } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useIngestStore,
  selectRuns,
  selectSelectedRunId,
  selectSetSelectedRunId,
  selectIngestTotal,
  selectIngestLoading,
  selectFetchMore,
} from "../../store/ingestStore";
import { StatusBadge } from "./StatusBadge";
import type { IngestRunItem } from "../../api/types";

// ─── Formatters ───────────────────────────────────────────────────────────────

/** Format cost at exactly 4 decimal places (I7). */
export function formatCost(usd: number): string {
  return `$${usd.toFixed(4)}`;
}

/** Format a UTC ISO string as "N minutes ago" using Intl.RelativeTimeFormat. */
export function formatRelativeTime(isoString: string, lang = "en"): string {
  const diff = (new Date(isoString).getTime() - Date.now()) / 1000; // negative = past
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });

  if (abs < 60)    return rtf.format(Math.round(diff), "second");
  if (abs < 3600)  return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

const ROW_HEIGHT = 80; // px per card row
const ERROR_TRUNCATE = 80;

// ─── Component ────────────────────────────────────────────────────────────────

interface IngestRunListProps {
  vaultId?: string;
}

export function IngestRunList({ vaultId }: IngestRunListProps) {
  const { t, i18n } = useTranslation();
  const runs = useIngestStore(useShallow(selectRuns));
  const total = useIngestStore(selectIngestTotal);
  const loading = useIngestStore(selectIngestLoading);
  const selectedRunId = useIngestStore(selectSelectedRunId);
  const setSelectedRunId = useIngestStore(selectSetSelectedRunId);
  const fetchMore = useIngestStore(selectFetchMore);

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
        {t("ingest.empty")}
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
      data-testid="ingest-run-list"
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
              onClick={() => setSelectedRunId(run.id)}
              t={t}
            />
          );
        })}

        {/* Load more button — below virtual content */}
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
            {loading ? t("common.loading") : t("ingest.loadMore")}
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Run card ─────────────────────────────────────────────────────────────────

interface RunCardProps {
  run: IngestRunItem;
  selected: boolean;
  lang: string;
  style: CSSProperties;
  onClick: () => void;
  t: (key: string) => string;
}

function RunCard({ run, selected, lang, style, onClick, t }: RunCardProps) {
  const [errorExpanded, setErrorExpanded] = useState(false);
  const hasError = Boolean(run.error_message);
  const errorText = run.error_message ?? "";
  const errorTruncated =
    errorText.length > ERROR_TRUNCATE ? errorText.slice(0, ERROR_TRUNCATE) + "…" : errorText;

  return (
    <div
      role="button"
      tabIndex={0}
      aria-selected={selected}
      aria-label={`Ingest run ${run.id.slice(0, 8)} — ${run.status}`}
      data-run-id={run.id}
      data-testid="ingest-run-card"
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
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClick(); }}
    >
      {/* Row 1: status + provider + cost */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <StatusBadge status={run.status} />
        <span style={{ fontSize: 12, color: "#8b949e" }}>
          {t(`provider.type.${run.provider_type}` as string) || run.provider_type}
        </span>
        <span style={{ fontSize: 12, color: "#6e7681", marginLeft: "auto" }}>
          {t("ingest.cost")}: <span style={{ fontFamily: "monospace", color: "#e6edf3" }}>{formatCost(run.total_cost_usd)}</span>
        </span>
      </div>

      {/* Row 2: pages created + relative time */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, color: "#6e7681" }}>
          {run.pages_created} {t("ingest.pagesCreated")}
        </span>
        <span style={{ fontSize: 11, color: "#484f58", marginLeft: "auto" }} title={run.started_at}>
          {formatRelativeTime(run.started_at, lang)}
        </span>
      </div>

      {/* Row 3: error (truncated, expandable) */}
      {hasError && (
        <div style={{ fontSize: 11, color: "#f85149", lineHeight: 1.3 }}>
          {errorExpanded ? errorText : errorTruncated}
          {errorText.length > ERROR_TRUNCATE && (
            <button
              onClick={(e) => { e.stopPropagation(); setErrorExpanded((v) => !v); }}
              style={{
                marginLeft: 4,
                fontSize: 10,
                color: "#8b949e",
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: 0,
                textDecoration: "underline",
              }}
            >
              {errorExpanded ? t("ingest.collapseError") : t("ingest.expandError")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
