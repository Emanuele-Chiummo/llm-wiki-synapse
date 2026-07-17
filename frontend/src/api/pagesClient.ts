/**
 * pagesClient.ts — typed API client for Synapse page-list and status endpoints.
 *
 * Keeps graphClient.ts focused on graph-only concerns (GET /graph, PATCH /position).
 * Base URL from VITE_API_BASE env var (default: "" (relative, proxied in dev / same-origin in prod)).
 * No secrets in this file (CLAUDE.md §12).
 */

import type {
  PageListItem,
  PageListResponse,
  PageContentResponse,
  PageContentPutResponse,
  RelatedPagesResponse,
  StatusResponse,
} from "./types";
import { checkResponse } from "./errors";
import { fetchWithTimeout } from "./http";
import { apiBase } from "./base";
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

/**
 * Fetch the page list for a vault.
 * GET /pages?vault_id=<vaultId>&limit=<limit>&offset=<offset>
 *
 * Phase-1: fetches up to 500 items (the full 140-node demo fits in one page).
 */
export async function fetchPages(
  vaultId: string = "default",
  options: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<PageListResponse> {
  const { limit = 500, offset = 0 } = options;
  const url =
    `${apiBase()}/pages?vault_id=${encodeURIComponent(vaultId)}` +
    `&limit=${limit}&offset=${offset}`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageListResponse;
}

/**
 * Fetch ALL pages for a vault by paginating GET /pages (which caps at limit=500).
 *
 * The nav tree must show every page — with a single limit=500 call, once a vault grows past
 * 500 pages the OLDEST pages (GET /pages orders created_at DESC) silently drop out, including
 * the singleton `overview` entry-point. This loops offset until a short page is returned.
 * Bounded (≤ maxPages requests) so a runaway can never loop forever.
 */
export async function fetchAllPages(
  vaultId: string = "default",
  signal?: AbortSignal,
  pageSize = 500,
  maxPages = 50,
): Promise<PageListResponse> {
  const items: PageListItem[] = [];
  let offset = 0;
  for (let i = 0; i < maxPages; i++) {
    const res = await fetchPages(vaultId, { limit: pageSize, offset }, signal);
    items.push(...res.items);
    if (res.items.length < pageSize) break;
    offset += pageSize;
  }
  return { items };
}

/**
 * Resolve a chat-citation slug (derived slugify(title), NOT a DB column) to the
 * live page carrying that title. GET /pages/by-slug/{slug} (v1.3.3).
 *
 * Throws ApiError(404) when no live page slugifies to it — callers surface a
 * toast instead of navigating (the old path fed the slug into /pages/{uuid}
 * routes and got a 422).
 */
export async function fetchPageBySlug(slug: string, signal?: AbortSignal): Promise<PageListItem> {
  const url = `${apiBase()}/pages/by-slug/${encodeURIComponent(slug)}`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageListItem;
}

/**
 * Fetch the raw markdown content of a single wiki page.
 * GET /pages/{id}/content
 *
 * Returns PageContentResponse on success.
 * Throws ApiError(404) if the page or backing file is missing/deleted.
 * The AbortSignal is propagated so the caller can cancel in-flight fetches
 * when the selected page changes (I3 — no stale updates after selection change).
 */
export async function fetchPageContent(
  pageId: string,
  signal?: AbortSignal,
): Promise<PageContentResponse> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}/content`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageContentResponse;
}

/**
 * Save the raw markdown content of a wiki page.
 * PUT /pages/{id}/content  body: { content, expected_hash }
 *
 * Returns PageContentPutResponse(200) on success.
 * Throws ApiError(409) if expected_hash is stale (concurrent edit on disk).
 * Throws ApiError(404) if the page is unknown.
 */
export async function savePageContent(
  pageId: string,
  content: string,
  expectedHash: string | null,
  signal?: AbortSignal,
): Promise<PageContentPutResponse> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}/content`;
  const body: { content: string; expected_hash: string | null } = {
    content,
    expected_hash: expectedHash,
  };
  const res = await fetchWithTimeout(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as PageContentPutResponse;
}

/**
 * Fetch related pages for a wiki page, ranked by 4-signal edge weight.
 * GET /pages/{id}/related?limit=<limit>
 *
 * Returns RelatedPagesResponse(items=[], total=0) when the page has no graph edges.
 * Throws ApiError(404) if the page is unknown.
 * The AbortSignal is propagated so the caller can cancel when the selection changes (I3).
 */
export async function fetchRelatedPages(
  pageId: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<RelatedPagesResponse> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}/related?limit=${limit}`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as RelatedPagesResponse;
}

// ─── Types for new page creation (R7-2) ──────────────────────────────────────

export type NewPageType = "concept" | "entity" | "source" | "synthesis" | "comparison" | "query";

export interface CreatePageRequest {
  title: string;
  page_type: NewPageType;
  /** Optional subdirectory within wiki/ (e.g. "entities"). Defaults to type-derived dir. */
  dir?: string;
  /** Optional initial markdown content. */
  content?: string;
}

export interface CreatePageResponse {
  id: string;
  file_path: string;
  title: string;
  page_type: string;
}

/**
 * Create a new wiki page.
 * POST /pages { title, page_type, dir?, content? } → 201 CreatePageResponse
 * Throws ApiError(409) if a page with that title/path already exists.
 *
 * AC-R7-2-2: on 201 the caller navigates to the new page in edit mode.
 */
export async function createPage(
  body: CreatePageRequest,
  signal?: AbortSignal,
): Promise<CreatePageResponse> {
  const url = `${apiBase()}/pages`;
  const res = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as CreatePageResponse;
}

/**
 * Fetch vault status (data_version, uptime, started_at).
 * GET /status
 */
export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const url = `${apiBase()}/status`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as StatusResponse;
}
