/**
 * GET /status contract types (FE-QUAL-11 split of api/types.ts).
 */

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
