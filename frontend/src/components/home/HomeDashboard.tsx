/**
 * HomeDashboard.tsx — Home landing section [F18][R12-1][A2+A3].
 *
 * Layout (A2+A3 amendment order):
 *   1. "STATO DEL SISTEMA" block — compact health strip from GET /health/detailed,
 *      fetched ONCE on section mount (component-local, no polling; manual refresh icon).
 *      Shows: component dots (ok/warn/down) + active provider/model + backend version
 *      + uptime + data version. Status from statusStore (already polled by ActivityBar).
 *   2. KPI row (existing — keep).
 *   3. Curated domain sections "SEZIONI" — from GET /stats/sections.
 *      Rendered ONLY when vocabulary has entries; empty vocab → small hint + Settings link.
 *   4. NEW "GRUPPI AUTOMATICI" grid — from GET /stats/groups.
 *      Card per group (label, pages_total, type mini-breakdown, top pages, last activity).
 *      Click → opens group's top page in Wiki (setActiveSection("pages") + localStorage
 *      slug key). 404 → block hidden silently.
 *   5. Recent activity (existing — keep, last).
 *
 * Group-click behavior: clicking a group card navigates to the Wiki section and writes
 * the top page's slug to localStorage key "synapse:groupTopPageSlug". This matches the
 * cheapest feasible mechanism: community-id filtering is not yet supported by the tree/
 * search filter, so we open the group's most-connected page — a useful proxy for the
 * group's content. This choice is documented here per AC instructions.
 *
 * INVARIANT I3: no heavy per-render work; stats + health fetched ONCE on mount, no polling.
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
} from "lucide-react";
import {
  getStatsOverview,
  getStatsSections,
  getStatsGroups,
  type StatsOverview,
  type StatsSections,
  type SectionEntry,
  type StatsGroups,
  type StatsGroup,
} from "../../api/statsClient";
import { getHealthDetailed, type DetailedHealth } from "../../api/healthClient";
import {
  useGraphStore,
  selectSetActiveSection,
} from "../../store/graphStore";
import { useProviderStore, selectActiveProvider } from "../../store/providerStore";
import { useStatusStore, selectBackendVersion } from "../../store/statusStore";

// ─── Constants ─────────────────────────────────────────────────────────────────

/** localStorage key used to pass a domain filter to the Wiki/NavTree section. */
const DOMAIN_FILTER_KEY = "synapse:domainFilter";

/**
 * localStorage key used to pass the top-page slug of a clicked group to the Wiki section.
 * Group-click mechanism: community-id tree filtering is not yet supported, so clicking
 * a group card opens its most-connected (highest-degree) page as the best proxy.
 */
const GROUP_TOP_PAGE_SLUG_KEY = "synapse:groupTopPageSlug";

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

const TYPE_COLORS: Record<string, string> = {
  concept: "var(--syn-accent)",
  entity: "#22c55e",
  source: "#f59e0b",
  synthesis: "#8b5cf6",
  comparison: "#06b6d4",
  untyped: "var(--syn-text-dim)",
};

