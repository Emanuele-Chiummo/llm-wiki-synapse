/**
 * vaultMetaClient.ts — API client for the vault meta-files endpoint (WS-D8 / K1 / I5).
 *
 * Contract (backend, implemented in parallel):
 *   GET /vault/meta?vault_id=<vaultId>
 *   → 200 { files: [{ name, path, title, content }] }
 *   → 404 when the vault is unknown (treated as empty — graceful)
 *
 * schema.md and purpose.md are never ingested as Page records (they are bootstrap
 * files with no frontmatter type). This endpoint reads them directly from disk so
 * they can be surfaced read-only in the wiki NavTree (P0-3 fix).
 *
 * No secrets in this file (CLAUDE.md §12).
 * ADR-0047 §6 Do-NOT: never cache apiBase() in a module-level const.
 */

import { apiBase } from "./base";
import { fetchWithTimeout } from "./http";
import { ApiError } from "./graphClient";

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface VaultMetaFile {
  /** Filename, e.g. "schema.md" */
  name: string;
  /** Vault-relative path, e.g. "schema.md" */
  path: string;
  /** Human-readable title, e.g. "Schema" */
  title: string;
  /** Raw markdown content */
  content: string;
}

export interface VaultMetaResponse {
  files: VaultMetaFile[];
}

// ─── Client ────────────────────────────────────────────────────────────────────

/**
 * fetchVaultMeta — fetch schema.md and purpose.md for a vault.
 *
 * GET /vault/meta?vault_id=<vaultId>
 *
 * Returns an empty files array when:
 *   - the endpoint returns 404 (vault unknown or endpoint not yet deployed)
 *   - the response is not parseable JSON
 *
 * Throws for non-404 network/server errors so callers can surface them.
 */
export async function fetchVaultMeta(
  vaultId: string = "default",
  signal?: AbortSignal,
): Promise<VaultMetaResponse> {
  const url = `${apiBase()}/vault/meta?vault_id=${encodeURIComponent(vaultId)}`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);

  // 404 → endpoint not yet deployed or vault unknown — treat gracefully as empty.
  if (res.status === 404) {
    return { files: [] };
  }

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

  try {
    return (await res.json()) as VaultMetaResponse;
  } catch {
    // Malformed JSON — treat as empty (graceful degradation).
    return { files: [] };
  }
}

/**
 * saveVaultMeta — write schema.md or purpose.md back to the vault (v1.5 P1, ADR-0066).
 *
 * PUT /vault/meta/{name}  body { content }  → 200 VaultMetaFile
 *
 * `name` must be exactly "schema.md" or "purpose.md" (the backend allow-list); any other
 * value is a 404. Throws ApiError on non-2xx so callers can surface the failure and keep
 * the editor dirty. Unlike fetchVaultMeta, this does NOT swallow errors — a failed save
 * must be visible.
 */
export async function saveVaultMeta(
  name: string,
  content: string,
  signal?: AbortSignal,
): Promise<VaultMetaFile> {
  const url = `${apiBase()}/vault/meta/${encodeURIComponent(name)}`;
  const res = await fetchWithTimeout(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    ...(signal !== undefined ? { signal } : {}),
  });

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

  return (await res.json()) as VaultMetaFile;
}

/**
 * fetchVaultOutputLanguage — read the active vault's AI output language (F3/ADR-0081).
 *
 * GET /vault/meta/output-language → { language: string | null }
 *
 * Returns null when unset (auto-detect) or the endpoint is unavailable (graceful).
 */
export async function fetchVaultOutputLanguage(signal?: AbortSignal): Promise<string | null> {
  const url = `${apiBase()}/vault/meta/output-language`;
  const res = await fetchWithTimeout(url, signal !== undefined ? { signal } : undefined);
  if (!res.ok) return null; // 404 / unavailable → treat as unset (auto)
  try {
    const body = (await res.json()) as { language?: string | null };
    return body.language ?? null;
  } catch {
    return null;
  }
}

/**
 * setVaultOutputLanguage — set the active vault's AI output language (F3/ADR-0081).
 *
 * PUT /vault/meta/output-language  body { language }  → 200
 *
 * Drives the ingest generation + overview language for THIS vault. Existing pages keep the
 * language they were generated in; new ingests (and the next overview regen) use the new
 * language. Throws ApiError on non-2xx so the caller can surface the failure.
 */
export async function setVaultOutputLanguage(
  language: string,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${apiBase()}/vault/meta/output-language`;
  const res = await fetchWithTimeout(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language }),
    ...(signal !== undefined ? { signal } : {}),
  });
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
