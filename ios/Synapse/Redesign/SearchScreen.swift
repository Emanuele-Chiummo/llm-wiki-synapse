import Observation
import SwiftUI

@Observable
@MainActor
final class SearchModel {
    var query = ""
    var selectedType: String?
    var state: LoadState<[API.SearchResult]> = .idle
    /// Token budget / approx tokens surfaced from the last response (F14 lineage).
    var approxTokens: Int?
    var tokenBudget: Int?

    private var inFlight: Task<Void, Never>?

    func runSearch(_ session: SynapseSession) {
        inFlight?.cancel()
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard q.count >= 1 else { state = .idle; return }
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured")
            return
        }
        state = .loading
        let type = selectedType
        inFlight = Task {
            do {
                let resp = try await client.search(q, type: type, k: 20)
                if Task.isCancelled { return }
                approxTokens = resp.approxTokens
                tokenBudget = resp.tokenBudget
                state = .loaded(resp.results)
            } catch {
                if Task.isCancelled { return }
                state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }
}

/// Real search surface — the iOS counterpart to the desktop Cerca/Search view.
/// Hits `GET /search` (the 4-phase retrieval pipeline: tokenized → graph-expand
/// → budget → assemble) and pushes results into the wiki reading stack.
struct SearchScreen: View {
    /// Optional pre-filled query (used by the screenshot harness to capture a
    /// populated results state deterministically).
    var initialQuery: String? = nil

    @Environment(SynapseSession.self) private var session
    @Environment(WikiNavigator.self) private var navigator
    @State private var model = SearchModel()

    private let types = ["concept", "entity", "source", "synthesis", "comparison", "query"]

    var body: some View {
        VStack(spacing: 0) {
            filterBar
                .padding(.horizontal, SynSpace.x6)
                .padding(.vertical, SynSpace.x3)
            Divider().overlay(SynColor.borderSubtle)
            results
        }
        .synScreenBackground()
        .navigationTitle("Search")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $model.query, placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Search the wiki")
        .onSubmit(of: .search) { model.runSearch(session) }
        .onChange(of: model.selectedType) { _, _ in
            if !model.query.isEmpty { model.runSearch(session) }
        }
        .task {
            if let initialQuery, model.query.isEmpty {
                model.query = initialQuery
                model.runSearch(session)
            }
        }
    }

    @ViewBuilder private var results: some View {
        switch model.state {
        case .idle:
            SynEmptyState(systemImage: "magnifyingglass",
                          title: "Search your knowledge base",
                          message: "Find pages by meaning, not just keywords — the same 4-phase retrieval the desktop uses.")
                .frame(maxHeight: .infinity)
        case .loading where model.state.value == nil:
            VStack(spacing: SynSpace.x3) {
                ForEach(0..<6, id: \.self) { _ in
                    SynSkeletonLine(height: 40).padding(.horizontal, SynSpace.x6)
                }
                Spacer()
            }
            .padding(.top, SynSpace.x5)
        case .failed(let message):
            SynErrorState(message: message) { model.runSearch(session) }
                .frame(maxHeight: .infinity)
        default:
            let items = model.state.value ?? []
            if items.isEmpty {
                SynEmptyState(systemImage: "doc.text.magnifyingglass",
                              title: "No results",
                              message: "Nothing matched “\(model.query)”. Try different words or another type.")
                    .frame(maxHeight: .infinity)
            } else {
                List {
                    ForEach(items) { r in
                        NavigationLink(value: WikiRoute.page(id: r.id, title: r.title)) {
                            resultRow(r)
                        }
                        .listRowInsets(EdgeInsets(top: 4, leading: SynSpace.x6, bottom: 4, trailing: SynSpace.x6))
                        .listRowSeparatorTint(SynColor.borderSubtle)
                        .listRowBackground(Color.clear)
                    }
                    if let budget = model.tokenBudget {
                        Text("\(items.count) result\(items.count == 1 ? "" : "s") · ~\(model.approxTokens ?? 0) / \(budget) context tokens")
                            .font(SynFont.caption)
                            .foregroundStyle(SynColor.textDim)
                            .listRowSeparator(.hidden)
                            .listRowBackground(Color.clear)
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
            }
        }
    }

    private func resultRow(_ r: API.SearchResult) -> some View {
        HStack(spacing: SynSpace.x5) {
            SynTypeGlyph(type: nil, size: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(r.title ?? "Untitled")
                    .font(SynFont.rowTitle).foregroundStyle(SynColor.text).lineLimit(1)
                HStack(spacing: SynSpace.x2) {
                    if let phase = r.phase {
                        Text(phase == "vector" ? "match" : "linked")
                            .font(SynFont.eyebrow)
                            .foregroundStyle(phase == "vector" ? SynColor.accent : SynColor.accent2)
                    }
                    if let score = r.score {
                        Text(String(format: "%.2f", score))
                            .font(SynFont.caption.monospacedDigit())
                            .foregroundStyle(SynColor.textDim)
                    }
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: SynSpace.x3) {
                Button { model.selectedType = nil } label: {
                    SynChip(text: "All", selected: model.selectedType == nil)
                }.buttonStyle(.plain)
                ForEach(types, id: \.self) { t in
                    Button { model.selectedType = (model.selectedType == t ? nil : t) } label: {
                        SynChip(text: SynColor.label(forType: t),
                                pageType: model.selectedType == t ? nil : t,
                                selected: model.selectedType == t)
                    }.buttonStyle(.plain)
                }
            }
        }
    }
}
