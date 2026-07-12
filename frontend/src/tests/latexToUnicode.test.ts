/**
 * latexToUnicode.test.ts — unit tests for the pure LaTeX→Unicode converter (F8 / ADR-0019 §2.6).
 *
 * Tests AC-F8-2 coverage (Greek, operators, arrows, sub/superscripts) and
 * AC-F8-3 (display math preserved as fenced code block, never silently dropped).
 */

import { describe, it, expect } from "vitest";
import { latexToUnicode } from "../components/chat/latexToUnicode";

// ─── Greek symbols ────────────────────────────────────────────────────────────

describe("latexToUnicode — Greek symbols (AC-F8-2)", () => {
  it("converts \\alpha → α", () => {
    expect(latexToUnicode("The value \\alpha is small")).toContain("α");
  });

  it("converts \\beta → β", () => {
    expect(latexToUnicode("\\beta")).toContain("β");
  });

  it("converts \\Gamma → Γ (uppercase)", () => {
    expect(latexToUnicode("\\Gamma")).toContain("Γ");
  });

  it("converts \\Omega → Ω (uppercase)", () => {
    expect(latexToUnicode("\\Omega")).toContain("Ω");
  });

  it("converts multiple Greeks in one string", () => {
    const result = latexToUnicode("\\alpha + \\beta = \\gamma");
    expect(result).toContain("α");
    expect(result).toContain("β");
    expect(result).toContain("γ");
  });
});

// ─── Math operators ───────────────────────────────────────────────────────────

describe("latexToUnicode — Math operators (AC-F8-2)", () => {
  it("converts \\sum → ∑", () => {
    expect(latexToUnicode("\\sum_{i=0}^{n}")).toContain("∑");
  });

  it("converts \\prod → ∏", () => {
    expect(latexToUnicode("\\prod")).toContain("∏");
  });

  it("converts \\int → ∫", () => {
    expect(latexToUnicode("\\int_a^b")).toContain("∫");
  });

  it("converts \\partial → ∂", () => {
    expect(latexToUnicode("\\partial f / \\partial x")).toContain("∂");
  });

  it("converts \\nabla → ∇", () => {
    expect(latexToUnicode("\\nabla \\cdot F")).toContain("∇");
  });

  it("converts \\infty → ∞", () => {
    expect(latexToUnicode("n \\to \\infty")).toContain("∞");
  });

  it("converts \\leq → ≤ and \\geq → ≥", () => {
    const r = latexToUnicode("a \\leq b \\geq c");
    expect(r).toContain("≤");
    expect(r).toContain("≥");
  });

  it("converts \\neq → ≠", () => {
    expect(latexToUnicode("a \\neq b")).toContain("≠");
  });

  it("converts \\approx → ≈", () => {
    expect(latexToUnicode("x \\approx 3.14")).toContain("≈");
  });

  it("converts \\pm → ±", () => {
    expect(latexToUnicode("x \\pm 1")).toContain("±");
  });

  it("converts \\times → ×", () => {
    expect(latexToUnicode("3 \\times 4")).toContain("×");
  });

  it("converts \\in → ∈ and \\notin → ∉", () => {
    const r = latexToUnicode("x \\in A, y \\notin B");
    expect(r).toContain("∈");
    expect(r).toContain("∉");
  });
});

// ─── Arrows ───────────────────────────────────────────────────────────────────

describe("latexToUnicode — Arrows (AC-F8-2)", () => {
  it("converts \\to → →", () => {
    expect(latexToUnicode("x \\to y")).toContain("→");
  });

  it("converts \\rightarrow → →", () => {
    expect(latexToUnicode("f\\rightarrow g")).toContain("→");
  });

  it("converts \\leftarrow → ←", () => {
    expect(latexToUnicode("A \\leftarrow B")).toContain("←");
  });

  it("converts \\leftrightarrow → ↔", () => {
    expect(latexToUnicode("A \\leftrightarrow B")).toContain("↔");
  });

  it("converts \\Rightarrow → ⇒", () => {
    expect(latexToUnicode("P \\Rightarrow Q")).toContain("⇒");
  });

  it("converts \\Leftrightarrow → ⇔", () => {
    expect(latexToUnicode("P \\Leftrightarrow Q")).toContain("⇔");
  });
});

// ─── Superscripts and subscripts ──────────────────────────────────────────────

