/**
 * renderMarkdown.ts — parse a settled message ONCE at stream end (ADR-0019 §2.6 / I3 / G3).
 *
 * Pipeline: latexToUnicode(raw) → wikilinkTransform → marked (GFM) → DOMPurify
 *
 * INVARIANT I3 / AC-G3-2: this function MUST NOT be called per token.
 *   - It is called ONLY from <MarkdownView>, which is rendered ONLY for settled messages.
 *   - The result is memoized on the immutable `content` string (React.memo + useMemo).
 *
 * In dev mode (__DEV__), a console.assert fires if called more than once with the same
 * input within a single React render pass (G3 assertion — ADR-0019 §4).
 *
 * Wikilink transform (Task A):
 *   [[Target|Label]] → <a class="wikilink" data-wikilink="Target">Label</a>
 *   [[Target]]       → <a class="wikilink" data-wikilink="Target">Target</a>
 *   Both Target and Label are HTML-escaped. DOMPurify is configured to keep data-wikilink.
 */

import { marked } from "marked";
import DOMPurify from "dompurify";
import { latexToUnicode } from "./latexToUnicode";

// Configure marked once (GFM mode, no async)
marked.setOptions({
  gfm: true,
  breaks: true,
});

// ─── Wikilink transform (Task A) ─────────────────────────────────────────────
//
// Runs BEFORE marked so marked treats the emitted <a> tags as inline HTML.
// Regex: /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g
//   Group 1 = target page title (required)
//   Group 2 = display label (optional)
// Limitations (v1, acceptable):
//   - Does not skip code spans/fences — a simple global regex is used per spec.
//   - Unmatched [[ (no closing ]]) is left as-is by the regex (no partial match).

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const WIKILINK_RE = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;

export function wikilinkTransform(raw: string): string {
  return raw.replace(WIKILINK_RE, (_match, target: string, label?: string) => {
    const safeTarget = escapeHtml(target.trim());
    const safeLabel = escapeHtml((label ?? target).trim());
    return `<a class="wikilink" data-wikilink="${safeTarget}">${safeLabel}</a>`;
  });
}

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
/**
 * stripLeadingFrontmatter — remove one or more CONSECUTIVE leading YAML frontmatter
 * blocks (`---\n…\n---`) anchored at the very start, so raw YAML never renders as body
 * text in the wiki reader.
 *
 * Why "one or more": legacy pages generated before the ingest write-path dedup fix carry
 * a DUPLICATED frontmatter block at the top of the body; looping strips both. A `---`
 * thematic break that appears LATER in the body is left untouched (only blocks anchored
 * at position 0, with a matching closing `---`/`...` fence, are removed). Non-matching
 * input is returned unchanged.
 *
 * Reader-only: intentionally NOT wired into renderMarkdown (chat content has no
 * frontmatter). NoteView applies it before renderMarkdown.
 */
export function stripLeadingFrontmatter(md: string): string {
  const fence = /^\s*---[ \t]*\r?\n[\s\S]*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/;
  let s = md;
  while (fence.test(s)) {
    const next = s.replace(fence, "");
    if (next === s) break; // safety: never loop forever
    s = next;
  }
  return s;
}

export function renderMarkdown(raw: string): string {
  devTrack(raw);

  const withUnicode = latexToUnicode(raw);
  // Transform [[wikilinks]] → inline <a class="wikilink"> BEFORE marked parses
  const withWikilinks = wikilinkTransform(withUnicode);
  // marked.parse in sync mode returns a string
  const html = marked.parse(withWikilinks) as string;
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
    // "data-slug"      — decorateCitations citation superscripts
    // "data-wikilink"  — wikilink anchor target for click-to-navigate (Task A)
    ALLOWED_ATTR: [
      "href", "target", "rel", "class", "id", "title", "role",
      "tabindex", "data-slug", "data-wikilink",
    ],
    // Force external links to open safely
    ADD_ATTR: ["rel"],
    FORCE_BODY: false,
  });
}
