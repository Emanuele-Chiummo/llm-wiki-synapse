/**
 * convertClient.ts — typed API client for Marker PDF conversion endpoints [F12][R11-1].
 *
 * Async contract (v1.4 W0 — async marker):
 *   POST /ingest/convert-marker  multipart files (1..10 PDFs) → 202 Accepted immediately
 *   GET  /ingest/convert-marker/status → live batch progress
 *   GET  /ingest/marker-health         → proxy to Marker service health
 *
 * Synchronous error codes (returned immediately, before background work):
 *   400 — more than 10 files
 *   409 — a batch is already running
 *   413 — file too large
 *   415 — non-PDF submitted
 *
 * All calls go through apiFetch (ADR-0052 §4.2 — single auth injection point).
 * No secrets in this file (CLAUDE.md §12).
 */

import { apiBase, apiFetch } from "./base";
import { errorMessageFromBody } from "./errors";

// ─── Batch-submit response (POST 202) ─────────────────────────────────────────

/** One queued file entry from POST /ingest/convert-marker 202 response. */
export interface BatchQueuedItem {
  /** Original filename as submitted. */
  file: string;
  /** Filesystem-safe stem used for output paths. */
  safe_stem: string;
  /** Path where the PDF is staged in the vault. */
  pdf_path: string;
}

/**
 * Response from POST /ingest/convert-marker (202 Accepted).
 * Returned immediately — the actual conversion runs in the background.
 * [F12][I7 bounded: ≤10 files per batch]
 */
export interface ConvertBatchResponse {
  batch_id: string;
  queued: BatchQueuedItem[];
  total: number;
}

// ─── Status-poll response (GET /ingest/convert-marker/status) ────────────────

/** Backend status for one file within a batch. */
export type ConvertFileStatus = "pending" | "converting" | "ok" | "failed";

/** Per-file detail from GET /ingest/convert-marker/status. */
export interface ConvertStatusFile {
  file: string;
  safe_stem: string;
  status: ConvertFileStatus;
  /** Error message when status is "failed", null otherwise. */
  detail: string | null;
  /** Path of the companion Markdown file when status is "ok", null otherwise. */
  companion_path: string | null;
}

/**
 * Response from GET /ingest/convert-marker/status (200).
 * Poll this every ~2.5 s while running is true; stop when running is false.
 */
export interface ConvertStatusResponse {
  batch_id: string;
  /** True while the batch is still processing. */
  running: boolean;
  total: number;
  done: number;
  /** Estimated seconds remaining (null when not calculable). */
  eta_seconds: number | null;
  files: ConvertStatusFile[];
}

// ─── Marker health response types (unchanged) ─────────────────────────────────

/** Response body from GET /ingest/marker-health when Marker is reachable. */
export interface MarkerHealthOk {
  status: "ok";
}

/** Response body from GET /ingest/marker-health when Marker is offline. */
export interface MarkerHealthOffline {
  status: "offline";
  detail: string;
}

export type MarkerHealthResponse = MarkerHealthOk | MarkerHealthOffline;

// ─── ConvertError ──────────────────────────────────────────────────────────────

/**
 * Error thrown when POST /ingest/convert-marker returns a synchronous error:
 *   400 — >10 files submitted
 *   409 — a batch is already running
 *   413 — file exceeds server size limit
 *   415 — non-PDF file submitted
 *
 * The `status` field carries the HTTP status code so callers can render
 * the correct i18n message.
 */
export class ConvertError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ConvertError";
    this.status = status;
    this.detail = detail;
  }
}

// ─── startConvert ─────────────────────────────────────────────────────────────

/**
 * startConvert — POST /ingest/convert-marker with a multipart files field.
 *
 * Submits 1..10 PDF files and returns immediately (202 Accepted) once the
 * backend has queued the batch.  The actual conversion runs in the background.
 *
 * On synchronous error (400/409/413/415) throws ConvertError with the backend
 * detail string so the UI can surface a meaningful message.
 *
 * IMPORTANT: do NOT set Content-Type manually — the browser sets the multipart
 * boundary automatically when using FormData.
 *
 * [F12][R11-1][I7 bounded: ≤10 files per call]
 */
export async function startConvert(
  files: File[],
  signal?: AbortSignal,
): Promise<ConvertBatchResponse> {
  const url = `${apiBase()}/ingest/convert-marker`;
  const form = new FormData();
  for (const file of files) {
    // Field name MUST be "files" (matches the FastAPI param `files: list[UploadFile]`).
    form.append("files", file);
  }
  const res = await apiFetch(url, {
    method: "POST",
    body: form,
    ...(signal !== undefined ? { signal } : {}),
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorMessageFromBody(body) ?? detail;
    } catch {
      // ignore parse error — use status text
    }
    throw new ConvertError(res.status, detail);
  }

  return (await res.json()) as ConvertBatchResponse;
}

// ─── getConvertStatus ─────────────────────────────────────────────────────────

/**
 * getConvertStatus — GET /ingest/convert-marker/status.
 *
 * Returns the live progress of the current (or last) batch.
 * Poll this every ~2.5 s while `running` is true; stop when `running` is false.
 *
 * [F12][R11-1]
 */
export async function getConvertStatus(signal?: AbortSignal): Promise<ConvertStatusResponse> {
  const url = `${apiBase()}/ingest/convert-marker/status`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorMessageFromBody(body) ?? detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status} ${detail}`);
  }

  return (await res.json()) as ConvertStatusResponse;
}

// ─── getMarkerHealth ───────────────────────────────────────────────────────────

/**
 * getMarkerHealth — GET /ingest/marker-health.
 *
 * Proxied by the backend to {MARKER_SERVICE_URL}/health.
 * Returns { status: "ok" } (200) or { status: "offline", detail: "..." } (503).
 * Never throws — network errors are caught and returned as offline status.
 *
 * [F12][R11-1][AC-R11-1-4]
 */
export async function getMarkerHealth(signal?: AbortSignal): Promise<MarkerHealthResponse> {
  try {
    const url = `${apiBase()}/ingest/marker-health`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    const body = (await res.json()) as MarkerHealthResponse;
    return body;
  } catch {
    return { status: "offline", detail: "Could not reach health endpoint" };
  }
}
