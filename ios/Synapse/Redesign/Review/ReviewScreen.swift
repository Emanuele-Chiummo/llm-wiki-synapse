import Observation
import SwiftUI

@Observable
@MainActor
final class ReviewModel {
    var state: LoadState<[API.ReviewItem]> = .idle
    /// IDs currently being resolved (so their row shows progress and disables actions).
    var busy: Set<String> = []
    var banner: String?

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if state.value == nil { state = .loading }
        do {
            state = .loaded(try await client.reviewQueue().items)
        } catch {
            if state.value == nil {
                state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }

    enum Action { case create, deepResearch, skip }

    func resolve(_ session: SynapseSession, item: API.ReviewItem, action: Action) async {
        guard let client = session.client(), !busy.contains(item.id) else { return }
        busy.insert(item.id)
        defer { busy.remove(item.id) }
        do {
            switch action {
            case .create: try await client.reviewCreate(itemID: item.id)
            case .deepResearch: try await client.reviewDeepResearch(itemID: item.id)
            case .skip: try await client.reviewSkip(itemID: item.id)
            }
            // Optimistically drop the resolved row, then reconcile with the server.
            if var items = state.value {
                items.removeAll { $0.id == item.id }
                state = .loaded(items)
            }
            banner = bannerText(action, title: item.displayTitle)
            await load(session)
        } catch {
            banner = (error as? SynAPIError)?.errorDescription ?? error.localizedDescription
        }
    }

    private func bannerText(_ action: Action, title: String) -> String {
        switch action {
        case .create: return "Created “\(title)”."
        case .deepResearch: return "Deep Research started for “\(title)”. Track it in Activity."
        case .skip: return "Skipped “\(title)”."
        }
    }
}

/// The HITL review queue (F9). Each proposal offers the three canonical actions —
/// Create · Deep-Research · Skip — via native swipe gestures (the iOS-idiomatic
/// choice for list rows) AND explicit buttons in the expanded detail, so the
/// actions are discoverable, not hidden behind a swipe. Live review-pending count
/// drives the More-tab badge; the list refreshes on the SSE data_version bump.
struct ReviewScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = ReviewModel()
    @State private var detail: API.ReviewItem?

    var body: some View {
        List {
            if let banner = model.banner {
                Section {
                    Label(banner, systemImage: "checkmark.circle.fill")
                        .font(SynFont.caption).foregroundStyle(SynColor.green)
                        .listRowBackground(Color.clear)
                }
            }
            content
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("Review")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await model.load(session) }
        .task { await model.load(session) }
        .onChange(of: session.dataVersion) { _, _ in Task { await model.load(session) } }
        .sheet(item: $detail) { item in
            ReviewDetailSheet(item: item) { action in
                Task { await model.resolve(session, item: item, action: action) }
                detail = nil
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading where model.state.value == nil:
            ForEach(0..<6, id: \.self) { _ in
                SynSkeletonLine(height: 52)
                    .listRowSeparator(.hidden).listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 4, leading: SynSpace.x6, bottom: 4, trailing: SynSpace.x6))
            }
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
                .listRowSeparator(.hidden).listRowBackground(Color.clear)
        default:
            let items = model.state.value ?? []
            if items.isEmpty {
                SynEmptyState(
                    systemImage: "checklist",
                    title: "Review queue is clear",
                    eyebrow: "All caught up",
                    message: "Proposals from ingest and chat land here for you to Create, Deep-Research, or Skip.")
                    .listRowSeparator(.hidden).listRowBackground(Color.clear)
            } else {
                ForEach(items) { item in row(item) }
            }
        }
    }

    private func row(_ item: API.ReviewItem) -> some View {
        Button { detail = item } label: { ReviewRow(item: item, busy: model.busy.contains(item.id)) }
            .buttonStyle(.plain)
            .listRowInsets(EdgeInsets(top: 4, leading: SynSpace.x6, bottom: 4, trailing: SynSpace.x6))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
            // Leading swipe — the constructive actions.
            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                Button { Task { await model.resolve(session, item: item, action: .create) } }
                    label: { Label("Create", systemImage: "checkmark") }
                    .tint(SynColor.green)
                Button { Task { await model.resolve(session, item: item, action: .deepResearch) } }
                    label: { Label("Research", systemImage: "sparkle.magnifyingglass") }
                    .tint(SynColor.accent)
            }
            // Trailing swipe — dismiss.
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                Button(role: .destructive) {
                    Task { await model.resolve(session, item: item, action: .skip) }
                } label: { Label("Skip", systemImage: "xmark") }
            }
    }
}

