import SwiftUI

/// Elevated content card (desktop `.syn-card`). Soft, cool shadow; optional
/// `elevated` intensifies it for hero cards.
struct SynCard<Content: View>: View {
    var padding: CGFloat = SynSpace.x6
    var elevated: Bool = false
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0, content: content)
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(SynColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: SynRadius.lg, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: SynRadius.lg, style: .continuous)
                    .strokeBorder(SynColor.border, lineWidth: 1)
            )
            .shadow(
                color: Color.black.opacity(elevated ? 0.10 : 0.05),
                radius: elevated ? 22 : 10,
                x: 0, y: elevated ? 10 : 4)
    }
}

/// Uppercase eyebrow above a card group (desktop `.syn-empty-state__eyebrow`
/// / section label).
struct SynSectionHeader: View {
    let text: String
    var accent: Bool = false
    var body: some View {
        Text(text.uppercased())
            .font(SynFont.eyebrow)
            .tracking(0.6)
            .foregroundStyle(accent ? SynColor.accent : SynColor.textMuted)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
