import SwiftUI

/// A lightweight, dependency-free markdown reading renderer for wiki pages
/// (Track iOS 2.1, Fase B). It does **block-level** layout itself (headings,
/// paragraphs, lists, code fences, blockquotes, `$$` math) and delegates
/// **inline** formatting (bold / italic / code / links) to Foundation's
/// `AttributedString(markdown:)` — i.e. it does not re-implement a full markdown
/// parser, per the Fase B brief.
///
/// Two Synapse-specific behaviours:
/// * `[[wikilink]]` / `[[target|alias]]` (K5) → a tappable link on the
///   `synwiki://<slug>` scheme; the reading view intercepts it via `openURL` and
///   navigates to that page.
/// * `$…$` / `$$…$$` LaTeX → Unicode via `LaTeXUnicode` (see its doc for the
///   documented trade-off vs the desktop's KaTeX).
///
/// I3 parity: the whole document is parsed **once** into `[Block]` (e.g. when a
/// chat stream settles or a page loads), never re-parsed per token / per frame.
struct MarkdownView: View {
    let blocks: [WikiMarkdownBlock]

    init(_ raw: String) { self.blocks = WikiMarkdownBlock.parse(raw) }
    init(blocks: [WikiMarkdownBlock]) { self.blocks = blocks }

    var body: some View {
        VStack(alignment: .leading, spacing: SynSpace.x5) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                view(for: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func view(for block: WikiMarkdownBlock) -> some View {
        switch block {
        case .heading(let level, let text):
            Text(text)
                .font(headingFont(level))
                .foregroundStyle(SynColor.text)
                .padding(.top, level <= 2 ? SynSpace.x2 : 0)
        case .paragraph(let text):
            Text(text)
                .font(SynFont.body)
                .foregroundStyle(SynColor.text)
                .lineSpacing(5)
                .tint(SynColor.accent)
        case .bullet(let items):
            VStack(alignment: .leading, spacing: SynSpace.x3) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    listRow(marker: "•", text: item)
                }
            }
        case .ordered(let items):
            VStack(alignment: .leading, spacing: SynSpace.x3) {
                ForEach(Array(items.enumerated()), id: \.offset) { idx, item in
                    listRow(marker: "\(idx + 1).", text: item)
                }
            }
        case .code(let code):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(.footnote, design: .monospaced))
                    .foregroundStyle(SynColor.text)
                    .padding(SynSpace.x5)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(SynColor.surfaceSunken)
            .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous)
                .strokeBorder(SynColor.border, lineWidth: 1))
        case .quote(let text):
            HStack(spacing: SynSpace.x4) {
                RoundedRectangle(cornerRadius: 2).fill(SynColor.accent).frame(width: 3)
                Text(text).font(SynFont.body).foregroundStyle(SynColor.textMuted)
                    .lineSpacing(4)
                Spacer(minLength: 0)
            }
        case .math(let latex):
            Text(LaTeXUnicode.convert(latex))
                .font(.system(.body, design: .serif))
                .foregroundStyle(SynColor.text)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, SynSpace.x3)
        }
    }

    private func listRow(marker: String, text: AttributedString) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: SynSpace.x3) {
            Text(marker)
                .font(SynFont.body.monospacedDigit())
                .foregroundStyle(SynColor.accent)
            Text(text).font(SynFont.body).foregroundStyle(SynColor.text)
                .tint(SynColor.accent).lineSpacing(4)
            Spacer(minLength: 0)
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .system(.title, design: .default).weight(.bold)
        case 2: return .system(.title2, design: .default).weight(.bold)
        case 3: return .system(.title3, design: .default).weight(.semibold)
        default: return .system(.headline, design: .default).weight(.semibold)
        }
    }
}

/// One parsed markdown block. Parsing is a one-shot pure function (I3).
enum WikiMarkdownBlock {
    case heading(level: Int, text: String)
    case paragraph(AttributedString)
    case bullet([AttributedString])
    case ordered([AttributedString])
    case code(String)
    case quote(AttributedString)
    case math(String)

    /// URL scheme a `[[wikilink]]` becomes, intercepted by the reading view.
    static let wikilinkScheme = "synwiki"

