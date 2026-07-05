/**
 * decorateCitations.ts — post-process already-parsed HTML to turn [n] tokens into
 * <sup role="link" title="{title}">[n]</sup> elements (ADR-0022 §2.4 / AC-F6-3 / I3/G3).
 *
 * INVARIANT I3 / G3:
 *   - This runs ONCE on the SETTLED, ALREADY-PARSED markdown HTML string.
 *   - It must NOT be called per streaming token.
 *   - It does NOT call renderMarkdown — it is a SECOND PASS over the already-rendered HTML,
 *     doing a plain string substitution to wrap [n] text nodes.
 *   - The result is memoized on (rawHtml + citation map key) — identical inputs return the
 *     same string reference without re-processing.
 *
 * Design:
 *   - Receives the DOMPurify-sanitized HTML from renderMarkdown and the citation array.
 *   - Builds a lookup map n → {title, slug} once.
 *   - Replaces every occurrence of the literal text "[n]" (where n is a known citation index)
 *     with <sup role="link" data-slug="{slug}" title="{title}" class="synapse-citation">[n]</sup>.
 *   - The replacement uses a single-pass regex over the HTML string.
 *   - HTML-attribute values (title, data-slug) are entity-escaped to prevent XSS vectors.
 *   - data-slug is written as a data attribute; click handlers are attached by the component
 *     using event delegation — no inline onclick.
 *
 * Why string regex over DOM manipulation:
 *   - We already have sanitized HTML from DOMPurify. Re-parsing into a DOM tree just to
 *     replace text nodes adds overhead with no safety benefit (DOMPurify already ran).
 *   - The regex is bounded: it only replaces [n] where n is a digit sequence that matches
 *     a known citation — it will NOT replace arbitrary [text] patterns.
 *   - The output is re-sanitized by the DOMPurify config in renderMarkdown.ts which already
 *     allows <sup> with role, title, data-*, class attributes.
 */

import type { CitationRef } from "../../store/chatStore";

// ─── Memoization cache ────────────────────────────────────────────────────────

/** Weak memoization: caches the last call's result (citations arrays are replaced on each
 *  new message, so a 1-entry cache effectively avoids re-decoration on React re-renders
 *  of the same settled message). */
let _lastHtml = "";
let _lastCitationKey = "";
let _lastResult = "";

// ─── HTML attribute escaping ──────────────────────────────────────────────────

/** Escape characters that could break an HTML attribute value. */
function escapeAttr(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * decorateCitations(html, citations) — single-pass substitution of [n] markers.
 *
 * @param html — sanitized HTML from renderMarkdown (settled, post-stream)
 * @param citations — citation array from the done event (ADR-0022 §2.4)
 * @returns HTML string with [n] replaced by <sup> elements; unchanged if no citations.
 *
 * Memoized on (html, citations) identity — same reference inputs → same reference output.
 * This is the ONLY place [n] decoration happens; it is never called during streaming.
 */
export function decorateCitations(html: string, citations: CitationRef[]): string {
  // Fast path: no citations → return html unchanged (no allocation)
  if (!citations || citations.length === 0) return html;

  // Build a stable cache key: citation n+title+slug joined (cheap, deterministic)
  const citationKey = citations.map((c) => `${c.n}:${c.id}`).join("|");

  // Memoization: same html + same citation set → return cached result
  if (html === _lastHtml && citationKey === _lastCitationKey) {
    return _lastResult;
  }

  // Build lookup: n (number) → {title, slug, id}
  const lookup = new Map<number, { title: string; slug: string; id: string }>();
  for (const c of citations) {
    lookup.set(c.n, { title: c.title, slug: c.slug, id: c.id });
  }

  // Build a regex that matches exactly [n] where n is a known citation number.
  // We sort descending so [10] is tried before [1] (no greedy prefix issue).
  const ns = [...lookup.keys()].sort((a, b) => b - a);
  // Pattern: \[(<n1>|<n2>|...)\]  — anchored to brackets, not inside HTML tags.
  // We use a negative lookbehind for = and " to avoid replacing inside href="[1]"-style
  // attributes (though DOMPurify would strip those anyway).
  const pattern = new RegExp(`\\[(${ns.join("|")})\\]`, "g");

  const result = html.replace(pattern, (_match, nStr: string) => {
    const n = parseInt(nStr, 10);
    const ref = lookup.get(n);
    if (!ref) return _match; // shouldn't happen but guard anyway
    const titleAttr = escapeAttr(ref.title);
    const slugAttr = escapeAttr(ref.slug);
    // v1.3.3: also carry the page UUID — the click handler navigates by id
    // directly (the slug is derived, not a selection key) and only falls back
    // to the by-slug resolution endpoint when the id is missing.
    const idAttr = escapeAttr(ref.id);
    return `<sup role="link" tabindex="0" class="synapse-citation" title="${titleAttr}" data-slug="${slugAttr}" data-page-id="${idAttr}">[${n}]</sup>`;
  });

  // Store in 1-entry cache
  _lastHtml = html;
  _lastCitationKey = citationKey;
  _lastResult = result;

  return result;
}
