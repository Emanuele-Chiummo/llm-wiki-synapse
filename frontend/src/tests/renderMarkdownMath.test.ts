/**
 * renderMarkdownMath.test.ts — display-math (KaTeX) integration in renderMarkdown
 * (G-P1-2 / ADR-0019 amendment). Verifies:
 *   - $$…$$ and \[…\] render as KaTeX HTML (not a raw ```math code block)
 *   - raw LaTeX is preserved into KaTeX (not pre-mangled by latexToUnicode)
 *   - inline math stays Unicode-only (KaTeX handles display math only)
 *   - display math is never silently dropped
 */

import { describe, it, expect } from "vitest";
import {
  renderMarkdown,
  extractDisplayMath,
  injectDisplayMath,
} from "../components/chat/renderMarkdown";

describe("extractDisplayMath", () => {
  it("pulls $$…$$ into a placeholder and preserves raw LaTeX", () => {
    const { text, blocks } = extractDisplayMath("before $$\\frac{a}{b}$$ after");
    expect(blocks).toEqual(["\\frac{a}{b}"]);
    expect(text).toContain("@@SYNAPSEMATH0@@");
    expect(text).not.toContain("$$");
  });

  it("pulls \\[…\\] blocks too, in order", () => {
    const { text, blocks } = extractDisplayMath("\\[x^2\\] and $$y_1$$");
    expect(blocks).toEqual(["y_1", "x^2"]); // $$ pass runs first, then \[ \]
    expect(text.match(/@@SYNAPSEMATH\d+@@/g)).toHaveLength(2);
  });

  it("returns empty blocks when there is no display math", () => {
    const { text, blocks } = extractDisplayMath("plain text, inline $x$ only");
    expect(blocks).toEqual([]);
    expect(text).toBe("plain text, inline $x$ only");
  });
});

describe("renderMarkdown — display math via KaTeX", () => {
  it("renders $$…$$ as KaTeX HTML, not a ```math code block", () => {
    const html = renderMarkdown("Energy: $$E = mc^2$$");
    expect(html).toContain("katex");
    expect(html).not.toContain("language-math");
    // KaTeX emits a MathML mirror with the source characters
    expect(html).toContain("<math");
  });

  it("renders \\[…\\] display blocks", () => {
    const html = renderMarkdown("\\[\\sum_{i=1}^{n} i\\]");
    expect(html).toContain("katex");
    expect(html).not.toContain("language-math");
  });

  it("preserves raw LaTeX commands into KaTeX (not Unicode-converted first)", () => {
    // \frac must reach KaTeX intact; if latexToUnicode had run on it the fraction
    // structure would be lost. KaTeX renders \frac as a fraction (mfrac in MathML).
    const html = renderMarkdown("$$\\frac{1}{2}$$");
    expect(html).toContain("mfrac");
  });

  it("keeps inline math Unicode-only (no KaTeX for single-$ )", () => {
    const html = renderMarkdown("inline $\\alpha$ symbol");
    expect(html).toContain("α");
    expect(html).not.toContain("katex");
  });

  it("never drops display-math content", () => {
    const html = renderMarkdown("$$\\text{quantum}$$");
    // Content survives somewhere in the output (KaTeX MathML text or fallback).
    expect(html.toLowerCase()).toContain("quantum");
  });
});

describe("injectDisplayMath", () => {
  it("is a no-op when there are no blocks", () => {
    expect(injectDisplayMath("<p>hi</p>", [])).toBe("<p>hi</p>");
  });

  it("leaves an unknown placeholder index untouched", () => {
    expect(injectDisplayMath("x @@SYNAPSEMATH5@@ y", ["a"])).toContain("@@SYNAPSEMATH5@@");
  });
});
