/**
 * HomeDashboard.tsx — Home landing section [F18][R12-1][A2+A3+A4].
 *
 * Layout (A2+A3+A4 amendment order):
 *   1. "STATO DEL SISTEMA" block — compact health strip from GET /health/detailed,
 *      fetched ONCE on section mount (component-local, no polling; manual refresh icon).
 *      Shows: component dots (ok/warn/down) + active provider/model + backend version
 *      + uptime + data version. Status from statusStore (already polled by ActivityBar).
 *   2. "LAVORI ATTIVI" block — A4 new. Visible ONLY when at least one job is active.
 *      No empty shell rendered when nothing is running. Sources:
 *        - Ingest: activityStore snapshot (already polled by ActivityBar; no new poller).
 *        - Deep Research: fetched ONCE on mount; refreshed on block's refresh icon.
 *        - Backfill domini: GET /ops/backfill-domains fetched ONCE on mount; refreshed too.
 *        - Import scan: SKIPPED — importScheduleStore requires its own fetch (not
 *          pre-hydrated at home mount); wiring a new fetch/poller would violate I3.
 *   3. KPI row (existing — keep).
 *   4. Curated domain sections "SEZIONI" — from GET /stats/sections.
 *      Rendered ONLY when vocabulary has entries; empty vocab → small hint + Settings link.
 *   5. "GRUPPI AUTOMATICI" grid — from GET /stats/groups.
 *      A4: TOP 4 rendered by default (ordered by pages_total DESC as delivered by server).
 *      If more exist, an "Espandi (N)" / "Comprimi" toggle reveals/hides the rest.
 *      Toggle state is component-local, default collapsed. aria-expanded on the button.
 *      Click → opens group's top page in Wiki (setActiveSection("pages") + localStorage
 *      slug key). 404 → block hidden silently.
 *   6. Recent activity (existing — keep, last).
 *
 * Group-click behavior: clicking a group card navigates to the Wiki section and writes
 * the top page's slug to localStorage key "synapse:groupTopPageSlug". This matches the
 * cheapest feasible mechanism: community-id filtering is not yet supported by the tree/
 * search filter, so we open the group's most-connected page — a useful proxy for the
 * group's content. This choice is documented here per AC instructions.
 *
 * INVARIANT I3: no heavy per-render work; stats + health fetched ONCE on mount, no polling.
 *               activityStore is already polled by ActivityBar — no new intervals added here.
 *               Deep-research and backfill status refresh only on mount + manual refresh icon.
 * INVARIANT I4: recent-activity capped at 10, sections/groups capped — no virtualisation.
 * INVARIANT I2: no graph layout runs here; communities_count read from /stats/overview.
 * No charting library imported — type bars are plain inline SVG.
 *
 * Design tokens: var(--syn-accent), var(--syn-border), var(--syn-bg-soft),
 * var(--syn-text-muted), var(--syn-text-dim), var(--syn-radius-md),
 * var(--syn-surface-sunken), var(--syn-surface-hover).
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  FileText,
  Link2,
  Users,
  ClipboardList,
  AlertTriangle,
  DollarSign,
  Database,
  Clock,
  Settings,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import {
  getStatsOverview,
  getStatsSections,
  getStatsGroups,
  getBackfillDomainStatus,
  type StatsOverview,
  type StatsSections,
  type SectionEntry,
  type StatsGroups,
  type StatsGroup,
  type BackfillDomainStatus,
} from "../../api/statsClient";
import { fetchResearchRuns } from "../../api/researchClient";
import { fetchCostsSummary } from "../../api/costsClient";
import type { ResearchRunSummary } from "../../api/types";
import { getHealthDetailed, type DetailedHealth } from "../../api/healthClient";
import {
  useGraphStore,
  selectSetActiveSection,
} from "../../store/graphStore";
import { useProviderStore, selectActiveProvider } from "../../store/providerStore";
import {
  useStatusStore,
  selectBackendVersion,
  selectStatusDataVersion,
} from "../../store/statusStore";
import { useActivityCounts, useActivityBatch, useActivityTasks } from "../../store/activityStore";

// ─── Constants ─────────────────────────────────────────────────────────────────

/** localStorage key used to pass a domain filter to the Wiki/NavTree section. */
const DOMAIN_FILTER_KEY = "synapse:domainFilter";

/**
 * localStorage key used to pass the Louvain community id filter to the Wiki/NavTree.
 * NavTree filters the page list to pages whose community column matches this id.
 */
const GROUP_FILTER_KEY = "synapse:groupFilter";

/**
 * localStorage key for the human-readable label shown in the NavTree filter banner.
 * Written alongside DOMAIN_FILTER_KEY or GROUP_FILTER_KEY so the banner has a label
 * without a second data fetch.
 */
const NAV_FILTER_LABEL_KEY = "synapse:navFilterLabel";

/** Custom event dispatched after writing filter keys so a mounted NavTree re-reads them. */
const NAV_FILTER_EVENT = "synapse:navFilter";

// ─── Helpers ───────────────────────────────────────────────────────────────────

