/**
 * sourcesClient.ts — typed API client for Synapse raw-source endpoints [F11 / v0.6].
 *
 * Endpoints consumed:
 *   GET  /sources                    → SourceListResponse
 *   GET  /sources/content?path=<rel> → SourceContentResponse
 *   GET  /sources/derived-pages?path=<rel> → SourceDerivedPagesResponse
 *   DELETE /sources?path=<rel>       → SourceDeleteResponse
 *   GET  /sources/raw?path=<rel>     → raw bytes (URL only — browser fetches directly)
 *   POST /ingest/trigger             → 202 (re-exported from ingestClient for convenience)
 *   POST /sources/ingest-all         → 202 { started, candidate_files } | 409
 *   GET  /sources/ingest-all/status  → { running, done, total }
 *
 * All `path` values are relative to raw/sources/ with forward slashes.
 * triggerIngest is re-exported from ingestClient so callers import both from one place.
 *
 * INVARIANT I3: no per-token work here; functions are pure async, no store subscriptions.
 * No secrets in this file (CLAUDE.md §12).
 */

import { ApiError } from "./graphClient";
import { apiBase, apiFetch } from "./base";

export { triggerIngest } from "./ingestClient";

// ─── Base URL ─────────────────────────────────────────────────────────────────
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

// ─── Types ────────────────────────────────────────────────────────────────────

/** One entry in the sources tree (file or directory). */
export interface SourceEntry {
  /** Relative path from raw/sources/ (forward slashes). */
  path: string;
  /** Display name (basename). */
  name: string;
  /** True when this entry is a directory. */
  is_dir: boolean;
  /** File extension including dot (absent for directories). */
  ext?: string;
  /** File size in bytes (absent for directories). */
  size_bytes?: number;
  /** Last-modified ISO-8601 timestamp (absent for directories). */
  mtime?: string;
}

/** Response from GET /sources. */
export interface SourceListResponse {
  entries: SourceEntry[];
  total: number;
  truncated: boolean;
}

/**
 * Content category returned by GET /sources/content.
 * Maps to preview strategy in SourcePreview.
 */
export type SourceCategory =
  | "text"
  | "markdown"
  | "image"
  | "pdf"
  | "document"
  | "data"
  | "code"
  | "av"
  | "other";

/** Response from GET /sources/content?path=<rel>. */
export interface SourceContentResponse {
  path: string;
  name: string;
  ext: string;
  size_bytes: number;
  mtime: string;
  category: SourceCategory;
  /** True when the file can be represented as UTF-8 text. */
  is_text: boolean;
  /** Present when is_text=true. */
  text?: string;
  /** True when at least one ingest run has processed this file. */
  ingested: boolean;
  /** IDs of wiki pages derived from this source. */
  page_ids: string[];
}

/** One derived wiki page from GET /sources/derived-pages. */
export interface SourceDerivedPage {
  id: string;
  title?: string;
  page_type?: string;
  file_path: string;
}

/** Response from DELETE /sources?path=<rel> (file). */
export interface SourceDeleteResponse {
  deleted_source: string;
  pages_deleted: number;
}

/**
 * Response from DELETE /sources?path=<dir> (directory).
 * Backend returns this shape when the path is a directory (recursive cascade).
 * 409 is thrown as ApiError when the directory exceeds the backend-configured max files.
 */
export interface SourceDeleteFolderResponse {
  deleted_source: string;
  files_deleted: number;
  pages_cascaded: number;
}

/** Response from POST /sources/ingest-all (202). */
export interface IngestAllResponse {
  /** True when a new background scan was started. False if there were no candidate files. */
  started: boolean;
  /** Number of files that are candidates for ingestion. */
  candidate_files: number;
}

/** Response from GET /sources/ingest-all/status. */
export interface IngestAllStatusResponse {
  /** True while the background scan is running. */
  running: boolean;
  /** Number of files processed so far in the current scan. */
  done: number;
  /** Total number of candidate files in the current scan. */
  total: number;
}

