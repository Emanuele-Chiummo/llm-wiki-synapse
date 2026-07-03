/**
 * opsScheduleClient.ts — typed HTTP client for GET /ops/schedules and
 * POST /ops/schedules/{op}/run-now (A5 / R12-7 FRONTEND).
 *
 * Backend contract (sprint v1.2):
 *   GET  /ops/schedules
 *     → { ops: [{ op, schedule, last_run_at, last_status, in_flight }] }
 *     Returns 404 on older backends (pre-v1.2) — caller maps to null (card hidden).
 *
 *   POST /ops/schedules/{op}/run-now
 *     → 202 { status: "triggered", op }
 *     → 409 if already in_flight
 *     → 400 if op is dormant (backfill with empty vocabulary)
 *     → 404 if op unknown
 *
 * No secrets in this file (CLAUDE.md §12).
 * Base URL from apiBase() (ADR-0047 §2.1).
 * Auth injected by apiFetch() (ADR-0052 §4.2).
 *
 * I7: 409 and 400 are surfaced to the caller — NOT silenced.
 */

import { apiBase, apiFetch } from "./base";

// ─── Types ────────────────────────────────────────────────────────────────────

/** Valid op identifiers understood by the backend. */
export type OpsScheduleOp = "lint" | "backfill" | "schema_review";

/** Valid schedule frequency strings from the backend. */
export type OpsScheduleFrequency = "off" | "hourly" | "daily" | "weekly";

/** One entry returned from GET /ops/schedules. */
export interface OpsScheduleEntry {
  op: OpsScheduleOp;
  schedule: OpsScheduleFrequency;
  last_run_at: string | null;
  last_status: string | null;
  in_flight: boolean;
}

/** GET /ops/schedules response envelope. */
export interface OpsSchedulesResponse {
  ops: OpsScheduleEntry[];
}

/** POST /ops/schedules/{op}/run-now success response (202). */
export interface RunOpNowResponse {
  status: "triggered";
  op: OpsScheduleOp;
}

/**
 * Error class for run-now specific HTTP failures (409 / 400).
 * `httpStatus` lets the caller distinguish in-flight (409) from dormant (400).
 */
export class RunOpNowError extends Error {
  constructor(
    public readonly httpStatus: number,
    message: string,
  ) {
    super(message);
    this.name = "RunOpNowError";
  }
}

// ─── Client functions ─────────────────────────────────────────────────────────

/**
 * getOpsSchedules — GET /ops/schedules
 *
 * Returns the schedule state for all ops, or null when the backend does not
 * expose this endpoint (404 → older backend → card should be hidden).
 * Throws on unexpected network or server errors (5xx, etc.).
 */
export async function getOpsSchedules(
  signal?: AbortSignal,
): Promise<OpsSchedulesResponse | null> {
  const url = `${apiBase()}/ops/schedules`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);

  if (res.status === 404) {
    // Pre-v1.2 backend — hide the card gracefully.
    return null;
  }

  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error
    }
    throw new Error(`GET /ops/schedules: ${detail}`);
  }

  return res.json() as Promise<OpsSchedulesResponse>;
}

/**
 * runOpNow — POST /ops/schedules/{op}/run-now
 *
 * Returns the triggered response on 202.
 * Throws RunOpNowError with httpStatus set for 409 (in-flight) and 400 (dormant).
 * Throws generic Error for 404 (unknown op) and 5xx.
 */
export async function runOpNow(op: OpsScheduleOp): Promise<RunOpNowResponse> {
  const url = `${apiBase()}/ops/schedules/${encodeURIComponent(op)}/run-now`;
  const res = await apiFetch(url, { method: "POST" });

  if (res.status === 202) {
    return res.json() as Promise<RunOpNowResponse>;
  }

  let detail = `${res.status}`;
  try {
    const body = (await res.json()) as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    // ignore parse error
  }

  // Surface 409 and 400 distinctly so the UI can render the right hint.
  if (res.status === 409 || res.status === 400) {
    throw new RunOpNowError(res.status, `POST /ops/schedules/${op}/run-now: ${detail}`);
  }

  throw new Error(`POST /ops/schedules/${op}/run-now: ${detail}`);
}
