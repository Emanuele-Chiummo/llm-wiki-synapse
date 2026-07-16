/**
 * Web-clipper config API contract types (F11, FE-QUAL-11 split of api/types.ts).
 * GET/PUT /clip/config (ADR-0040)
 */

/**
 * Response from GET /clip/config (ADR-0040 §2.3).
 * Mirrors McpInfoResponse structure: posture-only, token value NEVER returned.
 */
export interface ClipConfigResponse {
  /** Resolved enabled state (DB clip_enabled_db if set, else CLIP_ENABLED env). */
  enabled: boolean;
  /** True iff a token is available (DB hash OR CLIP_TOKEN env). NEVER the token value. */
  token_configured: boolean;
  /**
   * Which token source is authoritative:
   *   "db"  — token set via PUT /clip/config
   *   "env" — CLIP_TOKEN env bootstrap
   *   "none" — no token configured
   */
  token_source: "db" | "env" | "none";
  /** Resolved allowed-origins list (DB if set, else CLIP_ALLOWED_ORIGINS env). */
  allowed_origins: string[];
  /** CLIP_MAX_BODY_BYTES env — not runtime-settable. */
  max_body_bytes: number;
}

/**
 * Request body for PUT /clip/config (ADR-0040 §2.4).
 * All fields optional; omitting leaves that aspect unchanged.
 */
export interface ClipConfigRequest {
  /** Generate a new high-entropy token; return plaintext ONCE in generated_token. */
  rotate_token?: boolean | null;
  /** Clear DB token hash (falls back to CLIP_TOKEN env or none). */
  clear_token?: boolean | null;
  /** Set clip_enabled_db (DB wins over CLIP_ENABLED env when set). */
  set_enabled?: boolean | null;
  /** Replace DB allowed-origins (comma-separated string; "" clears to env fallback). */
  set_allowed_origins?: string | null;
}

/**
 * Response from PUT /clip/config (ADR-0040 §2.4).
 * Always reflects post-write posture.
 * generated_token ONLY present when rotate_token=true — show ONCE, then discard.
 */
export interface ClipConfigStateResponse {
  enabled: boolean;
  token_configured: boolean;
  token_source: "db" | "env" | "none";
  allowed_origins: string[];
  max_body_bytes: number;
  /**
   * The generated token plaintext — present ONLY for rotate_token:true requests.
   * Show once, discard, never store in Zustand or localStorage (ADR-0040 §2.1).
   */
  generated_token?: string | null;
}
