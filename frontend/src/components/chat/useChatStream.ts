/**
 * useChatStream.ts — NDJSON stream consumer for POST /chat/stream (ADR-0019 §2.1/§3 / F6).
 *
 * Transport: fetch() + response.body.getReader() + TextDecoder, split on \n.
 * Partial-line buffering: accumulate across chunks; only JSON.parse on complete lines.
 *
 * Per-token work (I3):
 *   - token event  → buffered locally, flushed via appendStreamDelta at most once per
 *                    animation frame (FE-PERF-3) [cheap string append, no parse]
 *   - think event  → same rAF-batched buffer as token
 *   - done  event  → pending buffer is flushed SYNCHRONOUSLY first, then finalizeTurn()
 *                    [triggers parse ONCE in MarkdownView]
 *   - error event  → pending buffer discarded + showToast + clearStream
 *
 * FE-PERF-3: a fast local (Ollama) stream can emit many tokens per animation frame.
 * Calling the store's set() once per token forces a re-render at token frequency.
 * Deltas are accumulated in a ref between frames and flushed with ONE set() call
 * (appendStreamDelta) per requestAnimationFrame tick — this changes the FREQUENCY of
 * store writes only. It does NOT introduce any new parsing: the flushed value is still
 * the raw, un-parsed buffer, and markdown/LaTeX parsing still happens exactly once, at
 * stream end (I3 unchanged).
 *
 * INVARIANT I7: total_cost_usd is always logged to console (structured) and returned
 *   via lastUsage in the store for display.
 *
 * Abort: AbortController is created per call; Cancel button calls abort(); unmount aborts.
 */

import { useRef, useCallback, useEffect } from "react";
import { useChatStore } from "../../store/chatStore";
import type { ChatMessage } from "../../store/chatStore";
import { openChatStream } from "../../api/chatClient";
import type { ChatStreamRequest, StreamEvent } from "../../api/chatClient";
import { showToast } from "../common/Toast";

export interface UseChatStreamReturn {
  send: (req: ChatStreamRequest) => void;
  abort: () => void;
}

