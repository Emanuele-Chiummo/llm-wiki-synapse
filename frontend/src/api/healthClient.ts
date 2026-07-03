/**
 * healthClient.ts — typed client for GET /health/detailed [F18][R12-1 A2].
 *
 * Fetched ONCE on HomeDashboard section mount (component-local, no polling loop — I3).
 * Returns null on 404 so the system-status block degrades gracefully when the backend
 * does not yet expose the endpoint.
 *
 * The endpoint NEVER returns 5xx — a failed probe is represented as ok=false on the
 * relevant component (see health.py AC-R9-2-1). We therefore treat any non-200, non-404
 * as a degraded payload rather than a thrown error, except network errors (re-thrown).
 *
 * All calls use apiFetch (ADR-0052 §4.2 — single auth injection point).
 * No secrets in this file (CLAUDE.md §12).
 */

import { apiBase, apiFetch } from "./base";

// ─── Response types ───────────────────────────────────────────────────────────

/** Health status for a single probed component. */
export interface ComponentHealth {
  ok: boolean | "skipped";
  latency_ms?: number | null;
  error?: string;
}

/** Watcher heartbeat component. */
export interface WatcherHealth {
  alive: boolean;
  last_event_at: string | null;
}

/** Import-scheduler component. */
export interface ImportSchedulerHealth {
  enabled: boolean;
  last_run_at: string | null;
  last_error: string | null;
}

/** Ingest queue snapshot. */
export interface IngestQueueHealth {
  running: number;
  pending: number;
  paused: boolean;
}

/** Graph cache warmth. */
export interface GraphCacheHealth {
  warm: boolean;
  last_recompute_at: string | null;
  node_count: number;
}

/** Embeddings toggle. */
export interface EmbeddingsHealth {
  enabled: boolean;
  ok: boolean | "skipped";
}

/** Per-component breakdown (GET /health/detailed shape from health.py). */
export interface DetailedHealthComponents {
  watcher: WatcherHealth;
  import_scheduler: ImportSchedulerHealth;
  ingest_queue: IngestQueueHealth;
  graph_cache: GraphCacheHealth;
  database: ComponentHealth;
  qdrant: ComponentHealth;
  embeddings: EmbeddingsHealth;
}

/** One entry in the last_errors ring buffer (last 5 in-process ERROR log lines). */
export interface HealthError {
  source: string;
  message: string;
  at: string;
}

/**
 * Full response from GET /health/detailed.
 * status: "ok" | "degraded" | "error"
 * Always HTTP 200 — status reflects the logical health of components.
 */
export interface DetailedHealth {
  status: "ok" | "degraded" | "error";
  components: DetailedHealthComponents;
  last_errors: HealthError[];
  checked_at: string;
}

// ─── Client function ──────────────────────────────────────────────────────────

/**
 * getHealthDetailed — GET /health/detailed.
 *
 * Returns the component health snapshot, or null on 404 (older backend).
 * Re-throws AbortError (caller's AbortSignal was fired) and network errors.
 * Any non-200 / non-404 is returned as null so the block hides gracefully.
 *
 * [F18][R12-1 A2]
 */
export async function getHealthDetailed(
  signal?: AbortSignal,
): Promise<DetailedHealth | null> {
  try {
    const url = `${apiBase()}/health/detailed`;
    const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
    if (res.status === 404) return null;
    if (!res.ok) return null; // degraded backend — hide block
    return (await res.json()) as DetailedHealth;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    // Network / parse errors — hide block silently
    return null;
  }
}
