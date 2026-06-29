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

import { useRef, useCallback } from "react";
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

  const appendToken = useChatStore((s) => s.appendToken);
  const appendThink = useChatStore((s) => s.appendThink);
  const setIsStreaming = useChatStore((s) => s.setIsStreaming);
  const setStreamError = useChatStore((s) => s.setStreamError);
  const finalizeTurn = useChatStore((s) => s.finalizeTurn);
  const clearStream = useChatStore((s) => s.clearStream);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    clearStream();
  }, [clearStream]);

  const send = useCallback(
    (req: ChatStreamRequest) => {
      // Abort any in-flight stream
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

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
              handleEvent(event);
            }
          }

          // Flush any remaining partial line (shouldn't happen with well-formed NDJSON)
          const remaining = lineBuffer.trim();
          if (remaining) {
            try {
              const event = JSON.parse(remaining) as StreamEvent;
              handleEvent(event);
            } catch {
              // discard
            }
          }
        } catch (err: unknown) {
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
