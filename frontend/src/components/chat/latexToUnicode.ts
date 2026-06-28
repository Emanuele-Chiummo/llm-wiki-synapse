/**
 * latexToUnicode.ts — pure lookup-table LaTeX → Unicode converter (F8 / ADR-0019 §2.6).
 *
 * Called ONCE per message at stream END (on the `done` event), never per token (I3 / AC-G3-2).
 *
 * Coverage (AC-F8-2):
 *   - Greek lowercase + uppercase
 *   - Math operators: sum, prod, int, partial, nabla, infinity, ...
 *   - Arrows: to, leftarrow, leftrightarrow, Rightarrow, Leftrightarrow, uparrow, downarrow
 *   - Comparison / logic: leq, geq, neq, approx, equiv, pm, times, div, cdot, cap, cup, in, ...
 *   - Inline sub/superscripts: x^2 → x², H_2O → H₂O (Unicode super/subscript ranges)
 *   - Inline math delimiters: \( … \) and $…$ unwrapped (content is processed, delimiters removed)
 *   - Display math ($$…$$, \[…\]) left as fenced code block (AC-F8-3 — never silently dropped)
 *
 * Unconvertible sequences are left as-is (AC-F8-3).
 * No KaTeX / MathJax dependency — F8 spec is "LaTeX → Unicode" only.
 */

// ─── Symbol table ─────────────────────────────────────────────────────────────

const SYMBOL_MAP: Record<string, string> = {
  // Greek lowercase
  "\\alpha": "α",
  "\\beta": "β",
  "\\gamma": "γ",
  "\\delta": "δ",
  "\\epsilon": "ε",
  "\\varepsilon": "ε",
  "\\zeta": "ζ",
  "\\eta": "η",
  "\\theta": "θ",
  "\\vartheta": "ϑ",
  "\\iota": "ι",
  "\\kappa": "κ",
  "\\lambda": "λ",
  "\\mu": "μ",
  "\\nu": "ν",
  "\\xi": "ξ",
  "\\pi": "π",
  "\\varpi": "ϖ",
  "\\rho": "ρ",
  "\\varrho": "ϱ",
  "\\sigma": "σ",
  "\\varsigma": "ς",
  "\\tau": "τ",
  "\\upsilon": "υ",
  "\\phi": "φ",
  "\\varphi": "φ",
  "\\chi": "χ",
  "\\psi": "ψ",
  "\\omega": "ω",
  // Greek uppercase
  "\\Alpha": "Α",
  "\\Beta": "Β",
  "\\Gamma": "Γ",
  "\\Delta": "Δ",
  "\\Epsilon": "Ε",
  "\\Zeta": "Ζ",
  "\\Eta": "Η",
  "\\Theta": "Θ",
  "\\Iota": "Ι",
  "\\Kappa": "Κ",
  "\\Lambda": "Λ",
  "\\Mu": "Μ",
  "\\Nu": "Ν",
  "\\Xi": "Ξ",
  "\\Pi": "Π",
  "\\Rho": "Ρ",
  "\\Sigma": "Σ",
  "\\Tau": "Τ",
  "\\Upsilon": "Υ",
  "\\Phi": "Φ",
  "\\Chi": "Χ",
  "\\Psi": "Ψ",
  "\\Omega": "Ω",
  // Math operators
  "\\sum": "∑",
  "\\prod": "∏",
  "\\int": "∫",
  "\\oint": "∮",
  "\\partial": "∂",
  "\\nabla": "∇",
  "\\infty": "∞",
  "\\forall": "∀",
  "\\exists": "∃",
  "\\nexists": "∄",
  "\\emptyset": "∅",
  "\\varnothing": "∅",
  "\\sqrt": "√",
  // Arrows
  "\\to": "→",
  "\\rightarrow": "→",
  "\\leftarrow": "←",
  "\\gets": "←",
  "\\leftrightarrow": "↔",
  "\\Rightarrow": "⇒",
  "\\Leftarrow": "⇐",
  "\\Leftrightarrow": "⇔",
  "\\uparrow": "↑",
  "\\downarrow": "↓",
  "\\updownarrow": "↕",
  "\\Uparrow": "⇑",
  "\\Downarrow": "⇓",
  "\\mapsto": "↦",
  "\\hookrightarrow": "↪",
  "\\hookleftarrow": "↩",
  // Comparison / relation
  "\\leq": "≤",
  "\\le": "≤",
  "\\geq": "≥",
  "\\ge": "≥",
  "\\neq": "≠",
  "\\ne": "≠",
  "\\approx": "≈",
  "\\equiv": "≡",
  "\\sim": "∼",
  "\\simeq": "≃",
  "\\cong": "≅",
  "\\propto": "∝",
  "\\ll": "≪",
  "\\gg": "≫",
  "\\subset": "⊂",
  "\\supset": "⊃",
  "\\subseteq": "⊆",
  "\\supseteq": "⊇",
  "\\in": "∈",
  "\\notin": "∉",
  "\\ni": "∋",
  // Binary ops
  "\\pm": "±",
  "\\mp": "∓",
  "\\times": "×",
  "\\div": "÷",
  "\\cdot": "·",
  "\\circ": "∘",
  "\\oplus": "⊕",
  "\\otimes": "⊗",
  "\\cap": "∩",
  "\\cup": "∪",
  "\\vee": "∨",
  "\\wedge": "∧",
  "\\neg": "¬",
  "\\setminus": "∖",
  // Misc
  "\\ldots": "…",
  "\\cdots": "⋯",
  "\\vdots": "⋮",
  "\\ddots": "⋱",
  "\\hbar": "ℏ",
  "\\ell": "ℓ",
  "\\Re": "ℜ",
  "\\Im": "ℑ",
  "\\aleph": "ℵ",
  "\\prime": "′",
  "\\angle": "∠",
  "\\perp": "⊥",
  "\\parallel": "∥",
  "\\triangle": "△",
  "\\square": "□",
  "\\langle": "⟨",
  "\\rangle": "⟩",
  "\\lfloor": "⌊",
  "\\rfloor": "⌋",
  "\\lceil": "⌈",
  "\\rceil": "⌉",
};

