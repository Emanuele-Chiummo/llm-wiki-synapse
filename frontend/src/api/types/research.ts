/**
 * Deep Research API contract types (F10, FE-QUAL-11 split of api/types.ts).
 * POST /research/start · GET /research/runs (ADR-0024 §8)
 */

export type ResearchStatus =
  "running" | "converged" | "max_iter_reached" | "budget_exhausted" | "error";

/** One item in GET /research/runs (summary, no synthesis_text) */
export interface ResearchRunSummary {
  id: string;
  vault_id: string;
  topic: string;
  status: ResearchStatus;
  iterations_used: number;
  sources_fetched: number;
  total_cost_usd: number;
  started_at: string; // ISO-8601
  completed_at: string | null; // ISO-8601 or null while running
}

export interface ResearchRunListResponse {
  items: ResearchRunSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** One fetched source in the run detail */
export interface ResearchSource {
  url: string;
  title: string | null;
  relevance_score: number | null;
  iteration: number;
}

/** Full detail from GET /research/runs/{id} */
export interface ResearchRunDetail {
  id: string;
  vault_id: string;
  topic: string;
  status: ResearchStatus;
  max_iter: number;
  token_budget: number;
  iterations_used: number;
  queries_used: string[];
  sources_fetched: number;
  total_cost_usd: number;
  synthesis_text: string | null;
  synthesis_page_id: string | null;
  sources: ResearchSource[];
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
}

export interface ResearchStartResponse {
  run_id: string;
}
