/**
 * useChatStream.ts — NDJSON stream consumer for POST /chat/stream (ADR-0019 §2.1/§3 / F6).
 *
 * Transport: fetch() + response.body.getReader() + TextDecoder, split on \n.
 * Partial-line buffering: accumulate across chunks; only JSON.parse on complete lines.
 *
 * Per-token work (I3):
 *   - token event  → appendToken(delta)   [cheap string append, no parse]
 *   - think event  → appendThink(delta)   [cheap string append, no parse]
 *   - done  event  → finalizeTurn()       [triggers parse ONCE in MarkdownView]
 *   - error event  → showToast + clearStream
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

  const appendToken = useChatStore((s) => s.appendToken);
  const appendThink = useChatStore((s) => s.appendThink);
  const setIsStreaming = useChatStore((s) => s.setIsStreaming);
  const setStreamError = useChatStore((s) => s.setStreamError);
  const finalizeTurn = useChatStore((s) => s.finalizeTurn);
  const clearStream = useChatStore((s) => s.clearStream);

  const abort = useCallback(() => {
    // F2/F3: delegate to store.abortStream() — it calls the registered fn (which aborts
    // the AbortController) and clears all streaming state in one atomic set().
    useChatStore.getState().abortStream();
    abortRef.current = null;
  }, []);

  // F3: abort in-flight stream on component unmount so a detached reader cannot keep
  // writing into the global store after the chat section is navigated away from.
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        useChatStore.getState().abortStream();
        abortRef.current = null;
      }
    };
  }, []);

  const send = useCallback(
    (req: ChatStreamRequest) => {
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
            // I3: cheap string append only — no parse
            appendToken(event.delta);
            break;

          case "think":
            // I3: cheap string append only — no parse
            appendThink(event.delta);
            break;

          case "done": {
            // Stream end: finalize the turn. MarkdownView will parse ONCE (AC-G3-2).
            const usage = {
              inputTokens: event.input_tokens,
              outputTokens: event.output_tokens,
              totalCostUsd: event.total_cost_usd,
            };

            // I7: log cost per run (structured)
            console.warn("[chat] turn done", {
              conversation_id: event.conversation_id,
              message_id: event.message_id,
              input_tokens: event.input_tokens,
              output_tokens: event.output_tokens,
              total_cost_usd: event.total_cost_usd,
              iterations_used: event.iterations_used,
              finish_reason: event.finish_reason,
              citations_count: event.citations?.length ?? 0,
            });

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
            setStreamError(event.message);
            clearStream();
            showToast(event.message, "error");
            break;
          }
        }
      }
    },
    [appendToken, appendThink, setIsStreaming, setStreamError, finalizeTurn, clearStream],
  );

  return { send, abort };
}
