import SwiftUI

/// A page reference used for `navigationDestination`-based push navigation from
/// anywhere (search results, citations, wikilinks, graph nodes).
struct PageRef: Hashable {
    let id: String
    let title: String?
    let type: String?
}

/// Rounded grouped-content card (light `#FFF` / dark `#1C1C1E`).
/// `elevated` adds a soft indigo-tinted shadow for hero cards.
struct Card<Content: View>: View {
    var padding: CGFloat? = nil
    var elevated: Bool = false
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(spacing: 0, content: content)
            .modifier(OptionalPadding(padding))
            .background(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .shadow(
                color: elevated ? Color(hex: 0x4F46E5).opacity(0.16) : .clear,
                radius: elevated ? 18 : 0, y: elevated ? 10 : 0)
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

/// Per-type SF Symbol inside a rounded tinted square — the list-row leading
/// glyph. Reads by shape and colour, not just colour.
struct TypeDot: View {
    let type: String?
    var size: CGFloat = 34
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
            .fill(Theme.tintBackground(forType: type, scheme: scheme))
            .frame(width: size, height: size)
            .overlay(
                Image(systemName: Theme.icon(forType: type))
                    .font(.system(size: size * 0.46, weight: .semibold))
                    .foregroundStyle(Theme.color(forType: type)))
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
                    HStack(spacing: 5) {
                        Image(systemName: "point.3.connected.trianglepath.dotted")
                            .font(.system(size: 11, weight: .bold))
                        Text(eyebrow.uppercased())
                            .font(.system(size: 13, weight: .heavy))
                            .tracking(0.8)
                    }
                    .foregroundStyle(Theme.signatureGradient)
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

/// A faint constellation of connected nodes — the "synapse" motif rendered
/// behind hero headers to tie every screen to the knowledge graph. Deterministic
/// (fixed node/edge set) and non-interactive.
struct NeuralMotif: View {
    var height: CGFloat = 120
    @Environment(\.colorScheme) private var scheme

    private static let nodes: [CGPoint] = [
        .init(x: 0.06, y: 0.58), .init(x: 0.22, y: 0.30), .init(x: 0.34, y: 0.80),
        .init(x: 0.47, y: 0.42), .init(x: 0.62, y: 0.72), .init(x: 0.71, y: 0.24),
        .init(x: 0.86, y: 0.55), .init(x: 0.96, y: 0.82),
    ]
    private static let edges: [(Int, Int)] = [
        (0, 1), (0, 2), (1, 3), (2, 3), (3, 4), (3, 5), (5, 6), (4, 6), (6, 7),
    ]
    private static let dotColors: [UInt32] = [
        0x8B85F5, 0x8B85F5, 0x14B8A6, 0xA855F7, 0x10B981, 0xA855F7, 0x0EA5E9, 0x8B85F5,
    ]

    var body: some View {
        Canvas { ctx, size in
            func pt(_ p: CGPoint) -> CGPoint {
                CGPoint(x: p.x * size.width, y: p.y * size.height)
            }
            for (a, b) in Self.edges {
                var path = Path()
                path.move(to: pt(Self.nodes[a]))
                path.addLine(to: pt(Self.nodes[b]))
                ctx.stroke(path,
                           with: .color(Theme.tint.opacity(scheme == .dark ? 0.22 : 0.14)),
                           lineWidth: 1)
            }
            for (i, p) in Self.nodes.enumerated() {
                let c = pt(p)
                let r: CGFloat = i.isMultiple(of: 3) ? 3.2 : 2.2
                ctx.fill(
                    Circle().path(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)),
                    with: .color(Color(hex: Self.dotColors[i]).opacity(scheme == .dark ? 0.6 : 0.42)))
            }
        }
        .frame(height: height)
        .allowsHitTesting(false)
    }
}

/// Soft aurora of drifting, blurred colour blobs behind the graph — the app's
/// visual soul. Faint enough that nodes stay legible; honours Reduce Motion.
struct AuroraBackground: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var drift = false

    var body: some View {
        ZStack {
            Theme.graphBackground
            blob(Theme.auroraColors[0], x: -0.30, y: -0.34, scale: 1.0)
            blob(Theme.auroraColors[1], x: 0.34, y: -0.12, scale: 1.1)
            blob(Theme.auroraColors[2], x: 0.10, y: 0.40, scale: 1.2)
            blob(Theme.auroraColors[3], x: -0.24, y: 0.30, scale: 0.9)
        }
        .ignoresSafeArea()
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 16).repeatForever(autoreverses: true)) {
                drift = true
            }
        }
    }

    private func blob(_ hex: UInt32, x: CGFloat, y: CGFloat, scale: CGFloat) -> some View {
        GeometryReader { geo in
            let d = min(geo.size.width, geo.size.height) * 1.15 * scale
            Circle()
                .fill(Color(hex: hex))
                .frame(width: d, height: d)
                .position(
                    x: geo.size.width * (0.5 + x) + (drift ? 16 : -16),
                    y: geo.size.height * (0.5 + y) + (drift ? -14 : 14))
                .blur(radius: 80)
                .opacity(0.20)
        }
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
                        .background(Theme.signatureGradient)
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
