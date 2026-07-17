/**
 * eventsStore.ts — GET /events SSE consumer (1.9.3 W1, FE-RT-2).
 *
 * ADDITIVE, non-regression: this is a SECOND channel that runs ALONGSIDE the
 * existing REST pollers (statusStore, activityStore, ingestStore,
 * importScheduleStore, researchStore, SourcesView ingest-all, ConvertPanel,
 * HomeDashboard synthesize-status). None of them are removed or disabled —
 * see the module docstrings of statusStore.ts/activityStore.ts for the
 * `sseHealthy` cadence hook this store feeds. If the stream cannot connect at
 * all (no /events route, network down, 3+ consecutive reconnect failures), the
 * poll chains simply keep running at their normal cadence forever — there is
 * no "give up permanently" path here.
 *
 * Wiring (I3 — reuse existing stores, no parallel state):
 *   - SSE `data_version` event → useStatusStore.setDataVersion() (same store
 *     HomeDashboard/GraphViewer already subscribe to via selectStatusDataVersion).
 *   - SSE `queue` event        → useActivityStore.applyCountsPatch() (merges
 *     into the existing snapshot; `tasks`/`batch` still come from the REST poll).
 *
 * Transport: fetch() + response.body.getReader() + TextDecoder, split on the
 * SSE frame delimiter ("\n\n"). Native EventSource is NOT used — see
 * `api/eventsClient.ts` docstring for why (custom auth headers).
 *
 * Reconnect: exponential backoff (1s → 2s → 4s → 8s → 16s → capped at 30s),
 * carrying `Last-Event-ID` so the server resyncs immediately on reconnect.
 * After 3 consecutive failed connection attempts, `healthy` flips to false —
 * this is the ONLY effect of a failed stream: the REST pollers' cadence
 * un-relaxes back to their normal active/idle timings (they never stopped
 * running). The reconnect loop itself keeps retrying forever in the
 * background (bounded per-attempt by the backoff cap, not by a give-up count),
 * so the stream re-attaches transparently once connectivity returns.
 */

import { create } from "zustand";
import { openEventsStream } from "../api/eventsClient";
import { useStatusStore } from "./statusStore";
import { useActivityStore } from "./activityStore";
import type { QueueCountsPatch } from "./activityStore";

// ─── Backoff schedule ──────────────────────────────────────────────────────────

const BACKOFF_SCHEDULE_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];
/** Consecutive failed connection attempts before `healthy` flips false (I7-style guard). */
const UNHEALTHY_AFTER_FAILURES = 3;

function backoffDelayMs(attempt: number): number {
  const idx = Math.min(attempt, BACKOFF_SCHEDULE_MS.length - 1);
  return BACKOFF_SCHEDULE_MS[idx] ?? 30_000;
}

// ─── Frame parsing ──────────────────────────────────────────────────────────────

interface ParsedFrame {
  id?: string;
  event?: string;
  data?: string;
}

/** Parse one SSE frame (already split on the blank-line delimiter) into id/event/data. */
export function parseSseFrame(frame: string): ParsedFrame {
  const out: ParsedFrame = {};
  for (const line of frame.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank / comment (heartbeat)
    if (line.startsWith("id: ")) out.id = line.slice(4);
    else if (line.startsWith("event: ")) out.event = line.slice(7);
    else if (line.startsWith("data: ")) out.data = line.slice(6);
  }
  return out;
}

/**
 * dispatchSseFrame — apply one parsed frame to the relevant store.
 * Exported for direct unit testing without spinning up the reader loop.
 */
export function dispatchSseFrame(frame: ParsedFrame): void {
  if (!frame.event || frame.data === undefined) return;
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return; // malformed data — ignore this frame, keep the stream alive
  }
  if (frame.event === "data_version") {
    const dv = (payload as { data_version?: unknown }).data_version;
    if (typeof dv === "number") {
      useStatusStore.getState().setDataVersion(dv);
    }
  } else if (frame.event === "queue") {
    const p = payload as Partial<QueueCountsPatch>;
    if (
      typeof p.paused === "boolean" &&
      typeof p.pending === "number" &&
      typeof p.processing === "number" &&
      typeof p.failed === "number" &&
      typeof p.completed_since_idle === "number" &&
      typeof p.total === "number"
    ) {
      useActivityStore.getState().applyCountsPatch(p as QueueCountsPatch);
    }
  }
}

// ─── Store ──────────────────────────────────────────────────────────────────────

export type EventsConnectionState = "idle" | "connecting" | "open" | "closed";

