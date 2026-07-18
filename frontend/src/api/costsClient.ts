/**
 * costsClient.ts — HTTP client for GET /costs/summary (R9-1, F17).
 *
 * No secrets in this file. Base URL from apiBase() (ADR-0047 §2.1).
 * I3: fetch once on mount; no background polling.
 */

import { apiBase, apiFetch } from "./base";
import { checkResponse } from "./errors";

// ─── Response shape (mirrors backend GET /costs/summary) ─────────────────────

export interface CostsByProvider {
  provider: string;
  total_usd: number;
  call_count: number;
}

export interface CostsByOperation {
  operation: string;
  total_usd: number;
  call_count: number;
}

export interface CostsByDay {
  date: string; // "YYYY-MM-DD"
  total_usd: number;
}

export interface CostsSummary {
  period: string; // "YYYY-MM"
  by_provider: CostsByProvider[];
  /** Explanatory note when providers cannot be fully disambiguated (optional). */
  by_provider_note?: string | null;
  by_operation: CostsByOperation[];
  by_day: CostsByDay[];
  monthly_total_usd: number;
  threshold_usd: number;
  threshold_alert: boolean;
}

// ─── Client ───────────────────────────────────────────────────────────────────

/**
 * fetchCostsSummary — GET /costs/summary?month=YYYY-MM
 *
 * @param month  Optional "YYYY-MM" string. Defaults to current month server-side.
 * @param signal Optional AbortSignal for cancellation.
 */
export async function fetchCostsSummary(
  month?: string | null,
  signal?: AbortSignal,
): Promise<CostsSummary> {
  const qs = month ? `?month=${encodeURIComponent(month)}` : "";
  const url = `${apiBase()}/costs/summary${qs}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return res.json() as Promise<CostsSummary>;
}
