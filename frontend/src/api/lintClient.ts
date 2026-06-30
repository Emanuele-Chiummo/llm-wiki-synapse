/**
 * lintClient.ts — typed API client for the K2 Lint-fix endpoints (ADR-0037 §6).
 *
 * POST /lint/scan                   → LintScanResponse (200): run + findings
 * GET  /lint/runs                   → LintRunListResponse
 * GET  /lint/runs/{id}              → LintRun
 * GET  /lint/findings?status=open   → LintFindingListResponse
 * POST /lint/findings/{id}/apply    → LintFinding (200): human-gated fix / acknowledge
 * POST /lint/findings/{id}/dismiss  → LintFinding (200)
 *
 * Apply semantics (ADR-0037 §6, CLAUDE.md §4b F15):
 *   - missing-xref + missing-page → real file write (proposed_action executed)
 *   - orphan-page + contradiction + stale-claim → flag-only; acknowledged with no write
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
} from "./types";
import { ApiError } from "./graphClient";

const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "";

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

/**
 * Start a bounded lint scan.
 * POST /lint/scan { vault_id, max_iter?, token_budget? }
 * Returns 200 { run, findings } immediately (synchronous bounded run — I7).
 * The run may take tens of seconds for large vaults; the UI shows a spinner.
 */
export async function runLintScan(
  params: LintScanRequest,
  signal?: AbortSignal,
): Promise<LintScanResponse> {
  const url = `${API_BASE}/lint/scan`;
  const res = await fetch(url, {
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
  let url = `${API_BASE}/lint/runs?limit=${limit}&offset=${offset}`;
  if (vaultId) url += `&vault_id=${encodeURIComponent(vaultId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as LintRunListResponse;
}

/**
 * Fetch a single lint run by id.
 * GET /lint/runs/{id}
 * 404 if unknown run_id.
 */
export async function fetchLintRun(
  runId: string,
  signal?: AbortSignal,
): Promise<LintRun> {
  const url = `${API_BASE}/lint/runs/${encodeURIComponent(runId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as LintRun;
}

/**
 * Fetch paginated lint findings.
 * GET /lint/findings?vault_id=<vaultId>&status=<status>&limit=<limit>&offset=<offset>
 *
 * status defaults to "open". vault_id is required by the backend.
 */
export async function fetchLintFindings(
  options: {
    vaultId: string;
    status?: "open" | "applied" | "dismissed";
    limit?: number;
    offset?: number;
  },
  signal?: AbortSignal,
): Promise<LintFindingListResponse> {
  const { vaultId, status = "open", limit = 50, offset = 0 } = options;
  const url =
    `${API_BASE}/lint/findings` +
    `?vault_id=${encodeURIComponent(vaultId)}` +
    `&status=${encodeURIComponent(status)}` +
    `&limit=${limit}&offset=${offset}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${API_BASE}/lint/findings/${encodeURIComponent(findingId)}/apply`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as LintFinding;
}

/**
 * Dismiss a lint finding.
 * POST /lint/findings/{id}/dismiss → 200 LintFinding
 */
export async function dismissLintFinding(findingId: string): Promise<LintFinding> {
  const url = `${API_BASE}/lint/findings/${encodeURIComponent(findingId)}/dismiss`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as LintFinding;
}
