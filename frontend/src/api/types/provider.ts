/**
 * Inference-provider API contract types (F17, FE-QUAL-11 split of api/types.ts).
 * GET/POST /provider/config · GET /provider/vendors · GET/PUT /provider/cli-auth
 */

// ─── GET/POST /provider/config (ADR-0018 §4) ─────────────────────────────────

export interface ProviderConfigItem {
  id: string;
  scope: "global" | "vault";
  operation: string | null;
  vault_id: string | null;
  provider_type: string; // "local" | "api" | "cli" — no hardcoded values (I6)
  model_id: string | null;
  base_url: string | null;
  max_iter: number | null;
  token_budget: number | null;
  is_fallback: boolean;
  created_at: string;
  updated_at: string;
  /**
   * v1.4 additions (F17 vendor catalog).
   * Optional for backward compat with pre-v1.4 backends.
   */
  api_key_configured?: boolean;
  api_key_masked?: string | null;
  reasoning_effort?: string | null;
}

export interface ProviderConfigListResponse {
  items: ProviderConfigItem[];
  total: number;
}

export interface CreateProviderConfigBody {
  scope: "global" | "vault";
  vault_id?: string | null;
  operation?: string | null;
  provider_type: string;
  model_id?: string | null;
  base_url?: string | null;
  max_iter?: number | null;
  token_budget?: number | null;
  is_fallback?: boolean;
  /** write-only plaintext; stored encrypted. 400 if SYNAPSE_SECRET_KEY not set. */
  api_key?: string;
  reasoning_effort?: string | null;
}

// ─── v1.4 vendor catalog (F17) ──────────────────────────────────────────────

/**
 * One vendor entry from GET /provider/vendors.
 * 15 supported vendors: anthropic, claude-cli, codex-cli, openai, gemini,
 * azure-openai, deepseek, atlas-cloud, groq, xai, nvidia-nim, kimi-moonshot,
 * kimi-cn, kimi-coding, ollama.
 */
export interface VendorInfo {
  id: string;
  display_name: string;
  provider_type: "api" | "local" | "cli";
  default_base_url: string | null;
  needs_api_key: boolean;
  model_presets: string[];
  notes: string;
}

export interface VendorListResponse {
  vendors: VendorInfo[];
}

/**
 * Body for PUT /provider/config/{id} (partial update).
 * api_key: absent=unchanged, non-empty=replace, ""=clear.
 */
export interface UpdateProviderConfigBody {
  model_id?: string | null;
  base_url?: string | null;
  /** absent=unchanged, non-empty=replace, ""=clear. */
  api_key?: string;
  reasoning_effort?: string | null;
  scope?: "global" | "vault";
  vault_id?: string | null;
  operation?: string | null;
}

/**
 * Body for POST /provider/test/connection and POST /provider/test/function.
 * Either config_id (use existing config) or inline ad-hoc credentials.
 */
export interface ProviderTestRequest {
  config_id?: string;
  provider_type?: string;
  model?: string;
  base_url?: string | null;
  api_key?: string;
}

export interface ProviderTestResponse {
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
}

// ─── GET/PUT /provider/cli-auth (F17, ADR-0043) ──────────────────────────────

/**
 * Response from GET /provider/cli-auth and PUT /provider/cli-auth (ADR-0043 §2.5).
 * Posture-only: the token value is NEVER returned by any endpoint.
 *
 * token_configured: true iff a DB or env signal is present.
 * token_source:     "db"  — token set via UI (vault_state.cli_oauth_token non-NULL)
 *                   "env" — any env signal present (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN or CLAUDE_CODE_USE_SUBSCRIPTION)
 *                   "none" — no signal anywhere
 * auth_mode:        "subscription"   — DB token set, or env subscription signal present
 *                   "api-key"        — env ANTHROPIC_API_KEY wins (no DB token)
 *                   "unconfigured"   — no credential at all
 *
 * The token value NEVER appears in this type or any GET/PUT response (ADR-0043 §2.5 / Do-NOT #2).
 */
export interface CliAuthConfig {
  token_configured: boolean;
  token_source: "db" | "env" | "none";
  auth_mode: "api-key" | "subscription" | "unconfigured";
}

/**
 * Request body for PUT /provider/cli-auth (ADR-0043 §2.5).
 * Exactly one of token or clear should be set per call.
 *
 * token: the pasted Claude subscription OAuth token (from `claude setup-token`).
 *        Stored plaintext in vault_state.cli_oauth_token — replayed into the spawned CLI.
 * clear: true ⇒ set cli_oauth_token = NULL (fall back to env / none).
 *
 * Empty body → 400 (nothing to do).
 * Empty/whitespace token → 422.
 * Server generates NO token — the user pastes their own (ADR-0043 §2.5 / Do-NOT #7).
 */
export interface CliAuthUpdateRequest {
  token?: string;
  clear?: boolean;
}
