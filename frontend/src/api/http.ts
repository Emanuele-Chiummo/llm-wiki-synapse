/**
 * Small shared HTTP helpers for frontend clients.
 *
 * Keeps request timeouts consistent without changing backend contracts.
 *
 * fetchWithTimeout delegates to apiFetch (from base.ts) so every timed call
 * automatically carries the Authorization header (ADR-0052 §4.2).
 */

import { apiFetch } from "./base";

export class ApiTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = "ApiTimeoutError";
  }
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<Response> {
  if (timeoutMs <= 0) {
    return apiFetch(input, init);
  }

  const controller = new AbortController();
  const parentSignal = init.signal;
  let didTimeout = false;

  const abortFromParent = () => {
    controller.abort(parentSignal?.reason);
  };

  if (parentSignal?.aborted) {
    abortFromParent();
  } else {
    parentSignal?.addEventListener("abort", abortFromParent, { once: true });
  }

  const timeoutId = globalThis.setTimeout(() => {
    didTimeout = true;
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);

  try {
    return await apiFetch(input, { ...init, signal: controller.signal });
  } catch (err) {
    if (didTimeout) {
      throw new ApiTimeoutError(timeoutMs);
    }
    throw err;
  } finally {
    globalThis.clearTimeout(timeoutId);
    parentSignal?.removeEventListener("abort", abortFromParent);
  }
}
