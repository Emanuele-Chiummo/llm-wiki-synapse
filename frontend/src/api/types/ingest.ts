/**
 * Ingest + live-queue API contract types (FE-QUAL-11 split of api/types.ts).
 * GET /ingest/runs · POST /ingest/upload · GET /ingest/queue (Activity Panel, F1)
 */

import type { PageType } from "./pages";

// ─── GET /ingest/runs (ADR-0018 §7) ──────────────────────────────────────────

/**
 * Terminal status added in v1.3 (R13-3): backend exposes "cancelled" on run objects
 * returned by GET /ingest/runs after a DELETE /ingest/{id} completes.
 * "cancelling" is a client-only optimistic state shown during the transition.
 */
export type IngestStatus =
  "running" | "completed" | "failed" | "converged_false" | "cancelling" | "cancelled";

/**
 * Non-convergence diagnostics (1.9.1 W5, NC-1): populated by the orchestrated JSON loop
 * and the block loop on every terminal outcome (converged or not); null for the
 * delegated/CLI route (no bounded loop to report) and for legacy rows.
 */
export interface IngestRunDiagnostics {
  stop_reason: "converged" | "max_iter" | "token_budget";
  iterations: number;
  last_errors: string[];
  tokens_used: number;
  token_budget: number;
}

export interface IngestRunItem {
  id: string;
  vault_id: string;
  status: IngestStatus;
  provider_type: string; // "local" | "api" | "cli" — no hardcoded values (I6)
  pages_created: number;
  /** Per generated page type; absent/null for legacy or unsuccessful runs. */
  page_type_counts?: Partial<Record<PageType, number>> | null;
  /** Non-convergence diagnostics (1.9.1 W5, NC-1); null on the delegated/CLI route or legacy rows. */
  diagnostics?: IngestRunDiagnostics | null;
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

// ─── POST /ingest/upload (ADR-0020 §2) ───────────────────────────────────────

export interface UploadResponse {
  file_path: string; // relative to vault_root, e.g. "raw/sources/notes.md"
  status: string; // "queued" — ingest runs async via the watcher (ADR-0020 §2)
  overwritten: boolean; // true if same-name file was replaced
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

/** Whole-batch progress for an in-progress POST /sources/ingest-all (else null). */
export interface QueueBatchProgress {
  running: boolean;
  done: number;
  total: number;
  /** Estimated seconds remaining for the whole batch (null when unknown). */
  eta_seconds?: number | null;
}

/**
 * Response from GET /ingest/queue.
 * completed_since_idle resets when the queue becomes idle again.
 */
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