interface EventsState {
  connectionState: EventsConnectionState;
  /**
   * True once the stream is open and has not yet accumulated
   * UNHEALTHY_AFTER_FAILURES consecutive reconnect failures. Consumed by
   * statusPollDelayMs / activityPollDelayMs to relax REST poll cadence.
   */
  healthy: boolean;
  /** Start the SSE connection loop. Idempotent — a second call while already
   * running is a no-op. Returns a cleanup function (mirrors the pollChain
   * `startPolling()` convention used across the other stores). */
  start: () => () => void;
  /** Force-stop the stream and any pending reconnect timer. */
  stop: () => void;
}

let controller: AbortController | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let refCount = 0;
let stopped = true;
let lastEventId: string | null = null;
let consecutiveFailures = 0;

function clearReconnectTimer(): void {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

/**
 * resetEventsStoreForTests — TEST-ONLY full reset of this module's internal mutable
 * state (controller/refCount/lastEventId/consecutiveFailures), in addition to the
 * Zustand-visible state that `stop()` already resets.
 *
 * Production `stop()` deliberately does NOT clear `lastEventId`/`consecutiveFailures`
 * — a real stop/restart cycle (e.g. a section remount) should resume from where the
 * connection left off. Tests need a hard reset between cases instead, since these
 * are module-level singletons shared across the whole test file.
 */
export function resetEventsStoreForTests(): void {
  useEventsStore.getState().stop();
  lastEventId = null;
  consecutiveFailures = 0;
}

export const useEventsStore = create<EventsState>((set) => ({
  connectionState: "idle",
  healthy: false,

  start: () => {
    refCount += 1;
    if (!stopped) {
      // Already running (refcounted like the other stores' pollChain.subscribe()).
      return () => {
        refCount -= 1;
        if (refCount <= 0) {
          refCount = 0;
          useEventsStore.getState().stop();
        }
      };
    }
    stopped = false;

    const runLoop = async (): Promise<void> => {
      while (!stopped) {
        set({ connectionState: "connecting" });
        const ctrl = new AbortController();
        controller = ctrl;
        try {
          const res = await openEventsStream(lastEventId, ctrl.signal);
          set({ connectionState: "open" });
          consecutiveFailures = 0;
          set({ healthy: true });

          const body = res.body;
          if (!body) throw new Error("GET /events: response body is null");
          const reader = body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let idx: number;
            while ((idx = buffer.indexOf("\n\n")) !== -1) {
              const rawFrame = buffer.slice(0, idx);
              buffer = buffer.slice(idx + 2);
              const parsed = parseSseFrame(rawFrame);
              if (parsed.id) lastEventId = parsed.id;
              dispatchSseFrame(parsed);
            }
          }
        } catch {
          if (ctrl.signal.aborted) {
            // stop() was called — exit the loop cleanly, no reconnect.
            return;
          }
        }

        if (stopped) return;
        set({ connectionState: "closed" });
        consecutiveFailures += 1;
        if (consecutiveFailures >= UNHEALTHY_AFTER_FAILURES) {
          set({ healthy: false });
        }

        // Wait out the backoff delay, but resolve immediately if stop() aborts the
        // SAME controller in the meantime — otherwise this promise would dangle
        // forever once clearReconnectTimer() cancels the setTimeout without ever
        // settling it (I7 — no orphaned pending work).
        const delay = backoffDelayMs(consecutiveFailures - 1);
        await new Promise<void>((resolve) => {
          const timer = setTimeout(() => {
            ctrl.signal.removeEventListener("abort", onAbort);
            resolve();
          }, delay);
          function onAbort(): void {
            clearTimeout(timer);
            resolve();
          }
          ctrl.signal.addEventListener("abort", onAbort, { once: true });
          reconnectTimer = timer;
        });
      }
    };

    void runLoop();

    return () => {
      refCount -= 1;
      if (refCount <= 0) {
        refCount = 0;
        useEventsStore.getState().stop();
      }
    };
  },

  stop: () => {
    stopped = true;
    refCount = 0;
    controller?.abort();
    controller = null;
    clearReconnectTimer();
    set({ connectionState: "idle", healthy: false });
  },
}));

// ─── Selectors ────────────────────────────────────────────────────────────────

export function selectEventsConnectionState(s: EventsState): EventsConnectionState {
  return s.connectionState;
}

export function selectEventsHealthy(s: EventsState): boolean {
  return s.healthy;
}

export function selectStartEventsStream(s: EventsState): EventsState["start"] {
  return s.start;
}
