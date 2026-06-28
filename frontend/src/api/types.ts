/**
 * API contract types for Synapse frontend.
 *
 * Mirrors the GET /graph response shape defined in:
 *   docs/sprints/v0.3-architecture.md §6
 *   docs/api/openapi.json
 *
 * INVARIANT I2: coords (x, y) come FROM the server; the client NEVER computes layout.
 */

// ─── GET /graph ─────────────────────────────────────────────────────────────

export interface GraphNode {
  /** UUID string — matches pages.id in Postgres */
  id: string;
  /** Page title */
  title: string;
  /** Page type (concept | entity | source | etc.) — may be null */
  type: string | null;
  /** FA2 x-coordinate — server-precomputed, stored in pages.x (I2) */
  x: number;
  /** FA2 y-coordinate — server-precomputed, stored in pages.y (I2) */
  y: number;
  /** Rendering hint: monotonic in degree, default 1.0 — derived, not persisted */
  size?: number;
  /** Incident-edge count — derived, not persisted */
  degree?: number;
}

export interface GraphEdge {
  /** Source page UUID */
  source: string;
  /** Target page UUID */
  target: string;
  /** Additive weight (I2 formula: 3·direct + 4·source_overlap + 1.5·AA + 1·same_type) */
  weight: number;
  /**
   * Edge kind (v0.4 contract).
   * "link"   = wikilink edge (direct structural reference)
   * "source" = shared-source-document overlap edge
   * Omitted / undefined = treat as "link" (back-compat with v0.3 server).
   */
  kind?: "link" | "source";
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Data version the coords correspond to */
  data_version: number;
  /** true = X-Graph-Cache: hit (no FA2 ran); false = miss (inline recompute) */
  cached: boolean;
}

/** Value of the X-Graph-Cache response header */
export type CacheStatus = "hit" | "miss" | "unknown";

// ─── GET /pages/{id} ─────────────────────────────────────────────────────────

export interface PageDetail {
  id: string;
  title: string;
  type: string | null;
  vault_id: string;
  /** Vault-relative file path, e.g. "demo/temperature_scaling.md" */
  file_path?: string;
  /** Source document IDs referenced by this page */
  sources?: string[];
  /** ISO-8601 creation timestamp */
  created_at?: string;
  /** ISO-8601 last-update timestamp */
  updated_at?: string;
}

// ─── GET /pages ──────────────────────────────────────────────────────────────

/** Canonical page types matching the knowledge graph legend */
export type PageType = "concept" | "entity" | "source" | "synthesis" | "comparison";

/** Single item in the GET /pages list response */
export interface PageListItem {
  id: string;
  vault_id: string;
  file_path: string;
  title: string;
  type: string | null;
  sources: string[];
  content_hash: string | null;
  created_at: string;
  updated_at: string;
}

/** Paginated response from GET /pages */
export interface PageListResponse {
  items: PageListItem[];
}

// ─── GET /status ─────────────────────────────────────────────────────────────

export interface StatusResponse {
  vault_id: string;
  data_version: number;
  started_at: string;
  uptime_seconds: number;
}

// ─── GET /ingest/runs (ADR-0018 §7) ──────────────────────────────────────────

export type IngestStatus = "running" | "completed" | "failed" | "converged_false";

export interface IngestRunItem {
  id: string;
  vault_id: string;
  status: IngestStatus;
  provider_type: string;        // "local" | "api" | "cli" — no hardcoded values (I6)
  pages_created: number;
  iterations_used: number;
  total_cost_usd: number;
  started_at: string;           // ISO-8601
  completed_at: string | null;  // ISO-8601 or null
  error_message: string | null;
}

export interface IngestRunListResponse {
  items: IngestRunItem[];
  total: number;
  limit: number;
  offset: number;
}

// ─── GET/POST /provider/config (ADR-0018 §4) ─────────────────────────────────

export interface ProviderConfigItem {
  id: string;
  scope: "global" | "vault";
  operation: string | null;
  vault_id: string | null;
  provider_type: string;         // "local" | "api" | "cli" — no hardcoded values (I6)
  model_id: string | null;
  base_url: string | null;
  max_iter: number | null;
  token_budget: number | null;
  is_fallback: boolean;
  created_at: string;
  updated_at: string;
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
}

// ─── POST /ingest/upload (ADR-0020 §2) ───────────────────────────────────────

export interface UploadResponse {
  file_path: string;    // relative to vault_root, e.g. "raw/sources/notes.md"
  status: string;       // "queued" — ingest runs async via the watcher (ADR-0020 §2)
  overwritten: boolean; // true if same-name file was replaced
}

// ─── GET/PUT /import-schedule (ADR-0020 §4.6) ────────────────────────────────

export type ImportFrequency = "15m" | "1h" | "6h" | "daily";

export type ImportLastStatus =
  | "ok"
  | "error"
  | "running"
  | "skipped_disabled"
  | "dir_missing"
  | null;

export interface ImportSchedule {
  enabled: boolean;
  source_dir: string | null;
  frequency: ImportFrequency;
  last_run_at: string | null;          // ISO-8601
  last_status: ImportLastStatus;
  last_imported_count: number;
  last_error: string | null;
}

export interface ImportSchedulePutBody {
  enabled?: boolean;
  source_dir?: string | null;
  frequency?: ImportFrequency;
}

export interface ImportSchedulePutResponse extends ImportSchedule {
  dir_ok: boolean;
  dir_message: string | null;
}
