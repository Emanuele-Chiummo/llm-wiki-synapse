/**
 * SystemStatusBlock.tsx — compact health strip for HomeDashboard (A2) [F18].
 * Fetches GET /health/detailed ONCE on mount; manual refresh icon.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, AlertTriangle, AlertCircle, RefreshCw } from "lucide-react";
import { getHealthDetailed, type DetailedHealth } from "../../api/healthClient";
import { formatUptime } from "./homeUtils";

// ─── Types ────────────────────────────────────────────────────────────────────

type ComponentKey =
  | "database"
  | "qdrant"
  | "watcher"
  | "ingest_queue"
  | "graph_cache"
  | "embeddings"
  | "import_scheduler";

const COMPONENT_KEYS: ComponentKey[] = [
  "database",
  "qdrant",
  "watcher",
  "ingest_queue",
  "graph_cache",
  "embeddings",
  "import_scheduler",
];

function getComponentStatus(
  health: DetailedHealth,
  key: ComponentKey,
): "ok" | "warn" | "down" | "skipped" {
  const comps = health.components;
  switch (key) {
    case "database": {
      return comps.database.ok === true ? "ok" : "down";
    }
    case "qdrant": {
      if (comps.qdrant.ok === "skipped") return "skipped";
      return comps.qdrant.ok === true ? "ok" : "down";
    }
    case "embeddings": {
      if (!comps.embeddings.enabled) return "skipped";
      if (comps.embeddings.ok === "skipped") return "skipped";
      return comps.embeddings.ok === true ? "ok" : "down";
    }
    case "watcher": {
      return comps.watcher.alive ? "ok" : "warn";
    }
    case "ingest_queue": {
      return comps.ingest_queue.paused ? "warn" : "ok";
    }
    case "graph_cache": {
      return comps.graph_cache.warm ? "ok" : "warn";
    }
    case "import_scheduler": {
      return comps.import_scheduler.last_error ? "warn" : "ok";
    }
  }
}

// ─── StatusDot ────────────────────────────────────────────────────────────────

function StatusDot({ status }: { status: "ok" | "warn" | "down" | "skipped" }) {
  const color =
    status === "ok"
      ? "var(--syn-green)"
      : status === "warn"
        ? "var(--syn-amber)"
        : status === "down"
          ? "var(--syn-error)"
          : "var(--syn-text-dim)";
  return (
    <span
      style={{
        display: "inline-block",
        width: 7,
        height: 7,
        borderRadius: "50%",
        background: color,
        flexShrink: 0,
      }}
      aria-hidden="true"
    />
  );
}

// ─── OverallStatusIcon ────────────────────────────────────────────────────────

function OverallStatusIcon({ status }: { status: "ok" | "degraded" | "error" }) {
  if (status === "ok") {
    return <CheckCircle2 size={14} style={{ color: "var(--syn-green)" }} aria-hidden="true" />;
  }
  if (status === "degraded") {
    return <AlertTriangle size={14} style={{ color: "var(--syn-amber)" }} aria-hidden="true" />;
  }
  return <AlertCircle size={14} style={{ color: "var(--syn-error)" }} aria-hidden="true" />;
}

// ─── SystemStatusBlock ────────────────────────────────────────────────────────

export interface SystemStatusBlockProps {
  activeProviderLabel: string | null;
  backendVersion: string | undefined;
  /** uptime_seconds from the /status poll (ActivityBar) — passed via prop to avoid a new fetch */
  statusUptimeSeconds: number | null;
  /** data_version from statusStore/graphStore */
  dataVersion: number | null;
}

export function SystemStatusBlock({
  activeProviderLabel,
  backendVersion,
  statusUptimeSeconds,
  dataVersion,
}: SystemStatusBlockProps) {
  const { t } = useTranslation();

  const [health, setHealth] = useState<DetailedHealth | null | "loading">("loading");
  const abortRef = useRef<AbortController | null>(null);

  const fetchHealth = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setHealth("loading");
    void (async () => {
      try {
        const result = await getHealthDetailed(ac.signal);
        if (!ac.signal.aborted) {
          setHealth(result);
        }
      } catch {
        if (!ac.signal.aborted) {
          setHealth(null);
        }
      }
    })();
  }, []);

  useEffect(() => {
    fetchHealth();
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [fetchHealth]);

  const overallStatus = health !== "loading" && health !== null ? health.status : null;

  return (
    <section
      aria-label={t("home.systemStatus.ariaLabel")}
      data-testid="home-system-status"
      style={{
        padding: "14px 16px",
        borderRadius: "var(--syn-radius-md)",
        border: `1px solid ${
          overallStatus === "error"
            ? "color-mix(in srgb, var(--syn-error) 30%, var(--syn-border) 70%)"
            : overallStatus === "degraded"
              ? "color-mix(in srgb, var(--syn-amber) 30%, var(--syn-border) 70%)"
              : "var(--syn-border)"
        }`,
        background: "var(--syn-bg-soft)",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Header row */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {overallStatus !== null && <OverallStatusIcon status={overallStatus} />}
          <span className="syn-eyebrow">{t("home.systemStatus.title")}</span>
          {overallStatus !== null && (
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t(
                `home.systemStatus.overall${overallStatus.charAt(0).toUpperCase()}${overallStatus.slice(1)}`,
              )}
            </span>
          )}
          {health === "loading" && (
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("home.systemStatus.loading")}
            </span>
          )}
        </div>
        <button
          data-testid="home-system-status-refresh"
          onClick={fetchHealth}
          title={t("home.systemStatus.refresh")}
          aria-label={t("home.systemStatus.refresh")}
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
          <RefreshCw size={12} aria-hidden="true" />
        </button>
      </div>

      {/* Meta strip: provider, version, uptime, data version */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "6px 20px",
          fontSize: 11,
          color: "var(--syn-text-muted)",
        }}
      >
        <span>
          <span style={{ color: "var(--syn-text-dim)" }}>{t("home.systemStatus.provider")}: </span>
          <span data-testid="home-status-provider">
            {activeProviderLabel ?? t("home.systemStatus.providerNone")}
          </span>
        </span>
        {backendVersion && backendVersion !== "dev" && (
          <span>
            <span style={{ color: "var(--syn-text-dim)" }}>{t("home.systemStatus.version")}: </span>
            <span data-testid="home-status-version">v{backendVersion}</span>
          </span>
        )}
        {statusUptimeSeconds !== null && (
          <span>
            <span style={{ color: "var(--syn-text-dim)" }}>{t("home.systemStatus.uptime")}: </span>
            <span data-testid="home-status-uptime">{formatUptime(statusUptimeSeconds)}</span>
          </span>
        )}
        {dataVersion !== null && (
          <span>
            <span style={{ color: "var(--syn-text-dim)" }}>
              {t("home.systemStatus.dataVersion")}:{" "}
            </span>
            <span data-testid="home-status-data-version">v{dataVersion}</span>
          </span>
        )}
      </div>

      {/* Component dots strip */}
      {health !== "loading" && health !== null && (
        <div
          data-testid="home-status-components"
          style={{ display: "flex", flexWrap: "wrap", gap: "5px 14px" }}
        >
          {COMPONENT_KEYS.map((key) => {
            const status = getComponentStatus(health, key);
            if (status === "skipped") return null;
            return (
              <span
                key={key}
                data-testid={`home-status-component-${key}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: 11,
                  color: "var(--syn-text-muted)",
                }}
              >
                <StatusDot status={status} />
                {t(`home.systemStatus.components.${key}`)}
              </span>
            );
          })}
        </div>
      )}
    </section>
  );
}
