import SwiftUI

/// Graph placeholder. Per ADR-0088 the renderer is a documented spike deferred
/// to Fase C: both a native SwiftUI `Canvas` renderer and a WKWebView sigma
/// embed consume the server-side FA2-precomputed coords from `GET /graph`, so
/// **I2 holds either way**. The native-vs-embed choice is gated on an on-device
/// performance check (fps / memory / gestures) that this environment (Simulator
/// only) can't run — so Fase B ships the honest placeholder, not a guess.
struct GraphScreen: View {
    var body: some View {
        ScrollView {
            VStack(spacing: SynSpace.x6) {
                SynEmptyState(
                    systemImage: "point.3.connected.trianglepath.dotted",
                    title: "Knowledge graph arrives in Fase C",
                    eyebrow: "Coming next",
                    message: "Renders the server-side FA2 layout (coords stay precomputed — invariant I2). Native renderer vs WKWebView embed is decided by an on-device perf check.")
                SynCard(padding: SynSpace.x5) {
                    SynSectionHeader(text: "Render approach — pending sign-off")
                    VStack(alignment: .leading, spacing: SynSpace.x3) {
                        bullet("Native SwiftUI Canvas consuming GET /graph coords — recommended")
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

#Preview("Graph — dark") { NavigationStack { GraphScreen() }.preferredColorScheme(.dark) }
