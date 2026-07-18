import SwiftUI

/// Per-type SF Symbol in a rounded tinted square — the list-row leading glyph.
/// Reads by shape *and* colour, not colour alone.
struct SynTypeGlyph: View {
    let type: String?
    var size: CGFloat = 34
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
            .fill(SynColor.tintBackground(forType: type, scheme: scheme))
            .frame(width: size, height: size)
            .overlay(
                Image(systemName: SynColor.icon(forType: type))
                    .font(.system(size: size * 0.46, weight: .semibold))
                    .foregroundStyle(SynColor.color(forType: type)))
    }
}

/// A generic redesign list row: leading type glyph, title (+ optional subtitle),
/// optional trailing chevron. Built to sit inside a `SynCard` group.
struct SynListRow: View {
    let title: String
    var subtitle: String? = nil
    var type: String? = nil
    var showsChevron: Bool = true

    var body: some View {
        HStack(spacing: SynSpace.x5) {
            SynTypeGlyph(type: type, size: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(SynFont.rowTitle)
                    .foregroundStyle(SynColor.text)
                    .lineLimit(1)
                if let subtitle {
                    Text(subtitle)
                        .font(SynFont.subhead)
                        .foregroundStyle(SynColor.textMuted)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: SynSpace.x3)
            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(SynColor.textDim)
            }
        }
        .padding(.vertical, SynSpace.x4)
        .contentShape(Rectangle())
    }
}

/// A hairline divider matching the desktop `.5px` in-card separators.
struct SynRowDivider: View {
    var leadingInset: CGFloat = 46
    var body: some View {
        Rectangle()
            .fill(SynColor.borderSubtle)
            .frame(height: 1)
            .padding(.leading, leadingInset)
    }
}
