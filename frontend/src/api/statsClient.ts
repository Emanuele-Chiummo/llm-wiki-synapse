/**
 * statsClient.ts — typed API client for /stats/overview and /stats/sections [F18][R12-1].
 *
 * GET /stats/overview → StatsOverview (global KPIs, capped 10 recent-activity items)
 * GET /stats/sections → StatsSections (one entry per vocab domain + untagged bucket last)
 *
 * Contract frozen in ADR-0054 §5. Both endpoints return null on 404 so the dashboard
 * degrades gracefully when the backend is still at v1.1 (no stats endpoints yet).
 *
 * All calls go through apiFetch (ADR-0052 §4.2 — single auth injection point).
 * No secrets in this file (CLAUDE.md §12).
 */

import { apiBase, apiFetch } from "./base";

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
        const body = (await res.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
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
        const body = (await res.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
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
