/**
 * eventsClient.ts — transport for GET /events (1.9.3 W1, FE-RT-2).
 *
 * INVARIANT I3: this module has zero parse logic — it only opens the connection.
 * Frame parsing + reconnect/backoff lives in `../store/eventsStore.ts`.
 *
 * Native `EventSource` is NOT used here: it cannot send custom headers, so it
 * cannot carry the app's Bearer token (ADR-0052 §4) or the Cloudflare Access
 * service-token headers — and the Bearer-token invariant explicitly forbids
 * putting the token in the URL/query string instead (app/auth.py Do-NOTs).
 * `apiFetch()` + a manual `ReadableStream` reader (same pattern as
 * `openChatStream` in chatClient.ts) is the only way to keep both headers.
 */

import { apiBase, apiFetch } from "./base";

/**
 * openEventsStream — open the SSE connection, carrying `Last-Event-ID` when
 * resuming after a reconnect (the backend re-sends the current state of both
 * signals as the very first frame regardless, per the server contract).
 */
export async function openEventsStream(
  lastEventId: string | null,
  signal: AbortSignal,
): Promise<Response> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;
  const res = await apiFetch(`${apiBase()}/events`, { headers, signal });
  if (!res.ok) {
    throw new Error(`GET /events: ${res.status}`);
  }
  return res;
}
