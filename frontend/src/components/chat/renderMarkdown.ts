/**
 * renderMarkdown.ts — parse a settled message ONCE at stream end (ADR-0019 §2.6 / I3 / G3).
 *
 * Pipeline: extractDisplayMath → latexToUnicode(raw) → wikilinkTransform → marked (GFM)
 *           → DOMPurify → injectDisplayMath (KaTeX)
 *
 * INVARIANT I3 / AC-G3-2: this function MUST NOT be called per token.
 *   - It is called ONLY from <MarkdownView>, which is rendered ONLY for settled messages.
 *   - The result is memoized on the immutable `content` string (React.memo + useMemo).
 *
 * In dev mode (__DEV__), a console.assert fires if called more than once with the same
 * input within a single React render pass (G3 assertion — ADR-0019 §4).
 *
 * Display math (KaTeX — ADR-0019 amendment, G-P1-2 / llm_wiki parity):
 *   $$…$$ and \[…\] blocks are EXTRACTED to placeholders BEFORE latexToUnicode runs (so the
 *   raw LaTeX is preserved verbatim), then re-inserted as KaTeX HTML AFTER DOMPurify. KaTeX
 *   is invoked with throwOnError:false + trust:false, so its output is XSS-safe by construction
 *   (no \href/\htmlClass escalation); on failure we fall back to a fenced code block so display
 *   math is NEVER silently dropped (preserves the AC-F8-3 guarantee). Inline math ($…$, \(…\))
 *   remains Unicode-only via latexToUnicode — KaTeX handles display math only.
 *
 * Wikilink transform (Task A):
 *   [[Target|Label]] → <a class="wikilink" data-wikilink="Target">Label</a>
 *   [[Target]]       → <a class="wikilink" data-wikilink="Target">Target</a>
 *   Both Target and Label are HTML-escaped. DOMPurify is configured to keep data-wikilink.
 */

import { marked } from "marked";
import DOMPurify from "dompurify";
import katex from "katex";
import "katex/dist/katex.min.css";
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

// ─── Display-math extraction / KaTeX injection (G-P1-2) ──────────────────────
//
// $$…$$ and \[…\] are pulled OUT before any Unicode/markdown processing so KaTeX
// receives the raw LaTeX untouched, then re-inserted as rendered HTML after DOMPurify.
// The placeholder token is deliberately free of characters that latexToUnicode,
// wikilinkTransform, or marked would rewrite (no `\`, `$`, `^`, `_`, `[`, `]`, `-`).

const MATH_PLACEHOLDER_PREFIX = "@@SYNAPSEMATH";
const MATH_PLACEHOLDER_SUFFIX = "@@";

/**
 * extractDisplayMath — replace $$…$$ / \[…\] blocks with placeholders.
 * Returns the rewritten text plus the ordered raw-LaTeX blocks.
 */
export function extractDisplayMath(raw: string): { text: string; blocks: string[] } {
  const blocks: string[] = [];
  const push = (latex: string): string => {
    const idx = blocks.length;
    blocks.push(latex.trim());
    return `${MATH_PLACEHOLDER_PREFIX}${idx}${MATH_PLACEHOLDER_SUFFIX}`;
  };
  const text = raw
    .replace(/\$\$([\s\S]*?)\$\$/g, (_m, inner: string) => push(inner))
    .replace(/\\\[([\s\S]*?)\\\]/g, (_m, inner: string) => push(inner));
  return { text, blocks };
}

/** Render one display-math LaTeX string to safe KaTeX HTML; never throws, never drops. */
function renderDisplayMath(latex: string): string {
  try {
    return katex.renderToString(latex, {
      displayMode: true,
      throwOnError: false, // renders an error node instead of throwing
      strict: false,
      trust: false, // disallow \href/\htmlClass etc. → XSS-safe output
      output: "htmlAndMathml",
    });
  } catch {
    // Defensive fallback (should not happen with throwOnError:false):
    // preserve the content as a fenced code block rather than dropping it.
    return `<pre><code class="language-math">${escapeHtml(latex)}</code></pre>`;
  }
}

/** Re-insert KaTeX HTML into placeholders left by extractDisplayMath. */
export function injectDisplayMath(html: string, blocks: string[]): string {
  if (blocks.length === 0) return html;
  const re = new RegExp(`${MATH_PLACEHOLDER_PREFIX}(\\d+)${MATH_PLACEHOLDER_SUFFIX}`, "g");
  return html.replace(re, (match, n: string) => {
    const latex = blocks[Number(n)];
    return latex === undefined ? match : renderDisplayMath(latex);
  });
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

  // Pull display math OUT first so latexToUnicode/marked never mangle the raw LaTeX.
  const { text, blocks } = extractDisplayMath(raw);
  const withUnicode = latexToUnicode(text);
  // Transform [[wikilinks]] → inline <a class="wikilink"> BEFORE marked parses
  const withWikilinks = wikilinkTransform(withUnicode);
  // marked.parse in sync mode returns a string
  const html = marked.parse(withWikilinks) as string;
  const sanitized = DOMPurify.sanitize(html, {
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
  // Re-insert KaTeX HTML AFTER sanitization. KaTeX output (trust:false) is XSS-safe
  // by construction; injecting it here avoids DOMPurify stripping KaTeX's span/MathML markup.
  return injectDisplayMath(sanitized, blocks);
}