/**
 * Sentinel error thrown when POST /sources/ingest-all returns 409 (scan already running).
 * The UI can catch this specifically to show "already running" rather than a generic error.
 */
export class IngestAllRunningError extends Error {
  constructor() {
    super("ingest-all already running");
    this.name = "IngestAllRunningError";
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore JSON parse failure
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

// ─── API functions ────────────────────────────────────────────────────────────

/**
 * List the raw-source file tree.
 * GET /sources → SourceListResponse
 */
export async function listSources(signal?: AbortSignal): Promise<SourceListResponse> {
  const url = `${apiBase()}/sources`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as SourceListResponse;
}

/**
 * Fetch metadata + text content for a single source file.
 * GET /sources/content?path=<rel> → SourceContentResponse
 * Path is URL-encoded automatically.
 */
export async function getSourceContent(
  path: string,
  signal?: AbortSignal,
): Promise<SourceContentResponse> {
  const url = `${apiBase()}/sources/content?path=${encodeURIComponent(path)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as SourceContentResponse;
}

/**
 * Fetch the wiki pages derived from a source file.
 * GET /sources/derived-pages?path=<rel> → SourceDerivedPage[]
 */
export async function getSourceDerivedPages(
  path: string,
  signal?: AbortSignal,
): Promise<SourceDerivedPage[]> {
  const url = `${apiBase()}/sources/derived-pages?path=${encodeURIComponent(path)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as SourceDerivedPage[];
}

/**
 * Delete a raw source file (and optionally its derived wiki pages).
 * DELETE /sources?path=<rel> → SourceDeleteResponse
 */
export async function deleteSource(
  path: string,
  signal?: AbortSignal,
): Promise<SourceDeleteResponse> {
  const url = `${apiBase()}/sources?path=${encodeURIComponent(path)}`;
  const res = await apiFetch(url, {
    method: "DELETE",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as SourceDeleteResponse;
}

/**
 * Build the direct URL to raw file bytes.
 * Use as src for <img>, <embed>, <audio>, <video> — the browser fetches it directly.
 * INVARIANT I3: never load raw bytes into JS; pass the URL to DOM elements only.
 */
export function sourceRawUrl(path: string): string {
  return `${apiBase()}/sources/raw?path=${encodeURIComponent(path)}`;
}

/**
 * Start a background scan that force-ingests every file in raw/sources/.
 * POST /sources/ingest-all → 202 IngestAllResponse
 * Throws IngestAllRunningError on 409 (scan already in progress).
 * INVARIANT I3: pure async, no side effects beyond the network call.
 */
export async function ingestAllSources(signal?: AbortSignal): Promise<IngestAllResponse> {
  const url = `${apiBase()}/sources/ingest-all`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  if (res.status === 409) {
    throw new IngestAllRunningError();
  }
  await checkResponse(res);
  return (await res.json()) as IngestAllResponse;
}

/**
 * Poll the progress of a running ingest-all scan.
 * GET /sources/ingest-all/status → IngestAllStatusResponse
 * When running=false the scan has finished (or was never started).
 * INVARIANT I3: pure async, no side effects.
 */
export async function getIngestAllStatus(signal?: AbortSignal): Promise<IngestAllStatusResponse> {
  const url = `${apiBase()}/sources/ingest-all/status`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as IngestAllStatusResponse;
}

/**
 * Delete a raw source directory and all files within it (recursive cascade).
 * DELETE /sources?path=<dir> → SourceDeleteFolderResponse
 * 409 ApiError is thrown when the directory exceeds the backend max-files safety cap.
 * INVARIANT I7: bounded by the backend safety cap; client surfaces 409 as a toast.
 */
export async function deleteFolderSource(
  path: string,
  signal?: AbortSignal,
): Promise<SourceDeleteFolderResponse> {
  const url = `${apiBase()}/sources?path=${encodeURIComponent(path)}`;
  const res = await apiFetch(url, {
    method: "DELETE",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as SourceDeleteFolderResponse;
}
