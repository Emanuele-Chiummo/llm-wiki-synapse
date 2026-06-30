/**
 * importScheduleClient.ts — API client for scheduled folder import (ADR-0020 §4.6 / Feature S).
 *
 * GET    /import-schedule        → ImportSchedule
 * PUT    /import-schedule        → ImportSchedulePutResponse (+ dir_ok/dir_message)
 * POST   /import-schedule/run-now → { status: "started" }  (202) or 409/400
 *
 * No secrets. No hardcoded model/provider IDs (I6).
 */

import type {
  ImportSchedule,
  ImportSchedulePutBody,
  ImportSchedulePutResponse,
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
      // ignore
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

/**
 * Fetch the current schedule config + last-run status.
 * Returns sane defaults (enabled:false, frequency:"1h", …) if no row exists.
 */
export async function getImportSchedule(signal?: AbortSignal): Promise<ImportSchedule> {
  const res = await fetch(`${API_BASE}/import-schedule`, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ImportSchedule;
}

/**
 * Save schedule config.
 * Returns the saved schedule + { dir_ok, dir_message }.
 * The backend saves even if dir_ok:false (save-then-warn semantics).
 */
export async function putImportSchedule(
  body: ImportSchedulePutBody,
  signal?: AbortSignal,
): Promise<ImportSchedulePutResponse> {
  const res = await fetch(`${API_BASE}/import-schedule`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ImportSchedulePutResponse;
}

/**
 * Trigger an immediate bounded scan.
 * 202 → scan started.
 * 409 → scan already running (throw ApiError with status 409).
 * 400 → disabled or dir missing (throw ApiError with status 400).
 */
export async function runImportNow(signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${API_BASE}/import-schedule/run-now`, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}
