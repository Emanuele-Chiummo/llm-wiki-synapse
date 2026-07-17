/**
 * cascadeDeleteClient.ts — typed API client for F13 cascade-delete endpoints.
 *
 * POST /pages/{id}/cascade-delete/preview  → CascadePreviewResponse (dry-run, read-only)
 * DELETE /pages/{id}                        → CascadeDeleteResult   (single-pass apply)
 *
 * ADR-0026 §6.1 (REST surface). No secrets in this file (CLAUDE.md §12).
 * Base URL from VITE_API_BASE env var (default: "" (relative, proxied in dev / same-origin in prod)).
 */

import type { CascadePreviewResponse, CascadeDeleteResult } from "./types";
import { checkResponse } from "./errors";
import { apiBase, apiFetch } from "./base";
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

/**
 * Dry-run: fetch the cascade-delete plan for a page WITHOUT mutating anything.
 * POST /pages/{pageId}/cascade-delete/preview → 200 CascadePreviewResponse
 * 404 if the page does not exist or is already soft-deleted.
 */
export async function previewCascadeDelete(
  pageId: string,
  signal?: AbortSignal,
): Promise<CascadePreviewResponse> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}/cascade-delete/preview`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as CascadePreviewResponse;
}

/**
 * Apply: execute the cascade delete (single pass, inference-free).
 * DELETE /pages/{pageId} → 200 CascadeDeleteResult
 * 404 on non-existent or already-soft-deleted page (idempotent double-delete, AC-F13-5c).
 */
export async function cascadeDelete(
  pageId: string,
  signal?: AbortSignal,
): Promise<CascadeDeleteResult> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}`;
  const res = await apiFetch(url, {
    method: "DELETE",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as CascadeDeleteResult;
}