function TypeBar({ pagesByType, total }: TypeBarProps) {
  if (total === 0) return null;
  const entries = Object.entries(pagesByType);
  let x = 0;
  return (
    <svg
      width="100%"
      height="6"
      viewBox="0 0 100 6"
      preserveAspectRatio="none"
      aria-hidden="true"
      style={{ borderRadius: 3, overflow: "hidden", display: "block" }}
    >
      {entries.map(([type, count]) => {
        const w = (count / total) * 100;
        const rect = (
          <rect
            key={type}
            x={x}
            y={0}
            width={w}
            height={6}
            fill={TYPE_COLORS[type] ?? "var(--syn-text-dim)"}
          />
        );
        x += w;
        return rect;
      })}
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

// ─── KPI card ─────────────────────────────────────────────────────────────────

interface KpiCardProps {
  icon: import("react").ReactNode;
  label: string;
  value: string | number;
  accent?: boolean;
  testId?: string;
}

function KpiCard({ icon, label, value, accent, testId }: KpiCardProps) {
  return (
    <div
      data-testid={testId ?? `kpi-${label}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "12px 14px",
        borderRadius: "var(--syn-radius-md)",
        border: `1px solid ${accent ? "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)" : "var(--syn-border)"}`,
        background: accent ? "var(--syn-accent-soft)" : "var(--syn-bg-soft)",
        minWidth: 0,
        flex: "1 1 110px",
      }}
    >
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
    </div>
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
            <span key={type} style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
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
            <span key={type} style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
              {count} {type}
            </span>
          ))}
        </div>
      )}

      {/* Top page (highest degree) */}
      {topPage ? (
        <div style={{ fontSize: 10, color: "var(--syn-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <span style={{ color: "var(--syn-text-dim)" }}>{t("home.groups.openTopPage")}: </span>
          {topPage.title}
        </div>
      ) : (
        <div style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
          {t("home.groups.noTopPages")}
        </div>
      )}

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

export function HomeDashboard() {
  const { t } = useTranslation();
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const activeProvider = useProviderStore(selectActiveProvider);
  const backendVersion = useStatusStore(selectBackendVersion);

  // Derive active provider label (type + model) — informational display
  const activeProviderLabel = activeProvider
    ? [activeProvider.provider_type, activeProvider.model_id].filter(Boolean).join(" / ")
    : null;

  // uptime and data_version come from the ActivityBar's /status poll via statusStore;
  // we read them from the store — no new poller (I3). Currently only backendVersion
  // is in statusStore; uptime/data_version are local state in ActivityBar.
  // Per A2 we accept them as null when unavailable (the /stats/overview also has data_version).
  // We read data_version from the overview response directly.

  const [overview, setOverview] = useState<StatsOverview | null | undefined>(undefined);
  const [sections, setSections] = useState<StatsSections | null | undefined>(undefined);
  const [groups, setGroups] = useState<StatsGroups | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Single fetch on mount — no polling (I3)
  useEffect(() => {
    const ac = new AbortController();
    setLoadError(null);

    async function load() {
      try {
        const [ov, sec, grp] = await Promise.all([
          getStatsOverview(ac.signal),
          getStatsSections(ac.signal),
          getStatsGroups(ac.signal),
        ]);
        if (ac.signal.aborted) return;
        setOverview(ov);
        setSections(sec);
        setGroups(grp);
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") return;
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    }

    void load();
    return () => ac.abort();
  }, []);

  // Section card click: write domain filter to localStorage + switch to wiki section
  const handleSectionNavigate = useCallback(
    (domain: string) => {
      try {
        if (domain === "untagged") {
          localStorage.removeItem(DOMAIN_FILTER_KEY);
        } else {
          localStorage.setItem(DOMAIN_FILTER_KEY, domain);
        }
      } catch {
        // localStorage may be unavailable in some environments — non-fatal
      }
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  // Group card click: open the group's top page in Wiki section.
  // Community-id tree filtering is not yet supported; we write the top page slug
  // so a future NavTree mount can scroll to it. The Wiki section opens regardless.
  const handleGroupOpen = useCallback(
    (group: StatsGroup) => {
      const topPage = group.top_pages[0];
      try {
        if (topPage?.slug) {
          localStorage.setItem(GROUP_TOP_PAGE_SLUG_KEY, topPage.slug);
        }
      } catch {
        // non-fatal
      }
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
        style={{ padding: 40, color: "var(--syn-text-muted)", fontSize: 13 }}
      >
        {t("common.loading")}
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
        maxWidth: 1100,
        margin: "0 auto",
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

      {/* ── 2. KPI row ── */}
      <section aria-label={t("home.kpi.ariaLabel")}>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
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
          />
          <KpiCard
            testId="kpi-lint-open"
            icon={<AlertTriangle size={14} aria-hidden="true" />}
            label={t("home.kpi.lintOpen")}
            value={overview.lint_open}
            accent={overview.lint_open > 0}
          />
          <KpiCard
            testId="kpi-monthly-cost"
            icon={<DollarSign size={14} aria-hidden="true" />}
            label={t("home.kpi.monthlyCost")}
            value={formatCost(overview.monthly_cost_usd)}
          />
          <KpiCard
            testId="kpi-data-version"
            icon={<Database size={14} aria-hidden="true" />}
            label={t("home.kpi.dataVersion")}
            value={`v${overview.data_version}`}
          />
        </div>
      </section>

      {/* ── 3. Curated domain sections "SEZIONI" ── */}
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

      {/* ── 4. NEW "GRUPPI AUTOMATICI" grid (A3) — hidden when groups is null (404) ── */}
      {groups !== null && groups !== undefined && groupList.length > 0 && (
        <section
          aria-label={t("home.groups.ariaLabel")}
          data-testid="home-groups-section"
        >
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
            {t("home.groups.title")}
          </h2>
          <div
            data-testid="home-groups-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {groupList.map((grp) => (
              <GroupCard
                key={grp.community}
                group={grp}
                onOpen={handleGroupOpen}
              />
            ))}
          </div>
        </section>
      )}

      {/* ── 5. Recent activity (last) ── */}
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
            {overview.recent_activity.map((item) => (
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
                  <span style={{ flex: 1, fontSize: 13, color: "var(--syn-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.title}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--syn-text-dim)", flexShrink: 0 }}>
                    {formatDate(item.updated_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
