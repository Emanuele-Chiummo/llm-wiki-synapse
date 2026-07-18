import SwiftUI

/// Wiki browsing mock — a filterable list of pages that pushes to a reading
/// detail. Realistic mock content; Fase B wires `/pages` + the markdown renderer.
struct WikiScreen: View {
    @State private var selectedType: String? = nil

    private let types = ["concept", "entity", "source", "synthesis", "comparison", "query"]

    private var pages: [RedesignMock.Page] {
        guard let t = selectedType else { return RedesignMock.wikiPages }
        return RedesignMock.wikiPages.filter { $0.type == t }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SynSpace.x5) {
                filterBar
                SynCard(padding: SynSpace.x5) {
                    if pages.isEmpty {
                        SynEmptyState(
                            systemImage: "doc.text.magnifyingglass",
                            title: "No pages of this type",
                            message: "Try another section, or ingest a source to grow the vault.")
                    } else {
                        ForEach(Array(pages.enumerated()), id: \.element.id) { idx, page in
                            NavigationLink(value: page) {
                                SynListRow(title: page.title, subtitle: page.summary, type: page.type)
                            }
                            .buttonStyle(.plain)
                            if idx < pages.count - 1 { SynRowDivider() }
                        }
                    }
                }
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x5)
        }
        .synScreenBackground()
        .navigationTitle("Wiki")
        .navigationBarTitleDisplayMode(.large)
        .navigationDestination(for: RedesignMock.Page.self) { _ in WikiReadingScreen() }
    }

    private var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: SynSpace.x3) {
                Button { selectedType = nil } label: {
                    SynChip(text: "All", selected: selectedType == nil)
                }.buttonStyle(.plain)
                ForEach(types, id: \.self) { t in
                    Button { selectedType = (selectedType == t ? nil : t) } label: {
                        SynChip(text: SynColor.label(forType: t),
                                pageType: selectedType == t ? nil : t,
                                selected: selectedType == t)
                    }.buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 2)
        }
    }
}

/// Wiki reading detail mock — header card + body + sources + related pages.
struct WikiReadingScreen: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SynSpace.x6) {
                headerCard
                Text(RedesignMock.readingBody)
                    .font(SynFont.body)
                    .foregroundStyle(SynColor.text)
                    .lineSpacing(5)
                sourcesCard
                relatedCard
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x5)
        }
        .synScreenBackground(false)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            HStack(spacing: SynSpace.x3) {
                SynTypeGlyph(type: RedesignMock.readingType, size: 40)
                SynChip(text: SynColor.label(forType: RedesignMock.readingType),
                        pageType: RedesignMock.readingType)
                Spacer()
            }
            Text(RedesignMock.readingTitle)
                .font(SynFont.largeTitle)
                .foregroundStyle(SynColor.text)
            HStack(spacing: SynSpace.x5) {
                metric("doc.on.doc", "\(RedesignMock.readingSources.count) sources")
                metric("link", "\(RedesignMock.readingRelated.count) links")
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

    private var sourcesCard: some View {
        SynCard(padding: SynSpace.x5) {
            SynSectionHeader(text: "Sources")
            VStack(alignment: .leading, spacing: SynSpace.x3) {
                ForEach(Array(RedesignMock.readingSources.enumerated()), id: \.offset) { idx, src in
                    HStack(spacing: SynSpace.x3) {
                        Text("[\(idx + 1)]")
                            .font(SynFont.caption.monospacedDigit())
                            .foregroundStyle(SynColor.accent)
                        Text(src).font(SynFont.subhead).foregroundStyle(SynColor.text)
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
                    ForEach(RedesignMock.readingRelated, id: \.self) { title in
                        Text(title)
                            .font(SynFont.subhead)
                            .foregroundStyle(SynColor.text)
                            .padding(.horizontal, SynSpace.x5)
                            .padding(.vertical, SynSpace.x4)
                            .background(SynColor.surface)
                            .overlay(
                                RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous)
                                    .strokeBorder(SynColor.border, lineWidth: 1))
                            .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
                    }
                }
                .padding(.horizontal, 2)
            }
        }
    }
}

#Preview("Wiki — light") {
    NavigationStack { WikiScreen() }.preferredColorScheme(.light)
}
#Preview("Wiki reading — dark") {
    NavigationStack { WikiReadingScreen() }.preferredColorScheme(.dark)
}
