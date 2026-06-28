/**
 * ingestClient.ts — typed API client for Synapse ingest endpoints (ADR-0018 §3).
 *
 * GET  /ingest/runs  → IngestRunListResponse
 * POST /ingest/trigger { file_path } → 202 Accepted
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 */

import type { IngestRunListResponse, UploadResponse } from "./types";
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
 * Fetch paginated ingest run history.
 * GET /ingest/runs?limit=<limit>&offset=<offset>[&vault_id=<vaultId>]
 */
export async function fetchIngestRuns(
  options: { limit?: number; offset?: number; vaultId?: string } = {},
  signal?: AbortSignal,
): Promise<IngestRunListResponse> {
  const { limit = 20, offset = 0, vaultId } = options;
  let url = `${API_BASE}/ingest/runs?limit=${limit}&offset=${offset}`;
  if (vaultId) url += `&vault_id=${encodeURIComponent(vaultId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as IngestRunListResponse;
}

/**
 * Trigger an ingest run for a single source file.
 * POST /ingest/trigger { file_path }
 * Returns 202 Accepted on success.
 */
export async function triggerIngest(
  filePath: string,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${API_BASE}/ingest/trigger`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_path: filePath }),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}

/**
 * Upload a document file to the vault.
 * POST /ingest/upload (multipart/form-data, field "file")
 * Returns 202 Accepted { file_path, status:'queued', overwritten } — ingest runs async via the watcher.
 * Errors: 415 (unsupported type), 413 (too large), 422 (unsafe filename).
 *
 * IMPORTANT: do NOT set Content-Type manually — the browser sets the
 * multipart boundary automatically when using FormData.
 *
 * ADR-0020 §3 / Feature U.
 */
export async function uploadDocument(
  file: File,
  signal?: AbortSignal,
): Promise<UploadResponse> {
  const url = `${API_BASE}/ingest/upload`;
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(url, {
    method: "POST",
    body: form,
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as UploadResponse;
}
