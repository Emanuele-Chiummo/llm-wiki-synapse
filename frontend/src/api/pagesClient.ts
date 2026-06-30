/**
 * pagesClient.ts — typed API client for Synapse page-list and status endpoints.
 *
 * Keeps graphClient.ts focused on graph-only concerns (GET /graph, PATCH /position).
 * Base URL from VITE_API_BASE env var (default: "" (relative, proxied in dev / same-origin in prod)).
 * No secrets in this file (CLAUDE.md §12).
 */

import type {
  PageListResponse,
  PageContentResponse,
  PageContentPutResponse,
  StatusResponse,
} from "./types";
import { ApiError } from "./graphClient";
import { fetchWithTimeout } from "./http";

const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "";

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
    `${API_BASE}/pages?vault_id=${encodeURIComponent(vaultId)}` +
    `&limit=${limit}&offset=${offset}`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageListResponse;
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
  const url = `${API_BASE}/pages/${encodeURIComponent(pageId)}/content`;
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
  const url = `${API_BASE}/pages/${encodeURIComponent(pageId)}/content`;
  const body: { content: string; expected_hash: string | null } = {
    content,
    expected_hash: expectedHash,
  };
  const res = await fetchWithTimeout(
    url,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      ...(signal !== undefined ? { signal } : {}),
    },
  );
  await checkResponse(res);
  return (await res.json()) as PageContentPutResponse;
}

/**
 * Fetch vault status (data_version, uptime, started_at).
 * GET /status
 */
export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const url = `${API_BASE}/status`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as StatusResponse;
}
