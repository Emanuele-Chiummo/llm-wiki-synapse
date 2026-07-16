/**
 * lintClient.ts — typed API client for the K2 Lint-fix endpoints (ADR-0037 §6).
 *
 * POST /lint/scan                         → LintScanResponse (200): run + findings
 * GET  /lint/runs                         → LintRunListResponse
 * GET  /lint/runs/{id}                    → LintRun
 * GET  /lint/findings?status=open         → LintFindingListResponse
 * POST /lint/findings/{id}/apply          → LintFinding (200): human-gated fix / acknowledge
 * POST /lint/findings/{id}/dismiss        → LintFinding (200)
 * POST /lint/findings/batch               → LintBatchResponse (B1-L5)
 * POST /lint/findings/{id}/send-to-review → 200; finding becomes status=applied (B1-L6)
 * DELETE /pages/{page_id}                → LintDeletePageResponse (B1-L9)
 *
 * Apply semantics (ADR-0037 §6, CLAUDE.md §4b F15):
 *   - missing-xref + missing-page → real file write (proposed_action executed)
 *   - broken-wikilink with suggested_target → real wikilink rewrite (B1-L3)
 *   - broken-wikilink without suggestion / orphan-page / contradiction / stale-claim
 *       → flag-only; acknowledged with no write
 *   The UI MUST reflect this distinction (see LINT_FLAG_ONLY_CATEGORIES in types.ts).
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 */

import type {
  LintScanResponse,
  LintScanRequest,
  LintRunListResponse,
  LintRun,
  LintFindingListResponse,
  LintFinding,
  LintBatchResponse,
  LintDeletePageResponse,
  LintCategory,
  LintSeverity,
} from "./types";
import { checkResponse } from "./errors";
import { apiBase, apiFetch } from "./base";
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

/**
 * Start a bounded lint scan.
 * POST /lint/scan?semantic=true|false { vault_id, max_iter?, token_budget? }
 * Returns 200 { run, findings } immediately (synchronous bounded run — I7).
 * The run may take tens of seconds for large vaults; the UI shows a spinner.
 *
 * B1-L8: semantic=false → deterministic-only scan (free, zero LLM cost).
 *         semantic=true (default) → structural + LLM semantic review.
 */
export async function runLintScan(
  params: LintScanRequest,
  signal?: AbortSignal,
  semantic = true,
): Promise<LintScanResponse> {
  const url = `${apiBase()}/lint/scan?semantic=${semantic ? "true" : "false"}`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as LintScanResponse;
}

/**
 * Fetch paginated lint run history.
 * GET /lint/runs?limit=<limit>&offset=<offset>[&vault_id=<vaultId>]
 */
