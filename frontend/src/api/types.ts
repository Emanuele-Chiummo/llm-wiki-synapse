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
  /**
   * Louvain community id (server-computed, v0.6+).
   * 0 = largest community; -1 = unassigned / isolated.
   * Absent on older server responses (non-breaking additive field).
   * INVARIANT I2: client NEVER recomputes community; only reads this value.
   */
  community?: number;
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

/** Community summary entry returned in the top-level communities array (v0.6+). */
export interface GraphCommunity {
  /** Community id (matches community field on GraphNode). */
  id: number;
  /** Number of nodes in this community. */
  size: number;
  /**
   * Louvain cohesion score (0–1).
   * Communities with cohesion < 0.1 are considered low-cohesion and
   * the UI marks them with a warning indicator in the legend.
   */
  cohesion: number;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Data version the coords correspond to */
  data_version: number;
  /** true = X-Graph-Cache: hit (no FA2 ran); false = miss (inline recompute) */
  cached: boolean;
  /**
   * Community summary list (v0.6+, server-computed Louvain).
   * Absent on older server responses (non-breaking additive field).
   * INVARIANT I2: client NEVER computes communities; only reads this list.
   */
  communities?: GraphCommunity[];
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
export type PageType = "concept" | "entity" | "source" | "synthesis" | "comparison" | "query";

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
  /**
   * Backend package version (additive, ADR-0054 §6, R12-3).
   * Absent on v1.1 and older backends — undefined means no banner.
   * "dev" means a local build with no version injected → no banner.
   */
  version?: string;
  /**
   * Pending review-queue items (additive, v1.2.x — NavRail badge).
   * Absent on older backends → undefined, badge hidden.
   */
  review_pending?: number;
  /**
   * Whether the active provider supports image inputs (B2 — vision gate).
   * Absent on older backends → undefined → treat as false (button stays disabled).
   */
  supports_vision?: boolean;
}

// ─── GET /ingest/runs (ADR-0018 §7) ──────────────────────────────────────────

/**
 * Terminal status added in v1.3 (R13-3): backend exposes "cancelled" on run objects
 * returned by GET /ingest/runs after a DELETE /ingest/{id} completes.
 * "cancelling" is a client-only optimistic state shown during the transition.
 */
export type IngestStatus =
  "running" | "completed" | "failed" | "converged_false" | "cancelling" | "cancelled";

export interface IngestRunItem {
  id: string;
  vault_id: string;
  status: IngestStatus;
  provider_type: string; // "local" | "api" | "cli" — no hardcoded values (I6)
  pages_created: number;
  iterations_used: number;
  total_cost_usd: number;
  started_at: string; // ISO-8601
  completed_at: string | null; // ISO-8601 or null
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
  provider_type: string; // "local" | "api" | "cli" — no hardcoded values (I6)
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
  file_path: string; // relative to vault_root, e.g. "raw/sources/notes.md"
  status: string; // "queued" — ingest runs async via the watcher (ADR-0020 §2)
  overwritten: boolean; // true if same-name file was replaced
}

// ─── POST /research/start + GET /research/runs (F10, ADR-0024 §8) ────────────

export type ResearchStatus =
  "running" | "converged" | "max_iter_reached" | "budget_exhausted" | "error";

