/**
 * searchClient.ts — typed API client for GET /search (F5, ADR-0022).
 *
 * Backend contract (main.py §2582, ADR-0022 §2.5):
 *   GET /search?q=<query>&vault_id=<id>&k=<n>&context_window=<n>
 *   → SearchResponse { query, context, results: SearchResultItem[], data_version, approx_tokens, token_budget }
 *
 * SearchResultItem fields (mirrored from backend SearchResultItem Pydantic model, line 1281):
 *   n       — 1-based citation index
 *   id      — UUID string (pages row id)
 *   title   — frontmatter title or filename stem (never empty)
 *   slug    — slugify(title), derived
 *   score   — cosine similarity (vector) or edge weight (expansion)
 *   phase   — "vector" | "expansion"
 *
 * INVARIANT I3: fetch is abortable; no per-token work; one request per user query.
 * No secrets in this file (CLAUDE.md §12).
 */

import { fetchWithTimeout } from "./http";
import { ApiError } from "./graphClient";
import { apiBase } from "./base";
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

const SEARCH_TIMEOUT_MS = 15_000;

// ─── Types ────────────────────────────────────────────────────────────────────

/**
 * One citation/result item from GET /search (mirrors backend SearchResultItem, main.py §1281).
 *
 * Fields:
 *   n      — 1-based citation index (contiguous from 1)
 *   id     — UUID of the pages row (matches Qdrant point id, ADR-0002)
 *   title  — frontmatter title or filename stem (never empty, §2.6)
 *   slug   — slugify(title), derived, not a DB column
 *   score  — cosine similarity (vector phase) or edge weight (expansion phase)
 *   phase  — "vector" | "expansion" — which retrieval phase produced this result
 */
export interface SearchResultItem {
  n: number;
  id: string;
  title: string;
  slug: string;
  score: number;
  phase: "vector" | "expansion";
}

/**
 * Full response from GET /search (ADR-0022 §2.5, AC-F5-6).
 * 0-hit → 200 with empty results array and empty context (AC-F5-7a).
 * READ-ONLY — never bumps data_version (AC-F5-5).
 */
export interface SearchResponse {
  query: string;
  context: string;
  results: SearchResultItem[];
  data_version: number;
  approx_tokens: number;
  token_budget: number;
}

/** Options for searchWiki — all optional beyond the required query. */
export interface SearchWikiOptions {
  vault_id?: string;
  /** Dense top-k for the vector phase (1–50); default 8. */
  k?: number;
  /** Context window override (4096–1_000_000); null → 32 768 default (F14). */
  context_window?: number | null;
  signal?: AbortSignal;
}

// ─── Client function ──────────────────────────────────────────────────────────

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

/**
 * Call GET /search with a query string and return structured results.
 *
 * Debounce, abort-controller lifecycle, and minimum-character enforcement
 * are the responsibility of the caller (SearchView.tsx uses a 300ms debounce
 * and a 2-character minimum per the spec).
 *
 * I3: single bounded fetch per user query; no per-token work; abortable.
 */
export async function searchWiki(
  query: string,
  opts: SearchWikiOptions = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (opts.vault_id) params.set("vault_id", opts.vault_id);
  if (opts.k !== undefined) params.set("k", String(opts.k));
  if (opts.context_window != null)
    params.set("context_window", String(opts.context_window));

  const url = `${apiBase()}/search?${params.toString()}`;

  const res = await fetchWithTimeout(
    url,
    opts.signal !== undefined ? { signal: opts.signal } : {},
    SEARCH_TIMEOUT_MS,
  );
  await checkResponse(res);
  return (await res.json()) as SearchResponse;
}