    static func parse(_ raw: String) -> [WikiMarkdownBlock] {
        let lines = raw.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var blocks: [WikiMarkdownBlock] = []
        var i = 0

        func flushParagraph(_ buf: inout [String]) {
            guard !buf.isEmpty else { return }
            let joined = buf.joined(separator: " ")
            blocks.append(.paragraph(inline(joined)))
            buf.removeAll()
        }

        var paragraph: [String] = []

        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Fenced code block ``` … ```
            if trimmed.hasPrefix("```") {
                flushParagraph(&paragraph)
                var code: [String] = []
                i += 1
                while i < lines.count && !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[i]); i += 1
                }
                i += 1 // skip closing fence
                blocks.append(.code(code.joined(separator: "\n")))
                continue
            }

            // Block math $$ … $$
            if trimmed == "$$" {
                flushParagraph(&paragraph)
                var math: [String] = []
                i += 1
                while i < lines.count && lines[i].trimmingCharacters(in: .whitespaces) != "$$" {
                    math.append(lines[i]); i += 1
                }
                i += 1
                blocks.append(.math(math.joined(separator: " ")))
                continue
            }
            if trimmed.hasPrefix("$$") && trimmed.hasSuffix("$$") && trimmed.count > 4 {
                flushParagraph(&paragraph)
                let inner = String(trimmed.dropFirst(2).dropLast(2))
                blocks.append(.math(inner))
                i += 1
                continue
            }

            // Blank line — paragraph boundary.
            if trimmed.isEmpty {
                flushParagraph(&paragraph)
                i += 1
                continue
            }

            // Heading
            if let (level, text) = headingParts(trimmed) {
                flushParagraph(&paragraph)
                blocks.append(.heading(level: level, text: stripInlineMarks(text)))
                i += 1
                continue
            }

            // Unordered list
            if isBullet(trimmed) {
                flushParagraph(&paragraph)
                var items: [AttributedString] = []
                while i < lines.count, isBullet(lines[i].trimmingCharacters(in: .whitespaces)) {
                    let content = stripBulletMarker(lines[i].trimmingCharacters(in: .whitespaces))
                    items.append(inline(content))
                    i += 1
                }
                blocks.append(.bullet(items))
                continue
            }

            // Ordered list
            if isOrdered(trimmed) {
                flushParagraph(&paragraph)
                var items: [AttributedString] = []
                while i < lines.count, isOrdered(lines[i].trimmingCharacters(in: .whitespaces)) {
                    let content = stripOrderedMarker(lines[i].trimmingCharacters(in: .whitespaces))
                    items.append(inline(content))
                    i += 1
                }
                blocks.append(.ordered(items))
                continue
            }

            // Blockquote
            if trimmed.hasPrefix(">") {
                flushParagraph(&paragraph)
                var quote: [String] = []
                while i < lines.count, lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    let c = lines[i].trimmingCharacters(in: .whitespaces)
                    quote.append(String(c.dropFirst()).trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                blocks.append(.quote(inline(quote.joined(separator: " "))))
                continue
            }

            paragraph.append(trimmed)
            i += 1
        }
        flushParagraph(&paragraph)
        return blocks
    }

    // MARK: Inline

    /// Build an inline `AttributedString`: wikilinks → tappable links, inline
    /// `$…$` math → Unicode, then Foundation inline markdown for the rest.
    static func inline(_ text: String) -> AttributedString {
        let pre = convertInlineMath(convertWikilinks(text))
        var options = AttributedString.MarkdownParsingOptions()
        options.interpretedSyntax = .inlineOnlyPreservingWhitespace
        options.failurePolicy = .returnPartiallyParsedIfPossible
        if var attr = try? AttributedString(markdown: pre, options: options) {
            // Tint links with the accent so wikilinks read as interactive.
            for run in attr.runs where run.link != nil {
                attr[run.range].foregroundColor = SynColor.accent
            }
            return attr
        }
        return AttributedString(pre)
    }

    /// `[[Target]]` / `[[Target|Alias]]` → `[Alias](synwiki://slug)`.
    static func convertWikilinks(_ text: String) -> String {
        guard text.contains("[[") else { return text }
        var out = ""
        var rest = Substring(text)
        while let open = rest.range(of: "[["), let close = rest.range(of: "]]", range: open.upperBound..<rest.endIndex) {
            out += rest[rest.startIndex..<open.lowerBound]
            let inner = String(rest[open.upperBound..<close.lowerBound])
            let parts = inner.split(separator: "|", maxSplits: 1).map(String.init)
            let target = parts.first ?? inner
            let alias = parts.count > 1 ? parts[1] : target
            let slug = API.slugify(target)
            // Escape closing paren in alias defensively.
            let safeAlias = alias.replacingOccurrences(of: ")", with: "")
            out += "[\(safeAlias)](\(wikilinkScheme)://\(slug))"
            rest = rest[close.upperBound...]
        }
        out += rest
        return out
    }

    /// Inline `$…$` → Unicode (skips `$$` which is handled as a block).
    static func convertInlineMath(_ text: String) -> String {
        guard text.contains("$") else { return text }
        var out = ""
        var rest = Substring(text)
        while let open = rest.firstIndex(of: "$") {
            out += rest[rest.startIndex..<open]
            let afterOpen = rest.index(after: open)
            guard let close = rest[afterOpen...].firstIndex(of: "$") else {
                out += rest[open...]; rest = rest[rest.endIndex...]; break
            }
            let math = String(rest[afterOpen..<close])
            out += LaTeXUnicode.convert(math)
            rest = rest[rest.index(after: close)...]
        }
        out += rest
        return out
    }

    private static func stripInlineMarks(_ s: String) -> String {
        s.replacingOccurrences(of: "**", with: "")
         .replacingOccurrences(of: "`", with: "")
         .replacingOccurrences(of: "*", with: "")
    }

    // MARK: Block detectors

    private static func headingParts(_ s: String) -> (Int, String)? {
        guard s.hasPrefix("#") else { return nil }
        var level = 0
        for ch in s { if ch == "#" { level += 1 } else { break } }
        guard level >= 1, level <= 6 else { return nil }
        let idx = s.index(s.startIndex, offsetBy: level)
        let text = s[idx...].trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }
        return (level, text)
    }

    private static func isBullet(_ s: String) -> Bool {
        s.hasPrefix("- ") || s.hasPrefix("* ") || s.hasPrefix("+ ")
    }
    private static func stripBulletMarker(_ s: String) -> String {
        String(s.dropFirst(2)).trimmingCharacters(in: .whitespaces)
    }
    private static func isOrdered(_ s: String) -> Bool {
        guard let dot = s.firstIndex(of: ".") else { return false }
        let numPart = s[s.startIndex..<dot]
        return !numPart.isEmpty && numPart.allSatisfy(\.isNumber)
            && s.index(after: dot) < s.endIndex && s[s.index(after: dot)] == " "
    }
    private static func stripOrderedMarker(_ s: String) -> String {
        guard let dot = s.firstIndex(of: ".") else { return s }
        return String(s[s.index(after: dot)...]).trimmingCharacters(in: .whitespaces)
    }
}
