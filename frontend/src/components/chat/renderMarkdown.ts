/**
 * renderMarkdown.ts — parse a settled message ONCE at stream end (ADR-0019 §2.6 / I3 / G3).
 *
 * Pipeline: latexToUnicode(raw) → marked (GFM) → DOMPurify
 *
 * INVARIANT I3 / AC-G3-2: this function MUST NOT be called per token.
 *   - It is called ONLY from <MarkdownView>, which is rendered ONLY for settled messages.
 *   - The result is memoized on the immutable `content` string (React.memo + useMemo).
 *
 * In dev mode (__DEV__), a console.assert fires if called more than once with the same
 * input within a single React render pass (G3 assertion — ADR-0019 §4).
 */

import { marked } from "marked";
import DOMPurify from "dompurify";
import { latexToUnicode } from "./latexToUnicode";

// Configure marked once (GFM mode, no async)
marked.setOptions({
  gfm: true,
  breaks: true,
});

// ─── G3 dev assertion ─────────────────────────────────────────────────────────

declare const __DEV__: boolean;

// In dev, track call count per content string within the current event loop tick.
// A count > 1 for the same content means the caller is parsing per-token.
const _devCallMap = new Map<string, number>();
let _devFlushScheduled = false;

function devTrack(raw: string): void {
  if (typeof __DEV__ === "undefined" || !__DEV__) return;
  const prev = _devCallMap.get(raw) ?? 0;
  _devCallMap.set(raw, prev + 1);
  console.assert(
    prev === 0,
    "[G3] renderMarkdown called more than once for the same content in a single tick — " +
      "this indicates per-token parsing, which violates I3. " +
      "Only call renderMarkdown from <MarkdownView> on settled messages.",
  );
  if (!_devFlushScheduled) {
    _devFlushScheduled = true;
    queueMicrotask(() => {
      _devCallMap.clear();
      _devFlushScheduled = false;
    });
  }
}

// ─── Main function ─────────────────────────────────────────────────────────────

/**
 * renderMarkdown(raw) — convert a raw assistant message string to safe HTML.
 *
 * Steps:
 *   1. latexToUnicode — lookup-table substitution (F8, no heavy dep)
 *   2. marked — GFM → HTML
 *   3. DOMPurify — strip any XSS vectors
 *
 * Returns a sanitized HTML string safe for dangerouslySetInnerHTML.
 * Parse count = 1 per unique content string (enforced by memoization in MarkdownView).
 */
export function renderMarkdown(raw: string): string {
  devTrack(raw);

  const withUnicode = latexToUnicode(raw);
  // marked.parse in sync mode returns a string
  const html = marked.parse(withUnicode) as string;
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      "p", "br", "strong", "em", "s", "del", "u",
      "h1", "h2", "h3", "h4", "h5", "h6",
      "ul", "ol", "li",
      "blockquote",
      "pre", "code",
      "table", "thead", "tbody", "tr", "th", "td",
      "a",
      "hr",
      "span",
      "sup", "sub",
    ],
    ALLOWED_ATTR: ["href", "target", "rel", "class", "id"],
    // Force external links to open safely
    ADD_ATTR: ["rel"],
    FORCE_BODY: false,
  });
}
