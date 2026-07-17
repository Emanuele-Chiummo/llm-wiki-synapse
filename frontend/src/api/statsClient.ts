/**
 * statsClient.ts — typed API client for /stats/overview, /stats/sections,
 * /stats/groups, and /ops/backfill-domains status [F18][R12-1][A2+A3+A4].
 *
 * GET /stats/overview         → StatsOverview (global KPIs, capped 10 recent-activity items)
 * GET /stats/sections         → StatsSections (one entry per vocab domain + untagged bucket last)
 * GET /stats/groups           → StatsGroups (community auto-groups, ordered by pages_total desc,
 *                               capped at 12) — 404 → null (backend still building)
 * GET /ops/backfill-domains   → BackfillDomainStatus (running bool + optional last_summary)
 *                               — 404/error → null (feature not yet active or no backfill run)
 * GET /ops/synthesize         → SynthesizeStatus (running bool + optional last_summary,
 *                               ADR-0067 D3) — 404/error → null (no synthesize run yet)
 *
 * Contract: /stats/overview and /stats/sections frozen in ADR-0054 §5.
 * /stats/groups contract: FROZEN by parallel backend agent (A2+A3 amendment).
 * Shape: { groups: [{ community, label, pages_total, pages_by_type, top_pages, last_activity }] }
 *
 * All calls go through apiFetch (ADR-0052 §4.2 — single auth injection point).
 * No secrets in this file (CLAUDE.md §12).
 */

import { apiBase, apiFetch } from "./base";
import { errorMessageFromBody } from "./errors";

// ─── Response types (ADR-0054 §5.1) ───────────────────────────────────────────

/** One entry in recent_activity (last 10 pages by updated_at DESC). */
export interface RecentActivityItem {
  page_id: string;
  title: string;
  /** Server-derived slug: title lowercased, [^a-z0-9]+ → "-". */
  slug: string;
  updated_at: string;
}

/** Global KPI snapshot (GET /stats/overview). Shape frozen in ADR-0054 §5.1. */
export interface StatsOverview {
  pages_total: number;
  /** Histogram of page_type → count. Absent types are simply missing from the object. */
  pages_by_type: Record<string, number>;
  links_total: number;
  communities_count: number;
  review_pending: number;
  lint_open: number;
  monthly_cost_usd: number;
  data_version: number;
  /** Capped at 10 items. */
  recent_activity: RecentActivityItem[];
}

/** One page in the top_pages list within a section (degree-ordered, cap 5). */
export interface SectionTopPage {
  id: string;
  title: string;
  /** Server-derived slug. */
  slug: string;
  degree: number;
}

/**
 * One section entry from GET /stats/sections.
 * domain == "untagged" is the virtual bucket for pages without any domain/* tag.
 * Shape frozen in ADR-0054 §5.2.
 */
export interface SectionEntry {
  domain: string;
  pages_total: number;
  pages_by_type: Record<string, number>;
  last_activity: string | null;
  /** Ordered by degree DESC, capped at 5. */
  top_pages: SectionTopPage[];
}

/** GET /stats/sections response envelope. */
export interface StatsSections {
  sections: SectionEntry[];
}

// ─── /stats/groups types (A2+A3 amendment) ───────────────────────────────────

/**
 * One page in a group's top_pages list (degree-ordered, capped at backend side).
 * Shape mirrors SectionTopPage — same field set, different endpoint.
 */
export interface GroupTopPage {
  id: string;
  title: string;
  slug: string;
  degree: number;
}

/**
 * One community auto-group from GET /stats/groups.
 * community: numeric community id from the graph layout.
 * label: human-readable label derived by the backend (most-connected node title).
 * pages_total: total pages in this community.
 * pages_by_type: histogram of page_type → count.
 * top_pages: ordered by degree DESC (backend-capped).
 * last_activity: ISO-8601 timestamp of most-recently-updated page, or null.
 */
export interface StatsGroup {
  community: number;
  label: string;
  pages_total: number;
  pages_by_type: Record<string, number>;
  top_pages: GroupTopPage[];
  last_activity: string | null;
}

/** GET /stats/groups response envelope. Ordered by pages_total desc, capped at 12. */
export interface StatsGroups {
  groups: StatsGroup[];
}

// ─── /ops/backfill-domains status (A4 amendment) ─────────────────────────────

/**
 * Status response from GET /ops/backfill-domains.
 * running: true while a backfill job is in progress.
 * last_summary: optional human-readable tag count from the last completed run
 *               (e.g. "42 pages tagged"). Null when no backfill has run yet.
 *
 * 404 → null (endpoint not implemented or feature dormant).
 * [F18][R12-2][A4]
 */
/** Summary of one completed backfill run — mirrors ops/backfill_domains.BackfillSummary. */
export interface BackfillSummary {
  processed: number;
  tagged: number;
  skipped: number;
  failed: number;
  total_cost_usd: number;
  stopped_reason: string;
  max_pages: number;
  token_budget: number;
  force: boolean;
}

export interface BackfillDomainStatus {
  running: boolean;
  // OBJECT, not string — the mistyped `string | null` let the summary object flow
  // into a React child untyped (owner-reported crash, v1.2.1).
  last_summary: BackfillSummary | null;
}

