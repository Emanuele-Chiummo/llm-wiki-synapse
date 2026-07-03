/**
 * scenariosClient.ts — typed API client for scenario template endpoints [R7-1 / F1].
 *
 * Endpoints:
 *   GET  /scenarios              → [{id, name, description}]
 *   POST /scenarios/{id}/apply   → {applied: true}
 *
 * INVARIANT I3: no side effects beyond the network call.
 * INVARIANT I6: no provider IDs here.
 * No secrets in this file (CLAUDE.md §12).
 */

import { apiBase, apiFetch } from "./base";
import { ApiError } from "./graphClient";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ScenarioItem {
  id: string;
  name: string;
  description: string;
}

export interface ScenarioApplyResponse {
  applied: boolean;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore JSON parse failure
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

// ─── API functions ────────────────────────────────────────────────────────────

/**
 * Fetch the list of available scenario templates.
 * GET /scenarios → { items: ScenarioItem[] } (envelope unwrapped here)
 */
export async function fetchScenarios(signal?: AbortSignal): Promise<ScenarioItem[]> {
  const url = `${apiBase()}/scenarios`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  const body = (await res.json()) as { items?: ScenarioItem[] };
  return Array.isArray(body.items) ? body.items : [];
}

/**
 * Apply a scenario template (writes purpose.md and schema.md).
 * POST /scenarios/{id}/apply → ScenarioApplyResponse
 */
export async function applyScenario(
  id: string,
  signal?: AbortSignal,
): Promise<ScenarioApplyResponse> {
  const url = `${apiBase()}/scenarios/${encodeURIComponent(id)}/apply`;
  const res = await apiFetch(url, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ScenarioApplyResponse;
}
