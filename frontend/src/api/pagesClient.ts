/**
 * pagesClient.ts — typed API client for Synapse page-list and status endpoints.
 *
 * Keeps graphClient.ts focused on graph-only concerns (GET /graph, PATCH /position).
 * Base URL from VITE_API_BASE env var (default: http://localhost:8000).
 * No secrets in this file (CLAUDE.md §12).
 */

import type { PageListResponse, StatusResponse } from "./types";
import { ApiError } from "./graphClient";

const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "http://localhost:8000";

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
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageListResponse;
}

/**
 * Fetch vault status (data_version, uptime, started_at).
 * GET /status
 */
export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const url = `${API_BASE}/status`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as StatusResponse;
}