/** One item in GET /research/runs (summary, no synthesis_text) */
export interface ResearchRunSummary {
  id: string;
  vault_id: string;
  topic: string;
  status: ResearchStatus;
  iterations_used: number;
  sources_fetched: number;
  total_cost_usd: number;
  started_at: string; // ISO-8601
  completed_at: string | null; // ISO-8601 or null while running
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

// ─── GET /review/queue + POST /review/queue/{id}/… (F9, ADR-0034 §7.1) ────────

/**
 * Five proposal types (ADR-0034 §3.1).
 * Old values (new_page / update_page / deep_research_candidate) are gone.
 */
export type ReviewItemType =
  "missing-page" | "suggestion" | "contradiction" | "duplicate" | "confirm";

/**
 * Item lifecycle (ADR-0034 §3.1 + ADR-0044 §3.1).
 * "approved" is gone — Create produces "created".
 * "dismissed" added in ADR-0044: human hid the item without acting.
 */
export type ReviewItemStatus =
  "pending" | "created" | "skipped" | "dismissed" | "deep_researched" | "auto_resolved";

/**
 * Convenience projection of a referenced page in a ReviewItem card.
 * Returned in the referenced_pages convenience join (ADR-0044 §6.1).
 */
export interface ReviewReferencedPage {
  id: string;
  title: string;
  type: string | null;
}

/**
 * One review_items row as returned by the API (ADR-0034 §7.1 + ADR-0044 §6.1).
 *
 * The item is a PROPOSAL: proposed_title + rationale describe what the LLM
 * recommends creating or investigating. The Create action lazily generates the
 * page on-demand (ADR-0034 §5); Deep Research and Skip close without writing.
 *
 * page_title is a convenience join from pages.title for the page_id FK (the
 * conflicting/context page for contradiction/duplicate types).
 *
 * pre_generated_query is REMOVED — superseded by rationale + the suggestion type.
 *
 * ADR-0044 additions:
 *   content_key          — stable FNV-1a dedup handle (opaque to UI)
 *   referenced_page_ids  — array of existing page-id strings the proposal is about
 *   referenced_pages     — [{id, title, type}] convenience join for the card (no extra round-trip)
 *   search_queries       — ≤3 pre-generated web-search query strings (Deep-Research seeds)
 */
export interface ReviewItem {
  id: string;
  vault_id: string;

  /** Proposal type (ADR-0034 §3.1): missing-page | suggestion | contradiction | duplicate | confirm */
  item_type: ReviewItemType;

  /** Item lifecycle: pending | created | skipped | dismissed | deep_researched | auto_resolved */
  status: ReviewItemStatus;

  /** Title the LLM proposes to create (required for missing-page; advisory for others). */
  proposed_title: string | null;

  /** Inferred PageType for the lazy skeleton: entity | concept | source | synthesis | comparison */
  proposed_page_type: string | null;

  /** Target wiki/ subdir (display only — recomputed at Create from the final type). */
  proposed_dir: string | null;

  /** Short human-readable "why this matters" (replaces the old follow-up questions). */
  rationale: string | null;

  /**
   * Review TARGET: the existing page a contradiction/duplicate conflicts with,
   * or the source-context page for a missing-page/suggestion. null when none applies.
   */
  page_id: string | null;

  /** Convenience join from pages.title for page_id (UI display). */
  page_title: string | null;

  /** Provenance: the page whose ingest produced this proposal. */
  source_page_id: string | null;

  /** Page produced by a successful Create action; null otherwise. */
  created_page_id: string | null;

  /** How the item closed: created | skipped | dismissed | researched | rule_resolved | llm_resolved. null while pending. */
  resolution: string | null;

  /** Set when the Deep-Research action fires (AC-F10-5); null otherwise. */
  deep_research_run_id: string | null;

  /**
   * ADR-0044 §6.1: stable FNV-1a content-derived dedup handle (opaque string or null).
   * null for confirm items (never deduped) and legacy rows.
   */
  content_key: string | null;

  /**
   * ADR-0044 §6.1: array of existing page-id strings this proposal is contextually about.
   * null/[] when none. Distinct from page_id (single primary conflict).
   * May contain stale ids if referenced pages were deleted — filtered at render time.
   */
  referenced_page_ids: string[] | null;

  /**
   * ADR-0044 §6.1: convenience join — [{id, title, type}] for referenced_page_ids.
   * Populated by the backend (bounded pages lookup); stale ids are already filtered.
   * null when referenced_page_ids is null/[].
   */
  referenced_pages: ReviewReferencedPage[] | null;

  /**
   * ADR-0044 §6.1: ≤3 pre-generated web-search query strings.
   * Shown on the card as "will search: …"; first entry seeds Deep Research.
   * null when the model produced none or for rule-based proposals.
   */
  search_queries: string[] | null;

