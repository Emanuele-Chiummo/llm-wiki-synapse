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
 *
 * Citation decoration (ADR-0022 §2.4 / AC-F6-3):
 *   - After renderMarkdown runs (parse step 1), decorateCitations runs (decoration step 2).
 *   - decorateCitations wraps [n] text tokens in <sup role="link" title="{title}">[n]</sup>.
 *   - Both steps are memoized on their respective inputs; decoration is NOT a second parse.
 *   - This satisfies I3/G3: exactly 1 renderMarkdown call + 1 string-pass decoration
 *     after the stream ends, never per token.
 */

import { useMemo, useCallback, memo, type ReactNode, type MouseEvent } from "react";
import { renderMarkdown } from "./renderMarkdown";
import { decorateCitations } from "./decorateCitations";
import { ThinkBlock } from "./ThinkBlock";
import type { CitationRef } from "../../store/chatStore";

interface MarkdownViewProps {
  /** Raw settled content — immutable after done (includes literal <think>…</think> if present). */
  content: string;
  /**
   * Citations from the done event (ADR-0022 §2.4). Optional; empty or absent = no decoration.
   * Passed through to decorateCitations — NEVER triggers a re-parse of the markdown.
   */
  citations?: CitationRef[];
  /**
   * Called when the user clicks a [n] citation superscript.
   * Receives the slug of the referenced page.
   * Optional — if not provided, citation clicks are no-ops (stub for when page navigation
   * is not yet wired in the calling context).
   */
  onCitationClick?: (slug: string, pageId?: string) => void;
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
  citations,
  onCitationClick,
}: MarkdownViewProps): ReactNode {
  // Parse exactly once per unique content string (immutable post-done — AC-G3-2)
  const { thinkContent, visibleContent } = useMemo(() => splitThink(content), [content]);

  // Step 1: renderMarkdown — called ONCE on settled content (I3 / G3).
  const rawHtml = useMemo(() => renderMarkdown(visibleContent), [visibleContent]);

  // Step 2: decorateCitations — single-pass string substitution over the already-parsed HTML.
  // This is NOT a second markdown parse; it only wraps [n] text tokens in <sup> tags.
  // Memoized on (rawHtml, citations) — re-runs only when the message or citations change.
  // During streaming, citations is undefined and rawHtml is never set, so this never runs.
  const html = useMemo(() => decorateCitations(rawHtml, citations ?? []), [rawHtml, citations]);

  // Event delegation: catch clicks on .synapse-citation elements within the rendered HTML.
  // Uses data-slug attribute written by decorateCitations. No inline onclick in the HTML.
  const handleBodyClick = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      if (!onCitationClick) return;
      const target = e.target as HTMLElement;
      const citEl = target.closest(".synapse-citation");
      if (citEl) {
        const slug = citEl.getAttribute("data-slug");
        // v1.3.3: prefer the page UUID when present (id navigates directly;
        // the derived slug needs a by-slug resolution roundtrip).
        const pageId = citEl.getAttribute("data-page-id");
        if (slug || pageId) {
          e.preventDefault();
          onCitationClick(slug ?? "", pageId ?? undefined);
        }
      }
    },
    [onCitationClick],
  );

  return (
    <div className="synapse-markdown">
      {thinkContent && <ThinkBlock content={thinkContent} streaming={false} />}
      <div
        className="synapse-markdown__body"
        // Safe: renderMarkdown runs through DOMPurify (renderMarkdown.ts).
        // decorateCitations only substitutes within text nodes using a bounded pattern.
        dangerouslySetInnerHTML={{ __html: html }}
        onClick={handleBodyClick}
        style={{
          color: "var(--syn-text)",
          lineHeight: 1.6,
          wordBreak: "break-word",
        }}
      />
    </div>
  );
});
