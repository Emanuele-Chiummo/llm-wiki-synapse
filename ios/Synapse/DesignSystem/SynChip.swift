import SwiftUI

/// Rounded pill chip (desktop `.syn-chip`). Two styles: a neutral tag, or a
/// page-type chip that carries the type's jewel-tone colour + SF Symbol.
struct SynChip: View {
    let text: String
    var systemImage: String? = nil
    /// When set, the chip is coloured by the page type and shows its glyph.
    var pageType: String? = nil
    var selected: Bool = false

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(spacing: SynSpace.x1) {
            if let glyph {
                Image(systemName: glyph).font(.caption2.weight(.semibold))
            }
            Text(text).font(SynFont.caption)
        }
        .foregroundStyle(foreground)
        .padding(.horizontal, SynSpace.x3)
        .padding(.vertical, SynSpace.x2)
        .background(background)
        .clipShape(Capsule())
        .overlay(Capsule().strokeBorder(borderColor, lineWidth: 1))
    }

    private var glyph: String? {
        if let systemImage { return systemImage }
        if let pageType { return SynColor.icon(forType: pageType) }
        return nil
    }

    private var foreground: Color {
        if selected { return SynColor.onAccent }
        if let pageType { return SynColor.color(forType: pageType) }
        return SynColor.textMuted
    }

    private var background: Color {
        if selected { return SynColor.accent }
        if let pageType { return SynColor.tintBackground(forType: pageType, scheme: scheme) }
        return SynColor.surfaceHover
    }

    private var borderColor: Color {
        if selected { return .clear }
        if pageType != nil { return .clear }
        return SynColor.border
    }
}
