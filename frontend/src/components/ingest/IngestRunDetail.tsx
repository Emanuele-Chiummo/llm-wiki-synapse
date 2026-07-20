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
import { useState, useCallback } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { IngestRunDiagnostics } from "../../api/types";
import {
  useIngestStore,
  selectRuns,
  selectSelectedRunId,
  selectRetryRun,
} from "../../store/ingestStore";
import { MaxRetriesExceededError } from "../../api/ingestClient";
import { showToast } from "../common/Toast";
import { formatCost } from "./IngestRunList";

export function IngestRunDetail({ vaultId }: { vaultId?: string } = {}) {
  const { t } = useTranslation();
  const runs = useIngestStore(useShallow(selectRuns));
  const selectedRunId = useIngestStore(selectSelectedRunId);
  const retryRun = useIngestStore(selectRetryRun);

  const [retrying, setRetrying] = useState(false);

  const run = runs.find((r) => r.id === selectedRunId) ?? null;

  const handleRetry = useCallback(async () => {
    if (!run) return;
    setRetrying(true);
    try {
      const res = await retryRun(run.id, vaultId);
      showToast(t("ingest.toastRetryQueued", { file: res.source_path }), "success");
    } catch (err: unknown) {
      if (err instanceof MaxRetriesExceededError) {
        showToast(t("ingest.toastRetryMaxRetries"), "error");
      } else {
        const detail = err instanceof Error ? err.message : t("common.unknown");
        showToast(t("ingest.toastRetryError", { detail }), "error");
      }
    } finally {
      setRetrying(false);
    }
  }, [run, retryRun, vaultId, t]);

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
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "var(--syn-text)" }}>
              {t("ingest.manifest")}
            </h3>
            <p
              style={{
                margin: "2px 0 0",
                fontSize: 11,
                color: "var(--syn-text-muted)",
                fontFamily: "var(--syn-font-mono)",
              }}
            >
              {run.id.slice(0, 8)}…
            </p>
          </div>
          {/* Retry button — shown only for retryable terminal states */}
          {(run.status === "converged_false" || run.status === "failed") && (
            <button
              data-testid="ingest-run-retry"
              onClick={() => void handleRetry()}
              disabled={retrying}
              aria-label={t("ingest.retryRun")}
              title={t("ingest.retryRun")}
              style={{
                background: "none",
                border: "1px solid var(--syn-border)",
                borderRadius: 4,
                padding: "4px 10px",
                cursor: retrying ? "wait" : "pointer",
                color: "var(--syn-accent)",
                fontSize: 12,
                fontWeight: 500,
                lineHeight: 1.4,
                flexShrink: 0,
                opacity: retrying ? 0.5 : 1,
              }}
            >
              {retrying ? "…" : t("ingest.retryRun")}
            </button>
          )}
        </div>
      </header>

      {/* Body */}
      <div style={{ flex: 1, overflow: "auto", padding: "12px 16px" }}>
        <DetailRow
          label={t("ingest.status.completed")}
          value={
            <span style={{ color: getStatusColor(run.status) }}>
              {t(
                `ingest.status.${run.status === "converged_false" ? "convergedFalse" : run.status}`,
                { defaultValue: run.status },
              )}
            </span>
          }
        />
        <DetailRow label={t("provider.label")} value={run.provider_type} />
        <DetailRow label={t("ingest.iterationsUsed")} value={String(run.iterations_used)} />
        <DetailRow label={t("ingest.pagesCreated")} value={String(run.pages_created)} />
        {run.page_type_counts &&
          Object.entries(run.page_type_counts).some(([, count]) => (count ?? 0) > 0) && (
            <div data-testid="ingest-page-type-counts" style={{ margin: "0 0 10px" }}>
              <dt style={dtStyle}>{t("ingest.typeDistribution")}</dt>
              <dd style={{ display: "flex", flexWrap: "wrap", gap: 5, margin: "4px 0 0" }}>
                {Object.entries(run.page_type_counts)
                  .filter(([, count]) => (count ?? 0) > 0)
                  .map(([pageType, count]) => (
                    <span
                      key={pageType}
                      style={{
                        border: "1px solid var(--syn-border)",
                        borderRadius: 999,
                        padding: "2px 7px",
                        fontSize: 11,
                        color: "var(--syn-text-muted)",
                      }}
                    >
                      {t(`nav.newPage.type.${pageType}`, { defaultValue: pageType })}: {count}
                    </span>
                  ))}
              </dd>
            </div>
          )}
        {/* UXA-06: contextual hint when a completed run produced no pages */}
        {run.pages_created === 0 && run.status === "completed" && (
          <div
            data-testid="ingest-zero-pages-hint"
            role="note"
            style={{
              marginBottom: 8,
              padding: "6px 10px",
              background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, transparent 70%)",
              borderRadius: 4,
              fontSize: 11,
              color: "var(--syn-text-muted)",
              lineHeight: 1.5,
            }}
          >
            {t("ingest.zeroPagesHint")}
          </div>
        )}
        <DetailRow
          label={t("ingest.cost")}
          value={
            <span
              style={{
                fontFamily: "var(--syn-font-mono)",
                color: costAnomaly ? "var(--syn-red)" : "var(--syn-text)",
              }}
            >
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
        <DetailRow
          label={t("ingest.startedAt")}
          value={new Date(run.started_at).toLocaleString()}
        />
        {run.completed_at && (
          <DetailRow
            label={t("ingest.completedAt")}
            value={new Date(run.completed_at).toLocaleString()}
          />
        )}
        {run.diagnostics && run.diagnostics.stop_reason !== "converged" && (
          <NonConvergenceDiagnostics diagnostics={run.diagnostics} />
        )}
        {run.error_message && (
          <div style={{ marginTop: 12 }}>
            <dt style={dtStyle}>{t("ingest.error")}</dt>
            <dd
              style={{
                margin: "4px 0 0",
                padding: 8,
                background: "color-mix(in srgb, var(--syn-red) 6%, var(--syn-mix-base) 94%)",
                border: "1px solid color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--syn-red)",
                fontFamily: "var(--syn-font-mono)",
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
    <dl
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        gap: "2px 12px",
        margin: "0 0 8px",
      }}
    >
      <dt style={dtStyle}>{label}</dt>
      <dd style={{ margin: 0, fontSize: 12, color: "var(--syn-text)", wordBreak: "break-word" }}>
        {value}
      </dd>
    </dl>
  );
}

/**
 * 1.9.1 W5 (NC-1): surfaces WHY a run didn't converge — the last iteration's validation
 * errors + tokens-used-vs-budget — instead of a bare "Non convergito" label.
 */
function NonConvergenceDiagnostics({ diagnostics }: { diagnostics: IngestRunDiagnostics }) {
  const { t } = useTranslation();
  return (
    <div data-testid="ingest-nonconvergence-diagnostics" style={{ marginTop: 12 }}>
      <div style={dtStyle}>{t("ingest.diagnostics.heading")}</div>
      <div
        style={{
          margin: "4px 0 0",
          padding: 8,
          background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
          border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, transparent 70%)",
          borderRadius: 4,
          fontSize: 11,
          color: "var(--syn-text-muted)",
          lineHeight: 1.5,
        }}
      >
        <DetailRow
          label={t("ingest.diagnostics.stopReason")}
          value={t(`ingest.diagnostics.stopReasonValue.${diagnostics.stop_reason}`, {
            defaultValue: diagnostics.stop_reason,
          })}
        />
        <DetailRow
          label={t("ingest.diagnostics.iterationsRun")}
          value={String(diagnostics.iterations)}
        />
        <DetailRow
          label={t("ingest.diagnostics.tokenBudget")}
          value={`${diagnostics.tokens_used} / ${diagnostics.token_budget}`}
        />
        <div style={{ marginTop: 6 }}>
          <div style={dtStyle}>{t("ingest.diagnostics.lastErrors")}</div>
          {diagnostics.last_errors.length === 0 ? (
            <div style={{ margin: "4px 0 0" }}>{t("ingest.diagnostics.noErrors")}</div>
          ) : (
            <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
              {diagnostics.last_errors.map((err, idx) => (
                <li key={idx} style={{ wordBreak: "break-word" }}>
                  {err}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function getStatusColor(status: string): string {
  switch (status) {
    case "running":
      return "var(--syn-accent)";
    case "completed":
      return "var(--syn-green)";
    case "failed":
      return "var(--syn-red)";
    case "converged_false":
      return "var(--syn-amber)";
    default:
      return "var(--syn-text-muted)";
  }
}
