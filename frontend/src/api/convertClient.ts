/**
 * convertClient.ts — typed API client for Marker PDF conversion endpoints [F12][R11-1].
 *
 * POST /ingest/convert-marker  multipart files[] (1..10 PDFs)
 * GET  /ingest/marker-health   proxy to Marker service health
 *
 * All calls go through apiFetch (ADR-0052 §4.2 — single auth injection point).
 * No secrets in this file (CLAUDE.md §12).
 */

import { apiBase, apiFetch } from "./base";

// ─── Response types ────────────────────────────────────────────────────────────

/** Successful per-file conversion result from POST /ingest/convert-marker. */
export interface ConvertFileResult {
  /** Original filename as submitted. */
  filename: string;
  /** Path written to vault/raw/sources/ (watcher will pick it up). */
  output_path: string;
  /** Marker extraction status string. */
  status: "ok";
}

/**
 * Error body returned when Marker is unreachable or returns a non-2xx status.
 * Backend contract: HTTP 502, body {"error":"marker_unavailable","detail":"..."}.
 */
export interface MarkerUnavailableError {
  error: "marker_unavailable";
  detail: string;
}

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
 * Error thrown when a convert-marker call returns 502 (Marker unreachable).
 * Carries the backend `detail` message for display in per-file status rows.
 */
export class MarkerError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "MarkerError";
    this.status = status;
    this.detail = detail;
  }
}

// ─── convertFiles ──────────────────────────────────────────────────────────────

/**
 * convertFiles — POST /ingest/convert-marker with a multipart files[] field.
 *
 * Submits 1..10 PDF files. On success returns the list of converted file results.
 * On Marker error (502) throws MarkerError with the backend detail string.
 * On other HTTP errors throws a generic Error.
 *
 * IMPORTANT: do NOT set Content-Type manually — the browser sets the multipart
 * boundary automatically when using FormData (same pattern as uploadDocument).
 *
 * [F12][R11-1][I7 bounded: ≤10 files per call]
 */
export async function convertFiles(
  files: File[],
  signal?: AbortSignal,
): Promise<ConvertFileResult[]> {
  const url = `${apiBase()}/ingest/convert-marker`;
  const form = new FormData();
  for (const file of files) {
    form.append("files[]", file);
  }
  const res = await apiFetch(url, {
    method: "POST",
    body: form,
    ...(signal !== undefined ? { signal } : {}),
  });

  if (res.status === 502) {
    let detail = "Marker unavailable";
    try {
      const body = (await res.json()) as Partial<MarkerUnavailableError>;
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error
    }
    throw new MarkerError(502, detail);
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status} ${detail}`);
  }

  return (await res.json()) as ConvertFileResult[];
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
