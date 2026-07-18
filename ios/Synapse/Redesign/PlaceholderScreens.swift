import SwiftUI

/// Chat placeholder (Fase A). Built from the design system so the tab is real and
/// demoable; Fase B wires streaming chat with cited refs (F6/F7).
struct ChatScreen: View {
    var body: some View {
        SynEmptyState(
            systemImage: "bubble.left.and.text.bubble.right.fill",
            title: "Chat arrives in Fase B",
            eyebrow: "Coming next",
            message: "Multi-conversation chat with streaming answers, collapsible reasoning, and [n] citations back into the wiki.",
            actionTitle: "See the plan",
            action: {})
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .synScreenBackground()
        .navigationTitle("Chat")
        .navigationBarTitleDisplayMode(.large)
    }
}

/// Graph placeholder (Fase A). Per ADR-0088 the renderer is a documented spike:
/// both the WKWebView sigma embed and a native Canvas/SpriteKit renderer consume
/// the server-side FA2-precomputed coords from `GET /graph` (I2 holds either way).
/// The native renderer is recommended, pending an on-device performance check.
struct GraphScreen: View {
    var body: some View {
        ScrollView {
            VStack(spacing: SynSpace.x6) {
                SynEmptyState(
                    systemImage: "point.3.connected.trianglepath.dotted",
                    title: "Knowledge graph arrives in Fase B",
                    eyebrow: "Coming next",
                    message: "Renders the server-side FA2 layout (coords stay precomputed — invariant I2). Native renderer vs WKWebView embed is decided by an on-device perf check.")
                SynCard(padding: SynSpace.x5) {
                    SynSectionHeader(text: "Render approach — pending sign-off")
                    VStack(alignment: .leading, spacing: SynSpace.x3) {
                        bullet("Native Canvas/SpriteKit consuming GET /graph coords — recommended")
                        bullet("WKWebView sigma.js embed — I2-safe fallback behind a flag")
                        bullet("Both need fps / memory / gesture numbers on a real device")
                    }
                    .padding(.top, SynSpace.x3)
                }
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x5)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .synScreenBackground()
        .navigationTitle("Graph")
        .navigationBarTitleDisplayMode(.large)
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: SynSpace.x3) {
            Image(systemName: "circle.fill").font(.system(size: 5)).foregroundStyle(SynColor.accent)
                .padding(.top, 6)
            Text(text).font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
            Spacer(minLength: 0)
        }
    }
}

/// More placeholder (Fase A) — the settings / secondary destinations hub.
struct MoreScreen: View {
    private let rows: [(String, String, String)] = [
        ("Provider", "cpu", "Local · API · CLI (F17)"),
        ("Server", "server.rack", "Connection & auth"),
        ("Review queue", "tray.full.fill", "5 pending suggestions"),
        ("Ingest", "arrow.down.doc.fill", "Add sources to the vault"),
        ("Deep research", "magnifyingglass", "SearXNG multi-query loop"),
        ("Appearance", "circle.lefthalf.filled", "Theme & text size"),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SynSpace.x4) {
                SynSectionHeader(text: "Settings & operations")
                SynCard(padding: SynSpace.x5) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { idx, row in
                        HStack(spacing: SynSpace.x5) {
                            Image(systemName: row.1)
                                .font(.callout.weight(.semibold))
                                .foregroundStyle(SynColor.accent)
                                .frame(width: 34, height: 34)
                                .background(SynColor.accentSoft)
                                .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
                            VStack(alignment: .leading, spacing: 1) {
                                Text(row.0).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                                Text(row.2).font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                            }
                            Spacer(minLength: SynSpace.x3)
                            Image(systemName: "chevron.right")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(SynColor.textDim)
                        }
                        .padding(.vertical, SynSpace.x3)
                        if idx < rows.count - 1 { SynRowDivider(leadingInset: 46) }
                    }
                }
                Text("Fase A shows the redesigned shell. These destinations become live in Fase B.")
                    .font(SynFont.caption)
                    .foregroundStyle(SynColor.textDim)
                    .padding(.horizontal, SynSpace.x2)
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x5)
        }
        .synScreenBackground()
        .navigationTitle("More")
        .navigationBarTitleDisplayMode(.large)
    }
}

#Preview("Chat — light") { NavigationStack { ChatScreen() } }
#Preview("Graph — dark") { NavigationStack { GraphScreen() }.preferredColorScheme(.dark) }
#Preview("More — light") { NavigationStack { MoreScreen() } }
