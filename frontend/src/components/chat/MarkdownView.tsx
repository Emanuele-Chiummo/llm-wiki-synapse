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
import { decorateCitations, decorateWebCitations } from "./decorateCitations";
import { ThinkBlock } from "./ThinkBlock";
import type { CitationRef } from "../../store/chatStore";
import type { WebCitationRef } from "../../api/chatClient";

interface MarkdownViewProps {
  /** Raw settled content — immutable after done (includes literal <think>…</think> if present). */
  content: string;
  /**
   * Citations from the done event (ADR-0022 §2.4). Optional; empty or absent = no decoration.
   * Passed through to decorateCitations — NEVER triggers a re-parse of the markdown.
   */
  citations?: CitationRef[];
  /**
   * Web citations from a SearXNG search (B2). Optional; empty or absent = no decoration.
   * Passed through to decorateWebCitations — never triggers a re-parse.
   */
  webCitations?: WebCitationRef[];
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
  webCitations,
  onCitationClick,
}: MarkdownViewProps): ReactNode {
  // Parse exactly once per unique content string (immutable post-done — AC-G3-2)
  const { thinkContent, visibleContent } = useMemo(() => splitThink(content), [content]);

  // Step 1: renderMarkdown — called ONCE on settled content (I3 / G3).
  const rawHtml = useMemo(() => renderMarkdown(visibleContent), [visibleContent]);

  // Step 2a: decorateCitations — single-pass [n] → <sup class="synapse-citation">.
  // Memoized on (rawHtml, citations) — never runs during streaming.
  const wikiDecoratedHtml = useMemo(
    () => decorateCitations(rawHtml, citations ?? []),
    [rawHtml, citations],
  );

  // Step 2b: decorateWebCitations — single-pass [Wn] → <sup class="synapse-web-citation">.
  // Runs after step 2a; memoized on (wikiDecoratedHtml, webCitations).
  const html = useMemo(
    () => decorateWebCitations(wikiDecoratedHtml, webCitations ?? []),
    [wikiDecoratedHtml, webCitations],
  );

  // Event delegation: catch clicks on citation elements within the rendered HTML.
  const handleBodyClick = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement;

      // Web citation: open URL in new tab (B2).
      const webCitEl = target.closest(".synapse-web-citation");
      if (webCitEl) {
        const url = webCitEl.getAttribute("data-url");
        if (url) {
          e.preventDefault();
          window.open(url, "_blank", "noopener,noreferrer");
        }
        return;
      }

      // Wiki citation: navigate to page (ADR-0022 §2.4 / AC-R8-6-2).
      if (!onCitationClick) return;
      const citEl = target.closest(".synapse-citation");
      if (citEl) {
        const slug = citEl.getAttribute("data-slug");
        // v1.3.3: prefer the page UUID when present.
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
