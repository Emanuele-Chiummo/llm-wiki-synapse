/**
 * apiTokensClient.ts — typed API client for scoped API tokens (PF-AUTH-1, 1.9.4 W4).
 *
 * POST   /config/api-tokens       → ApiTokenCreateResponse (plaintext shown once, 201)
 * GET    /config/api-tokens       → ApiTokenListResponse (no secret, active tokens only)
 * DELETE /config/api-tokens/{id}  → 204 (soft-delete / revoke)
 *
 * No secrets in this file (CLAUDE.md §12) — the plaintext returned by createApiToken()
 * flows straight into the caller's one-time-reveal state; it is never logged here.
 */

import type {
  ApiTokenCreateRequest,
  ApiTokenCreateResponse,
  ApiTokenListResponse,
} from "./types";
import { ApiError } from "./errors";
import { apiBase, apiFetch } from "./base";
import { formatDetail } from "./providerClient";

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      detail = formatDetail(body.detail) ?? detail;
    } catch {
      // ignore parse error
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

/**
 * List active (non-revoked) API tokens. Never includes the secret or its hash.
 * GET /config/api-tokens
 */
export async function fetchApiTokens(signal?: AbortSignal): Promise<ApiTokenListResponse> {
  const url = `${apiBase()}/config/api-tokens`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ApiTokenListResponse;
}

/**
 * Create a new scoped API token. The response's `token` field is the PLAINTEXT
 * secret shown exactly once — the caller MUST surface it in a one-time reveal UI
 * and never persist it beyond that.
 * POST /config/api-tokens
 */
export async function createApiToken(
  body: ApiTokenCreateRequest,
  signal?: AbortSignal,
): Promise<ApiTokenCreateResponse> {
  const url = `${apiBase()}/config/api-tokens`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ApiTokenCreateResponse;
}

/**
 * Revoke (soft-delete) an API token by id. 204 No Content on success.
 * DELETE /config/api-tokens/{id}
 */
export async function revokeApiToken(id: string, signal?: AbortSignal): Promise<void> {
  const url = `${apiBase()}/config/api-tokens/${encodeURIComponent(id)}`;
  const res = await apiFetch(url, {
    method: "DELETE",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}
