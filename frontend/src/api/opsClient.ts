/**
 * opsClient.ts — lightweight HTTP client for on-demand ops trigger endpoints [F18][F9].
 *
 * POST /ops/backfill-domains  → trigger domain classification for unclassified pages
 * POST /ops/reclassify-types  → trigger type reclassification for untyped pages
 * POST /ops/synthesize        → trigger the bounded corpus-level synthesis/comparison
 *                                generator (ADR-0067 D3) — writes synthesis/comparison
 *                                pages from high-confidence graph clusters, proposes
 *                                borderline clusters to the F9 review queue.
 *
 * These are fire-on-demand triggers distinct from the schedule-driven equivalents
 * in opsScheduleClient.ts (POST /ops/schedules/{op}/run-now).
 *
 * No secrets in this file (CLAUDE.md §12).
 * Auth injected by apiFetch() (ADR-0052 §4.2).
 * Bounded internally: the backend enforces max_iter + token_budget (I7).
 */

import { apiBase, apiFetch } from "./base";

/** Generic response envelope for ops trigger endpoints. */
export interface OpsTriggerResponse {
  status: string;
}

/** Additive response metadata returned by the v1.6 corpus synthesis trigger. */
export interface SynthesizeTriggerResponse extends OpsTriggerResponse {
  max_pages?: number;
  mode?: string;
  max_candidates?: number;
  token_budget?: number;
  force?: boolean;
}

async function postOp(path: string, signal?: AbortSignal): Promise<OpsTriggerResponse> {
  const url = `${apiBase()}${path}`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(`POST ${path}: ${detail}`);
  }
  return res.json() as Promise<OpsTriggerResponse>;
}

async function postJsonOp(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<OpsTriggerResponse> {
  const url = `${apiBase()}${path}`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const responseBody = (await res.json()) as { detail?: string };
      if (responseBody.detail) detail = responseBody.detail;
    } catch {
      /* ignore */
    }
    throw new Error(`POST ${path}: ${detail}`);
  }
  return res.json() as Promise<OpsTriggerResponse>;
}

/**
 * triggerBackfillDomains — POST /ops/backfill-domains
 *
 * Triggers a bounded domain-classification run for pages not yet tagged with
 * a domain/* vocabulary tag.
 */
export async function triggerBackfillDomains(signal?: AbortSignal): Promise<OpsTriggerResponse> {
  return postOp("/ops/backfill-domains", signal);
}

/**
 * triggerReclassifyTypes — POST /ops/reclassify-types
 *
 * Triggers a bounded type-reclassification run for pages without a page_type.
 */
export async function triggerReclassifyTypes(signal?: AbortSignal): Promise<OpsTriggerResponse> {
  return postOp("/ops/reclassify-types", signal);
}

/**
 * triggerSynthesize — POST /ops/synthesize (ADR-0067 D3).
 *
 * Triggers a bounded corpus-level pass that seeds candidate clusters from the
 * 4-signal graph and, per cluster, either auto-writes a synthesis/comparison
 * page (high confidence) or proposes it to the F9 review queue (borderline).
 * The backend request model has all-default fields, but FastAPI still treats
 * body params as a body contract; send an explicit empty object so strict
 * deployments do not return 422.
 */
export interface SynthesizeTriggerOptions {
  max_pages?: number;
  max_candidates?: number;
  token_budget?: number;
  force?: boolean;
  mode?: "auto" | "review-only";
}

export async function triggerSynthesize(
  options: SynthesizeTriggerOptions = {},
  signal?: AbortSignal,
): Promise<SynthesizeTriggerResponse> {
  return postJsonOp("/ops/synthesize", options, signal);
}

// ─── System self-update (R12-3, B1: Watchtower HTTP API) ──────────────────────────

/** GET /ops/update-status — deployment update availability (read-only; safe to poll). */
export interface UpdateStatus {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  /**
   * Watchtower HTTP API is configured → POST /ops/system-update can act. When false the UI must
   * hide the "Update system" button (there is nothing to trigger).
   */
  update_supported: boolean;
}

/** POST /ops/system-update — result of poking Watchtower. */
export interface SystemUpdateResponse {
  triggered: boolean;
  message: string;
}

/** fetchUpdateStatus — GET /ops/update-status. Compares running vs latest release (server caches ~1h). */
export async function fetchUpdateStatus(signal?: AbortSignal): Promise<UpdateStatus> {
  const url = `${apiBase()}/ops/update-status`;
  const res = await apiFetch(url, { ...(signal !== undefined ? { signal } : {}) });
  if (!res.ok) throw new Error(`GET /ops/update-status: ${res.status}`);
  return res.json() as Promise<UpdateStatus>;
}

/**
 * triggerSystemUpdate — POST /ops/system-update (B1).
 *
 * Pokes Watchtower to pull the latest images and recreate the labelled containers. The backend is
 * itself recreated, so the connection may drop right after the 202 — the caller should treat a
 * network error immediately following the request as "update started" rather than a failure.
 */
export async function triggerSystemUpdate(signal?: AbortSignal): Promise<SystemUpdateResponse> {
  const url = `${apiBase()}/ops/system-update`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(`POST /ops/system-update: ${detail}`);
  }
  return res.json() as Promise<SystemUpdateResponse>;
}
