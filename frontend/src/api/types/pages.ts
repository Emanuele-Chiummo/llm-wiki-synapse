/**
 * Page API contract types (FE-QUAL-11 split of api/types.ts).
 * GET /pages/{id} · GET /pages · GET/PUT /pages/{id}/content · GET /pages/{id}/related
 */

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
  /**
   * Dominant vocabulary domain derived server-side from 'domain/<name>' tags.
   * null when the page is untagged or no domain vocabulary is configured.
   * Absent on pre-v1.5 server responses (non-breaking additive field).
   * Same derivation logic as GET /stats/sections (stats.py _derive_domain).
   */
  domain?: string | null;
  /**
   * Louvain community id persisted by GraphEngine.recompute().
   * null until the first graph recompute (G-P0-2, I2).
   * Absent on pre-v1.5 server responses (non-breaking additive field).
   */
  community?: number | null;
}

/** Paginated response from GET /pages */
export interface PageListResponse {
  items: PageListItem[];
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