export async function fetchLintRuns(
  options: { limit?: number; offset?: number; vaultId?: string } = {},
  signal?: AbortSignal,
): Promise<LintRunListResponse> {
  const { limit = 20, offset = 0, vaultId } = options;
  let url = `${apiBase()}/lint/runs?limit=${limit}&offset=${offset}`;
  if (vaultId) url += `&vault_id=${encodeURIComponent(vaultId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as LintRunListResponse;
}

/**
 * Fetch a single lint run by id.
 * GET /lint/runs/{id}
 * 404 if unknown run_id.
 */
export async function fetchLintRun(runId: string, signal?: AbortSignal): Promise<LintRun> {
  const url = `${apiBase()}/lint/runs/${encodeURIComponent(runId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as LintRun;
}

/**
 * Fetch paginated lint findings.
 * GET /lint/findings?vault_id=<vaultId>&status=<status>&limit=<limit>&offset=<offset>
 *                  [&category=<cat>][&severity=<sev>]
 *
 * status defaults to "open". vault_id is required by the backend.
 * B1-L10: optional category and severity filters.
 */
export async function fetchLintFindings(
  options: {
    vaultId: string;
    status?: "open" | "applied" | "dismissed";
    limit?: number;
    offset?: number;
    /** B1-L10: optional category filter. */
    category?: LintCategory | null;
    /** B1-L10: optional severity filter. */
    severity?: LintSeverity | null;
  },
  signal?: AbortSignal,
): Promise<LintFindingListResponse> {
  const { vaultId, status = "open", limit = 50, offset = 0, category, severity } = options;
  let url =
    `${apiBase()}/lint/findings` +
    `?vault_id=${encodeURIComponent(vaultId)}` +
    `&status=${encodeURIComponent(status)}` +
    `&limit=${limit}&offset=${offset}`;
  if (category) url += `&category=${encodeURIComponent(category)}`;
  if (severity) url += `&severity=${encodeURIComponent(severity)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as LintFindingListResponse;
}

/**
 * Apply (human-gate) a lint finding.
 * POST /lint/findings/{id}/apply → 200 LintFinding
 *
 * For missing-xref / missing-page: executes the proposed_action (file write).
 * For orphan-page / contradiction / stale-claim: flag-only acknowledgement — no write.
 * 409 if the finding is not open (already applied / dismissed).
 */
export async function applyLintFinding(findingId: string): Promise<LintFinding> {
  const url = `${apiBase()}/lint/findings/${encodeURIComponent(findingId)}/apply`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as LintFinding;
}

/**
 * Dismiss a lint finding.
 * POST /lint/findings/{id}/dismiss → 200 LintFinding
 */
export async function dismissLintFinding(findingId: string): Promise<LintFinding> {
  const url = `${apiBase()}/lint/findings/${encodeURIComponent(findingId)}/dismiss`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as LintFinding;
}

/**
 * Server-side per-request cap on batch size (I7 — mirrors _BATCH_MAX_IDS in the
 * lint router). Selections larger than this are split into successive requests
 * client-side so "Fix/Ignore/Send selected" works on any number of findings.
 */
const BATCH_CHUNK_SIZE = 200;

/**
 * Batch apply / dismiss / send-to-review for multiple findings (B1-L5).
 * POST /lint/findings/batch { ids: string[], action: "apply"|"dismiss"|"send-to-review" }
 * → 200 { results, ok_count, error_count }
 *
 * The endpoint caps each request at 200 ids (I7). To keep the caller free of that
 * limit, selections beyond the cap are split into ≤200-id chunks sent sequentially
 * (the server processes findings sequentially anyway), and the per-chunk responses
 * are merged into one aggregate response.
 */
export async function batchLintAction(
  ids: string[],
  action: "apply" | "dismiss" | "send-to-review",
  signal?: AbortSignal,
): Promise<LintBatchResponse> {
  const url = `${apiBase()}/lint/findings/batch`;
  const merged: LintBatchResponse = { results: [], ok_count: 0, error_count: 0 };

  for (let i = 0; i < ids.length; i += BATCH_CHUNK_SIZE) {
    const chunk = ids.slice(i, i + BATCH_CHUNK_SIZE);
    const res = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: chunk, action }),
      ...(signal !== undefined ? { signal } : {}),
    });
    await checkResponse(res);
    const page = (await res.json()) as LintBatchResponse;
    merged.results.push(...page.results);
    merged.ok_count += page.ok_count;
    merged.error_count += page.error_count;
  }

  return merged;
}

/**
 * Send a single lint finding to the review queue (B1-L6).
 * POST /lint/findings/{id}/send-to-review → 200; finding becomes status=applied.
 */
export async function sendLintFindingToReview(findingId: string): Promise<LintFinding> {
  const url = `${apiBase()}/lint/findings/${encodeURIComponent(findingId)}/send-to-review`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as LintFinding;
}

/**
 * Delete a wiki page (B1-L9).
 * DELETE /pages/{page_id} → 200 { deleted_page_id, cleaned_references }
 *
 * This is the two-stage-confirmed orphan-page Delete path. The caller MUST
 * present a two-stage confirm (armed-red pattern) before invoking this.
 * On success the referencing wiki links are rewritten to plain text server-side.
 */
export async function deleteWikiPage(pageId: string): Promise<LintDeletePageResponse> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}`;
  const res = await apiFetch(url, { method: "DELETE" });
  await checkResponse(res);
  return (await res.json()) as LintDeletePageResponse;
}
