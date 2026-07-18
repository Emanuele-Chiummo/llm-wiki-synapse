import Observation
import SwiftUI

/// Per-stack navigation state so reading views can push further pages
/// programmatically (a `[[wikilink]]` tap can't use a `NavigationLink`). Bound
/// as the `NavigationStack` path; `NavigationLink(value:)` and `push` both feed it.
@Observable
@MainActor
final class WikiNavigator {
    var path: [WikiRoute] = []
    func push(_ route: WikiRoute) { path.append(route) }
}

/// A `NavigationStack` that owns a `WikiNavigator`, registers the single
/// `WikiRoute` reading destination, and injects the navigator so any reading
/// view (Home's recent list, the Wiki list, a wikilink or related tap) can push
/// further pages. Used by the Home and Wiki tabs.
struct WikiStack<Root: View>: View {
    @ViewBuilder var root: () -> Root
    @State private var navigator = WikiNavigator()

    var body: some View {
        NavigationStack(path: $navigator.path) {
            root()
                .navigationDestination(for: WikiRoute.self) { route in
                    switch route {
                    case .page(let id, let title):
                        WikiReadingScreen(pageID: id, title: title)
                    case .search:
                        SearchScreen()
                    }
                }
        }
        .environment(navigator)
    }
}

// MARK: - Wiki list

@Observable
@MainActor
final class WikiListModel {
    var state: LoadState<[API.Page]> = .idle
    var selectedType: String?

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured")
            return
        }
        if state.value == nil { state = .loading }
        do {
            // Server-side type filter avoids over-fetching (FE-PERF-2 parity).
            let list = try await client.pages(type: selectedType, limit: 300)
            state = .loaded(list.items)
        } catch {
            if state.value == nil {
                state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }
}

/// Wiki browsing — a filterable, virtualised list of real `/pages` that pushes
/// to a reading view. Grouping/filtering by type mirrors the desktop NavTree.
struct WikiScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = WikiListModel()

    private let types = ["concept", "entity", "source", "synthesis", "comparison", "query"]

    var body: some View {
        List {
            Section {
                filterBar
                    .listRowInsets(EdgeInsets(top: SynSpace.x2, leading: SynSpace.x6,
                                              bottom: SynSpace.x3, trailing: SynSpace.x6))
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }
            content
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("Wiki")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink(value: WikiRoute.search) {
                    Image(systemName: "magnifyingglass")
                }
                .accessibilityLabel("Search the wiki")
            }
        }
        .refreshable { await model.load(session) }
        .task(id: model.selectedType) { await model.load(session) }
        .onChange(of: session.dataVersion) { _, _ in Task { await model.load(session) } }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading where model.state.value == nil:
            ForEach(0..<8, id: \.self) { _ in
                SynSkeletonLine(height: 44)
                    .listRowSeparator(.hidden).listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 4, leading: SynSpace.x6, bottom: 4, trailing: SynSpace.x6))
            }
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
                .listRowSeparator(.hidden).listRowBackground(Color.clear)
        default:
            let pages = model.state.value ?? []
            if pages.isEmpty {
                SynEmptyState(systemImage: "doc.text.magnifyingglass",
                              title: "No pages of this type",
                              message: "Try another section, or ingest a source to grow the vault.")
                    .listRowSeparator(.hidden).listRowBackground(Color.clear)
            } else {
                // SwiftUI List lazily realises rows — the virtualisation the
                // Fase B brief requires (I4 parity), no eager render of 300 rows.
                ForEach(pages) { page in
                    NavigationLink(value: WikiRoute.page(id: page.id, title: page.displayTitle)) {
                        SynListRow(title: page.displayTitle,
                                   subtitle: subtitle(for: page),
                                   type: page.type, showsChevron: false)
                    }
                    .listRowInsets(EdgeInsets(top: 2, leading: SynSpace.x6, bottom: 2, trailing: SynSpace.x6))
                    .listRowSeparatorTint(SynColor.borderSubtle)
                    .listRowBackground(Color.clear)
                }
            }
        }
    }

    private func subtitle(for page: API.Page) -> String? {
        var bits: [String] = [SynColor.label(forType: page.type)]
        if let n = page.sources?.count, n > 0 { bits.append("\(n) source\(n == 1 ? "" : "s")") }
        return bits.joined(separator: " · ")
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
            .padding(.horizontal, 2)
        }
    }
}

// MARK: - Wiki reading

@Observable
@MainActor
final class WikiReadingModel {
    var content: LoadState<API.PageContent> = .idle
    var related: [API.RelatedPage] = []

    func load(_ session: SynapseSession, id: String) async {
        guard let client = session.client() else {
            content = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured")
            return
        }
        content = .loading
        do {
            async let page = client.pageContent(id: id)
            async let rel = client.relatedPages(id: id, limit: 8)
            let (c, r) = try await (page, rel)
            content = .loaded(c)
            related = r.items
        } catch {
            content = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
        }
    }