  created_at: string; // ISO-8601
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

/** 200 response for POST /review/queue/sweep (ADR-0034 §7) */
export interface ReviewSweepResponse {
  rule_resolved: number;
  llm_resolved: number;
  kept: number;
}

/**
 * Request body for POST /review/queue/bulk (ADR-0044 §6).
 * Bounded: len(ids) ≤ REVIEW_BULK_MAX_IDS (default 200) — 400 otherwise (I7).
 * Only pending ids are mutated; already-terminal ids are counted in skipped_terminal.
 */
export interface ReviewBulkRequest {
  vault_id: string;
  action: "skip" | "dismiss" | "mark-resolved";
  ids: string[];
}

/** 200 response for POST /review/queue/bulk (ADR-0044 §6) */
export interface ReviewBulkResponse {
  updated: number;
  skipped_terminal: number;
}

/** 200 response for DELETE /review/queue/resolved (ADR-0044 §6) */
export interface ReviewClearResolvedResponse {
  deleted: number;
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

// ─── GET /pages/{id}/content · PUT /pages/{id}/content (Wiki Note Editor) ────

/**
 * Response from GET /pages/{id}/content.
 * 404 if the page is unknown, deleted, or the backing file is missing.
 *
 * v0.6: backend now also returns `type` and `sources` — both optional/nullable
 * so older server responses remain valid (additive, no breaking change).
 */
export interface PageContentResponse {
  id: string;
  title: string | null;
  file_path: string;
  content: string;
  content_hash: string;
  updated_at: string; // ISO-8601
  /** Page type (concept | entity | source | …) — may be null or absent on old servers. */
  type?: string | null;
  /** Source document IDs referenced by this page — may be null or absent on old servers. */
  sources?: string[] | null;
  /** Frontmatter tags — may be null or absent on old servers / pages predating tags. */
  tags?: string[] | null;
}

// ─── GET /pages/{id}/related ─────────────────────────────────────────────────

/**
 * One item in GET /pages/{id}/related?limit=10.
 * Ranked by 4-signal edge weight (highest first).
 */
export interface RelatedPageItem {
  page_id: string;
  title: string;
  /** Page type — may be null. */
  type: string | null;
  /** 4-signal relevance score (3·direct + 4·source_overlap + 1.5·AA + 1·same_type). */
  score: number;
}

/**
 * Response from GET /pages/{id}/related?limit=10.
 * items is empty + total is 0 when the page has no edges.
 * 404 if the page_id is unknown.
 */
export interface RelatedPagesResponse {
  items: RelatedPageItem[];
  total: number;
}

/**
 * Request body for PUT /pages/{id}/content.
 * expected_hash: the content_hash from the last GET — used for optimistic-concurrency.
 * Pass null to skip the hash check (last-write-wins, not recommended).
 */
export interface PageContentPutBody {
  content: string;
  expected_hash: string | null;
}

/**
 * Response from PUT /pages/{id}/content (200 OK).
 */
export interface PageContentPutResponse {
  id: string;
  content_hash: string;
  updated_at: string; // ISO-8601
}

// ─── GET/PUT /import-schedule (ADR-0020 §4.6) ────────────────────────────────

export type ImportFrequency = "15m" | "1h" | "6h" | "daily";

export type ImportLastStatus =
  "ok" | "error" | "running" | "skipped_disabled" | "dir_missing" | null;

export interface ImportSchedule {
  enabled: boolean;
  source_dir: string | null;
  frequency: ImportFrequency;
  last_run_at: string | null; // ISO-8601
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

// ─── GET/PUT /clip/config (F11, ADR-0040) ────────────────────────────────────

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

// ─── GET/PUT /web-search/config (F10, ADR-0041) ──────────────────────────────

/**
 * Response from GET /web-search/config (ADR-0041 §2.3).
 * The SearXNG URL is NOT a secret — it IS returned in full (unlike clip/mcp tokens).
 * SearXNG is the ONLY supported web-search backend (I9).
 */
export interface WebSearchConfigResponse {
  /** True iff a SearXNG URL is available (DB or env). */
  configured: boolean;
  /** Resolved SearXNG base URL (DB wins over env). null when neither is set. */
  url: string | null;
  /** Resolved SearXNG categories list. */
  categories: string[];
  /** Resolved max queries per deep-research iteration (1–50). */
  max_queries: number;
  /**
   * Which URL source is authoritative:
   *   "db"  — URL set via PUT /web-search/config
   *   "env" — SEARXNG_URL env var
   *   "none" — no URL configured
   */
  source: "db" | "env" | "none";
}

/**
 * Request body for PUT /web-search/config (ADR-0041 §2.4).
 * All fields optional. No provider field — SearXNG is the ONLY backend (I9).
 */
export interface WebSearchConfigRequest {
  /** Set the SearXNG base URL (must be a valid http/https URL). */
  set_url?: string | null;
  /** Comma-separated categories (e.g. "general,news"). "" clears to default. */
  set_categories?: string | null;
  /** Max queries per deep-research iteration (1–50). */
  set_max_queries?: number | null;
  /** Clear ALL DB overrides; falls back to env / code defaults. */
  clear?: boolean | null;
}

/**
 * Response from PUT /web-search/config (ADR-0041 §2.4).
 * Always reflects post-write posture. Same shape as WebSearchConfigResponse.
 */
export interface WebSearchConfigStateResponse {
  configured: boolean;
  url: string | null;
  categories: string[];
  max_queries: number;
  source: "db" | "env" | "none";
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

// ─── GET /ingest/queue (Activity Panel, F1) ──────────────────────────────────

/** Status of an individual task in the live ingest queue. */
export type QueueTaskStatus = "pending" | "processing" | "failed";

/**
 * One live task entry from GET /ingest/queue.
 * run_id is only present when the task is being actively processed (status=processing).
 * error is only present when status=failed.
 * started_at is only present when status=processing.
 *
 * v0.6 additions (phase/progress/timing):
 *   phase           — human-readable current step (e.g. "analyzing", "generating (2/3)",
 *                     "validating", "writing", "agent running", "queued", "failed");
 *                     null when not yet available or not applicable.
 *   progress        — coarse 0..1 fraction for orchestrated route; null for
 *                     indeterminate/delegated (CLI) tasks — show spinner, NOT a 0% bar.
 *   elapsed_seconds — seconds since task started; null when not started.
 *   eta_seconds     — best-effort estimate of seconds remaining; null = unknown
 *                     (no history yet — do NOT render "~0s"; render nothing).
 */
export interface QueueTask {
  run_id?: string | undefined;
  source_path: string;
  filename: string;
  status: QueueTaskStatus;
  retry_count: number;
  error?: string | undefined;
  started_at?: string | undefined;
  /** Current ingest phase label; null when not available. */
  phase?: string | null;
  /** Coarse progress 0..1 (orchestrated); null = indeterminate (delegated/CLI). */
  progress?: number | null;
  /** Elapsed seconds since task start; null when not started. */
  elapsed_seconds?: number | null;
  /** Best-effort ETA in seconds remaining; null = unknown. */
  eta_seconds?: number | null;
}

/**
 * Response from GET /ingest/queue.
 * completed_since_idle resets when the queue becomes idle again.
 */
/** Whole-batch progress for an in-progress POST /sources/ingest-all (else null). */
export interface QueueBatchProgress {
  running: boolean;
  done: number;
  total: number;
  /** Estimated seconds remaining for the whole batch (null when unknown). */
  eta_seconds?: number | null;
}

export interface IngestQueueSnapshot {
  paused: boolean;
  pending: number;
  processing: number;
  failed: number;
  completed_since_idle: number;
  total: number;
  tasks: QueueTask[];
  /** Batch progress when a bulk "index all" is running (null otherwise). */
  batch?: QueueBatchProgress | null;
}

/**
 * Response from DELETE /ingest/{run_id} (R13-3).
 * 202 → status:"cancelling" (running run signalled; transitions to "cancelled" on next poll)
 * 200 → status:"cancelled"  (queued run cancelled immediately)
 * cleaned_pages is present on 202; may be absent on 200 (queued run had no pages yet).
 */
export interface CancelRunResponse {
  run_id: string;
  status: "cancelling" | "cancelled";
  cleaned_pages?: number;
}

/** 202 response from POST /ingest/runs/{id}/retry */
export interface RetryRunResponse {
  run_id_prev: string;
  source_path: string;
  retry_count: number;
  status: "queued";
}

/** 200 response from POST /ingest/queue/pause */
export interface PauseQueueResponse {
  paused: true;
}

/** 200 response from POST /ingest/queue/resume */
export interface ResumeQueueResponse {
  paused: false;
  drained: number;
}

// ─── POST /lint/scan + GET /lint/runs + GET /lint/findings (K2, ADR-0037 §6) ──

/** Lint finding categories (ADR-0037 §6, B1-L1). */
export type LintCategory =
  | "orphan-page"
  | "missing-xref"
  | "contradiction"
  | "stale-claim"
  | "missing-page"
  /** B1-L1: deterministic category derived from links.dangling=True. Zero LLM cost. */
  | "broken-wikilink";

/** Finding lifecycle. */
export type LintFindingStatus = "open" | "applied" | "dismissed";

/** Severity levels. */
export type LintSeverity = "info" | "warning" | "error";

/** Lint run status. */
export type LintRunStatus = "running" | "completed" | "error";

/**
 * Categories that are FLAG-ONLY: the apply endpoint acknowledges but does not
 * rewrite any wiki file. The UI should label the action "Acknowledge" instead of
 * "Fix" and show no file-write expectation.
 *
 * NOTE: "broken-wikilink" is NOT flag-only — when suggested_target is present it
 * has a real Fix (rewrite dangling [[link]] in the referencing page body); when
 * absent the row is treated as acknowledge per-row. The distinction is made at
 * render time using the suggested_target field, not via this set.
 */
export const LINT_FLAG_ONLY_CATEGORIES = new Set<LintCategory>([
  "orphan-page",
  "contradiction",
  "stale-claim",
]);

/**
 * One lint_findings row (ADR-0037 §6, B1-L2).
 * proposed_action is null for flag-only categories.
 * suggested_target / suggested_page_id added for broken-wikilink (B1-L2): the
 * tolerant resolver computed the best-match existing page at scan time.
 */
export interface LintFinding {
  id: string;
  lint_run_id: string;
  vault_id: string;
  category: LintCategory;
  severity: LintSeverity;
  target_page_id: string | null;
  target_title: string | null;
  description: string;
  proposed_action: string | null;
  status: LintFindingStatus;
  resolution_note: string | null;
  created_at: string; // ISO-8601
  reviewed_at: string | null; // ISO-8601
  /** B1-L2: for broken-wikilink, the suggested existing page title to rewrite to. */
  suggested_target: string | null;
  /** B1-L2: for broken-wikilink, the FK to the suggested existing page. */
  suggested_page_id: string | null;
}

/** Paginated response from GET /lint/findings. */
export interface LintFindingListResponse {
  items: LintFinding[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One lint_runs row (ADR-0037 §6).
 */
export interface LintRun {
  id: string;
  vault_id: string;
  status: LintRunStatus;
  max_iter: number;
  token_budget: number;
  iterations_used: number;
  findings_count: number;
  total_cost_usd: number;
  started_at: string; // ISO-8601
  completed_at: string | null; // ISO-8601
  error_message: string | null;
  created_at: string; // ISO-8601
}

/** Paginated response from GET /lint/runs. */
export interface LintRunListResponse {
  items: LintRun[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Response from POST /lint/scan (ADR-0037 §6).
 * Returns the run row AND its findings in one call.
 */
export interface LintScanResponse {
  run: LintRun;
  findings: LintFinding[];
}

/** Request body for POST /lint/scan. */
export interface LintScanRequest {
  vault_id: string;
  max_iter?: number | null;
  token_budget?: number | null;
}

// ─── B1: Batch action + page-delete (B1-L5, B1-L9) ──────────────────────────

/** One result entry in a POST /lint/findings/batch response. */
export interface LintBatchResultEntry {
  id: string;
  status: "ok" | "error";
  detail?: string | null;
}

/**
 * Response from POST /lint/findings/batch (B1-L5).
 * Bounded: ≤200 ids per call (I7).
 */
export interface LintBatchResponse {
  results: LintBatchResultEntry[];
  ok_count: number;
  error_count: number;
}

/**
 * Response from DELETE /pages/{page_id} (B1-L9).
 * Matches the existing CascadeDeleteResult shape used by cascade-delete preview.
 * Re-exported here as a convenience alias so lint code can import it without
 * pulling in the cascade-delete types.
 */
export type LintDeletePageResponse = CascadeDeleteResult;
