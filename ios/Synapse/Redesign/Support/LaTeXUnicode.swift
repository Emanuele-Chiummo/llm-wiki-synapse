import Foundation

/// A small, dependency-free LaTeX → Unicode converter for inline/block math in
/// the wiki reading view (Track iOS 2.1, Fase B).
///
/// **Trade-off (documented):** the desktop renders math with KaTeX in a web
/// view; a faithful native equivalent (KaTeX/MathJax in a WKWebView, or a
/// native TeX layout engine like SwiftMath) is deferred — it would add a heavy
/// dependency or per-block web views for marginal gain on a phone. Instead we
/// map the common LaTeX tokens (Greek letters, operators, arrows, and
/// super/subscripts that have Unicode forms) to Unicode text, which reads
/// cleanly inline and needs no dependency. Anything without a Unicode form
/// (fractions, integrals with limits, matrices) degrades to lightly-cleaned
/// source text rather than raw `\command` noise. This is the "reasonable
/// degrade" sanctioned for Fase B; a full renderer is Fase C+ work.
enum LaTeXUnicode {

    /// Convert a LaTeX fragment (already stripped of `$`/`$$` delimiters) to a
    /// best-effort Unicode string.
    static func convert(_ latex: String) -> String {
        var s = latex

        // 1) Named symbols (longest-first so \Gamma matches before \gamma etc.
        //    is irrelevant here, but multi-char names must precede prefixes).
        for (tex, uni) in symbols {
            s = s.replacingOccurrences(of: tex, with: uni)
        }

        // 2) \frac{a}{b} -> (a)/(b)  (no Unicode fraction bar; parenthesise).
        s = replaceFrac(in: s)

        // 3) Superscripts / subscripts: ^{...} / _{...} and single-char ^x / _x.
        s = replaceScripts(in: s, marker: "^", table: superscripts)
        s = replaceScripts(in: s, marker: "_", table: subscripts)

        // 4) Strip remaining braces and leftover backslashes from unknown macros.
        s = s.replacingOccurrences(of: "{", with: "")
             .replacingOccurrences(of: "}", with: "")
             .replacingOccurrences(of: "\\,", with: " ")
             .replacingOccurrences(of: "\\!", with: "")
             .replacingOccurrences(of: "\\", with: "")

        return s.trimmingCharacters(in: .whitespaces)
    }

    // MARK: Fractions

    private static func replaceFrac(in input: String) -> String {
        var s = input
        while let range = s.range(of: "\\frac") {
            // Parse the two following {...} groups.
            let after = s[range.upperBound...]
            guard let (num, r1) = braceGroup(after),
                  let (den, _) = braceGroup(s[r1...]) else { break }
            let replacement = "(\(num))/(\(den))"
            // Rebuild: everything up to \frac + replacement + everything after 2nd group.
            guard let endOfSecond = secondGroupEnd(s, fracRange: range) else { break }
            s.replaceSubrange(range.lowerBound..<endOfSecond, with: replacement)
        }
        return s
    }

    /// Return the text of the first `{...}` group at the start of `sub` plus the
    /// index just past its closing brace (in the parent string's index space).
    private static func braceGroup(_ sub: Substring) -> (String, String.Index)? {
        guard let open = sub.firstIndex(of: "{") else { return nil }
        var depth = 0
        var i = open
        while i < sub.endIndex {
            let c = sub[i]
            if c == "{" { depth += 1 }
            else if c == "}" { depth -= 1; if depth == 0 {
                let inner = String(sub[sub.index(after: open)..<i])
                return (inner, sub.index(after: i))
            } }
            i = sub.index(after: i)
        }
        return nil
    }

    private static func secondGroupEnd(_ s: String, fracRange: Range<String.Index>) -> String.Index? {
        let after = s[fracRange.upperBound...]
        guard let (_, r1) = braceGroup(after),
              let (_, r2) = braceGroup(s[r1...]) else { return nil }
        return r2
    }

    // MARK: Super/subscripts

