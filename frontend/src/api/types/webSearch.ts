/**
 * Web-search config API contract types (F10, FE-QUAL-11 split of api/types.ts).
 * GET/PUT /web-search/config (ADR-0041)
 */

/**
 * Response from GET /web-search/config (ADR-0041 §2.3).
 * The SearXNG URL is NOT a secret — it IS returned in full (unlike clip/mcp tokens).
 * SearXNG is the ONLY supported web-search backend (I9).
 */
export interface WebSearchConfigResponse {
  /** True iff a SearXNG URL is available (DB or env). */
  configured: boolean;
  /** Resolved SearXNG base URL (DB wins over env). null when neither is set. */
  url: string | null;
  /** Resolved SearXNG categories list. */
  categories: string[];
  /** Resolved max queries per deep-research iteration (1–50). */
  max_queries: number;
  /**
   * Which URL source is authoritative:
   *   "db"  — URL set via PUT /web-search/config
   *   "env" — SEARXNG_URL env var
   *   "none" — no URL configured
   */
  source: "db" | "env" | "none";
}

/**
 * Request body for PUT /web-search/config (ADR-0041 §2.4).
 * All fields optional. No provider field — SearXNG is the ONLY backend (I9).
 */
export interface WebSearchConfigRequest {
  /** Set the SearXNG base URL (must be a valid http/https URL). */
  set_url?: string | null;
  /** Comma-separated categories (e.g. "general,news"). "" clears to default. */
  set_categories?: string | null;
  /** Max queries per deep-research iteration (1–50). */
  set_max_queries?: number | null;
  /** Clear ALL DB overrides; falls back to env / code defaults. */
  clear?: boolean | null;
}

/**
 * Response from PUT /web-search/config (ADR-0041 §2.4).
 * Always reflects post-write posture. Same shape as WebSearchConfigResponse.
 */
export interface WebSearchConfigStateResponse {
  configured: boolean;
  url: string | null;
  categories: string[];
  max_queries: number;
  source: "db" | "env" | "none";
}