    /// Resolve a tapped `[[wikilink]]` slug to a page id and push it.
    func openWikilink(slug: String, session: SynapseSession, navigator: WikiNavigator) async {
        guard let client = session.client() else { return }
        do {
            let page = try await client.pageBySlug(slug)
            navigator.push(.page(id: page.id, title: page.displayTitle))
        } catch {
            // Fallback: a fuzzy search, push the top hit if any (dead links stay inert).
            if let hit = try? await client.search(slug.replacingOccurrences(of: "-", with: " "), k: 1).results.first {
                navigator.push(.page(id: hit.id, title: hit.title))
            }
        }
    }
}

/// Real wiki reading view — markdown body (headings/lists/code/quote), tappable
/// `[[wikilinks]]` (K5), inline/block LaTeX, sources and related pages.
struct WikiReadingScreen: View {
    let pageID: String
    var title: String?

    @Environment(SynapseSession.self) private var session
    @Environment(WikiNavigator.self) private var navigator
    @State private var model = WikiReadingModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SynSpace.x6) {
                switch model.content {
                case .idle, .loading:
                    readingSkeleton
                case .failed(let message):
                    SynErrorState(message: message) {
                        Task { await model.load(session, id: pageID) }
                    }
                case .loaded(let page):
                    headerCard(page)
                    MarkdownView(page.content.isEmpty
                                 ? "_This page has no content yet._" : page.content)
                    if let sources = page.sources, !sources.isEmpty { sourcesCard(sources) }
                    if !model.related.isEmpty { relatedCard }
                }
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x5)
        }
        .synScreenBackground(false)
        .navigationTitle(model.content.value?.title ?? title ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.load(session, id: pageID) }
        // Intercept wikilink taps and navigate instead of opening a browser.
        .environment(\.openURL, OpenURLAction { url in
            guard url.scheme == WikiMarkdownBlock.wikilinkScheme else { return .systemAction }
            let slug = url.host ?? url.absoluteString
                .replacingOccurrences(of: "\(WikiMarkdownBlock.wikilinkScheme)://", with: "")
            Task { await model.openWikilink(slug: slug, session: session, navigator: navigator) }
            return .handled
        })
    }

    private func headerCard(_ page: API.PageContent) -> some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            HStack(spacing: SynSpace.x3) {
                SynTypeGlyph(type: page.type, size: 40)
                SynChip(text: SynColor.label(forType: page.type), pageType: page.type)
                Spacer()
            }
            Text(page.title ?? title ?? "Untitled")
                .font(SynFont.largeTitle)
                .foregroundStyle(SynColor.text)
            HStack(spacing: SynSpace.x5) {
                if let n = page.sources?.count, n > 0 {
                    metric("doc.on.doc", "\(n) source\(n == 1 ? "" : "s")")
                }
                if !model.related.isEmpty { metric("link", "\(model.related.count) related") }
                if let d = page.updatedAt { metric("clock", relativeTime(d)) }
            }
        }
    }

    private func metric(_ icon: String, _ text: String) -> some View {
        HStack(spacing: SynSpace.x2) {
            Image(systemName: icon).font(.caption2)
            Text(text).font(SynFont.caption)
        }
        .foregroundStyle(SynColor.textMuted)
    }

    private func sourcesCard(_ sources: [String]) -> some View {
        SynCard(padding: SynSpace.x5) {
            SynSectionHeader(text: "Sources")
            VStack(alignment: .leading, spacing: SynSpace.x3) {
                ForEach(Array(sources.enumerated()), id: \.offset) { idx, src in
                    HStack(spacing: SynSpace.x3) {
                        Text("[\(idx + 1)]")
                            .font(SynFont.caption.monospacedDigit())
                            .foregroundStyle(SynColor.accent)
                        Text(src).font(SynFont.subhead).foregroundStyle(SynColor.text)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                }
            }
            .padding(.top, SynSpace.x3)
        }
    }

    private var relatedCard: some View {
        VStack(alignment: .leading, spacing: SynSpace.x3) {
            SynSectionHeader(text: "Related")
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: SynSpace.x3) {
                    ForEach(model.related) { rel in
                        Button {
                            navigator.push(.page(id: rel.pageID, title: rel.title))
                        } label: {
                            HStack(spacing: SynSpace.x3) {
                                SynTypeGlyph(type: rel.type, size: 28)
                                Text(rel.title ?? "Untitled")
                                    .font(SynFont.subhead)
                                    .foregroundStyle(SynColor.text)
                                    .lineLimit(1)
                            }
                            .padding(.horizontal, SynSpace.x4)
                            .padding(.vertical, SynSpace.x3)
                            .background(SynColor.surface)
                            .overlay(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous)
                                .strokeBorder(SynColor.border, lineWidth: 1))
                            .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 2)
            }
        }
    }

    private var readingSkeleton: some View {
        VStack(alignment: .leading, spacing: SynSpace.x5) {
            SynSkeletonLine(height: 34, widthFraction: 0.7)
            ForEach(0..<6, id: \.self) { _ in SynSkeletonLine(height: 14) }
            SynSkeletonLine(height: 14, widthFraction: 0.5)
        }
    }

    private func relativeTime(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: date, relativeTo: Date())
    }
}
