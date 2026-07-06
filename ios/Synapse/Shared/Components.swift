import SwiftUI

/// A page reference used for `navigationDestination`-based push navigation from
/// anywhere (search results, citations, wikilinks, graph nodes).
struct PageRef: Hashable {
    let id: String
    let title: String?
    let type: String?
}

/// Rounded grouped-content card (light `#FFF` / dark `#1C1C1E`).
struct Card<Content: View>: View {
    var padding: CGFloat? = nil
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(spacing: 0, content: content)
            .modifier(OptionalPadding(padding))
            .background(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

private struct OptionalPadding: ViewModifier {
    let value: CGFloat?
    init(_ v: CGFloat?) { value = v }
    func body(content: Content) -> some View {
        if let value { content.padding(value) } else { content }
    }
}

/// Uppercase section label used above grouped cards.
struct SectionHeader: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 13, weight: .semibold))
            .tracking(0.3)
            .foregroundStyle(Theme.label2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.top, 18)
            .padding(.bottom, 8)
    }
}

/// Small coloured type pill, e.g. `Concept`.
struct TypePill: View {
    let type: String?
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        Text(Theme.label(forType: type))
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Theme.color(forType: type))
            .padding(.horizontal, 9)
            .padding(.vertical, 3)
            .background(Theme.tintBackground(forType: type, scheme: scheme))
            .clipShape(Capsule())
    }
}

/// Coloured dot inside a rounded tinted square — the list-row leading glyph.
struct TypeDot: View {
    let type: String?
    var size: CGFloat = 34
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
            .fill(Theme.tintBackground(forType: type, scheme: scheme))
            .frame(width: size, height: size)
            .overlay(
                Circle()
                    .fill(Theme.color(forType: type))
                    .frame(width: size * 0.27, height: size * 0.27))
    }
}

/// Circular header button (theme toggle, add, …).
struct RoundHeaderButton: View {
    let systemImage: String
    var filled: Bool = false
    var size: CGFloat = 38
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: size * 0.44, weight: .semibold))
                .foregroundStyle(filled ? Color.white : Theme.label)
                .frame(width: size, height: size)
                .background(filled ? Theme.tint : Theme.fieldBackground)
                .clipShape(Circle())
        }
        .buttonStyle(.plain)
    }
}

/// Big screen title with an optional eyebrow line, matching the design's
/// `Synapse / Wiki` header. Trailing view holds action buttons.
struct LargeHeader<Trailing: View>: View {
    let title: String
    var eyebrow: String? = nil
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 2) {
                if let eyebrow {
                    Text(eyebrow.uppercased())
                        .font(.system(size: 13, weight: .semibold))
                        .tracking(0.6)
                        .foregroundStyle(Theme.tint)
                }
                Text(title)
                    .font(.system(size: 33, weight: .bold))
                    .foregroundStyle(Theme.label)
            }
            Spacer()
            trailing()
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
        .padding(.bottom, 4)
    }
}

extension LargeHeader where Trailing == EmptyView {
    init(title: String, eyebrow: String? = nil) {
        self.init(title: title, eyebrow: eyebrow, trailing: { EmptyView() })
    }
}

/// Chevron used at the trailing edge of navigation rows.
struct DisclosureChevron: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(Theme.label3)
    }
}

/// Centered loading spinner with a caption.
struct LoadingState: View {
    var text: String = "Caricamento…"
    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text(text).font(.system(size: 15)).foregroundStyle(Theme.label2)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }
}

/// Empty / informational state with an SF Symbol.
struct EmptyState: View {
    let systemImage: String
    let title: String
    var message: String? = nil
    var tint: Color = Theme.tint
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.system(size: 34, weight: .regular))
                .foregroundStyle(tint)
                .padding(.bottom, 4)
            Text(title)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.label)
            if let message {
                Text(message)
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.label2)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 40)
        .padding(.vertical, 36)
    }
}

/// Inline error card with a retry button — shown when a request fails.
struct ErrorState: View {
    let message: String
    var retry: (() -> Void)? = nil
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 30))
                .foregroundStyle(Theme.destructive)
            Text(message)
                .font(.system(size: 15))
                .foregroundStyle(Theme.label2)
                .multilineTextAlignment(.center)
            if let retry {
                Button(action: retry) {
                    Text("Riprova")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 10)
                        .background(Theme.tint)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 30)
        .padding(.vertical, 30)
    }
}

/// A generic list row: leading type dot, title (+ optional subtitle), trailing.
struct PageRow<Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    var type: String?
    var dotSize: CGFloat = 32
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 12) {
            TypeDot(type: type, size: dotSize)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(Theme.label)
                    .lineLimit(1)
                if let subtitle {
                    Text(subtitle)
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.label2)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            trailing()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .contentShape(Rectangle())
    }
}

/// A hairline divider matching the design's `.5px` separators inside cards.
struct RowDivider: View {
    var body: some View {
        Rectangle()
            .fill(Theme.separator)
            .frame(height: 0.5)
            .padding(.leading, 14)
    }
}

extension View {
    /// Fill the screen with the themed background behind scrolling content.
    func screenBackground() -> some View {
        background(Theme.background.ignoresSafeArea())
    }
}
