/**
 * appConfigClient.ts — typed HTTP client for the /config/app endpoints (ADR-0053, R11-2).
 *
 * Backend contract (live in backend/app/main.py):
 *   GET  /config/app         → { settings: [{ key, value, source }] }  — 8 migrated keys
 *   PUT  /config/app/{key}   body { value: string } → 204
 *   DELETE /config/app/{key} → 204 (resets to env default)
 *
 * The 8 allowed keys match ALLOWED_CONFIG_KEYS in config_overrides.py:
 *   pdf_extractor, marker_service_url, marker_timeout_seconds,
 *   cost_alert_threshold_usd, embeddings_enabled, embedding_format,
 *   overview_language, wikilink_enrich_enabled
 *
 * source is "env" when the env-var baseline governs, "override" when a DB row exists.
 * Auth: handled by the SynapseAuthMiddleware (ADR-0052) — no per-route dependency.
 * I7: the backend serves all reads from an in-process cache; no per-request DB scan.
 * I6: embedding-related keys (S5/S6) route through existing backend seams — the client
 *     just sends strings, never interpreting embedding shapes.
 * No secrets in this file. Base URL from apiBase() (ADR-0047 §2.1).
 */

import { apiBase, apiFetch } from "./base";

// ─── Types ────────────────────────────────────────────────────────────────────

/** One entry in the GET /config/app response. */
export interface AppConfigEntry {
  key: string;
  value: string;
  source: "env" | "override";
}

/** GET /config/app response envelope. */
export interface AppConfigResponse {
  settings: AppConfigEntry[];
}

/**
 * Runtime-tunable config keys.
 * S1–S8: SPRINT-v1.1 §2 R11-2 MIGRATED list.
 * S9: domain_vocabulary (ADR-0054 §2.1, F18) — JSON array of domain name strings.
 * A5: lint_schedule / backfill_schedule — ops schedule frequencies (R12-7).
 */
export type AppConfigKey =
  | "pdf_extractor"
  | "marker_service_url"
  | "marker_timeout_seconds"
  | "cost_alert_threshold_usd"
  | "embeddings_enabled"
  | "embedding_format"
  | "overview_language"
  | "wikilink_enrich_enabled"
  | "domain_vocabulary"
  | "lint_schedule"
  | "backfill_schedule";

// ─── Client functions ─────────────────────────────────────────────────────────

/**
 * getAppConfig — GET /config/app
 * Returns all 8 migrated settings with their effective values and source badges.
 * No DB round-trip — the backend serves from in-process cache (I7).
 */
export async function getAppConfig(signal?: AbortSignal): Promise<AppConfigResponse> {
  const url = `${apiBase()}/config/app`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`GET /config/app: ${detail}`);
  }
  return res.json() as Promise<AppConfigResponse>;
}

/**
 * putAppConfig — PUT /config/app/{key}
 * Upserts an override for one of the 8 allowed keys.
 * Returns 204 on success; throws on 400 (invalid_key), 422 (validation), or 4xx/5xx.
 *
 * I6: S5/S6 values are plain strings ("true"/"false", "ollama"/"openai") — the backend
 * routes them through the existing ADR-0030/0031 seams. This client sends strings only.
 */
export async function putAppConfig(key: AppConfigKey, value: string): Promise<void> {
  const url = `${apiBase()}/config/app/${encodeURIComponent(key)}`;
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string; error?: string };
      if (body.error) detail = body.error;
      else if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`PUT /config/app/${key}: ${detail}`);
  }
  // 204 No Content — no body to parse
}

/**
 * resetAppConfig — DELETE /config/app/{key}
 * Removes the override row; the setting reverts to the env-var baseline.
 * This is the ONLY reset mechanism (ADR-0053 §3.3 — PUT {value:null} is rejected 422).
 */
export async function resetAppConfig(key: AppConfigKey): Promise<void> {
  const url = `${apiBase()}/config/app/${encodeURIComponent(key)}`;
  const res = await apiFetch(url, { method: "DELETE" });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string; error?: string };
      if (body.error) detail = body.error;
      else if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`DELETE /config/app/${key}: ${detail}`);
  }
  // 204 No Content
}
