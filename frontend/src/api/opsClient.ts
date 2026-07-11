/**
 * opsClient.ts — lightweight HTTP client for on-demand ops trigger endpoints [F18][F9].
 *
 * POST /ops/backfill-domains  → trigger domain classification for unclassified pages
 * POST /ops/reclassify-types  → trigger type reclassification for untyped pages
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
