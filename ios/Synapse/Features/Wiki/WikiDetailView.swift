import SwiftUI

struct WikiDetailView: View {
    let page: PageRef
    @EnvironmentObject private var settings: AppSettings

    @State private var content: PageContent?
    @State private var related: [RelatedPage] = []
    @State private var loadError: String?
    @State private var isLoading = true

    @Environment(\.dismiss) private var dismiss

    private var type: String? { content?.type ?? page.type }
    private var title: String { content?.title ?? page.title ?? "Pagina" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                if let loadError {
                    ErrorState(message: loadError) { Task { await load() } }
                } else if isLoading {
                    LoadingState()
                } else {
                    bodySection
                    if !related.isEmpty { linksSection }
                    graphAndAskCard
                }
            }
            .padding(.bottom, 28)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task { await load() }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Button {
                    dismiss()
                } label: {
                    HStack(spacing: 3) {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 16, weight: .semibold))
                        Text("Indietro").font(.system(size: 16))
                    }
                    .foregroundStyle(Theme.tint)
                }
                .buttonStyle(.plain)
                Spacer()
                ThemeToggleButton(size: 34)
            }
            .padding(.horizontal, 12)
            .padding(.top, 8)

            VStack(alignment: .leading, spacing: 10) {
                TypePill(type: type)
                Text(title)
                    .font(.system(size: 30, weight: .bold))
                    .foregroundStyle(Theme.label)
                    .fixedSize(horizontal: false, vertical: true)
                if let meta = metaLine {
                    Text(meta)
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.label2)
                }
            }
            .padding(.horizontal, 22)
            .padding(.top, 6)
        }
    }

    private var metaLine: String? {
        var parts: [String] = []
        if let d = content?.updatedAt { parts.append("Aggiornata \(RelativeDate.string(d))") }
        if !related.isEmpty { parts.append("\(related.count) collegamenti") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: Body

    private var bodySection: some View {
        MarkdownBodyView(blocks: Markdown.blocks(from: content?.content ?? ""))
            .padding(.horizontal, 22)
            .padding(.top, 12)
    }

    // MARK: Links (related pages)

    private var linksSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Collegamenti")
            FlowLayout(spacing: 8) {
                ForEach(related) { r in
                    NavigationLink(value: PageRef(id: r.pageID, title: r.title, type: r.type)) {
                        HStack(spacing: 7) {
                            Circle().fill(Theme.color(forType: r.type)).frame(width: 8, height: 8)
                            Text(r.title ?? "Pagina")
                                .font(.system(size: 15, weight: .medium))
                                .foregroundStyle(Theme.tint)
                                .lineLimit(1)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Theme.card)
                        .overlay(
                            RoundedRectangle(cornerRadius: 11, style: .continuous)
                                .stroke(Theme.separator, lineWidth: 0.5))
                        .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 20)
        }
    }

    // MARK: Local graph preview + ask chat

    private var graphAndAskCard: some View {
        Card {
            NavigationLink(value: MoreRoute.graphFocus(page.id)) {
                MiniGraphPreview(centerType: type, neighbors: related)
            }
            .buttonStyle(.plain)

            RowDivider()

            NavigationLink(value: MoreRoute.askAbout(title)) {
                HStack(spacing: 10) {
                    Image(systemName: "bubble.left.and.text.bubble.right.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(.white)
                        .frame(width: 32, height: 32)
                        .background(Theme.signatureGradient)
                        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Chiedi alla chat")
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(Theme.label)
                        Text("Interroga questa pagina con citazioni")
                            .font(.system(size: 13))
                            .foregroundStyle(Theme.label2)
                    }
                    Spacer()
                    DisclosureChevron()
                }
                .padding(14)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.top, 20)
        .navigationDestination(for: MoreRoute.self) { MoreRoute.destination($0) }
    }

    // MARK: Data

    private func load() async {
        guard let client = settings.makeClient() else {
            loadError = APIError.notConfigured.errorDescription
            isLoading = false
            return
        }
        isLoading = true
        loadError = nil
        do {
            async let rel = try? client.relatedPages(id: page.id)
            let c = try await client.pageContent(id: page.id)
            content = c
            related = (await rel)?.items ?? []
        } catch {
            loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        isLoading = false
    }
}

/// A small non-interactive radial preview of a page's neighbourhood, echoing the
/// design's "local graph" card.
struct MiniGraphPreview: View {
    let centerType: String?
    let neighbors: [RelatedPage]

    var body: some View {
        ZStack {
            Theme.graphBackground
            GeometryReader { geo in
                let c = CGPoint(x: geo.size.width / 2, y: geo.size.height / 2)
                let shown = Array(neighbors.prefix(5))
                let r = min(geo.size.width, geo.size.height) * 0.34
                ZStack {
                    ForEach(Array(shown.enumerated()), id: \.element.id) { idx, n in
                        let a = angle(idx, count: shown.count)
                        let p = CGPoint(x: c.x + cos(a) * r, y: c.y + sin(a) * r * 0.7)
                        Path { $0.move(to: c); $0.addLine(to: p) }
                            .stroke(Theme.separator, lineWidth: 1.5)
                        Circle().fill(Theme.color(forType: n.type))
                            .frame(width: 14, height: 14).position(p)
                    }
                    Circle().fill(Theme.color(forType: centerType))
                        .frame(width: 30, height: 30).position(c)
                }
            }
            VStack {
                Spacer()
                HStack {
                    Text("Vedi nel grafo →")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.label)
                        .padding(.horizontal, 10).padding(.vertical, 4)
                        .background(Theme.barBackground)
                        .clipShape(Capsule())
                    Spacer()
                }
                .padding(10)
            }
        }
        .frame(height: 120)
    }

    private func angle(_ i: Int, count: Int) -> CGFloat {
        guard count > 0 else { return 0 }
        return (2 * .pi / CGFloat(count)) * CGFloat(i) - .pi / 2
    }
}
