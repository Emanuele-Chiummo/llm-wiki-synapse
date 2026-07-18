/**
 * HomeDashboard.tsx — Home landing section orchestrator [F18][R12-1][A2+A3+A4].
 *
 * Layout (A2+A3+A4 amendment order):
 *   1. "STATO DEL SISTEMA" block — compact health strip from GET /health/detailed.
 *   2. "LAVORI ATTIVI" block — A4 new. Visible ONLY when at least one job is active.
 *   3. KPI row (composition hero + secondary metric grid).
 *   4. Curated domain sections "SEZIONI" — from GET /stats/sections.
 *   5. "GRUPPI AUTOMATICI" grid — from GET /stats/groups.
 *      TOP 4 by default (collapsed); Espandi/Comprimi toggle reveals the rest.
 *   6. Recent activity (last).
 *
 * Sub-components (extracted to their own files in this directory):
 *   SystemStatusBlock  ActiveJobsBlock  KpiCard  CompositionHero
 *   SectionCard  GroupCard  WikiThesisBlock  QuickActionsBlock
 *   ReviewPreviewBlock  OpenQuestionsBlock  DataQualityNudge  SynthesizeNudge
 *
 * INVARIANT I3: no heavy per-render work; stats + health fetched ONCE on mount, no polling.
 *               activityStore is already polled by ActivityBar — no new intervals added here.
 * INVARIANT I4: recent-activity capped at 10; no virtualisation.
 * INVARIANT I2: no graph layout runs here.
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
  Settings,
  CheckCircle2,
  Loader2,
  ChevronDown,
  ChevronUp,
  Database,
} from "lucide-react";
import {
  getStatsOverview,
  getStatsSections,
  getStatsGroups,
  getSynthesizeStatus,
  type StatsOverview,
  type StatsSections,
  type StatsGroups,
  type StatsGroup,
  type SynthesizeStatus,
} from "../../api/statsClient";
import { fetchCostsSummary } from "../../api/costsClient";
import {
  useAppStore,
  selectSetActiveSection,
  selectVaultId,
  selectSelectPage,
} from "../../store/appStore";
import { useProviderStore, selectActiveProvider } from "../../store/providerStore";
import {
  useStatusStore,
  selectBackendConnectionState,
  selectBackendVersion,
  selectStatusDataVersion,
} from "../../store/statusStore";
import { useActivityCounts, useActivityBatch, useActivityTasks } from "../../store/activityStore";
import { ErrorState } from "../common/ErrorState";
import { Skeleton } from "../ui/Skeleton";
import { HomeGettingStarted } from "./HomeGettingStarted";
import { readSetupState } from "../setup/setupState";
import { providerVerificationFingerprint } from "../setup/providerVerification";
import { usePollChain } from "../../hooks/usePollChain";
import {
  formatCost,
  formatDate,
  DOMAIN_FILTER_KEY,
  GROUP_FILTER_KEY,
  NAV_FILTER_LABEL_KEY,
  NAV_FILTER_EVENT,
} from "./homeUtils";
import { SystemStatusBlock } from "./SystemStatusBlock";
import { ActiveJobsBlock } from "./ActiveJobsBlock";
import { KpiCard, CompositionHero } from "./KpiSection";
import { SectionCard } from "./SectionCard";
import { GroupCard } from "./GroupCard";
import { WikiThesisBlock } from "./WikiThesisBlock";
import { QuickActionsBlock } from "./QuickActionsBlock";
import { ReviewPreviewBlock } from "./ReviewPreviewBlock";
import { OpenQuestionsBlock } from "./OpenQuestionsBlock";
import { DataQualityNudge } from "./DataQualityNudge";
import { SynthesizeNudge, SYNTHESIZE_STATUS_POLL_MS } from "./SynthesizeNudge";

// Re-export typeColor for any external consumers that previously imported it here.
export { typeColor } from "./homeUtils";

// ─── Reduced-motion detection (UX-1) ─────────────────────────────────────────
const reducedMotion =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── Constants ─────────────────────────────────────────────────────────────────

/** Number of groups to show by default before "Espandi" toggle (A4). */
const GROUPS_DEFAULT_CAP = 4;

function isStatsRequestAbort(err: unknown, signal: AbortSignal): boolean {
  if (signal.aborted) return true;
  if (!(err instanceof Error)) return false;
  if (err.name === "AbortError") return true;
  return err.message.trim().toLowerCase() === "request cancelled";
}

// ─── HomeDashboard ─────────────────────────────────────────────────────────────