// ─── Superscript / subscript digit maps ──────────────────────────────────────

const SUPER: Record<string, string> = {
  "0": "⁰",
  "1": "¹",
  "2": "²",
  "3": "³",
  "4": "⁴",
  "5": "⁵",
  "6": "⁶",
  "7": "⁷",
  "8": "⁸",
  "9": "⁹",
  "+": "⁺",
  "-": "⁻",
  "=": "⁼",
  "(": "⁽",
  ")": "⁾",
  n: "ⁿ",
  i: "ⁱ",
};

const SUB: Record<string, string> = {
  "0": "₀",
  "1": "₁",
  "2": "₂",
  "3": "₃",
  "4": "₄",
  "5": "₅",
  "6": "₆",
  "7": "₇",
  "8": "₈",
  "9": "₉",
  "+": "₊",
  "-": "₋",
  "=": "₌",
  "(": "₍",
  ")": "₎",
  a: "ₐ",
  e: "ₑ",
  i: "ᵢ",
  o: "ₒ",
  u: "ᵤ",
  r: "ᵣ",
  v: "ᵥ",
  x: "ₓ",
  n: "ₙ",
};

function convertSuper(chars: string): string {
  return chars
    .split("")
    .map((c) => SUPER[c] ?? c)
    .join("");
}

function convertSub(chars: string): string {
  return chars
    .split("")
    .map((c) => SUB[c] ?? c)
    .join("");
}

// ─── Main converter ───────────────────────────────────────────────────────────

/**
 * latexToUnicode — convert LaTeX markup in a string to Unicode equivalents.
 *
 * Processing order (applied to inline math regions only):
 *   1. Named symbols (longest-match from SYMBOL_MAP)
 *   2. Superscripts x^{...} or x^c
 *   3. Subscripts x_{...} or x_c
 *
 * Display math ($$…$$ / \[…\]) is wrapped in a fenced code block (AC-F8-3).
 * Inline delimiters \(…\) and $…$ are unwrapped after processing.
 *
 * This is a pure function — no side effects, no DOM, testable in Node/jsdom.
 */
export function latexToUnicode(input: string): string {
  // 1. Protect display math — wrap in fenced code block (AC-F8-3, never silently drop)
  let result = input
    // $$…$$ (may be multi-line)
    .replace(/\$\$([\s\S]*?)\$\$/g, (_m, inner: string) => "\n```math\n" + inner.trim() + "\n```\n")
    // \[…\]
    .replace(/\\\[([\s\S]*?)\\\]/g, (_m, inner: string) => "\n```math\n" + inner.trim() + "\n```\n");

  // 2. Process inline math: \(…\) — convert content, strip delimiters
  result = result.replace(/\\\(([\s\S]*?)\\\)/g, (_m, inner: string) =>
    convertInline(inner),
  );

  // 3. Process inline math: $…$ (single $, not $$)
  // Use a pattern that avoids matching already-replaced $$
  result = result.replace(/(?<!\$)\$(?!\$)((?:[^$\\]|\\[\s\S])*?)\$(?!\$)/g, (_m, inner: string) =>
    convertInline(inner),
  );

  // 4. Convert bare LaTeX symbols outside math delimiters (common in prose)
  result = applySymbols(result);

  // 5. Apply super/subscript conversion globally (bare x^2, H_2O in prose — AC-F8-2)
  result = applySupSub(result);

  return result;
}

/** Convert the content of an inline math region. */
function convertInline(inner: string): string {
  let s = applySymbols(inner);
  s = applySupSub(s);
  return s;
}

/** Apply SYMBOL_MAP replacements (longest-match by sort order). */
function applySymbols(s: string): string {
  // Sort by length descending so longer tokens match first (e.g. \varepsilon before \var)
  // This is called per message (once, on done) so the sort cost is irrelevant.
  const keys = Object.keys(SYMBOL_MAP).sort((a, b) => b.length - a.length);
  let result = s;
  for (const key of keys) {
    // Escape the key for regex (backslash → \\\\, etc.)
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // Replace all occurrences
    result = result.replace(new RegExp(escaped, "g"), SYMBOL_MAP[key] ?? key);
  }
  return result;
}

/** Apply super/subscript conversions. */
function applySupSub(s: string): string {
  // x^{abc} or x^c  (superscript)
  let result = s.replace(/\^\{([^}]*)\}/g, (_m, inner: string) => convertSuper(inner));
  result = result.replace(/\^([0-9a-zA-Z+\-=()])/g, (_m, c: string) => convertSuper(c));
  // x_{abc} or x_c  (subscript)
  result = result.replace(/_\{([^}]*)\}/g, (_m, inner: string) => convertSub(inner));
  result = result.replace(/_([0-9a-zA-Z+\-=()])/g, (_m, c: string) => convertSub(c));
  return result;
}
