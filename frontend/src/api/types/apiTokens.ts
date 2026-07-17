/**
 * Scoped API token API contract types (PF-AUTH-1, 1.9.4 W4).
 * POST/GET/DELETE /config/api-tokens
 */

/** Request body for POST /config/api-tokens. */
export interface ApiTokenCreateRequest {
  label: string;
  /** null (default) = global token; non-null = scoped to that vault_id. */
  vault_id?: string | null;
  /** true = the token may only be used for GET/HEAD/OPTIONS requests. */
  read_only?: boolean;
}

/**
 * Response from POST /config/api-tokens.
 *
 * `token` is the PLAINTEXT secret — shown exactly once, here, and never again by any
 * other endpoint. The UI MUST show it in a one-time reveal dialog and never persist it.
 */
export interface ApiTokenCreateResponse {
  id: string;
  label: string;
  vault_id: string | null;
  read_only: boolean;
  created_at: string;
  /** Plaintext bearer secret — one-time reveal, never returned again. */
  token: string;
}

/** One row in GET /config/api-tokens. NEVER includes the secret/hash. */
export interface ApiTokenListItem {
  id: string;
  label: string;
  vault_id: string | null;
  read_only: boolean;
  created_at: string;
  last_used_at: string | null;
}

/** Response from GET /config/api-tokens. */
export interface ApiTokenListResponse {
  tokens: ApiTokenListItem[];
}