export function HomeDashboard() {
  const { t } = useTranslation();
  const setActiveSection = useAppStore(selectSetActiveSection);
  const vaultId = useAppStore(selectVaultId);
  const selectPageAction = useAppStore(selectSelectPage);
  const activeProvider = useProviderStore(selectActiveProvider);
  const connectionState = useStatusStore(selectBackendConnectionState);
  const backendVersion = useStatusStore(selectBackendVersion);

  // WS-A [F16/F4/F18]: subscribe to data_version from the ActivityBar's existing
  // GET /status poll. When it changes, re-fetch stats.
  // INVARIANT I3: no re-render on same-version tick.
  const statusDataVersion = useStatusStore(selectStatusDataVersion);

  // Ingest counts from activityStore — already polled by ActivityBar; no new poller (I3).
  const { processing: ingestProcessing, pending: ingestPending } = useActivityCounts();

  // WS-C [F3/F16]: batch progress and task list for ingest progress bar.
  const ingestBatch = useActivityBatch();
  const allTasks = useActivityTasks();
  const ingestTasks = allTasks.filter((tk) => tk.status === "processing");

  // v1.6: shared synthesize status (FE-ARCH-2: shared poll chain).
  const [synthesizeStatus, setSynthesizeStatus] = useState<SynthesizeStatus | null>(null);
  const synthesizePoll = usePollChain<SynthesizeStatus | null>({
    fetch: (signal) => getSynthesizeStatus(signal).catch(() => null),
    onResult: (result) => setSynthesizeStatus(result),
    intervalFor: (result) => (result?.running === true ? SYNTHESIZE_STATUS_POLL_MS : null),
    initialDelayMs: 0,
  });
  useEffect(() => {
    synthesizePoll.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  const [costByDay, setCostByDay] = useState<number[] | null>(null);
  const statsAbortRef = useRef<AbortController | null>(null);

  // FE-PERF-2: below-the-fold sections deferred to idle time.
  const [deferredReady, setDeferredReady] = useState(false);
  useEffect(() => {
    const w = window as typeof window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (h: number) => void;
    };
    let handle: number;
    if (typeof w.requestIdleCallback === "function") {
      handle = w.requestIdleCallback(() => setDeferredReady(true), { timeout: 500 });
    } else {
      handle = window.setTimeout(() => setDeferredReady(true), 0);
    }
    return () => {
      if (typeof w.cancelIdleCallback === "function") w.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, []);

  // UX-1: true ONLY while a version-bump-triggered stats re-fetch is in-flight.
  const [isRefetching, setIsRefetching] = useState(false);
  const setIsRefetchingRef = useRef<(v: boolean) => void>(setIsRefetching);
  setIsRefetchingRef.current = setIsRefetching;

  const lastFetchedVersionRef = useRef<number | null>(null);
  const statusDataVersionRef = useRef<number | null>(statusDataVersion);
  statusDataVersionRef.current = statusDataVersion;

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
        lastFetchedVersionRef.current = statusDataVersionRef.current ?? -1;
      } catch (err: unknown) {
        if (isStatsRequestAbort(err, signal)) return;
        setLoadError(err instanceof Error ? err.message : String(err));
      } finally {
        setIsRefetchingRef.current(false);
      }
    })();
  }, []);

  const reloadStats = useCallback(() => {
    statsAbortRef.current?.abort();
    const ac = new AbortController();
    statsAbortRef.current = ac;
    loadStats(ac.signal);
  }, [loadStats]);

  useEffect(() => {
    lastFetchedVersionRef.current = statusDataVersionRef.current;
    reloadStats();
    return () => statsAbortRef.current?.abort();
  }, [reloadStats]);

  // Daily cost series for the sparkline — deferred (FE-PERF-2).
  useEffect(() => {
    if (!deferredReady) return;
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
  }, [deferredReady]);

  // WS-A [AC-WS-A-1, AC-WS-A-3]: re-fetch stats when data_version bumps.
  useEffect(() => {
    if (statusDataVersion === null) return;
    if (statusDataVersion === lastFetchedVersionRef.current) return;
    setIsRefetching(true);
    reloadStats();
    return undefined;
  }, [statusDataVersion, reloadStats]);

  // Section card click: write domain filter to localStorage, switch section.
  const handleSectionNavigate = useCallback(
    (domain: string) => {
      try {
        if (domain === "untagged") {
          localStorage.removeItem(DOMAIN_FILTER_KEY);
          localStorage.removeItem(NAV_FILTER_LABEL_KEY);
        } else {
          localStorage.setItem(DOMAIN_FILTER_KEY, domain);
          localStorage.setItem(NAV_FILTER_LABEL_KEY, domain);
        }
        localStorage.removeItem(GROUP_FILTER_KEY);
      } catch {
        // localStorage may be unavailable in some environments — non-fatal
      }
      window.dispatchEvent(new Event(NAV_FILTER_EVENT));
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  // Group card click: write community id filter to localStorage, switch section.
  const handleGroupOpen = useCallback(
    (group: StatsGroup) => {
      try {
        localStorage.setItem(GROUP_FILTER_KEY, String(group.community));
        localStorage.setItem(NAV_FILTER_LABEL_KEY, group.label || `Group ${group.community}`);
        localStorage.removeItem(DOMAIN_FILTER_KEY);
      } catch {
        // non-fatal
      }
      window.dispatchEvent(new Event(NAV_FILTER_EVENT));
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  const handleActivityClick = useCallback(
    (_slug: string) => {
      setActiveSection("pages");
    },
    [setActiveSection],
  );

  // ── Error state ────────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <div
        data-testid="home-dashboard-error"
        style={{ width: "min(560px, calc(100% - 32px))", margin: "auto" }}
      >
        <ErrorState
          title={t("home.error.title")}
          error={loadError}
          onRetry={() => {
            setOverview(undefined);
            reloadStats();
          }}
        />
      </div>
    );
  }

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
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Skeleton width={220} height={24} radius={6} />
          <Skeleton width={300} height={14} radius={4} />
        </div>
        <Skeleton height={72} radius={8} />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
            gap: 10,
          }}
        >
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} height={78} radius={8} />
          ))}
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
            gap: 12,
          }}
        >
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
        <Database
          size={32}
          aria-hidden="true"
          style={{ color: "var(--syn-text-dim)", opacity: 0.5 }}
        />
        <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--syn-text)" }}>
          {t("home.placeholder.title")}
        </p>
        <p style={{ margin: 0, fontSize: 13, color: "var(--syn-text-muted)", lineHeight: 1.6 }}>
          {t("home.placeholder.body")}
        </p>
      </div>
    );
  }

  if (overview.pages_total === 0) {
    const setupState = readSetupState();
    const providerReady =
      setupState.providerVerified &&
      activeProvider !== null &&
      activeProvider.is_fallback !== true &&
      setupState.providerFingerprint !== null &&
      setupState.providerFingerprint === providerVerificationFingerprint(activeProvider);

    return (
      <HomeGettingStarted
        backendReady={connectionState === "online"}
        providerReady={providerReady}
        workspaceName={vaultId}
        onImport={() => setActiveSection("ingest")}
        onConfigureProvider={() => setActiveSection("settings")}
        onOpenProjects={() => setActiveSection("projects")}
      />
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
        width: "100%",
        boxSizing: "border-box",
        overflowY: "auto",
        height: "100%",
      }}
    >
      {/* ── Header ── */}
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: "var(--syn-text)" }}>
            {t("home.title")}
          </h1>
          {/* UX-1: "updating…" pill — visible ONLY while a version-bump stats re-fetch is in-flight. */}
          {isRefetching && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: 10,
                fontWeight: 600,
                color: "var(--syn-accent)",
                background: "var(--syn-accent-soft)",
                borderRadius: "var(--syn-radius-pill)",
                padding: "2px 7px",
              }}
            >
              <Loader2
                size={10}
                aria-hidden="true"
                style={{
                  animation: reducedMotion ? "none" : "syn-spin 0.8s linear infinite",
                  flexShrink: 0,
                }}
              />
              {t("home.updating")}
            </span>
          )}
        </div>
        <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--syn-text-muted)" }}>
          {t("home.subtitle")}
        </p>
      </div>

      {/* ── 1a. Wiki thesis hero (v1.5) ── */}
      <WikiThesisBlock />

      {/* ── 1b. Quick actions row (v1.5) ── */}
      <QuickActionsBlock setActiveSection={setActiveSection} />

      {/* ── 1. System Status block (A2) ── */}
      <SystemStatusBlock
        activeProviderLabel={activeProviderLabel}
        backendVersion={backendVersion}
        statusUptimeSeconds={null}
        dataVersion={overview.data_version}
      />

      {/* ── 2. Active Jobs block (A4 + WS-C) — only rendered when something is running ──
          FE-PERF-2: mount deferred to idle time. */}
      {deferredReady && (
        <ActiveJobsBlock
          ingestProcessing={ingestProcessing}
          ingestPending={ingestPending}
          ingestBatch={ingestBatch}
          ingestTasks={ingestTasks}
          synthesizeRunning={synthesizeStatus?.running === true}
          onNavigateIngest={() => setActiveSection("ingest")}
          onNavigateResearch={() => setActiveSection("deep-search")}
          onNavigateBackfill={() => setActiveSection("settings")}
          onNavigateSynthesize={() => setActiveSection("pages")}
        />
      )}

      {/* ── 3. KPI row — composition hero + secondary metric grid ── */}
      <section
        aria-label={t("home.kpi.ariaLabel")}
        style={{ display: "flex", flexDirection: "column", gap: 10 }}
      >
        <CompositionHero
          pagesTotal={overview.pages_total}
          pagesByType={overview.pages_by_type}
          onClick={() => setActiveSection("pages")}
        />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
            gap: 10,
          }}
        >
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
            tone={overview.review_pending === 0 ? "good" : undefined}
            onClick={() => setActiveSection("review")}
          />
          <KpiCard
            testId="kpi-lint-open"
            icon={
              overview.lint_open === 0 ? (
                <CheckCircle2 size={14} aria-hidden="true" />
              ) : (
                <AlertTriangle size={14} aria-hidden="true" />
              )
            }
            label={t("home.kpi.lintOpen")}
            value={overview.lint_open === 0 ? t("home.kpi.lintClean") : overview.lint_open}
            tone={overview.lint_open === 0 ? "good" : "warn"}
            onClick={() => setActiveSection("lint")}
          />
          <KpiCard
            testId="kpi-monthly-cost"
            icon={<DollarSign size={14} aria-hidden="true" />}
            label={t("home.kpi.monthlyCost")}
            value={formatCost(overview.monthly_cost_usd)}
            sparkline={costByDay ?? undefined}
          />
        </div>
      </section>

      {/* ── 3a. Data quality nudge (v1.5) ── */}
      <DataQualityNudge overview={overview} sections={sections} />

      {/* ── 3a-bis. Synthesize nudge (v1.5.3) ── */}
      <SynthesizeNudge
        overview={overview}
        synthesizeStatus={synthesizeStatus}
        onTriggered={synthesizePoll.start}
      />

      {/* ── 3b. Review preview + open questions — deferred (FE-PERF-2) ── */}
      {deferredReady && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 16,
          }}
        >
          <ReviewPreviewBlock
            vaultId={vaultId}
            reviewTotal={overview.review_pending}
            setActiveSection={setActiveSection}
          />
          <OpenQuestionsBlock
            vaultId={vaultId}
            onOpenPage={(pageId) => {
              selectPageAction(pageId, "tree");
              setActiveSection("pages");
            }}
          />
        </div>
      )}

      {/* ── 4. Curated domain sections "SEZIONI" ── */}
      {sections !== null && (
        <section aria-label={t("home.sections.ariaLabel")}>
          <h2 className="syn-eyebrow" style={{ margin: "0 0 12px" }}>
            {t("home.sections.title")}
          </h2>

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
                <SectionCard key={sec.domain} section={sec} onNavigate={handleSectionNavigate} />
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── 5. "GRUPPI AUTOMATICI" grid (A3+A4) ── */}
      {groups !== null && groups !== undefined && groupList.length > 0 && (
        <section aria-label={t("home.groups.ariaLabel")} data-testid="home-groups-section">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              marginBottom: 12,
              justifyContent: "space-between",
            }}
          >
            <h2 className="syn-eyebrow" style={{ margin: 0 }}>
              {t("home.groups.title")}
            </h2>

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
              <GroupCard key={grp.community} group={grp} onOpen={handleGroupOpen} />
            ))}
          </div>
        </section>
      )}

      {/* ── 6. Recent activity (last) ── */}
      <section aria-label={t("home.activity.ariaLabel")}>
        <h2 className="syn-eyebrow" style={{ margin: "0 0 12px" }}>
          {t("home.activity.title")}
        </h2>
        {overview.recent_activity.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: "var(--syn-text-dim)" }}>
            {t("home.activity.empty")}
          </p>
        ) : (
          <ul
            data-testid="home-recent-activity"
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {overview.recent_activity.map((item) => {
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
                      (e.currentTarget as HTMLButtonElement).style.background =
                        "var(--syn-surface-hover)";
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                    }}
                  >
                    <FileText
                      size={12}
                      aria-hidden="true"
                      style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
                    />
                    <span
                      style={{
                        flex: 1,
                        fontSize: 13,
                        color: label ? "var(--syn-text)" : "var(--syn-text-dim)",
                        fontStyle: label ? "normal" : "italic",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {label || t("home.activity.untitled")}
                    </span>
                    <span
                      style={{
                        fontSize: 11,
                        color: "var(--syn-text-dim)",
                        flexShrink: 0,
                        fontFamily: "var(--syn-font-mono)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
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