describe("latexToUnicode — Sub/superscripts (AC-F8-2)", () => {
  it("converts x^2 → x²", () => {
    const r = latexToUnicode("x^2");
    expect(r).toContain("²");
  });

  it("converts x^{10} → x¹⁰", () => {
    const r = latexToUnicode("x^{10}");
    expect(r).toContain("¹");
    expect(r).toContain("⁰");
  });

  it("converts H_2O → H₂O", () => {
    const r = latexToUnicode("H_2O");
    expect(r).toContain("₂");
  });

  it("converts n_i → nᵢ", () => {
    const r = latexToUnicode("n_i");
    expect(r).toContain("ᵢ");
  });

  it("converts a_{n} → aₙ", () => {
    const r = latexToUnicode("a_{n}");
    expect(r).toContain("ₙ");
  });
});

// ─── Inline math delimiters ───────────────────────────────────────────────────

describe("latexToUnicode — Inline math delimiters", () => {
  it("processes content inside \\(…\\) and strips delimiters", () => {
    const r = latexToUnicode("The value \\(\\alpha\\) is small.");
    expect(r).toContain("α");
    expect(r).not.toContain("\\(");
    expect(r).not.toContain("\\)");
  });

  it("processes content inside $…$ and strips delimiters", () => {
    const r = latexToUnicode("Energy $E = mc^2$ is famous.");
    expect(r).toContain("²");
    expect(r).not.toContain("$E");
  });
});

// ─── Display math preservation (AC-F8-3) ─────────────────────────────────────

describe("latexToUnicode — Display math preserved, never dropped (AC-F8-3)", () => {
  it("wraps $$…$$ in a fenced code block and preserves content", () => {
    const r = latexToUnicode("See $$E = mc^2$$ for reference.");
    expect(r).toContain("```math");
    expect(r).toContain("```");
    // Content is preserved (^2 may be converted to ² — both are fine; block not dropped)
    const hasMath = r.includes("mc^2") || r.includes("mc²");
    expect(hasMath).toBe(true);
  });

  it("wraps \\[…\\] in a fenced code block and preserves content", () => {
    const r = latexToUnicode("Formula: \\[\\int_a^b f(x)dx\\]");
    expect(r).toContain("```math");
    // Content is preserved (\\int may be converted to ∫ by the symbol pass — both are fine)
    // The key invariant is the block is not silently dropped
    const hasMath = r.includes("∫") || r.includes("\\int");
    expect(hasMath).toBe(true);
  });

  it("does not silently drop display math content", () => {
    const complex = "$$\\begin{matrix} a & b \\\\ c & d \\end{matrix}$$";
    const r = latexToUnicode(complex);
    // Content is preserved in the fenced block
    expect(r).toContain("begin{matrix}");
    expect(r).not.toHaveLength(0);
  });
});

// ─── No-op on plain text ──────────────────────────────────────────────────────

describe("latexToUnicode — plain text passthrough", () => {
  it("returns plain text unchanged", () => {
    const plain = "Hello, world! This has no LaTeX.";
    expect(latexToUnicode(plain)).toBe(plain);
  });

  it("returns empty string unchanged", () => {
    expect(latexToUnicode("")).toBe("");
  });
});

// ─── Code regions protected (AC-F8-3 fence + user code) ──────────────────────

describe("latexToUnicode — code regions left byte-for-byte intact (AC-F8-3)", () => {
  it("does NOT convert symbols inside the generated ```math display fence", () => {
    const r = latexToUnicode("See $$E = \\sum_i x_i$$ done.");
    expect(r).toContain("```math");
    // Inside the fence the LaTeX must stay literal — never ∑ / ᵢ
    expect(r).toContain("\\sum_i x_i");
    expect(r).not.toContain("∑");
    expect(r).not.toContain("ᵢ");
  });

  it("does NOT convert symbols inside a user fenced code block", () => {
    const src = "Prose \\alpha here\n\n```python\nx_2 = a \\times b  # \\sum\n```\n";
    const r = latexToUnicode(src);
    // Prose alpha IS converted
    expect(r).toContain("α");
    // Code block content is untouched
    expect(r).toContain("x_2 = a \\times b  # \\sum");
    expect(r).not.toContain("×");
    expect(r).not.toContain("₂");
  });

  it("does NOT convert symbols inside an inline code span", () => {
    const r = latexToUnicode("Use `H_2O` literally but H_2O in prose.");
    // Inline code untouched
    expect(r).toContain("`H_2O`");
    // Prose subscript converted
    expect(r).toContain("H₂O");
  });
});

// ─── Unconvertible sequences left as-is (AC-F8-3) ────────────────────────────

describe("latexToUnicode — unconvertible sequences (AC-F8-3)", () => {
  it("leaves unknown LaTeX commands as-is", () => {
    const r = latexToUnicode("\\unknowncommand{abc}");
    expect(r).toContain("\\unknowncommand");
  });
});