    private static func replaceScripts(in input: String, marker: Character,
                                       table: [Character: Character]) -> String {
        var out = ""
        var it = input.startIndex
        while it < input.endIndex {
            let c = input[it]
            if c == marker {
                let next = input.index(after: it)
                if next < input.endIndex, input[next] == "{" {
                    // ^{...} — map each mappable char, else keep raw.
                    if let close = matchClose(input, from: next) {
                        let inner = input[input.index(after: next)..<close]
                        out += inner.map { table[$0].map(String.init) ?? String($0) }.joined()
                        it = input.index(after: close)
                        continue
                    }
                } else if next < input.endIndex, let mapped = table[input[next]] {
                    out.append(mapped)
                    it = input.index(after: next)
                    continue
                }
            }
            out.append(c)
            it = input.index(after: it)
        }
        return out
    }

    private static func matchClose(_ s: String, from openBraceBefore: String.Index) -> String.Index? {
        // openBraceBefore points at '{'.
        var depth = 0
        var i = openBraceBefore
        while i < s.endIndex {
            if s[i] == "{" { depth += 1 }
            else if s[i] == "}" { depth -= 1; if depth == 0 { return i } }
            i = s.index(after: i)
        }
        return nil
    }

    // MARK: Tables

    private static let symbols: [(String, String)] = [
        ("\\alpha", "α"), ("\\beta", "β"), ("\\gamma", "γ"), ("\\delta", "δ"),
        ("\\epsilon", "ε"), ("\\varepsilon", "ε"), ("\\zeta", "ζ"), ("\\eta", "η"),
        ("\\theta", "θ"), ("\\iota", "ι"), ("\\kappa", "κ"), ("\\lambda", "λ"),
        ("\\mu", "μ"), ("\\nu", "ν"), ("\\xi", "ξ"), ("\\pi", "π"), ("\\rho", "ρ"),
        ("\\sigma", "σ"), ("\\tau", "τ"), ("\\phi", "φ"), ("\\varphi", "φ"),
        ("\\chi", "χ"), ("\\psi", "ψ"), ("\\omega", "ω"),
        ("\\Gamma", "Γ"), ("\\Delta", "Δ"), ("\\Theta", "Θ"), ("\\Lambda", "Λ"),
        ("\\Xi", "Ξ"), ("\\Pi", "Π"), ("\\Sigma", "Σ"), ("\\Phi", "Φ"),
        ("\\Psi", "Ψ"), ("\\Omega", "Ω"),
        ("\\times", "×"), ("\\cdot", "·"), ("\\div", "÷"), ("\\pm", "±"),
        ("\\mp", "∓"), ("\\leq", "≤"), ("\\le", "≤"), ("\\geq", "≥"), ("\\ge", "≥"),
        ("\\neq", "≠"), ("\\approx", "≈"), ("\\equiv", "≡"), ("\\sim", "∼"),
        ("\\propto", "∝"), ("\\infty", "∞"), ("\\partial", "∂"), ("\\nabla", "∇"),
        ("\\sum", "∑"), ("\\prod", "∏"), ("\\int", "∫"), ("\\sqrt", "√"),
        ("\\in", "∈"), ("\\notin", "∉"), ("\\subset", "⊂"), ("\\subseteq", "⊆"),
        ("\\cup", "∪"), ("\\cap", "∩"), ("\\forall", "∀"), ("\\exists", "∃"),
        ("\\rightarrow", "→"), ("\\to", "→"), ("\\leftarrow", "←"),
        ("\\Rightarrow", "⇒"), ("\\Leftarrow", "⇐"), ("\\leftrightarrow", "↔"),
        ("\\mapsto", "↦"), ("\\langle", "⟨"), ("\\rangle", "⟩"),
        ("\\cdots", "⋯"), ("\\ldots", "…"), ("\\dots", "…"), ("\\star", "★"),
        ("\\odot", "⊙"), ("\\oplus", "⊕"), ("\\otimes", "⊗"),
    ]

    private static let superscripts: [Character: Character] = [
        "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
        "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽",
        ")": "⁾", "n": "ⁿ", "i": "ⁱ", "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "x": "ˣ",
    ]

    private static let subscripts: [Character: Character] = [
        "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
        "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "=": "₌", "(": "₍",
        ")": "₎", "a": "ₐ", "e": "ₑ", "i": "ᵢ", "j": "ⱼ", "n": "ₙ", "x": "ₓ",
    ]
}
