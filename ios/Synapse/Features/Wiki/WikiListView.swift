import SwiftUI

struct WikiListView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var app: AppModel

    @State private var stats: StatsOverview?
    @State private var pages: [PageSummary] = []
    @State private var loadError: String?
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                LargeHeader(title: "Wiki", eyebrow: "Synapse") {
                    HStack(spacing: 8) {
                        ThemeToggleButton()
                        NavigationLink(value: MoreRoute.ingest) {
                            Image(systemName: "plus")
                                .font(.system(size: 17, weight: .bold))
                                .foregroundStyle(.white)
                                .frame(width: 38, height: 38)
                                .background(Theme.tint)
                                .clipShape(Circle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.top, 8)

                statStrip

                if let loadError {
                    ErrorState(message: loadError) { Task { await load() } }
                } else if isLoading && pages.isEmpty {
                    LoadingState()
                } else if pages.isEmpty {
                    EmptyState(
                        systemImage: "tray",
                        title: "Nessuna pagina",
                        message: "Importa documenti per iniziare a costruire il tuo wiki.")
                } else {
                    recentSection
                    allSection
                }
            }
            .padding(.bottom, 24)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: MoreRoute.self) { MoreRoute.destination($0) }
        .refreshable { await load() }
        .task(id: settings.serverURLString) { await load() }
    }

    // MARK: Stat strip

    private var statStrip: some View {
        HStack(spacing: 10) {
            StatCard(value: stats.map { compact($0.pagesTotal) } ?? "—", label: "Pagine")
            StatCard(value: stats.map { compact($0.linksTotal) } ?? "—", label: "Collegamenti")
            NavigationLink(value: MoreRoute.review) {
                StatCard(
                    value: "\(app.reviewCount)",
                    label: "Da rivedere",
                    valueColor: Theme.tint)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 20)
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    // MARK: Recent

    private var recentSection: some View {
        Group {
            SectionHeader(text: "Aggiornate di recente")
            Card {
                let recent = Array(pages.prefix(3))
                ForEach(Array(recent.enumerated()), id: \.element.id) { idx, p in
                    NavigationLink(value: p.pageRef) {
                        PageRow(
                            title: p.displayTitle,
                            subtitle: subtitle(for: p),
                            type: p.type,
                            dotSize: 34
                        ) { DisclosureChevron() }
                    }
                    .buttonStyle(.plain)
                    if idx < recent.count - 1 { RowDivider() }
                }
            }
            .padding(.horizontal, 16)
        }
    }

    private var allSection: some View {
        Group {
            SectionHeader(text: "Tutte le pagine")
            Card {
                ForEach(Array(pages.enumerated()), id: \.element.id) { idx, p in
                    NavigationLink(value: p.pageRef) {
                        PageRow(title: p.displayTitle, type: p.type) {
                            TypePill(type: p.type)
                        }
                    }
                    .buttonStyle(.plain)
                    if idx < pages.count - 1 { RowDivider() }
                }
            }
            .padding(.horizontal, 16)
        }
    }

    // MARK: Data

    private func load() async {
        guard let client = settings.makeClient() else {
            loadError = APIError.notConfigured.errorDescription
            return
        }
        isLoading = true
        loadError = nil
        await app.refresh(settings)
        do {
            async let statsCall = try? client.statsOverview()
            let list = try await client.pages(limit: 200)
            stats = await statsCall
            pages = list.items
        } catch {
            loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        isLoading = false
    }

    private func subtitle(for p: PageSummary) -> String {
        let type = Theme.label(forType: p.type)
        if let d = p.updatedAt { return "\(type) · \(RelativeDate.string(d))" }
        return type
    }

    private func compact(_ n: Int) -> String {
        if n >= 1000 {
            let v = Double(n) / 1000
            return String(format: v >= 10 ? "%.0fk" : "%.1fk", v)
        }
        return "\(n)"
    }
}

private struct StatCard: View {
    let value: String
    let label: String
    var valueColor: Color = Theme.label
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.system(size: 24, weight: .bold))
                .foregroundStyle(valueColor)
            Text(label)
                .font(.system(size: 12))
                .foregroundStyle(Theme.label2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

/// Cycles system → light → dark, mirroring the design's sun/moon toggle.
struct ThemeToggleButton: View {
    @EnvironmentObject private var settings: AppSettings
    var size: CGFloat = 38
    var body: some View {
        RoundHeaderButton(systemImage: glyph, size: size) {
            switch settings.appearance {
            case .system: settings.appearance = .light
            case .light: settings.appearance = .dark
            case .dark: settings.appearance = .system
            }
        }
    }
    private var glyph: String {
        switch settings.appearance {
        case .system: return "circle.lefthalf.filled"
        case .light: return "moon"
        case .dark: return "sun.max"
        }
    }
}

extension PageSummary {
    var pageRef: PageRef { PageRef(id: id, title: title, type: type) }
}

enum RelativeDate {
    static func string(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.locale = Locale(identifier: "it_IT")
        f.unitsStyle = .full
        return f.localizedString(for: date, relativeTo: Date())
    }
}