export function useChatStream(): UseChatStreamReturn {
  const abortRef = useRef<AbortController | null>(null);
  /**
   * F4 double-submit guard: each send() call increments this counter and captures
   * a snapshot (`myGen`). Before any store write the async loop checks
   * `generationRef.current !== myGen` — if true, a newer send() has superseded
   * this stream, so all callbacks become no-ops and stream state is NOT clobbered.
   */
  const generationRef = useRef(0);

  const appendStreamDelta = useChatStore((s) => s.appendStreamDelta);
  const setIsStreaming = useChatStore((s) => s.setIsStreaming);
  const setStreamError = useChatStore((s) => s.setStreamError);
  const finalizeTurn = useChatStore((s) => s.finalizeTurn);
  const clearStream = useChatStore((s) => s.clearStream);

  // ── FE-PERF-3: rAF-batched token/think flush ──────────────────────────────
  // Deltas accumulate here between animation frames; a single appendStreamDelta()
  // call flushes both buffers together at most once per frame.
  const pendingContentRef = useRef("");
  const pendingThinkRef = useRef("");
  const rafIdRef = useRef<number | null>(null);

  const cancelScheduledFlush = useCallback(() => {
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
  }, []);

  /** Flush whatever is pending right now (synchronously) — cancels any scheduled rAF. */
  const flushPendingSync = useCallback(() => {
    cancelScheduledFlush();
    const content = pendingContentRef.current;
    const think = pendingThinkRef.current;
    pendingContentRef.current = "";
    pendingThinkRef.current = "";
    if (content || think) appendStreamDelta(content, think);
  }, [appendStreamDelta, cancelScheduledFlush]);

  /** Discard whatever is pending (used on abort/error — the buffer is being thrown away). */
  const discardPending = useCallback(() => {
    cancelScheduledFlush();
    pendingContentRef.current = "";
    pendingThinkRef.current = "";
  }, [cancelScheduledFlush]);

  const scheduleFlush = useCallback(() => {
    if (rafIdRef.current !== null) return; // already scheduled for this frame
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      const content = pendingContentRef.current;
      const think = pendingThinkRef.current;
      pendingContentRef.current = "";
      pendingThinkRef.current = "";
      if (content || think) appendStreamDelta(content, think);
    });
  }, [appendStreamDelta]);

  const abort = useCallback(() => {
    // F2/F3: delegate to store.abortStream() — it calls the registered fn (which aborts
    // the AbortController) and clears all streaming state in one atomic set().
    // FE-PERF-3: also drop any not-yet-flushed buffer so a late rAF can't re-populate
    // streamingContent after abortStream() already cleared it.
    discardPending();
    useChatStore.getState().abortStream();
    abortRef.current = null;
  }, [discardPending]);

  // F3: abort in-flight stream on component unmount so a detached reader cannot keep
  // writing into the global store after the chat section is navigated away from.
  useEffect(() => {
    return () => {
      discardPending();
      if (abortRef.current) {
        useChatStore.getState().abortStream();
        abortRef.current = null;
      }
    };
  }, [discardPending]);

  const send = useCallback(
    (req: ChatStreamRequest) => {
      // FE-PERF-3: drop any buffered-but-not-yet-flushed deltas from a prior (now
      // superseded) generation BEFORE abortStream() clears the store — otherwise a
      // stale rAF flush scheduled by the previous stream could merge its leftover
      // buffer into this new stream's content once it fires.
      discardPending();
      // F2/F3: abort any in-flight stream via the store (clears stream state + calls the
      // registered abort fn for the old controller).
      useChatStore.getState().abortStream();
      abortRef.current = null;

      // F4: snapshot the generation ID for THIS stream so its callbacks can detect
      // when a newer send() has superseded them and become no-ops.
      const myGen = ++generationRef.current;

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      // Register this controller's abort fn so external callers (conversation switch,
      // unmount) can abort without prop-drilling (F2/F3).
      useChatStore.getState().setStreamAbortFn(() => ctrl.abort());

      setIsStreaming(true);
      setStreamError(null);

      void (async () => {
        try {
          const res = await openChatStream(req, ctrl.signal);
          if (!res.body) throw new Error("Response body is null");

          const reader = res.body.getReader();
          const decoder = new TextDecoder("utf-8");
          let lineBuffer = "";

          // Read NDJSON stream: accumulate chunks, split on \n, parse complete lines
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // F4: bail out if a newer send() superseded this stream
            if (generationRef.current !== myGen) return;

            lineBuffer += decoder.decode(value, { stream: true });
            const lines = lineBuffer.split("\n");
            // Last element may be an incomplete line — keep it in buffer
            lineBuffer = lines.pop() ?? "";

            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed) continue; // skip blank separators
              let event: StreamEvent;
              try {
                event = JSON.parse(trimmed) as StreamEvent;
              } catch {
                // Malformed line — skip gracefully
                continue;
              }
              // F4: check generation before each event so a late-firing reader cannot
              // write into the store after stream #2 has already started.
              if (generationRef.current !== myGen) return;
              handleEvent(event);
            }
          }

          // Flush any remaining partial line (shouldn't happen with well-formed NDJSON)
          if (generationRef.current !== myGen) return;
          const remaining = lineBuffer.trim();
          if (remaining) {
            try {
              const event = JSON.parse(remaining) as StreamEvent;
              if (generationRef.current !== myGen) return;
              handleEvent(event);
            } catch {
              // discard
            }
          }
        } catch (err: unknown) {
          // F4: if superseded, the catch is a no-op — do NOT clear stream state that
          // belongs to the newer send() (avoids clearStream() clobbering stream #2).
          if (generationRef.current !== myGen) return;
          // FE-PERF-3: discard any not-yet-flushed buffer — clearStream() below wipes
          // the store's streamingContent/streamingThink; a late rAF flush must not
          // re-populate them afterwards.
          discardPending();
          if (err instanceof Error && err.name === "AbortError") {
            // User-initiated abort — clear stream, no toast
            clearStream();
            return;
          }
          const message = err instanceof Error ? err.message : "Stream error";
          setStreamError(message);
          clearStream();
          showToast(message, "error");
        }
      })();

      function handleEvent(event: StreamEvent): void {
        // F4: guard against superseded-stream callbacks reaching the store
        if (generationRef.current !== myGen) return;
        switch (event.type) {
          case "token":
            // FE-PERF-3: buffer locally; flushed via appendStreamDelta at most once
            // per animation frame (I3: still a cheap string append, no parse).
            pendingContentRef.current += event.delta;
            scheduleFlush();
            break;

          case "think":
            // FE-PERF-3: same rAF-batched buffer as "token" (I3: no parse).
            pendingThinkRef.current += event.delta;
            scheduleFlush();
            break;

          case "done": {
            // FE-PERF-3: flush any buffered-but-not-yet-applied deltas synchronously
            // BEFORE reading streamingContent/streamingThink below — otherwise the
            // final message could be missing the tail tokens that arrived just
            // before "done" but hadn't been flushed to the store by rAF yet.
            flushPendingSync();

            // Stream end: finalize the turn. MarkdownView will parse ONCE (AC-G3-2).
            const usage = {
              inputTokens: event.input_tokens,
              outputTokens: event.output_tokens,
              totalCostUsd: event.total_cost_usd,
            };

            // I7: structured per-run cost telemetry. Dev-only (import.meta.env.DEV is
            // statically false in prod builds → dead-code eliminated, same pattern as
            // api/base.ts) so it never floods a user's console; console.info keeps it
            // below warn level so genuine stream warnings stay visible in DevTools.
            if (import.meta.env.DEV) {
              // eslint-disable-next-line no-console -- dev-only structured cost telemetry
              console.info("[chat] turn done", {
                conversation_id: event.conversation_id,
                message_id: event.message_id,
                input_tokens: event.input_tokens,
                output_tokens: event.output_tokens,
                total_cost_usd: event.total_cost_usd,
                iterations_used: event.iterations_used,
                finish_reason: event.finish_reason,
                citations_count: event.citations?.length ?? 0,
              });
            }

            // Build the settled message from the accumulated streaming buffer.
            // We read directly from the store snapshot here (not a selector) because
            // this is inside an async callback, not a render.
            const state = useChatStore.getState();
            const raw = state.streamingContent;
            const think = state.streamingThink;
            // Compose the raw content with think block if present (AC-F7-2: stored un-mutated)
            const fullContent = think ? `<think>${think}</think>${raw}` : raw;

            const msg: ChatMessage = {
              id: event.message_id,
              conversation_id: event.conversation_id,
              role: "assistant",
              content: fullContent,
              input_tokens: event.input_tokens,
              output_tokens: event.output_tokens,
              total_cost_usd: event.total_cost_usd,
              created_at: new Date().toISOString(),
              // ADR-0022 §2.4: additive citations field from done event.
              // Empty array when backend omits the field (non-breaking for old backends).
              citations: event.citations ?? [],
              // B2: web citations from SearXNG search (only set when present — exactOptionalPropertyTypes).
              ...(event.web_citations !== undefined ? { web_citations: event.web_citations } : {}),
            };

            finalizeTurn(msg, usage);
            break;
          }

          case "error": {
            // I7: log cost incurred before failure
            console.warn("[chat] stream error", {
              code: event.code,
              message: event.message,
              total_cost_usd: event.total_cost_usd,
            });
            // FE-PERF-3: discard any not-yet-flushed buffer — clearStream() below wipes
            // streamingContent/streamingThink; a late rAF flush must not undo that.
            discardPending();
            setStreamError(event.message);
            clearStream();
            showToast(event.message, "error");
            break;
          }
        }
      }
    },
    [
      discardPending,
      flushPendingSync,
      scheduleFlush,
      setIsStreaming,
      setStreamError,
      finalizeTurn,
      clearStream,
    ],
  );

  return { send, abort };
}
