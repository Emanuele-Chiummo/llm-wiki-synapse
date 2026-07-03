/**
 * HomeDashboard.tsx — Home landing section [F18][R12-1][ADR-0054 §5].
 *
 * Layout:
 *   (a) KPI row: pages total, links, communities, review pending, lint open,
 *       monthly AI spend, data version — compact cards using var(--syn-*) tokens.
 *   (b) Recent activity list (cap 10) — click opens that page in Wiki (pages section).
 *   (c) Section cards grid: one card per domain (vocabulary order) + untagged last.
 *       Click on a section card → sets graphStore.activeSection = "pages" and writes
 *       a domainFilter to localStorage (key: "synapse:domainFilter") for the wiki tree
 *       to read on next mount. Chosen mechanism: localStorage signal + section switch.
 *       Rationale: the NavTree already reads its filter from its own local state on
 *       render; a lightweight localStorage entry lets HomeDashboard dispatch a filter
 *       without touching NavTree internals or adding Zustand store surface this sprint.
 *       The filter is consumed opportunistically — if NavTree does not read it yet, the
 *       user still lands on the Wiki section and can filter manually. This is the
 *       "cheapest correct path" per R12-1 AC-R12-1-7.
 *   (d) Empty states: 404 backend → friendly placeholder; empty vocabulary → global
 *       KPIs only + hint.
 *
 * INVARIANT I3: no heavy per-render work; stats fetched ONCE on mount, no polling.
 * INVARIANT I4: recent-activity capped at 10, sections capped at vocab size — no
 *   virtualisation needed (both well under 50 items in any realistic vault).
 * INVARIANT I2: no graph layout runs here; communities_count read from /stats/overview.
 * No charting library imported — sparkline is plain inline SVG (R12-1 AC-R12-1-6).
 *
 * Design tokens: var(--syn-accent), var(--syn-border), var(--syn-bg-soft),
 * var(--syn-text-muted), var(--syn-text-dim), var(--syn-radius-md),
 * var(--syn-surface-sunken), var(--syn-surface-hover).
 */

import { useEffect, useState, useCallback } from "react";
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
} from "lucide-react";
import {
  getStatsOverview,
  getStatsSections,
  type StatsOverview,
  type StatsSections,
  type SectionEntry,
} from "../../api/statsClient";
import {
  useGraphStore,
  selectSetActiveSection,
} from "../../store/graphStore";

// ─── Constants ─────────────────────────────────────────────────────────────────

/** localStorage key used to pass a domain filter to the Wiki/NavTree section. */
const DOMAIN_FILTER_KEY = "synapse:domainFilter";

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

// ─── HomeDashboard ─────────────────────────────────────────────────────────────

export function HomeDashboard() {
  const { t } = useTranslation();
  const setActiveSection = useGraphStore(selectSetActiveSection);

  const [overview, setOverview] = useState<StatsOverview | null | undefined>(undefined);
  const [sections, setSections] = useState<StatsSections | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Single fetch on mount — no polling (I3)
  useEffect(() => {
    const ac = new AbortController();
    setLoadError(null);

    async function load() {
      try {
        const [ov, sec] = await Promise.all([
          getStatsOverview(ac.signal),
          getStatsSections(ac.signal),
        ]);
        if (ac.signal.aborted) return;
        setOverview(ov);
        setSections(sec);
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

  // Recent activity: click → open that page in wiki section
  const handleActivityClick = useCallback(
    (_slug: string) => {
      // For now navigate to pages section; the tree will show the last-selected page.
      // A future sprint can deep-link by slug once NavTree supports a URL param.
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

      {/* ── KPI row ── */}
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

      {/* ── Recent activity ── */}
      <section aria-label={t("home.activity.ariaLabel")}>
        <h2
          style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}
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

      {/* ── Section cards ── */}
      <section aria-label={t("home.sections.ariaLabel")}>
        <h2
          style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}
        >
          {t("home.sections.title")}
        </h2>

        {/* 404 on /stats/sections — sections panel itself unavailable */}
        {sections === null && (
          <p style={{ margin: 0, fontSize: 13, color: "var(--syn-text-dim)" }}>
            {t("home.placeholder.body")}
          </p>
        )}

        {/* No vocabulary configured → hint to settings */}
        {sections !== null && !hasVocabSections && (
          <div
            data-testid="home-sections-empty"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 8,
              padding: "16px 20px",
              borderRadius: "var(--syn-radius-md)",
              border: "1px dashed var(--syn-border)",
              background: "var(--syn-surface-sunken)",
            }}
          >
            <p style={{ margin: 0, fontSize: 13, color: "var(--syn-text-muted)" }}>
              {t("home.sections.emptyVocab")}
            </p>
            <button
              data-testid="home-sections-go-settings"
              onClick={() => setActiveSection("settings")}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 12px",
                borderRadius: "var(--syn-radius-md)",
                border: "1px solid var(--syn-border)",
                background: "transparent",
                color: "var(--syn-text-muted)",
                fontSize: 12,
                cursor: "pointer",
                width: "fit-content",
              }}
            >
              <Settings size={12} aria-hidden="true" />
              {t("home.sections.goSettings")}
            </button>
          </div>
        )}

        {/* Section grid */}
        {sections !== null && sectionList.length > 0 && (
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
    </div>
  );
}
