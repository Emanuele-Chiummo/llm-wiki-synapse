/**
 * providerClient.ts — typed API client for Synapse provider configuration (ADR-0018 §4).
 *
 * GET  /provider/config              → ProviderConfigListResponse
 * POST /provider/config { body }     → ProviderConfigItem (201 Created)
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6) — all values come from the API response.
 */

import type {
  ProviderConfigListResponse,
  ProviderConfigItem,
  CreateProviderConfigBody,
} from "./types";
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
 * Fetch all provider config rows.
 * GET /provider/config
 */
export async function fetchProviderConfigs(
  signal?: AbortSignal,
): Promise<ProviderConfigListResponse> {
  const url = `${API_BASE}/provider/config`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ProviderConfigListResponse;
}

/**
 * Create a new provider config row.
 * POST /provider/config
 * 201 Created → returns the created row.
 */
export async function createProviderConfig(
  body: CreateProviderConfigBody,
  signal?: AbortSignal,
): Promise<ProviderConfigItem> {
  const url = `${API_BASE}/provider/config`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ProviderConfigItem;
}

/**
 * Delete a provider config row by ID.
 * DELETE /provider/config/{id}
 * 204 No Content on success.
 */
export async function deleteProviderConfig(
  id: string,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${API_BASE}/provider/config/${encodeURIComponent(id)}`;
  const res = await fetch(url, {
    method: "DELETE",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}
