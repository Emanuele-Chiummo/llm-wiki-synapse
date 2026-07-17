/**
 * errors.ts — shared API error type + error-envelope parsing (FE-QUAL-5, ADR-0086).
 *
 * This module is the ONE place that knows the wire shape of a Synapse error response.
 * As of 2.0.0 every error body is the stable envelope (ADR-0086):
 *
 *   { "error": { "code": "not_found", "message": "...", "status": 404, "details": null } }
 *
 * `ApiError` carries `status`, `message`, and the stable machine-readable `code` so callers
 * can branch on `code` instead of parsing message strings. Clients should read an error body
 * through `errorMessageFromBody` / `errorCodeFromBody` (or just call `checkResponse`) rather
 * than reaching into `body.error` themselves — that keeps the shape in this file only.
 *
 * No secrets, API keys, or auth tokens live in this file (CLAUDE.md §12).
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The stable error envelope (ADR-0086). `details` is optional structured data. */
export interface ErrorEnvelope {
  code: string;
  message: string;
  status: number;
  details?: unknown;
}

interface ErrorEnvelopeBody {
  error?: Partial<ErrorEnvelope>;
}

/**
 * parseErrorEnvelope — extract the `error` object from a parsed JSON body, if present and
 * well-formed. Returns `undefined` for non-envelope bodies (so callers can fall back).
 */
export function parseErrorEnvelope(body: unknown): Partial<ErrorEnvelope> | undefined {
  if (body && typeof body === "object" && "error" in body) {
    const err = (body as ErrorEnvelopeBody).error;
    if (err && typeof err === "object") return err;
  }
  return undefined;
}

/** errorMessageFromBody — the human-readable message from an envelope body, or `undefined`. */
export function errorMessageFromBody(body: unknown): string | undefined {
  const err = parseErrorEnvelope(body);
  if (err && typeof err.message === "string" && err.message) return err.message;
  return undefined;
}

/** errorCodeFromBody — the stable machine-readable code from an envelope body, or `undefined`. */
export function errorCodeFromBody(body: unknown): string | undefined {
  const err = parseErrorEnvelope(body);
  if (err && typeof err.code === "string" && err.code) return err.code;
  return undefined;
}

/**
 * checkResponse — throws `ApiError` when `res.ok` is false.
 *
 * Parses the stable error envelope (ADR-0086) and uses `error.message` as the error text
 * and `error.code` as the machine-readable code; falls back to `res.statusText` if the body
 * isn't JSON or isn't an envelope.
 */
export async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let message = res.statusText;
    let code: string | undefined;
    try {
      const body = await res.json();
      message = errorMessageFromBody(body) ?? message;
      code = errorCodeFromBody(body);
    } catch {
      // ignore parse error; use statusText
    }
    throw new ApiError(res.status, `${res.status} ${message}`, code);
  }
}
