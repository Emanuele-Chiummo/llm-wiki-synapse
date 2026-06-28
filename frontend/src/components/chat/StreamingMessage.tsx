/**
 * StreamingMessage.tsx — renders the in-flight assistant turn (ADR-0019 §3 / I3 / G3).
 *
 * INVARIANT I3 / AC-G3-4:
 *   - This is the ONLY component that subscribes to selectStreamingContent / selectStreamingThink.
 *   - Renders raw text in white-space:pre-wrap with a blinking cursor.
 *   - NO markdown parse, NO LaTeX conversion, NO syntax highlighting per token.
 *   - The settled messages array and MessageList virtualizer do NOT subscribe here.
 *
 * When streamingContent is empty and streamingThink is non-empty, the think block
 * is still "open" (</think> not yet seen).  Once the first token arrives, the think
 * block closes and content starts flowing.
 */

import { type ReactNode } from "react";
import { useChatStore, selectStreamingContent, selectStreamingThink } from "../../store/chatStore";
import { ThinkBlock } from "./ThinkBlock";

export function StreamingMessage(): ReactNode {
  // Only this component reads the two streaming buffers (AC-G3-4)
  const content = useChatStore(selectStreamingContent);
  const think = useChatStore(selectStreamingThink);

  const hasThink = think.length > 0;
  // The think stream is still live if we have think content but no visible content yet
  const thinkStreaming = hasThink && content.length === 0;

  return (
    <div
      className="synapse-streaming-message"
      style={{ color: "#e6edf3", lineHeight: 1.6 }}
      aria-live="polite"
      aria-atomic="false"
    >
      {hasThink && (
        <ThinkBlock content={think} streaming={thinkStreaming} />
      )}

      {/* Raw buffer — NO parse (I3 / G3). white-space:pre-wrap preserves newlines. */}
      <div
        style={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {content}
        {/* Blinking cursor */}
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: 7,
            height: 14,
            background: "#58a6ff",
            marginLeft: 2,
            verticalAlign: "text-bottom",
            animation: "synapse-blink 1s step-end infinite",
          }}
        />
      </div>
    </div>
  );
}
