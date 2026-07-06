import SwiftUI

/// Routes reachable from the "Altro" menu and via deep links from other screens.
enum MoreRoute: Hashable {
    case research
    case review
    case ingest
    case settings
    case graphFocus(String)   // focus the graph on a page id
    case askAbout(String)     // open chat pre-seeded with a question about a title

    @ViewBuilder
    static func destination(_ route: MoreRoute) -> some View {
        switch route {
        case .research: ResearchView()
        case .review: ReviewView()
        case .ingest: IngestView()
        case .settings: SettingsView()
        case .graphFocus(let id): GraphView(focusPageID: id)
        case .askAbout(let title): ChatView(seedQuestion: "Riassumi la pagina \"\(title)\" con citazioni")
        }
    }
}

struct MoreView: View {
    @EnvironmentObject private var app: AppModel

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                LargeHeader(title: "Altro") { ThemeToggleButton() }
                    .padding(.top, 8)

                Card {
                    row(.research, icon: "sparkle.magnifyingglass", color: Color(hex: 0x0EA5E9),
                        title: "Deep research", subtitle: "Ricerca web agentica, con citazioni")
                    RowDivider()
                    row(.review, icon: "checkmark.seal", color: Color(hex: 0xA855F7),
                        title: "Coda di revisione", subtitle: "Proposte AI da approvare",
                        badge: app.reviewCount)
                    RowDivider()
                    row(.ingest, icon: "square.and.arrow.down", color: Color(hex: 0xF59E0B),
                        title: "Importa documenti", subtitle: "PDF, DOCX, Markdown, web clipper")
                    RowDivider()
                    row(.settings, icon: "gearshape", color: Color(hex: 0x8E8E93),
                        title: "Impostazioni", subtitle: "Provider AI, aspetto, vault")
                }
                .padding(.horizontal, 16)
                .padding(.top, 14)

                Text(footer)
                    .font(.system(size: 13))
                    .foregroundStyle(Theme.label3)
                    .padding(.vertical, 24)
            }
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: MoreRoute.self) { MoreRoute.destination($0) }
    }

    private var footer: String {
        let v = app.serverVersion.map { "v\($0)" } ?? "non connesso"
        return "Synapse · \(v)"
    }

    @ViewBuilder
    private func row(
        _ route: MoreRoute, icon: String, color: Color, title: String, subtitle: String,
        badge: Int = 0
    ) -> some View {
        NavigationLink(value: route) {
            HStack(spacing: 13) {
                Image(systemName: icon)
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(color)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.system(size: 17)).foregroundStyle(Theme.label)
                    Text(subtitle).font(.system(size: 13)).foregroundStyle(Theme.label2)
                }
                Spacer()
                if badge > 0 {
                    Text("\(badge)")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6).frame(minWidth: 22, minHeight: 22)
                        .background(Theme.destructive)
                        .clipShape(Capsule())
                }
                DisclosureChevron()
            }
            .padding(14)
        }
        .buttonStyle(.plain)
    }
}
