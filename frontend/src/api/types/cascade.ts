/**
 * Cascade-delete API contract types (F13, FE-QUAL-11 split of api/types.ts).
 * POST /pages/{id}/cascade-delete/preview · DELETE /pages/{id} (ADR-0026 §6.1)
 */

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
