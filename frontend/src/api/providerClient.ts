/**
 * providerClient.ts — typed API client for Synapse provider configuration (ADR-0018 §4).
 *
 * GET  /provider/config              → ProviderConfigListResponse
 * POST /provider/config { body }     → ProviderConfigItem (201 Created)
 * GET  /config/embedding             → EmbeddingConfig (read-only, from env vars)
 * GET  /mcp/info                     → McpInfoResponse (read-only, ADR-0027; extended ADR-0032/0033)
 * PUT  /mcp/remote                   → McpRemoteStateResponse (ADR-0032)
 * PUT  /mcp/auth                     → McpAuthResponse (ADR-0033)
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6) — all values come from the API response.
 */

import type {
  ProviderConfigListResponse,
  ProviderConfigItem,
  CreateProviderConfigBody,
  UpdateProviderConfigBody,
  VendorListResponse,
  ProviderTestRequest,
  ProviderTestResponse,
  ClipConfigResponse,
  ClipConfigRequest,
  ClipConfigStateResponse,
  WebSearchConfigResponse,
  WebSearchConfigRequest,
  WebSearchConfigStateResponse,
  CliAuthConfig,
  CliAuthUpdateRequest,
} from "./types";
import { ApiError } from "./graphClient";
import { apiBase, apiFetch } from "./base";
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
 * Fetch all provider config rows.
 * GET /provider/config
 */
