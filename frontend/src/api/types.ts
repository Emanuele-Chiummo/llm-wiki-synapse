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

// ─── POST /research/start + GET /research/runs (F10, ADR-0024 §8) ────────────

export type ResearchStatus =
  | "running"
  | "converged"
  | "max_iter_reached"
  | "budget_exhausted"
  | "error";

/** One item in GET /research/runs (summary, no synthesis_text) */
export interface ResearchRunSummary {
  id: string;
  vault_id: string;
  topic: string;
  status: ResearchStatus;
  iterations_used: number;
  sources_fetched: number;
  total_cost_usd: number;
  started_at: string;           // ISO-8601
  completed_at: string | null;  // ISO-8601 or null while running
}

export interface ResearchRunListResponse {
  items: ResearchRunSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** One fetched source in the run detail */
export interface ResearchSource {
  url: string;
  title: string | null;
  relevance_score: number | null;
  iteration: number;
}

/** Full detail from GET /research/runs/{id} */
export interface ResearchRunDetail {
  id: string;
  vault_id: string;
  topic: string;
  status: ResearchStatus;
  max_iter: number;
  token_budget: number;
  iterations_used: number;
  queries_used: string[];
  sources_fetched: number;
  total_cost_usd: number;
  synthesis_text: string | null;
  synthesis_page_id: string | null;
  sources: ResearchSource[];
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
}

export interface ResearchStartResponse {
  run_id: string;
}

// ─── GET /review/queue + POST /review/queue/{id}/… (F9, ADR-0025 §3.5) ────────

export type ReviewItemType =
  | "new_page"
  | "update_page"
  | "deep_research_candidate";

export type ReviewItemStatus =
  | "pending"
  | "approved"
  | "skipped"
  | "deep_researched";

/** One review_items row as returned by the API (ADR-0025 §3.5, AC-F9-5). */
export interface ReviewItem {
  id: string;
  vault_id: string;
  page_id: string | null;
  /** Convenience join from pages.title (AC-F9-5). */
  page_title: string | null;
  item_type: ReviewItemType;
  status: ReviewItemStatus;
  /** Newline-separated 1–3 follow-up questions; null when generation failed (I7). */
  pre_generated_query: string | null;
  /** Set when the Deep-Research action fires (AC-F10-5); null otherwise. */
  deep_research_run_id: string | null;
  created_at: string;   // ISO-8601
  reviewed_at: string | null;
}

export interface ReviewQueueResponse {
  items: ReviewItem[];
  total: number;
  limit: number;
  offset: number;
}

/** 202 response for POST /review/queue/{id}/deep-research */
export interface ReviewDeepResearchResponse {
  review_item_id: string;
  run_id: string;
}

// ─── POST /pages/{id}/cascade-delete/preview + DELETE /pages/{id} (F13, ADR-0026 §6.1) ──────

/** One dead [[Target]] → plain-text rewrite entry in the cascade preview. */
export interface WikilinkRewrite {
  source_page_id: string;
  file_path: string;
  target_title: string;
  occurrences: number;
}

/**
 * Response from POST /pages/{id}/cascade-delete/preview (dry-run, read-only).
 * Mirrors CascadePreviewResponse in main.py (ADR-0026 §6.1).
 */
export interface CascadePreviewResponse {
  target_page_id: string;
  target_title: string | null;
  target_file_path: string;
  will_delete: string[];
  will_preserve_with_pruned_source: string[];
  wikilinks_to_rewrite: WikilinkRewrite[];
  index_entry_will_be_removed: boolean;
  raw_source_to_delete: string | null;
  shared_entity_warnings: string[];
  match_methods_used: Record<string, string>;
}

/**
 * Response from DELETE /pages/{id} (single-pass cascade delete).
 * Mirrors CascadeDeleteResponse in main.py (ADR-0026 §6.1, AC-F13-5).
 */
export interface CascadeDeleteResult {
  deleted_page_id: string;
  wikilinks_cleaned: number;
  index_entry_removed: boolean;
  shared_entity_warnings: string[];
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
