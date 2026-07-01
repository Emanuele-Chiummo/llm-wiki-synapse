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
 *
 * All `path` values are relative to raw/sources/ with forward slashes.
 * triggerIngest is re-exported from ingestClient so callers import both from one place.
 *
 * INVARIANT I3: no per-token work here; functions are pure async, no store subscriptions.
 * No secrets in this file (CLAUDE.md §12).
 */

import { ApiError } from "./graphClient";

export { triggerIngest } from "./ingestClient";

// ─── Base URL ─────────────────────────────────────────────────────────────────

const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "";

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

/** Response from DELETE /sources?path=<rel>. */
export interface SourceDeleteResponse {
  deleted_source: string;
  pages_deleted: number;
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
  const url = `${API_BASE}/sources`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${API_BASE}/sources/content?path=${encodeURIComponent(path)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${API_BASE}/sources/derived-pages?path=${encodeURIComponent(path)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${API_BASE}/sources?path=${encodeURIComponent(path)}`;
  const res = await fetch(url, {
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
  return `${API_BASE}/sources/raw?path=${encodeURIComponent(path)}`;
}