/** Summary of one completed synthesize run — mirrors ops/synthesize.SynthesizeSummary. */
export interface SynthesizeSummary {
  candidates: number;
  candidates_evaluated?: number;
  processed: number;
  synthesis_written: number;
  comparison_written: number;
  pages_written: number;
  proposed: number;
  skipped: number;
  failed: number;
  total_cost_usd: number;
  stopped_reason: string;
  max_pages: number;
  token_budget: number;
  force: boolean;
  /** Additive v1.6 diagnostics; absent on pre-v1.6 completed runs. */
  duplicates_skipped?: number;
  untagged_skipped?: number;
  max_candidates?: number;
  mode?: string;
}

export interface SynthesizeStatus {
  running: boolean;
  current?: {
    max_pages: number;
    max_candidates: number;
    token_budget: number;
    force: boolean;
    mode: "auto" | "review-only";
    phase?: string;
  } | null;
  last_summary: SynthesizeSummary | null;
}

// ─── Client functions ─────────────────────────────────────────────────────────

/**
 * getStatsOverview — GET /stats/overview.
 *
 * Returns the global KPI snapshot, or null when the backend returns 404
 * (v1.1 backend without the stats endpoints — graceful degradation per R12-1 AC-R12-1-5).
 * Throws for any other non-2xx status so callers can surface unexpected errors.
 *
 * [F18][R12-1][ADR-0054 §5.1]
 */
export async function getStatsOverview(signal?: AbortSignal): Promise<StatsOverview | null> {
  try {
    const url = `${apiBase()}/stats/overview`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    if (res.status === 404) return null;
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        detail = errorMessageFromBody(body) ?? detail;
      } catch {
        // ignore parse error
      }
      throw new Error(`GET /stats/overview: ${detail}`);
    }
    return (await res.json()) as StatsOverview;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    // Re-throw genuine errors; swallow network errors as null only for 404
    throw err;
  }
}

/**
 * getStatsSections — GET /stats/sections.
 *
 * Returns sections in vocabulary order with untagged last, or null on 404
 * (older backend). When vocabulary is dormant the backend returns
 * { sections: [{domain:"untagged", ...}] } — still a valid non-null response.
 *
 * [F18][R12-1][ADR-0054 §5.2]
 */
export async function getStatsSections(signal?: AbortSignal): Promise<StatsSections | null> {
  try {
    const url = `${apiBase()}/stats/sections`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    if (res.status === 404) return null;
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        detail = errorMessageFromBody(body) ?? detail;
      } catch {
        // ignore parse error
      }
      throw new Error(`GET /stats/sections: ${detail}`);
    }
    return (await res.json()) as StatsSections;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    throw err;
  }
}

/**
 * getBackfillDomainStatus — GET /ops/backfill-domains.
 *
 * Returns {running, last_summary} when the endpoint exists, or null on 404 /
 * any error (graceful hide — the A4 backfill row is simply not shown).
 * Never throws (AbortError is re-thrown for cleanup).
 *
 * NOTE: this is a lightweight status GET, not the POST trigger. The shape
 * {running: bool, last_summary: string|null} is the assumed contract with the
 * backend. If the backend returns 200 from POST only (synchronous), callers
 * should treat any non-404 200 as running=false (backfill already completed).
 *
 * [F18][R12-2][A4]
 */
export async function getBackfillDomainStatus(
  signal?: AbortSignal,
): Promise<BackfillDomainStatus | null> {
  try {
    const url = `${apiBase()}/ops/backfill-domains`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    if (res.status === 404) return null;
    if (res.status === 405) return null; // endpoint is POST-only on this backend version
    if (!res.ok) return null;
    return (await res.json()) as BackfillDomainStatus;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    return null;
  }
}

/**
 * getSynthesizeStatus — GET /ops/synthesize (ADR-0067 D3).
 *
 * Returns {running, last_summary} when the endpoint exists, or null on 404 /
 * any error (graceful hide — same degrade-safe contract as getBackfillDomainStatus).
 * Never throws (AbortError is re-thrown for cleanup).
 *
 * [F18][ADR-0067 D3]
 */
export async function getSynthesizeStatus(signal?: AbortSignal): Promise<SynthesizeStatus | null> {
  try {
    const url = `${apiBase()}/ops/synthesize`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    if (res.status === 404) return null;
    if (res.status === 405) return null; // endpoint is POST-only on this backend version
    if (!res.ok) return null;
    return (await res.json()) as SynthesizeStatus;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    return null;
  }
}

/**
 * getStatsGroups — GET /stats/groups.
 *
 * Returns community auto-groups ordered by pages_total DESC, capped at 12, or
 * null on 404 (backend not yet implemented — A2+A3 parallel agent is building it).
 * 404 is the graceful-hide signal: the "GRUPPI AUTOMATICI" block does not render.
 *
 * Never throws (non-404 non-2xx → null; AbortError re-thrown for cleanup).
 *
 * [F18][R12-1][A2+A3]
 */
export async function getStatsGroups(signal?: AbortSignal): Promise<StatsGroups | null> {
  try {
    const url = `${apiBase()}/stats/groups`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    if (res.status === 404) return null;
    if (!res.ok) return null; // tolerate unexpected errors gracefully
    return (await res.json()) as StatsGroups;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    // Network / parse errors — hide block silently
    return null;
  }
}