export async function fetchProviderConfigs(
  signal?: AbortSignal,
): Promise<ProviderConfigListResponse> {
  const url = `${apiBase()}/provider/config`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${apiBase()}/provider/config`;
  const res = await apiFetch(url, {
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
  const url = `${apiBase()}/provider/config/${encodeURIComponent(id)}`;
  const res = await apiFetch(url, {
    method: "DELETE",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}

/**
 * Partial-update an existing provider config row.
 * PUT /provider/config/{id}
 * 200 OK → returns the updated row.
 *
 * api_key handling: absent=unchanged, non-empty string=replace, ""=clear.
 */
export async function updateProviderConfig(
  id: string,
  body: UpdateProviderConfigBody,
  signal?: AbortSignal,
): Promise<ProviderConfigItem> {
  const url = `${apiBase()}/provider/config/${encodeURIComponent(id)}`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ProviderConfigItem;
}

/**
 * Fetch the vendor catalog (static list of supported providers).
 * GET /provider/vendors
 * Returns 15 vendors; needs_api_key + model_presets drive the expanded-row UI.
 */
export async function fetchVendors(
  signal?: AbortSignal,
): Promise<VendorListResponse> {
  const url = `${apiBase()}/provider/vendors`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as VendorListResponse;
}

/**
 * Run a connection smoke-test (can the backend reach the provider?).
 * POST /provider/test/connection
 */
export async function testProviderConnection(
  body: ProviderTestRequest,
  signal?: AbortSignal,
): Promise<ProviderTestResponse> {
  const url = `${apiBase()}/provider/test/connection`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ProviderTestResponse;
}

/**
 * Run a function/tool-call smoke-test against the provider.
 * POST /provider/test/function
 */
export async function testProviderFunction(
  body: ProviderTestRequest,
  signal?: AbortSignal,
): Promise<ProviderTestResponse> {
  const url = `${apiBase()}/provider/test/function`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ProviderTestResponse;
}

export interface EmbeddingConfig {
  embedding_url: string;
  embedding_model: string;
  embedding_dim: number;
  /** ADR-0030: reflects EMBEDDINGS_ENABLED env var — read-only, not an interactive toggle. */
  embeddings_enabled: boolean;
}

/**
 * Fetch current embedding configuration (read-only, from env vars).
 * GET /config/embedding
 */
export async function fetchEmbeddingConfig(
  signal?: AbortSignal,
): Promise<EmbeddingConfig> {
  const url = `${apiBase()}/config/embedding`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as EmbeddingConfig;
}

// ─── MCP info (ADR-0027) ──────────────────────────────────────────────────────

/** One tool as returned by GET /mcp/info. All values come from the live MCP registry (I6). */
export interface McpToolInfo {
  name: string;
  description: string;
  /** JSON-Schema object for the tool arguments. Properties count = param count. */
  input_schema: {
    type?: string;
    properties?: Record<string, unknown>;
    required?: string[];
    [key: string]: unknown;
  };
}

/**
 * Response from GET /mcp/info (ADR-0027 §2.1, extended ADR-0032 §2.5, ADR-0033 §2.5).
 *
 * Fields added in ADR-0032:
 *   http_enabled        — true iff MCP_AUTH_TOKEN is set (alias: token_configured). Retained for
 *                         backward compat; both reflect the same boolean.
 *   remote_write_enabled — env-driven; whether write_page is on the HTTP surface.
 *   token_configured    — named-for-UI alias of http_enabled.
 *   remote_enabled      — the persisted runtime toggle flag (RemoteMcpFlag in the server process).
 *   mount_path          — the mount constant ("/mcp/server"); UI builds the URL as
 *                         window.location.origin + mount_path (I6 — no host hardcoded).
 *
 * Fields added in ADR-0033:
 *   token_source        — which token is authoritative: "db" (UI-set hash), "env" (bootstrap
 *                         fallback), or "none" (no token configured). NEVER the token value.
 *   allow_without_token — the persisted "allow access without token" flag. When true, private
 *                         sources (loopback/LAN/Tailscale) may connect without a bearer token;
 *                         public (Cloudflare tunnel) sources are always token-gated (ADR-0033 §2.3).
 *
 * The bearer token and its hash are NEVER returned by any GET (ADR-0032 §2.5, ADR-0033 §2.1).
 */
export interface McpInfoResponse {
  server_name: string;
  transport: string;
  entry_point_command: string;
  tool_count: number;
  tools: McpToolInfo[];
  /** True iff MCP_AUTH_TOKEN is set. Retained for backward compat (ADR-0029). */
  http_enabled: boolean;
  /** True iff MCP_REMOTE_WRITE_ENABLED env var is set. Env-driven, not toggled in UI. */
  remote_write_enabled: boolean;
  /** Named-for-UI alias of http_enabled: true iff a bearer token is configured (ADR-0032). */
  token_configured: boolean;
  /** The persisted runtime toggle flag. True means the /mcp/server endpoint is reachable. */
  remote_enabled: boolean;
  /** The server mount path constant, e.g. "/mcp/server". URL = origin + mount_path (I6). */
  mount_path: string;
  /**
   * Which token is authoritative (ADR-0033 §2.5):
   *   "db"   — UI-set token (PBKDF2 hash stored in vault_state.mcp_access_token_hash)
   *   "env"  — bootstrap env token (MCP_AUTH_TOKEN, plaintext compare)
   *   "none" — no token configured
   * The token value and hash are never returned. Use token_configured for boolean gating.
   */
  token_source: "db" | "env" | "none";
  /**
   * Whether "allow access without a token" is enabled (ADR-0033 §2.3).
   * When true, private-source (loopback/LAN/Tailscale) requests may connect unauthenticated.
   * Public (Cloudflare tunnel) sources always require the token regardless of this flag.
   */
  allow_without_token: boolean;
}

// ─── MCP auth (ADR-0033) ─────────────────────────────────────────────────────

/**
 * Request body for PUT /mcp/auth (ADR-0033 §2.5).
 * All fields are optional; omitted = unchanged.
 *
 * Mutual exclusion: only one of rotate_token / token / clear_token should be set per call.
 */
export interface McpAuthRequest {
  /**
   * Generate a new random high-entropy token, store its PBKDF2 hash, and return the
   * plaintext ONCE in the response as `generated_token`. Never echoed again after this call.
   */
  rotate_token?: boolean;
  /**
   * Set an owner-supplied explicit token. Stored as PBKDF2 hash; NOT echoed back.
   * `generated_token` will be null in the response (owner already knows it).
   */
  token?: string;
  /** Clear the stored token hash (sets mcp_access_token_hash = NULL). */
  clear_token?: boolean;
  /**
   * Set the "allow access without a token" flag. Applies only to private-source requests
   * (loopback / LAN / Tailscale). Public (Cloudflare tunnel) is always token-gated (ADR-0033 §2.3).
   */
  allow_without_token?: boolean;
}

/**
 * Response from PUT /mcp/auth (ADR-0033 §2.5).
 * Always returns the authoritative posture after the write.
 *
 * CRITICAL: `generated_token` is present ONLY when the request included `rotate_token: true`.
 * It MUST be shown to the user exactly once and then discarded — it is never returned again.
 * The UI must never persist it in any store (I3: local state only, cleared on dismiss).
 */
export interface McpAuthResponse {
  token_configured: boolean;
  token_source: "db" | "env" | "none";
  allow_without_token: boolean;
  remote_enabled: boolean;
  mount_path: string;
  /**
   * The generated token plaintext — present ONLY for rotate_token:true requests.
   * Show once, discard, never store in Zustand or localStorage (ADR-0033 §2.1).
   */
  generated_token?: string | null;
}

/**
 * Response from PUT /mcp/remote (ADR-0032 §2.4).
 * Returns the authoritative posture after the write (post-clamp).
 */
export interface McpRemoteStateResponse {
  /** The resulting persisted runtime flag (post-clamp). */
  remote_enabled: boolean;
  /** Whether MCP_AUTH_TOKEN is set — the security floor. */
  token_configured: boolean;
  /** The mount path constant, e.g. "/mcp/server". */
  mount_path: string;
  /**
   * True iff the request asked enabled=true but no token is configured — the server
   * refused to enable and clamped to false. The UI must treat this as "still off" and
   * show the no-token hint (ADR-0032 §2.4).
   */
  clamped: boolean;
}

/**
 * Fetch MCP server introspection (read-only, from the live FastMCP registry).
 * GET /mcp/info  — ADR-0027 §2.1 / ADR-0032 §2.5.
 * Display only — no tool invocation (I9). The toggle PUT is separate (setRemoteMcpEnabled).
 */
export async function fetchMcpInfo(
  signal?: AbortSignal,
): Promise<McpInfoResponse> {
  const url = `${apiBase()}/mcp/info`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as McpInfoResponse;
}

/**
 * Toggle the remote MCP endpoint on or off (ADR-0032 §2.4).
 * PUT /mcp/remote  — body: { enabled: boolean }.
 *
 * Always returns 200 with the authoritative post-clamp posture. If clamped=true, the
 * server refused to enable because no token is configured; the UI must keep the toggle
 * off and show the no-token hint.
 *
 * I3: this is a single fetch/PUT called on toggle interaction — no store churn.
 */
export async function setRemoteMcpEnabled(
  enabled: boolean,
  signal?: AbortSignal,
): Promise<McpRemoteStateResponse> {
  const url = `${apiBase()}/mcp/remote`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as McpRemoteStateResponse;
}

/**
 * Set, rotate, clear the MCP access token or toggle "allow without token" (ADR-0033 §2.5).
 * PUT /mcp/auth  — body: McpAuthRequest.
 *
 * Returns the authoritative posture after the write. If `rotate_token` was true in the
 * request, `generated_token` in the response contains the plaintext exactly ONCE —
 * the caller must display it immediately and then discard it (never store it).
 *
 * I3: single fetch/PUT per user interaction; no Zustand store churn.
 * ADR-0033 §2.1: the token value is never returned by GET /mcp/info;
 *   generated_token appears only in this PUT response when rotate_token=true.
 */
export async function setMcpAuth(
  body: McpAuthRequest,
  signal?: AbortSignal,
): Promise<McpAuthResponse> {
  const url = `${apiBase()}/mcp/auth`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as McpAuthResponse;
}

// ─── Web Clipper config (F11, ADR-0040) ──────────────────────────────────────

/**
 * Fetch current web clipper ingress posture.
 * GET /clip/config — ADR-0040 §2.3.
 * Returns posture-only: enabled, token_configured, token_source, allowed_origins,
 * max_body_bytes. Token value NEVER returned.
 * I3: single fetch on mount; no Zustand store.
 */
export async function fetchClipConfig(
  signal?: AbortSignal,
): Promise<ClipConfigResponse> {
  const url = `${apiBase()}/clip/config`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ClipConfigResponse;
}

/**
 * Set, rotate, or clear the clip ingress token + enabled/origins (ADR-0040 §2.4).
 * PUT /clip/config — body: ClipConfigRequest.
 *
 * Returns authoritative posture after write. If rotate_token=true, generated_token
 * contains the plaintext exactly ONCE — caller must display and discard (never store).
 *
 * I3: single fetch/PUT per user interaction; no Zustand store churn.
 */
export async function setClipConfig(
  body: ClipConfigRequest,
  signal?: AbortSignal,
): Promise<ClipConfigStateResponse> {
  const url = `${apiBase()}/clip/config`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ClipConfigStateResponse;
}

// ─── Web Search config (F10, ADR-0041) ───────────────────────────────────────

/**
 * Fetch current SearXNG web-search posture.
 * GET /web-search/config — ADR-0041 §2.3.
 * The URL is NOT a secret and IS returned in full (unlike clip/mcp tokens).
 * SearXNG is the ONLY supported web-search backend (I9).
 * I3: single fetch on mount; no Zustand store.
 */
export async function fetchWebSearchConfig(
  signal?: AbortSignal,
): Promise<WebSearchConfigResponse> {
  const url = `${apiBase()}/web-search/config`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as WebSearchConfigResponse;
}

/**
 * Set or clear the SearXNG web-search configuration (ADR-0041 §2.4).
 * PUT /web-search/config — body: WebSearchConfigRequest.
 *
 * No provider field — SearXNG is the ONLY web-search backend (I9).
 * Returns authoritative posture after write.
 *
 * I3: single fetch/PUT per user interaction; no Zustand store churn.
 */
export async function setWebSearchConfig(
  body: WebSearchConfigRequest,
  signal?: AbortSignal,
): Promise<WebSearchConfigStateResponse> {
  const url = `${apiBase()}/web-search/config`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as WebSearchConfigStateResponse;
}

// ─── CLI Auth config (F17, ADR-0043) ─────────────────────────────────────────

/**
 * Fetch current CLI subscription auth posture (ADR-0043 §2.5).
 * GET /provider/cli-auth
 *
 * Returns posture-only: token_configured, token_source, auth_mode.
 * The token value is NEVER returned (ADR-0043 Do-NOT #2).
 *
 * The /provider prefix is already in the API_PREFIXES NetworkOnly list
 * and the vite dev proxy — no vite config change needed.
 *
 * I3: single fetch on mount; no Zustand store.
 */
export async function getCliAuthConfig(
  signal?: AbortSignal,
): Promise<CliAuthConfig> {
  const url = `${apiBase()}/provider/cli-auth`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as CliAuthConfig;
}

/**
 * Set or clear the CLI subscription OAuth token (ADR-0043 §2.5).
 * PUT /provider/cli-auth — body: CliAuthUpdateRequest.
 *
 * Set:   body { token: "<pasted value>" } — stores in vault_state.cli_oauth_token.
 * Clear: body { clear: true }             — sets cli_oauth_token = NULL.
 *
 * Returns the authoritative posture after the write.
 * The token value is NEVER returned (ADR-0043 Do-NOT #2).
 * The server does NOT generate a token — the user pastes their own
 * from `claude setup-token` (ADR-0043 Do-NOT #7).
 *
 * Status codes:
 *   200 — set or clear succeeded; response is the post-write posture.
 *   400 — empty body (neither token nor clear).
 *   422 — token is empty/whitespace or absurd length.
 *
 * I3: single fetch/PUT per user interaction; no Zustand store churn.
 */
export async function setCliAuthConfig(
  body: CliAuthUpdateRequest,
  signal?: AbortSignal,
): Promise<CliAuthConfig> {
  const url = `${apiBase()}/provider/cli-auth`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as CliAuthConfig;
}
