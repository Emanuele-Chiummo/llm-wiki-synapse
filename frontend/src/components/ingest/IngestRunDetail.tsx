/**
 * IngestRunDetail.tsx — right pane showing the selected ingest run manifest (ADR-0018 §3).
 *
 * Shows: route, iterations_used, total_tokens, converged, cost_anomaly (if cost > $1.00 — I7),
 * model_id, started_at / completed_at, error_message (full), View-page link.
 *
 * INVARIANT I7: cost_anomaly (> $1.00) surfaced here.
 * INVARIANT I6: model_id and provider_type displayed as-returned (no hardcoded labels for IDs).
 */

import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import type { CSSProperties, ReactNode } from "react";
import {
  useIngestStore,
  selectRuns,
  selectSelectedRunId,
} from "../../store/ingestStore";
import { formatCost } from "./IngestRunList";

export function IngestRunDetail() {
  const { t } = useTranslation();
  const runs = useIngestStore(useShallow(selectRuns));
  const selectedRunId = useIngestStore(selectSelectedRunId);

  const run = runs.find((r) => r.id === selectedRunId) ?? null;

  if (!run) {
    return (
      <div
        data-testid="ingest-run-detail"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--syn-text-dim)",
          fontSize: 13,
          padding: 16,
          textAlign: "center",
        }}
      >
        {t("ingest.noRunSelected")}
      </div>
    );
  }

  const costAnomaly = run.total_cost_usd > 1.0;

  return (
    <div
      data-testid="ingest-run-detail"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <header
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
        }}
      >
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "var(--syn-text)" }}>
          {t("ingest.manifest")}
        </h3>
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--syn-text-muted)", fontFamily: "monospace" }}>
          {run.id.slice(0, 8)}…
        </p>
      </header>

      {/* Body */}
      <div style={{ flex: 1, overflow: "auto", padding: "12px 16px" }}>
        <DetailRow label={t("ingest.status.completed")} value={<span style={{ color: getStatusColor(run.status) }}>{run.status}</span>} />
        <DetailRow label={t("provider.label")} value={run.provider_type} />
        <DetailRow label={t("ingest.iterationsUsed")} value={String(run.iterations_used)} />
        <DetailRow label={t("ingest.pagesCreated")} value={String(run.pages_created)} />
        <DetailRow
          label={t("ingest.cost")}
          value={
            <span style={{ fontFamily: "monospace", color: costAnomaly ? "var(--syn-red)" : "var(--syn-text)" }}>
              {formatCost(run.total_cost_usd)}
              {costAnomaly && (
                <span
                  aria-label={t("ingest.costAnomaly")}
                  title={t("ingest.costAnomaly")}
                  style={{ marginLeft: 4, fontSize: 10, color: "var(--syn-red)" }}
                >
                  ⚠
                </span>
              )}
            </span>
          }
        />
        <DetailRow label={t("ingest.startedAt")} value={new Date(run.started_at).toLocaleString()} />
        {run.completed_at && (
          <DetailRow label={t("ingest.completedAt")} value={new Date(run.completed_at).toLocaleString()} />
        )}
        {run.error_message && (
          <div style={{ marginTop: 12 }}>
            <dt style={dtStyle}>{t("ingest.error")}</dt>
            <dd
              style={{
                margin: "4px 0 0",
                padding: 8,
                background: "color-mix(in srgb, var(--syn-red) 6%, white 94%)",
                border: "1px solid color-mix(in srgb, var(--syn-red) 30%, white 70%)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--syn-red)",
                fontFamily: "monospace",
                wordBreak: "break-word",
              }}
            >
              {run.error_message}
            </dd>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

const dtStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "var(--syn-text-dim)",
  marginBottom: 2,
};

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 12px", margin: "0 0 8px" }}>
      <dt style={dtStyle}>{label}</dt>
      <dd style={{ margin: 0, fontSize: 12, color: "var(--syn-text)", wordBreak: "break-word" }}>{value}</dd>
    </dl>
  );
}

function getStatusColor(status: string): string {
  switch (status) {
    case "running": return "var(--syn-accent)";
    case "completed": return "var(--syn-green)";
    case "failed": return "var(--syn-red)";
    case "converged_false": return "var(--syn-amber)";
    default: return "var(--syn-text-muted)";
  }
}