// MARK: - Row

private struct ReviewRow: View {
    let item: API.ReviewItem
    let busy: Bool

    var body: some View {
        SynCard(padding: SynSpace.x5) {
            HStack(alignment: .top, spacing: SynSpace.x4) {
                SynTypeGlyph(type: item.proposedPageType, size: 38)
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.displayTitle).font(SynFont.rowTitle)
                        .foregroundStyle(SynColor.text).lineLimit(2)
                    HStack(spacing: SynSpace.x2) {
                        SynChip(text: SynColor.label(forType: item.proposedPageType),
                                pageType: item.proposedPageType)
                        SynChip(text: originLabel, systemImage: originIcon)
                    }
                    if let r = item.rationale, !r.isEmpty {
                        Text(r).font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 0)
                if busy { ProgressView() }
                else {
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold))
                        .foregroundStyle(SynColor.textDim)
                }
            }
            Text("Swipe → Create / Research · ← Skip")
                .font(SynFont.eyebrow).foregroundStyle(SynColor.textDim)
                .padding(.top, SynSpace.x3)
        }
    }

    private var originLabel: String {
        switch item.proposalOrigin {
        case "chat": return "from chat"
        case "ingest": return "from ingest"
        case "lint": return "from lint"
        default: return item.proposalOrigin
        }
    }
    private var originIcon: String {
        switch item.proposalOrigin {
        case "chat": return "bubble.left"
        case "ingest": return "tray.and.arrow.down"
        case "lint": return "checkmark.seal"
        default: return "sparkles"
        }
    }
}

// MARK: - Detail sheet (explicit buttons)

private struct ReviewDetailSheet: View {
    let item: API.ReviewItem
    var onAction: (ReviewModel.Action) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: SynSpace.x6) {
                    HStack(spacing: SynSpace.x4) {
                        SynTypeGlyph(type: item.proposedPageType, size: 44)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.displayTitle).font(SynFont.title)
                                .foregroundStyle(SynColor.text)
                            SynChip(text: SynColor.label(forType: item.proposedPageType),
                                    pageType: item.proposedPageType)
                        }
                    }
                    if let r = item.rationale, !r.isEmpty {
                        SynCard(padding: SynSpace.x5) {
                            SynSectionHeader(text: "Why this was proposed")
                            Text(r).font(SynFont.subhead).foregroundStyle(SynColor.text)
                                .padding(.top, SynSpace.x3)
                        }
                    }
                    if let queries = item.searchQueries, !queries.isEmpty {
                        SynCard(padding: SynSpace.x5) {
                            SynSectionHeader(text: "Pre-generated research queries")
                            VStack(alignment: .leading, spacing: SynSpace.x2) {
                                ForEach(Array(queries.enumerated()), id: \.offset) { _, qy in
                                    Label(qy, systemImage: "magnifyingglass")
                                        .font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                                }
                            }
                            .padding(.top, SynSpace.x3)
                        }
                    }
                    VStack(spacing: SynSpace.x3) {
                        SynButton(title: "Create page", systemImage: "checkmark",
                                  kind: .primary, fullWidth: true) { onAction(.create) }
                        SynButton(title: "Deep-Research & ingest", systemImage: "sparkle.magnifyingglass",
                                  kind: .secondary, fullWidth: true) { onAction(.deepResearch) }
                        SynButton(title: "Skip", systemImage: "xmark",
                                  kind: .ghost, fullWidth: true) { onAction(.skip) }
                    }
                }
                .padding(SynSpace.x6)
            }
            .synScreenBackground()
            .navigationTitle("Proposal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Close") { dismiss() } } }
        }
    }
}
