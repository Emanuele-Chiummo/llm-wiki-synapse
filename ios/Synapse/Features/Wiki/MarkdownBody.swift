import SwiftUI

/// A minimal block-level Markdown renderer tuned to match the design's body
/// styling (headings, paragraphs, block quotes, bullet lists). Inline emphasis
/// and code are handled via `AttributedString(markdown:)`; `[[wikilinks]]` are
/// flattened to their display text.
enum MarkdownBlock: Identifiable {
    case heading(String, level: Int)
    case paragraph(AttributedString)
    case quote(AttributedString)
    case bullet(AttributedString)

    var id: String {
        switch self {
        case .heading(let t, let l): return "h\(l)-\(t)"
        case .paragraph(let a): return "p-\(a.characters.count)-\(String(a.characters.prefix(24)))"
        case .quote(let a): return "q-\(String(a.characters.prefix(24)))"
        case .bullet(let a): return "b-\(String(a.characters.prefix(24)))"
        }
    }
}

enum Markdown {
    /// Strip YAML frontmatter and parse the body into block-level elements.
    static func blocks(from raw: String) -> [MarkdownBlock] {
        var lines = raw.replacingOccurrences(of: "\r\n", with: "\n")
            .components(separatedBy: "\n")

        // Drop a leading `---` … `---` YAML frontmatter block.
        if let first = lines.first(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }),
           first.trimmingCharacters(in: .whitespaces) == "---",
           let startIdx = lines.firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" })
        {
            if let endIdx = lines[(startIdx + 1)...]
                .firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" })
            {
                lines.removeSubrange(0...endIdx)
            }
        }

        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            let joined = paragraph.joined(separator: " ")
            blocks.append(.paragraph(inline(joined)))
            paragraph.removeAll()
        }

        for rawLine in lines {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { flushParagraph(); continue }

            if line.hasPrefix("#") {
                flushParagraph()
                let hashes = line.prefix { $0 == "#" }.count
                let text = line.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)
                blocks.append(.heading(flattenWikilinks(text), level: min(hashes, 3)))
            } else if line.hasPrefix(">") {
                flushParagraph()
                let text = line.dropFirst().trimmingCharacters(in: .whitespaces)
                blocks.append(.quote(inline(text)))
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                flushParagraph()
                let text = String(line.dropFirst(2))
                blocks.append(.bullet(inline(text)))
            } else if line == "---" || line == "***" {
                flushParagraph()  // horizontal rule → treated as a break
            } else {
                paragraph.append(line)
            }
        }
        flushParagraph()
        return blocks
    }

    /// Convert `[[Target|Alias]]` / `[[Target]]` to plain display text.
    private static func flattenWikilinks(_ s: String) -> String {
        guard s.contains("[[") else { return s }
        var out = s
        while let open = out.range(of: "[["), let close = out.range(of: "]]", range: open.upperBound..<out.endIndex) {
            let inner = String(out[open.upperBound..<close.lowerBound])
            let display = inner.split(separator: "|").last.map(String.init) ?? inner
            out.replaceSubrange(open.lowerBound..<close.upperBound, with: display)
        }
        return out
    }

    private static func inline(_ s: String) -> AttributedString {
        let flat = flattenWikilinks(s)
        if let attr = try? AttributedString(
            markdown: flat,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))
        {
            return attr
        }
        return AttributedString(flat)
    }
}

/// Renders parsed Markdown blocks with the design's typography.
struct MarkdownBodyView: View {
    let blocks: [MarkdownBlock]
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(blocks) { block in
                switch block {
                case .heading(let text, _):
                    Text(text)
                        .font(.system(size: 19, weight: .bold))
                        .foregroundStyle(Theme.label)
                case .paragraph(let text):
                    Text(text)
                        .font(.system(size: 16))
                        .lineSpacing(4)
                        .foregroundStyle(Theme.label)
                        .fixedSize(horizontal: false, vertical: true)
                case .quote(let text):
                    Text(text)
                        .font(.system(size: 15))
                        .italic()
                        .foregroundStyle(Theme.label2)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.leading, 14)
                        .overlay(alignment: .leading) {
                            Rectangle().fill(Theme.tint).frame(width: 3)
                        }
                case .bullet(let text):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Circle().fill(Theme.label2).frame(width: 5, height: 5)
                            .offset(y: -2)
                        Text(text)
                            .font(.system(size: 16))
                            .lineSpacing(4)
                            .foregroundStyle(Theme.label)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