function formatCost(usd: number): string {
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatUptime(s: number | undefined | null): string {
  if (s == null) return "–";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ─── Plain SVG mini-bar for type breakdown ────────────────────────────────────

interface TypeBarProps {
  pagesByType: Record<string, number>;
  total: number;
}

/**
 * Single source of truth for page-type colour: the same --syn-type-* tokens the
 * wiki type badges and the graph use. Previously this component carried its OWN
 * ad-hoc hex map (entity=green, concept=blue…) that disagreed with those tokens,
 * so one page type read as different colours across the app. Now they match.
 */
export function typeColor(type: string): string {
  const known = new Set([
    "concept",
    "entity",
    "source",
    "synthesis",
    "comparison",
    "query",
    "overview",
  ]);
  return known.has(type) ? `var(--syn-type-${type})` : "var(--syn-type-other)";
}

function TypeBar({ pagesByType, total }: TypeBarProps) {
  if (total === 0) return null;
  const entries = Object.entries(pagesByType).filter(([, count]) => count > 0);
  return (
    // HTML flex bar (not SVG) so segments carry a real 2px surface gap and their
    // own rounded ends — the dataviz "gap between fills" spec — and colour resolves
    // from CSS tokens directly.
    <div
      aria-hidden="true"
      style={{ display: "flex", gap: 2, height: 6, width: "100%" }}
    >
      {entries.map(([type, count]) => (
        <div
          key={type}
          style={{
            flexGrow: count,
            flexBasis: 0,
            minWidth: 2,
            background: typeColor(type),
            borderRadius: 2,
          }}
        />
      ))}
    </div>
  );
}

// ─── Skeleton ──────────────────────────────────────────────────────────────────

/** A single shimmering placeholder block (see .syn-skeleton in theme.css). */
function Skeleton({
  width,
  height,
  radius = 8,
}: {
  width?: number | string;
  height: number | string;
  radius?: number;
}) {
  return (
    <div
      className="syn-skeleton"
      aria-hidden="true"
      style={{ width: width ?? "100%", height, borderRadius: radius }}
    />
  );
}

// ─── Sparkline ─────────────────────────────────────────────────────────────────

/**
 * Tiny inline trend line for a KPI (e.g. daily cost over the last 30 days).
 * Stretched to the card width; non-scaling stroke keeps the line crisp.
 */
function Sparkline({ values, color = "var(--syn-accent)" }: { values: number[]; color?: string }) {
  if (values.length < 2) return null;
  const W = 100;
  const H = 22;
  const PAD = 1.5;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const stepX = (W - PAD * 2) / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = PAD + i * stepX;
    const y = PAD + (H - PAD * 2) * (1 - (v - min) / range);
    return [x, y] as const;
  });
  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1] ?? [0, 0];
  const first = pts[0] ?? [0, 0];
  const area = `${line} L${last[0].toFixed(1)} ${H} L${first[0].toFixed(1)} ${H} Z`;
  return (
    <svg
      width="100%"
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      style={{ display: "block", overflow: "visible" }}
    >
      <path d={area} fill={color} opacity={0.1} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last[0]} cy={last[1]} r={2} fill={color} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// ─── System Status Block ───────────────────────────────────────────────────────

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
      // Scheduler is informational — not critical
      return comps.import_scheduler.last_error ? "warn" : "ok";
    }
  }
}

