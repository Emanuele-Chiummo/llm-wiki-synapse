import Observation
import SwiftUI

/// Home dashboard view model — loads `/stats/overview` and re-loads live when
/// the SSE `data_version` bumps (I3-friendly: one fetch on change, not polling).
@Observable
@MainActor
final class HomeModel {
    var state: LoadState<API.StatsOverview> = .idle

    /// Section cards, ordered like the desktop, derived from `pages_by_type`.
    static let typeOrder = ["concept", "entity", "source", "synthesis", "comparison", "query"]

    func load(_ session: SynapseSession, force: Bool = false) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured")
            return
        }
        if case .loaded = state, !force {} else if !force { state = .loading }
        do {
            let stats = try await client.statsOverview()
            state = .loaded(stats)
        } catch {
            // Keep showing stale data on a background refresh failure.
            if state.value == nil {
                state = .failed((error as? SynAPIError)?.errorDescription
                                ?? error.localizedDescription)
            }
        }
    }
}

/// Home dashboard (F18 lineage) — the redesign landing surface, now API-backed:
/// live stats, section composition, recently-updated pages and the review-queue
/// count, refreshing on the SSE push channel instead of static mock numbers.
struct HomeScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = HomeModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SynSpace.x7) {
                switch model.state {
                case .idle, .loading where model.state.value == nil:
                    skeleton
                case .failed(let message):
                    SynErrorState(message: message) { Task { await model.load(session, force: true) } }
                default:
                    if let stats = model.state.value { content(stats) }
                }
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x6)
        }
        .synScreenBackground()
        .navigationTitle("Home")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await model.load(session, force: true) }
        .task { await model.load(session) }
        // Live: any data_version bump re-pulls stats (SSE-driven, no poll loop).
        .onChange(of: session.dataVersion) { _, _ in
            Task { await model.load(session, force: true) }
        }
    }

    // MARK: Loaded content

    @ViewBuilder private func content(_ stats: API.StatsOverview) -> some View {
        hero(stats)
        domainsGrid(stats)
        recentSection(stats)
        if !session.streamHealthy { reconnectingNotice }
    }

    private func hero(_ stats: API.StatsOverview) -> some View {
        VStack(alignment: .leading, spacing: SynSpace.x5) {
            HStack(spacing: SynSpace.x2) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.footnote.weight(.bold))
                Text("SYNAPSE").font(SynFont.eyebrow).tracking(1.2)
                Spacer()
                liveDot
            }
            .foregroundStyle(SynColor.onAccent.opacity(0.9))

            Text(session.vaultID)
                .font(SynFont.largeTitle)
                .foregroundStyle(SynColor.onAccent)

            HStack(spacing: SynSpace.x8) {
                heroStat("\(stats.pagesTotal)", "Pages")
                heroStat("\(stats.pagesByType["source"] ?? 0)", "Sources")
                heroStat("\(stats.linksTotal)", "Links")
            }
        }
        .padding(SynSpace.x7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SynColor.signatureGradient)
        .clipShape(RoundedRectangle(cornerRadius: SynRadius.xl, style: .continuous))
        .shadow(color: SynColor.accent.opacity(0.28), radius: 20, x: 0, y: 10)
    }

    private var liveDot: some View {
        HStack(spacing: SynSpace.x1) {
            Circle()
                .fill(session.streamHealthy ? SynColor.onAccent : SynColor.onAccent.opacity(0.4))
                .frame(width: 7, height: 7)
            Text(session.streamHealthy ? "Live" : "Reconnecting")
                .font(SynFont.eyebrow)
        }
        .foregroundStyle(SynColor.onAccent.opacity(0.9))
    }

    private func heroStat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title2.weight(.bold)).foregroundStyle(SynColor.onAccent)
                .contentTransition(.numericText())
            Text(label).font(SynFont.caption).foregroundStyle(SynColor.onAccent.opacity(0.8))
        }
    }

    private func domainsGrid(_ stats: API.StatsOverview) -> some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            SynSectionHeader(text: "Sections")
            LazyVGrid(
                columns: [GridItem(.flexible(), spacing: SynSpace.x4),
                          GridItem(.flexible(), spacing: SynSpace.x4)],
                spacing: SynSpace.x4
            ) {
                ForEach(orderedTypes(stats.pagesByType), id: \.0) { type, count in
                    domainCard(type: type, count: count)
                }
            }
        }
    }

    /// Proper English plural for a section label (no naive "+s": entity→Entities).
    static func pluralLabel(for type: String) -> String {
        let plurals = [
            "concept": "Concepts", "entity": "Entities", "source": "Sources",
            "synthesis": "Syntheses", "comparison": "Comparisons", "query": "Queries",
            "overview": "Overviews", "index": "Index", "log": "Log", "other": "Other",
        ]
        if let p = plurals[type.lowercased()] { return p }
        return SynColor.label(forType: type) + "s"
    }

    private func orderedTypes(_ byType: [String: Int]) -> [(String, Int)] {
        var seen = Set<String>()
        var out: [(String, Int)] = []
        for t in HomeModel.typeOrder where (byType[t] ?? 0) > 0 {
            out.append((t, byType[t] ?? 0)); seen.insert(t)
        }
        for (t, c) in byType.sorted(by: { $0.value > $1.value }) where !seen.contains(t) && c > 0 {
            out.append((t, c))
        }
        return out
    }

    private func domainCard(type: String, count: Int) -> some View {
        SynCard(padding: SynSpace.x5) {
            HStack(spacing: SynSpace.x4) {
                SynTypeGlyph(type: type, size: 38)
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(count)")
                        .font(.title3.weight(.bold))
                        .foregroundStyle(SynColor.text)
                        .contentTransition(.numericText())
                    Text(HomeScreen.pluralLabel(for: type))
                        .font(SynFont.caption)
                        .foregroundStyle(SynColor.textMuted)
                }
                Spacer(minLength: 0)
            }
        }
    }

    private func recentSection(_ stats: API.StatsOverview) -> some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            HStack {
                SynSectionHeader(text: "Recently updated")
                Spacer()
                if session.reviewPending > 0 {
                    SynChip(text: "\(session.reviewPending) in review",
                            systemImage: "tray.full.fill")
                }
            }
            if stats.recentActivity.isEmpty {
                SynCard(padding: SynSpace.x5) {
                    SynEmptyState(
                        systemImage: "doc.text",
                        title: "No pages yet",
                        message: "Ingest a source to start growing the vault.")
                }
            } else {
                SynCard(padding: SynSpace.x5) {
                    ForEach(Array(stats.recentActivity.enumerated()), id: \.element.id) { idx, act in
                        NavigationLink(value: WikiRoute.page(id: act.pageID, title: act.displayTitle)) {
                            SynListRow(title: act.displayTitle,
                                       subtitle: relativeTime(act.updatedAt),
                                       type: nil)
                        }
                        .buttonStyle(.plain)
                        if idx < stats.recentActivity.count - 1 { SynRowDivider() }
                    }
                }
            }
        }
    }

    private var reconnectingNotice: some View {
        HStack(spacing: SynSpace.x3) {
            Image(systemName: "wifi.exclamationmark").foregroundStyle(SynColor.amber)
            Text("Live updates paused — reconnecting to the server.")
                .font(SynFont.caption).foregroundStyle(SynColor.textMuted)
            Spacer(minLength: 0)
        }
        .padding(SynSpace.x4)
        .background(SynColor.amber.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
    }

    private var skeleton: some View {
        VStack(alignment: .leading, spacing: SynSpace.x6) {
            SynSkeleton(cornerRadius: SynRadius.xl).frame(height: 150)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: SynSpace.x4) {
                ForEach(0..<4, id: \.self) { _ in
                    SynSkeleton(cornerRadius: SynRadius.lg).frame(height: 64)
                }
            }
            SynSkeleton(cornerRadius: SynRadius.lg).frame(height: 180)
        }
    }

    private func relativeTime(_ date: Date?) -> String? {
        guard let date else { return nil }
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: date, relativeTo: Date())
    }
}

/// Navigation payload pushed inside the Home / Wiki stack: a reading view
/// (from Home's recent list, the Wiki list, a citation or a wikilink tap) or the
/// Search surface (from the Wiki toolbar), both resolved by `WikiStack`.
enum WikiRoute: Hashable {
    case page(id: String, title: String?)
    case search

    var pageID: String { if case .page(let id, _) = self { return id }; return "" }
    var title: String? { if case .page(_, let t) = self { return t }; return nil }
}
