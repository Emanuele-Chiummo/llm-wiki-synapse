/**
 * errors.ts — shared API error type + response-checking helper (FE-QUAL-5).
 *
 * `ApiError` used to live in `graphClient.ts` (an odd home for a cross-cutting
 * concern) and `checkResponse()` was copy-pasted verbatim into 11 other API
 * clients. This module is the ONE place both live now — every client imports
 * from here instead of re-implementing the same `!res.ok` → parse `detail` →
 * throw dance.
 *
 * No secrets, API keys, or auth tokens live in this file (CLAUDE.md §12).
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * checkResponse — throws `ApiError` when `res.ok` is false.
 *
 * Attempts to parse a JSON body with a `detail` string field (FastAPI's
 * standard error shape) and uses it as the error message; falls back to
 * `res.statusText` if the body isn't JSON or has no `detail` field.
 */
export async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error; use statusText
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}
