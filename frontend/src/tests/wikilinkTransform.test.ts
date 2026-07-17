/**
 * wikilinkTransform.test.ts — unit tests for the wikilink pre-processor (Task A).
 *
 * Coverage:
 *   1. [[Target]] → anchor with visible text = Target, data-wikilink = Target
 *   2. [[Target|Label]] → anchor with visible text = Label, data-wikilink = Target
 *   3. Unmatched [[ (no closing ]]) → left unchanged
 *   4. HTML-escaping: special chars in target/label are escaped
 *   5. Multiple wikilinks in one string all transformed
 *   6. Plain text with no wikilinks is returned unchanged
 *   7. renderMarkdown integration: wikilink HTML survives DOMPurify sanitization
 *      (data-wikilink is in ALLOWED_ATTR)
 */

import { describe, it, expect } from "vitest";
import { wikilinkTransform } from "../components/chat/renderMarkdown";

// ─── wikilinkTransform unit tests ─────────────────────────────────────────────

describe("wikilinkTransform", () => {
  it("converts [[Target]] to an anchor with visible text = Target", () => {
    const result = wikilinkTransform("See [[Temperature Scaling]] for details.");
    expect(result).toContain('class="wikilink"');
    expect(result).toContain('data-wikilink="Temperature Scaling"');
    expect(result).toContain(">Temperature Scaling</a>");
  });

  it("converts [[Target|Label]] to an anchor with visible text = Label", () => {
    const result = wikilinkTransform("See [[Temperature Scaling|temp scaling]] here.");
    expect(result).toContain('class="wikilink"');
    expect(result).toContain('data-wikilink="Temperature Scaling"');
    expect(result).toContain(">temp scaling</a>");
    // The pipe and label part must NOT appear in data-wikilink
    expect(result).not.toContain('data-wikilink="Temperature Scaling|temp scaling"');
  });

  it("leaves an unmatched [[ (no closing ]]) unchanged", () => {
    const raw = "This has [[ no closing bracket";
    expect(wikilinkTransform(raw)).toBe(raw);
  });

  it("leaves a single [ bracket unchanged (not a wikilink)", () => {
    const raw = "This [is not] a wikilink";
    expect(wikilinkTransform(raw)).toBe(raw);
  });

  it("HTML-escapes special characters in target", () => {
    const result = wikilinkTransform("[[A & B]]");
    expect(result).toContain('data-wikilink="A &amp; B"');
  });

  it("HTML-escapes double quotes in target", () => {
    const result = wikilinkTransform('[["quoted"]]');
    expect(result).toContain('data-wikilink="&quot;quoted&quot;"');
  });

  it("HTML-escapes special characters in label", () => {
    const result = wikilinkTransform("[[Target|A & B label]]");
    expect(result).toContain(">A &amp; B label</a>");
  });

  it("transforms multiple wikilinks in one string", () => {
    const result = wikilinkTransform("[[Alpha]] and [[Beta|B]] are linked.");
    expect(result).toContain('data-wikilink="Alpha"');
    expect(result).toContain(">Alpha</a>");
    expect(result).toContain('data-wikilink="Beta"');
    expect(result).toContain(">B</a>");
  });

  it("returns plain text unchanged when no wikilinks present", () => {
    const raw = "Just plain text with no brackets at all.";
    expect(wikilinkTransform(raw)).toBe(raw);
  });

  it("emits anchors without href attribute (navigation via data attribute only)", () => {
    const result = wikilinkTransform("[[SomePage]]");
    // Must NOT contain href — no browser navigation should fire
    expect(result).not.toMatch(/href=/);
  });

  it("trims whitespace from target and label", () => {
    // Extra whitespace inside [[ ]] is common in hand-typed notes
    const result = wikilinkTransform("[[ My Page ]]");
    expect(result).toContain('data-wikilink="My Page"');
    expect(result).toContain(">My Page</a>");
  });
});

// ─── DOMPurify integration: data-wikilink must survive sanitization ────────────
//
// We cannot import DOMPurify directly in a test (it requires a DOM); but we CAN
// call renderMarkdown (which runs DOMPurify internally) and verify the attr survives.
// renderMarkdown is the real function here — no mock.

import { renderMarkdown } from "../components/chat/renderMarkdown";

describe("renderMarkdown — wikilinks survive DOMPurify (Task A integration)", () => {
  it("[[Target]] in markdown body produces a .wikilink anchor in final HTML", () => {
    const html = renderMarkdown("See [[Temperature Scaling]] for details.");
    expect(html).toContain('class="wikilink"');
    expect(html).toContain('data-wikilink="Temperature Scaling"');
    expect(html).toContain(">Temperature Scaling</a>");
  });

  it("[[Target|Label]] produces correct visible text and data-wikilink", () => {
    const html = renderMarkdown("See [[Temperature Scaling|temp]] here.");
    expect(html).toContain('data-wikilink="Temperature Scaling"');
    expect(html).toContain(">temp</a>");
  });

  it("regular text without wikilinks is unaffected", () => {
    const html = renderMarkdown("Just plain **bold** text.");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).not.toContain("wikilink");
  });
});
