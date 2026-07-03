/**
 * ingestClient.ts — typed API client for Synapse ingest endpoints (ADR-0018 §3).
 *
 * GET  /ingest/runs  → IngestRunListResponse
 * POST /ingest/trigger { file_path } → 202 Accepted
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 */

import type {
  IngestRunListResponse,
  UploadResponse,
  IngestQueueSnapshot,
  CancelRunResponse,
  RetryRunResponse,
  PauseQueueResponse,
  ResumeQueueResponse,
} from "./types";
import { ApiError } from "./graphClient";
import { apiBase, apiFetch } from "./base";

/** Sentinel error thrown when retry is refused because retry_count >= 3 */
export class MaxRetriesExceededError extends Error {
  constructor() {
    super("max_retries_exceeded");
    this.name = "MaxRetriesExceededError";
  }
}
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

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
  let url = `${apiBase()}/ingest/runs?limit=${limit}&offset=${offset}`;
  if (vaultId) url += `&vault_id=${encodeURIComponent(vaultId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${apiBase()}/ingest/trigger`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_path: filePath }),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}

// ─── Live ingest queue endpoints (Activity Panel, F1) ─────────────────────────

/**
 * Fetch the live ingest queue snapshot.
 * GET /ingest/queue
 */
export async function getIngestQueue(signal?: AbortSignal): Promise<IngestQueueSnapshot> {
  const url = `${apiBase()}/ingest/queue`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as IngestQueueSnapshot;
}

/**
 * Cancel an active ingest run.
 * POST /ingest/runs/{id}/cancel
 * Returns 202 Accepted; 404 if unknown; 409 if already terminal.
 */
export async function cancelIngestRun(
  id: string,
  signal?: AbortSignal,
): Promise<CancelRunResponse> {
  const url = `${apiBase()}/ingest/runs/${encodeURIComponent(id)}/cancel`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as CancelRunResponse;
}

/**
 * Retry a failed ingest run.
 * POST /ingest/runs/{id}/retry
 * Returns 202 Accepted; 409 with detail:"max_retries_exceeded" when retry_count >= 3; 404 unknown.
 * Throws MaxRetriesExceededError on 409.
 */
export async function retryIngestRun(
  id: string,
  signal?: AbortSignal,
): Promise<RetryRunResponse> {
  const url = `${apiBase()}/ingest/runs/${encodeURIComponent(id)}/retry`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  if (res.status === 409) {
    throw new MaxRetriesExceededError();
  }
  await checkResponse(res);
  return (await res.json()) as RetryRunResponse;
}

/**
 * Pause the ingest queue (idempotent).
 * POST /ingest/queue/pause
 */
export async function pauseIngestQueue(signal?: AbortSignal): Promise<PauseQueueResponse> {
  const url = `${apiBase()}/ingest/queue/pause`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as PauseQueueResponse;
}

/**
 * Resume the ingest queue (idempotent).
 * POST /ingest/queue/resume
 */
export async function resumeIngestQueue(signal?: AbortSignal): Promise<ResumeQueueResponse> {
  const url = `${apiBase()}/ingest/queue/resume`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ResumeQueueResponse;
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
  const url = `${apiBase()}/ingest/upload`;
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch(url, {
    method: "POST",
    body: form,
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as UploadResponse;
}
