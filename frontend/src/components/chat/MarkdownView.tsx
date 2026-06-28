/**
 * MarkdownView.tsx — renders a SETTLED assistant message ONCE (ADR-0019 §2.6 / I3 / G3).
 *
 * INVARIANT I3 / AC-G3-2:
 *   - This component is ONLY rendered for settled (post-done) messages.
 *   - It is NOT used during streaming (StreamingMessage handles that).
 *   - renderMarkdown() is called once, memoized on the immutable content string.
 *   - Re-renders (e.g., parent re-renders from unrelated state) do NOT re-parse
 *     because useMemo has content as its only dep and it never changes post-done.
 *
 * Content may contain a raw <think>…</think> prefix (AC-F7-2: stored un-mutated).
 * We split it off here and render it via ThinkBlock; the remainder is the visible text.
 */

import { useMemo, memo, type ReactNode } from "react";
import { renderMarkdown } from "./renderMarkdown";
import { ThinkBlock } from "./ThinkBlock";

interface MarkdownViewProps {
  /** Raw settled content — immutable after done (includes literal <think>…</think> if present). */
  content: string;
}

/**
 * Extract a leading <think>…</think> block from raw content.
 * Returns { thinkContent, visibleContent }.
 * This is a simple string scan (not a regex loop), called once per render (settled, memoized).
 */
function splitThink(raw: string): { thinkContent: string; visibleContent: string } {
  const OPEN = "<think>";
  const CLOSE = "</think>";
  if (!raw.startsWith(OPEN)) {
    return { thinkContent: "", visibleContent: raw };
  }
  const closeIdx = raw.indexOf(CLOSE);
  if (closeIdx === -1) {
    // Malformed — treat entire content as think
    return { thinkContent: raw.slice(OPEN.length), visibleContent: "" };
  }
  return {
    thinkContent: raw.slice(OPEN.length, closeIdx),
    visibleContent: raw.slice(closeIdx + CLOSE.length).trimStart(),
  };
}

export const MarkdownView = memo(function MarkdownView({
  content,
}: MarkdownViewProps): ReactNode {
  // Parse exactly once per unique content string (immutable post-done — AC-G3-2)
  const { thinkContent, visibleContent } = useMemo(() => splitThink(content), [content]);
  const html = useMemo(() => renderMarkdown(visibleContent), [visibleContent]);

  return (
    <div className="synapse-markdown">
      {thinkContent && <ThinkBlock content={thinkContent} streaming={false} />}
      <div
        className="synapse-markdown__body"
        // Safe: renderMarkdown runs through DOMPurify (renderMarkdown.ts)
        dangerouslySetInnerHTML={{ __html: html }}
        style={{
          color: "#e6edf3",
          lineHeight: 1.6,
          wordBreak: "break-word",
        }}
      />
    </div>
  );
});