function StatusDot({ status }: { status: "ok" | "warn" | "down" | "skipped" }) {
  const color =
    status === "ok"
      ? "#22c55e"
      : status === "warn"
        ? "#f59e0b"
        : status === "down"
          ? "var(--syn-error, #ef4444)"
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

function OverallStatusIcon({ status }: { status: "ok" | "degraded" | "error" }) {
  if (status === "ok") {
    return <CheckCircle2 size={14} style={{ color: "#22c55e" }} aria-hidden="true" />;
  }
  if (status === "degraded") {
    return <AlertTriangle size={14} style={{ color: "#f59e0b" }} aria-hidden="true" />;
  }
  return <AlertCircle size={14} style={{ color: "var(--syn-error, #ef4444)" }} aria-hidden="true" />;
}

interface SystemStatusBlockProps {
  activeProviderLabel: string | null;
  backendVersion: string | undefined;
  /** uptime_seconds from the /status poll (ActivityBar) — passed via prop to avoid a new fetch */
  statusUptimeSeconds: number | null;
  /** data_version from statusStore/graphStore */
  dataVersion: number | null;
}

function SystemStatusBlock({
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
      const result = await getHealthDetailed(ac.signal);
      if (!ac.signal.aborted) {
        setHealth(result);
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
            ? "color-mix(in srgb, var(--syn-error, #ef4444) 30%, var(--syn-border) 70%)"
            : overallStatus === "degraded"
              ? "color-mix(in srgb, #f59e0b 30%, var(--syn-border) 70%)"
              : "var(--syn-border)"
        }`,
        background: "var(--syn-bg-soft)",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {overallStatus !== null && <OverallStatusIcon status={overallStatus} />}
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--syn-text-muted)",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            {t("home.systemStatus.title")}
          </span>
          {overallStatus !== null && (
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t(`home.systemStatus.overall${overallStatus.charAt(0).toUpperCase()}${overallStatus.slice(1)}`)}
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
            <span style={{ color: "var(--syn-text-dim)" }}>{t("home.systemStatus.dataVersion")}: </span>
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
                style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--syn-text-muted)" }}
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

// ─── Active Jobs Block (A4 + WS-C) ───────────────────────────────────────────

interface BatchProgress {
  running: boolean;
  done: number;
  total: number;
  eta_seconds: number | null;
}

interface IngestTaskProgress {
  phase?: string | null;
  progress?: number | null;
  eta_seconds?: number | null;
}

interface ActiveJobsBlockProps {
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
   * Used when batch is null to show per-task phase and aggregate ETA.
   */
  ingestTasks: IngestTaskProgress[];
  onNavigateIngest: () => void;
  onNavigateResearch: () => void;
  onNavigateBackfill: () => void;
}

/**
 * JobRow — one row in the active-jobs list.
 * icon: small indicator (spinner when running, or other icon).
 * label: truncated text describing the job.
 * meta: optional secondary info (counts, summary).
 * onClick: navigates to the relevant section.
 */
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
      <span style={{ flexShrink: 0, color: "var(--syn-accent)", display: "flex", alignItems: "center" }}>
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
        <span style={{ fontSize: 11, color: "var(--syn-text-dim)", flexShrink: 0 }}>
          {meta}
        </span>
      )}
      <span style={{ fontSize: 11, color: "var(--syn-accent)", flexShrink: 0 }}>→</span>
    </button>
  );
}

/**
 * ActiveJobsBlock — renders the "LAVORI ATTIVI" section.
 *
 * Fetches deep-research running runs and backfill-domain status ONCE on mount
 * (plus on manual refresh). Ingest counts are passed as props from activityStore
 * (already polled by ActivityBar — I3 compliant, no new interval).
 *
 * Renders nothing (returns null) when no job is active.
 * [F18][A4]
 */
function ActiveJobsBlock({
  ingestProcessing,
  ingestPending,
  ingestBatch,
  ingestTasks,
  onNavigateIngest,
  onNavigateResearch,
  onNavigateBackfill,
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

  // Build the list of active job rows
  const hasIngest = ingestProcessing > 0 || ingestPending > 0;
  const hasResearch = runningResearch.length > 0;
  const hasBackfill = backfillStatus?.running === true;

  // ── WS-C [F3/F16]: Compute ingest progress values ──────────────────────────
  // Batch mode (bulk "index all"): use batch.done/total/eta_seconds directly.
  // Single-file mode (no batch): aggregate tasks[] progress for an overall %.
  // I3: pure arithmetic from existing store data — no heavy computation per render.

  let ingestPct: number | null = null;
  let ingestEtaSeconds: number | null = null;
  let ingestDone: number | null = null;
  let ingestTotal: number | null = null;

  // Clamp any computed percentage into [0, 100] — defends against a task.progress that
  // arrives already scaled (0..100) or a transient over-count in batch.done.
  const clampPct = (n: number) => Math.min(100, Math.max(0, Math.round(n)));

  if (ingestBatch !== null && ingestBatch.total > 0) {
    // Batch mode: use the dedicated batch counters.
    ingestPct = clampPct((ingestBatch.done / ingestBatch.total) * 100);
    ingestDone = ingestBatch.done;
    ingestTotal = ingestBatch.total;
    ingestEtaSeconds = ingestBatch.eta_seconds;
  } else if (ingestTasks.length > 0) {
    // Single-file mode: average progress across processing tasks that report progress.
    const withProgress = ingestTasks.filter((tk) => tk.progress != null);
    if (withProgress.length > 0) {
      const avg = withProgress.reduce((sum, tk) => sum + (tk.progress ?? 0), 0) / withProgress.length;
      ingestPct = clampPct(avg * 100);
    }
    // ETA: minimum non-null eta_seconds across processing tasks (best-case remaining).
    const etas = ingestTasks.filter((tk) => tk.eta_seconds != null).map((tk) => tk.eta_seconds as number);
    ingestEtaSeconds = etas.length > 0 ? Math.min(...etas) : null;
  }

  const hasIngestProgress = ingestPct !== null;

  // Nothing active yet (still loading) → don't flash an empty block
  if (jobsLoading && !hasIngest) return null;

  // Nothing active at all → render nothing
  if (!hasIngest && !hasResearch && !hasBackfill) return null;

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
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2, justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={12} style={{ color: "var(--syn-accent)" }} aria-hidden="true" />
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--syn-text-muted)",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            {t("home.activeJobs.title")}
          </span>
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

      {/* Ingest row — WS-C: progress bar + ETA + done/total (AC-WS-C-1/2/3/5) */}
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
          {/* WS-C AC-WS-C-1: overall progress bar — pure CSS, no canvas (I3) */}
          {hasIngestProgress && (
            <div
              style={{ padding: "0 10px 4px" }}
            >
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
              {/* WS-C AC-WS-C-1/2: percentage + ETA */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 3 }}>
                <span
                  data-testid="home-active-jobs-ingest-pct"
                  style={{ fontSize: 10, color: "var(--syn-text-muted)", fontVariantNumeric: "tabular-nums" }}
                >
                  {ingestPct}%
                </span>
                {/* WS-C AC-WS-C-2: ETA hidden when null */}
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
          {/* WS-C AC-WS-C-3: per-task phase labels (single-file mode, no batch) */}
          {ingestBatch === null && ingestTasks.length > 0 && (
            <div
              data-testid="home-active-jobs-ingest-phases"
              style={{ padding: "0 10px 4px", display: "flex", flexDirection: "column", gap: 1 }}
            >
              {ingestTasks.slice(0, 3).map((tk, idx) =>
                tk.phase ? (
                  <span
                    key={idx}
                    style={{ fontSize: 10, color: "var(--syn-text-dim)" }}
                  >
                    {/* Reuse existing activity.phase.* i18n keys (AC-WS-C-3).
                        Falls back to raw phase string for unknown phases (e.g. "generating (2/3)"). */}
                    {t(`activity.phase.${tk.phase}`, { defaultValue: tk.phase })}
                  </span>
                ) : null,
              )}
            </div>
          )}
        </div>
      )}

      {/* Deep Research running rows — one per running run (topic truncated) */}
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
          // meta must be a STRING — passing the raw last_summary object crashed React
          // ("Objects are not valid as a React child", owner report v1.2.1).
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
    </section>
  );
}

// ─── KPI card ─────────────────────────────────────────────────────────────────

interface KpiCardProps {
  icon: import("react").ReactNode;
  label: string;
  value: string | number;
  accent?: boolean;
  testId?: string;
  /** When set the card becomes a real navigation control (button + hover + focus ring). */
  onClick?: () => void;
  /** Optional trend series rendered as a sparkline under the value. */
  sparkline?: number[] | undefined;
}

function KpiCard({ icon, label, value, accent, testId, onClick, sparkline }: KpiCardProps) {
  const baseStyle: import("react").CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    padding: "12px 14px",
    borderRadius: "var(--syn-radius-md)",
    border: `1px solid ${accent ? "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)" : "var(--syn-border)"}`,
    background: accent ? "var(--syn-accent-soft)" : "var(--syn-bg-soft)",
    boxShadow: "var(--syn-shadow-soft)",
    minWidth: 0,
    flex: "1 1 110px",
  };

  const body = (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: accent ? "var(--syn-accent)" : "var(--syn-text-dim)", flexShrink: 0 }}>
          {icon}
        </span>
        <span style={{ fontSize: 11, color: "var(--syn-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
        </span>
      </div>
      <span
        style={{
          fontSize: 22,
          fontWeight: 700,
          color: accent ? "var(--syn-accent)" : "var(--syn-text)",
          lineHeight: 1,
          letterSpacing: "-0.02em",
        }}
      >
        {value}
      </span>
      {sparkline && sparkline.length >= 2 && (
        <div style={{ marginTop: 4 }}>
          <Sparkline values={sparkline} />
        </div>
      )}
    </>
  );

  // Non-interactive by default: a plain <div> so the card never *looks* clickable
  // unless it actually navigates somewhere.
  if (!onClick) {
    return (
      <div data-testid={testId ?? `kpi-${label}`} style={baseStyle}>
        {body}
      </div>
    );
  }

  // Interactive: a real <button> — cursor, hover, and the global :focus-visible ring.
  return (
    <button
      type="button"
      data-testid={testId ?? `kpi-${label}`}
      onClick={onClick}
      aria-label={`${label}: ${value}`}
      style={{ ...baseStyle, cursor: "pointer", textAlign: "left", transition: "border-color 0.12s ease, background 0.12s ease" }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = accent
          ? "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)"
          : "var(--syn-border)";
      }}
    >
      {body}
    </button>
  );
}

// ─── Section card ─────────────────────────────────────────────────────────────

interface SectionCardProps {
  section: SectionEntry;
  onNavigate: (domain: string) => void;
}

function SectionCard({ section, onNavigate }: SectionCardProps) {
  const { t } = useTranslation();
  const isUntagged = section.domain === "untagged";
  const typeEntries = Object.entries(section.pages_by_type);

  return (
    <button
      data-testid={`section-card-${section.domain}`}
      onClick={() => onNavigate(section.domain)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "14px 16px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid var(--syn-border)",
        background: isUntagged ? "var(--syn-surface-sunken)" : "var(--syn-bg-soft)",
        boxShadow: "var(--syn-shadow-soft)",
        cursor: "pointer",
        textAlign: "left",
        transition: "border-color 0.12s ease, background 0.12s ease",
        width: "100%",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
        (e.currentTarget as HTMLButtonElement).style.background = isUntagged
          ? "var(--syn-surface-sunken)"
          : "var(--syn-bg-soft)";
      }}
    >
      {/* Domain name + page count */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, justifyContent: "space-between" }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: isUntagged ? "var(--syn-text-muted)" : "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {isUntagged ? t("home.sections.untaggedLabel") : section.domain}
        </span>
        <span
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: isUntagged ? "var(--syn-text-muted)" : "var(--syn-accent)",
            flexShrink: 0,
          }}
        >
          {section.pages_total}
        </span>
      </div>

      {/* Type mini-bar */}
      {section.pages_total > 0 && (
        <TypeBar pagesByType={section.pages_by_type} total={section.pages_total} />
      )}

      {/* Type breakdown text */}
      {typeEntries.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 8px" }}>
          {typeEntries.map(([type, count]) => (
            <span
              key={type}
              style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--syn-text-dim)" }}
            >
              <span
                aria-hidden="true"
                style={{ width: 6, height: 6, borderRadius: 2, background: typeColor(type), flexShrink: 0 }}
              />
              {count} {type}
            </span>
          ))}
        </div>
      )}

      {/* Last activity */}
      {section.last_activity && (
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <Clock size={10} aria-hidden="true" style={{ color: "var(--syn-text-dim)", flexShrink: 0 }} />
          <span style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
            {formatDate(section.last_activity)}
          </span>
        </div>
      )}

      {/* Top pages */}
      {section.top_pages.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
          {section.top_pages.slice(0, 3).map((p) => (
            <span key={p.id} style={{ fontSize: 10, color: "var(--syn-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {p.title}
            </span>
          ))}
        </div>
      )}

      {/* Navigate hint */}
      <span style={{ fontSize: 10, color: "var(--syn-accent)", marginTop: 2 }}>
        {t("home.sections.filterHint")} →
      </span>
    </button>
  );
}

// ─── Group card ────────────────────────────────────────────────────────────────

interface GroupCardProps {
  group: StatsGroup;
  onOpen: (group: StatsGroup) => void;
}

function GroupCard({ group, onOpen }: GroupCardProps) {
  const { t } = useTranslation();
  const typeEntries = Object.entries(group.pages_by_type);
  const topPage = group.top_pages[0];

  return (
    <button
      data-testid={`group-card-${group.community}`}
      onClick={() => onOpen(group)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "14px 16px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid var(--syn-border)",
        background: "var(--syn-bg-soft)",
        boxShadow: "var(--syn-shadow-soft)",
        cursor: "pointer",
        textAlign: "left",
        transition: "border-color 0.12s ease, background 0.12s ease",
        width: "100%",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-bg-soft)";
      }}
    >
      {/* Label + page count */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, justifyContent: "space-between" }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {group.label}
        </span>
        <span style={{ fontSize: 16, fontWeight: 700, color: "var(--syn-accent)", flexShrink: 0 }}>
          {group.pages_total}
          <span style={{ fontSize: 10, fontWeight: 400, color: "var(--syn-text-dim)", marginLeft: 2 }}>
            {t("home.groups.pages")}
          </span>
        </span>
      </div>

      {/* Type mini-bar */}
      {group.pages_total > 0 && (
        <TypeBar pagesByType={group.pages_by_type} total={group.pages_total} />
      )}

      {/* Type breakdown text */}
      {typeEntries.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 8px" }}>
          {typeEntries.map(([type, count]) => (
            <span
              key={type}
              style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--syn-text-dim)" }}
            >
              <span
                aria-hidden="true"
                style={{ width: 6, height: 6, borderRadius: 2, background: typeColor(type), flexShrink: 0 }}
              />
              {count} {type}
            </span>
          ))}
        </div>
      )}

      {/* Top page (highest degree) — informational only; click browses ALL group members */}
      {topPage ? (
        <div style={{ fontSize: 10, color: "var(--syn-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <span style={{ color: "var(--syn-text-dim)" }}>{t("home.groups.topPage")}: </span>
          {topPage.title}
        </div>
      ) : (
        <div style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
          {t("home.groups.noTopPages")}
        </div>
      )}

      {/* Browse hint */}
      <span style={{ fontSize: 10, color: "var(--syn-accent)", marginTop: 2 }}>
        {t("home.groups.browseHint")} →
      </span>

      {/* Last activity */}
      {group.last_activity && (
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <Clock size={10} aria-hidden="true" style={{ color: "var(--syn-text-dim)", flexShrink: 0 }} />
          <span style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
            {formatDate(group.last_activity)}
          </span>
        </div>
      )}
    </button>
  );
}

// ─── HomeDashboard ─────────────────────────────────────────────────────────────

/** Number of groups to show by default before "Espandi" toggle (A4). */
const GROUPS_DEFAULT_CAP = 4;

export function HomeDashboard() {
  const { t } = useTranslation();
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const activeProvider = useProviderStore(selectActiveProvider);
  const backendVersion = useStatusStore(selectBackendVersion);

  // WS-A [F16/F4/F18]: subscribe to data_version from the ActivityBar's existing
  // GET /status poll. When it changes, re-fetch stats (overview + sections + groups).
  // INVARIANT I3: no render occurs on every tick — only when the version value changes.
  // No new poller introduced; the ActivityBar's STATUS_POLL_MS (30s) is the sole driver.
  const statusDataVersion = useStatusStore(selectStatusDataVersion);

  // Ingest counts from activityStore — already polled by ActivityBar; no new poller (I3).
  const { processing: ingestProcessing, pending: ingestPending } = useActivityCounts();

  // WS-C [F3/F16]: batch progress and task list for ingest progress bar (AC-WS-C-1/2/3).
  const ingestBatch = useActivityBatch();
  const allTasks = useActivityTasks();
  // Filter to processing tasks only — these carry phase/progress/eta (AC-WS-C-3).
  const ingestTasks = allTasks.filter((tk) => tk.status === "processing");

  // A4 — expand/collapse state for GRUPPI AUTOMATICI (component-local, default collapsed).
  const [groupsExpanded, setGroupsExpanded] = useState(false);

  // Derive active provider label (type + model) — informational display
  const activeProviderLabel = activeProvider
    ? [activeProvider.provider_type, activeProvider.model_id].filter(Boolean).join(" / ")
    : null;

  const [overview, setOverview] = useState<StatsOverview | null | undefined>(undefined);
  const [sections, setSections] = useState<StatsSections | null | undefined>(undefined);
  const [groups, setGroups] = useState<StatsGroups | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Daily-cost series for the monthly-cost KPI sparkline (last 30 days). Fetched
  // separately and non-blocking: a costs error must never break the dashboard.
  const [costByDay, setCostByDay] = useState<number[] | null>(null);

  // Track the last data_version for which we successfully fetched stats.
  // WS-A: only re-fetch when the version advances — no spurious re-renders (I3).
  const lastFetchedVersionRef = useRef<number | null>(null);
  // Ref to the current statusDataVersion so the async fetch callback can read
  // the latest value without it being a dep of the useCallback (stable ref pattern).
  const statusDataVersionRef = useRef<number | null>(statusDataVersion);
  statusDataVersionRef.current = statusDataVersion;

  // Stable fetch function — called on mount and on dataVersion change.
  // Does not need statusDataVersion in its deps: it reads from the ref.
  const loadStats = useCallback((signal: AbortSignal) => {
    setLoadError(null);
    void (async () => {
      try {
        const [ov, sec, grp] = await Promise.all([
          getStatsOverview(signal),
          getStatsSections(signal),
          getStatsGroups(signal),
        ]);
        if (signal.aborted) return;
        setOverview(ov);
        setSections(sec);
        setGroups(grp);
        // Record the version we just fetched against (AC-WS-A-3: no re-fetch on
        // same-version tick). If statusDataVersion is null (backend pre-WS-A or
        // first poll not yet received), store -1 as sentinel so we don't loop.
        lastFetchedVersionRef.current = statusDataVersionRef.current ?? -1;
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") return;
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    })();
  }, []); // Stable: reads state via refs, no reactive deps needed.

  // Initial fetch on mount.
  useEffect(() => {
    const ac = new AbortController();
    loadStats(ac.signal);
    return () => ac.abort();
  }, [loadStats]);

  // Daily cost series for the sparkline — fetch once on mount, best-effort.
  useEffect(() => {
    const ac = new AbortController();
    void (async () => {
      try {
        const summary = await fetchCostsSummary(null, ac.signal);
        if (ac.signal.aborted) return;
        setCostByDay(summary.by_day.map((d) => d.total_usd));
      } catch {
        // Non-fatal: leave the KPI without a sparkline.
      }
    })();
    return () => ac.abort();
  }, []);

  // WS-A [AC-WS-A-1, AC-WS-A-3]: re-fetch stats when data_version bumps.
  // Guard: skip if version hasn't changed from last fetch, skip initial null.
  // I3 compliance: no re-render on same-version tick.
  useEffect(() => {
    if (statusDataVersion === null) return;
    if (statusDataVersion === lastFetchedVersionRef.current) return;
    // Version has advanced — re-fetch stats without a full page reload (AC-WS-A-1).
    const ac = new AbortController();
    loadStats(ac.signal);
    return () => ac.abort();
  }, [statusDataVersion, loadStats]); // Only re-run when the polled version changes.

  // Section card click: write domain filter to localStorage, clear group filter,
  // dispatch event so a mounted NavTree re-reads immediately, then switch section.
  const handleSectionNavigate = useCallback(
    (domain: string) => {
      try {
        if (domain === "untagged") {
          // "untagged" means no domain filter — clear both filters entirely
          localStorage.removeItem(DOMAIN_FILTER_KEY);
          localStorage.removeItem(NAV_FILTER_LABEL_KEY);
        } else {
          localStorage.setItem(DOMAIN_FILTER_KEY, domain);
          localStorage.setItem(NAV_FILTER_LABEL_KEY, domain);
        }
        // Always clear the competing group filter when navigating by domain
        localStorage.removeItem(GROUP_FILTER_KEY);
      } catch {
        // localStorage may be unavailable in some environments — non-fatal
      }
      window.dispatchEvent(new Event(NAV_FILTER_EVENT));
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  // Group card click: write the community id filter to localStorage, clear the
  // domain filter, dispatch event so a mounted NavTree re-reads, then switch section.
  // The NavTree will show only pages belonging to this Louvain community.
  const handleGroupOpen = useCallback(
    (group: StatsGroup) => {
      try {
        localStorage.setItem(GROUP_FILTER_KEY, String(group.community));
        localStorage.setItem(
          NAV_FILTER_LABEL_KEY,
          group.label || `Group ${group.community}`,
        );
        // Always clear the competing domain filter when navigating by group
        localStorage.removeItem(DOMAIN_FILTER_KEY);
      } catch {
        // non-fatal
      }
      window.dispatchEvent(new Event(NAV_FILTER_EVENT));
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  // Recent activity: click → open that page in wiki section
  const handleActivityClick = useCallback(
    (_slug: string) => {
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  // ── Loading state ──────────────────────────────────────────────────────────
  if (overview === undefined) {
    return (
      <div
        data-testid="home-dashboard-loading"
        aria-busy="true"
        aria-label={t("common.loading")}
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 24,
          padding: "28px 32px 48px",
          width: "100%",
          boxSizing: "border-box",
        }}
      >
        {/* Title */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Skeleton width={220} height={24} radius={6} />
          <Skeleton width={300} height={14} radius={4} />
        </div>
        {/* System status */}
        <Skeleton height={72} radius={8} />
        {/* KPI grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10 }}>
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} height={78} radius={8} />
          ))}
        </div>
        {/* Section cards */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} height={150} radius={8} />
          ))}
        </div>
      </div>
    );
  }

  // ── 404 placeholder (v1.1 backend without stats endpoints) ────────────────
  if (overview === null) {
    return (
      <div
        data-testid="home-dashboard-placeholder"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
          padding: "60px 32px",
          textAlign: "center",
          maxWidth: 480,
          margin: "0 auto",
        }}
      >
        <Database size={32} aria-hidden="true" style={{ color: "var(--syn-text-dim)", opacity: 0.5 }} />
        <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--syn-text)" }}>
          {t("home.placeholder.title")}
        </p>
        <p style={{ margin: 0, fontSize: 13, color: "var(--syn-text-muted)", lineHeight: 1.6 }}>
          {t("home.placeholder.body")}
        </p>
      </div>
    );
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <div
        data-testid="home-dashboard-error"
        style={{ padding: 40, color: "var(--syn-error, #ef4444)", fontSize: 13 }}
      >
        {loadError}
      </div>
    );
  }

  const sectionList = sections?.sections ?? [];
  const hasVocabSections = sectionList.some((s) => s.domain !== "untagged");
  const groupList = groups?.groups ?? [];

  return (
    <div
      data-testid="home-dashboard"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 32,
        padding: "28px 32px 48px",
        // Full-width dashboard (owner request, v1.2.1): no max-width cap — the KPI
        // and section grids are responsive (auto-fill minmax) and use the space.
        width: "100%",
        boxSizing: "border-box",
        overflowY: "auto",
        height: "100%",
      }}
    >
      {/* ── Header ── */}
      <div>
        <h1
          style={{ margin: 0, fontSize: 20, fontWeight: 700, color: "var(--syn-text)" }}
        >
          {t("home.title")}
        </h1>
        <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--syn-text-muted)" }}>
          {t("home.subtitle")}
        </p>
      </div>

      {/* ── 1. System Status block (A2) ── */}
      <SystemStatusBlock
        activeProviderLabel={activeProviderLabel}
        backendVersion={backendVersion}
        statusUptimeSeconds={null}
        dataVersion={overview.data_version}
      />

      {/* ── 2. Active Jobs block (A4 + WS-C) — only rendered when something is running ── */}
      <ActiveJobsBlock
        ingestProcessing={ingestProcessing}
        ingestPending={ingestPending}
        ingestBatch={ingestBatch}
        ingestTasks={ingestTasks}
        onNavigateIngest={() => setActiveSection("ingest")}
        onNavigateResearch={() => setActiveSection("deep-search")}
        onNavigateBackfill={() => setActiveSection("settings")}
      />

      {/* ── 3. KPI row ── */}
      <section aria-label={t("home.kpi.ariaLabel")}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
            gap: 10,
          }}
        >
          <KpiCard
            testId="kpi-pages-total"
            icon={<FileText size={14} aria-hidden="true" />}
            label={t("home.kpi.pagesTotal")}
            value={overview.pages_total}
          />
          <KpiCard
            testId="kpi-links-total"
            icon={<Link2 size={14} aria-hidden="true" />}
            label={t("home.kpi.linksTotal")}
            value={overview.links_total}
          />
          <KpiCard
            testId="kpi-communities"
            icon={<Users size={14} aria-hidden="true" />}
            label={t("home.kpi.communities")}
            value={overview.communities_count}
          />
          <KpiCard
            testId="kpi-review-pending"
            icon={<ClipboardList size={14} aria-hidden="true" />}
            label={t("home.kpi.reviewPending")}
            value={overview.review_pending}
            accent={overview.review_pending > 0}
            onClick={() => setActiveSection("review")}
          />
          <KpiCard
            testId="kpi-lint-open"
            icon={<AlertTriangle size={14} aria-hidden="true" />}
            label={t("home.kpi.lintOpen")}
            value={overview.lint_open}
            accent={overview.lint_open > 0}
            onClick={() => setActiveSection("lint")}
          />
          <KpiCard
            testId="kpi-monthly-cost"
            icon={<DollarSign size={14} aria-hidden="true" />}
            label={t("home.kpi.monthlyCost")}
            value={formatCost(overview.monthly_cost_usd)}
            sparkline={costByDay ?? undefined}
          />
          <KpiCard
            testId="kpi-data-version"
            icon={<Database size={14} aria-hidden="true" />}
            label={t("home.kpi.dataVersion")}
            value={`v${overview.data_version}`}
          />
        </div>
      </section>

      {/* ── 4. Curated domain sections "SEZIONI" ── */}
      {sections !== null && (
        <section aria-label={t("home.sections.ariaLabel")}>
          <h2
            style={{
              margin: "0 0 12px",
              fontSize: 11,
              fontWeight: 700,
              color: "var(--syn-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
            }}
          >
            {t("home.sections.title")}
          </h2>

          {/* No vocabulary configured → small hint only (A2: no prominent placeholder) */}
          {!hasVocabSections && (
            <div
              data-testid="home-sections-empty"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "10px 14px",
                borderRadius: "var(--syn-radius-md)",
                border: "1px dashed var(--syn-border)",
                background: "var(--syn-surface-sunken)",
              }}
            >
              <span style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
                {t("home.sections.emptyVocabHint")}
              </span>
              <button
                data-testid="home-sections-go-settings"
                onClick={() => setActiveSection("settings")}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 8px",
                  borderRadius: "var(--syn-radius-md)",
                  border: "1px solid var(--syn-border)",
                  background: "transparent",
                  color: "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                <Settings size={10} aria-hidden="true" />
                {t("home.sections.goSettings")}
              </button>
            </div>
          )}

          {/* Section grid — only when vocabulary has entries */}
          {hasVocabSections && (
            <div
              data-testid="home-sections-grid"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
                gap: 12,
              }}
            >
              {sectionList.map((sec) => (
                <SectionCard
                  key={sec.domain}
                  section={sec}
                  onNavigate={handleSectionNavigate}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── 5. "GRUPPI AUTOMATICI" grid (A3+A4) — hidden when groups is null (404) ── */}
      {/* A4: top 4 shown by default; Espandi/Comprimi toggle reveals the full capped list. */}
      {groups !== null && groups !== undefined && groupList.length > 0 && (
        <section
          aria-label={t("home.groups.ariaLabel")}
          data-testid="home-groups-section"
        >
          {/* Section header row: title + expand/collapse toggle */}
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, justifyContent: "space-between" }}>
            <h2
              style={{
                margin: 0,
                fontSize: 11,
                fontWeight: 700,
                color: "var(--syn-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
              }}
            >
              {t("home.groups.title")}
            </h2>

            {/* Expand/collapse toggle — only rendered when there are more than GROUPS_DEFAULT_CAP groups */}
            {groupList.length > GROUPS_DEFAULT_CAP && (
              <button
                data-testid="home-groups-toggle"
                aria-expanded={groupsExpanded}
                onClick={() => setGroupsExpanded((prev) => !prev)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 10px",
                  borderRadius: "var(--syn-radius-md)",
                  border: "1px solid var(--syn-border)",
                  background: "transparent",
                  color: "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: "pointer",
                  flexShrink: 0,
                  transition: "border-color 0.1s ease",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
                }}
              >
                {groupsExpanded ? (
                  <>
                    <ChevronUp size={11} aria-hidden="true" />
                    {t("home.groups.collapse")}
                  </>
                ) : (
                  <>
                    <ChevronDown size={11} aria-hidden="true" />
                    {t("home.groups.expand", { count: groupList.length - GROUPS_DEFAULT_CAP })}
                  </>
                )}
              </button>
            )}
          </div>

          <div
            data-testid="home-groups-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {(groupsExpanded ? groupList : groupList.slice(0, GROUPS_DEFAULT_CAP)).map((grp) => (
              <GroupCard
                key={grp.community}
                group={grp}
                onOpen={handleGroupOpen}
              />
            ))}
          </div>
        </section>
      )}

      {/* ── 6. Recent activity (last) ── */}
      <section aria-label={t("home.activity.ariaLabel")}>
        <h2
          style={{
            margin: "0 0 12px",
            fontSize: 11,
            fontWeight: 700,
            color: "var(--syn-text-muted)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          {t("home.activity.title")}
        </h2>
        {overview.recent_activity.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: "var(--syn-text-dim)" }}>
            {t("home.activity.empty")}
          </p>
        ) : (
          <ul
            data-testid="home-recent-activity"
            style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}
          >
            {overview.recent_activity.map((item) => {
              // A page can be persisted before its title is generated; fall back to a
              // muted placeholder so the row never renders as a blank icon + date.
              const label = item.title.trim();
              return (
              <li key={item.page_id}>
                <button
                  data-testid={`home-activity-item-${item.slug}`}
                  onClick={() => handleActivityClick(item.slug)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    width: "100%",
                    padding: "6px 10px",
                    borderRadius: 6,
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
                  <FileText size={12} aria-hidden="true" style={{ color: "var(--syn-text-dim)", flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: 13, color: label ? "var(--syn-text)" : "var(--syn-text-dim)", fontStyle: label ? "normal" : "italic", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {label || t("home.activity.untitled")}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--syn-text-dim)", flexShrink: 0 }}>
                    {formatDate(item.updated_at)}
                  </span>
                </button>
              </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
