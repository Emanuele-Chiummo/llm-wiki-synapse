import SwiftUI

/// Home dashboard mock (F18 lineage) — the redesign landing surface. Realistic
/// mock content demonstrating the new design system; Fase B wires the stats API.
struct HomeScreen: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SynSpace.x7) {
                hero
                domainsGrid
                recentSection
                activitySection
            }
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x6)
        }
        .synScreenBackground()
        .navigationTitle("Home")
        .navigationBarTitleDisplayMode(.large)
    }

    // Hero: wordmark + vault + headline stat, on the brand gradient.
    private var hero: some View {
        VStack(alignment: .leading, spacing: SynSpace.x5) {
            HStack(spacing: SynSpace.x2) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.footnote.weight(.bold))
                Text("SYNAPSE").font(SynFont.eyebrow).tracking(1.2)
            }
            .foregroundStyle(SynColor.onAccent.opacity(0.9))

            Text(RedesignMock.vaultName)
                .font(SynFont.largeTitle)
                .foregroundStyle(SynColor.onAccent)

            HStack(spacing: SynSpace.x8) {
                heroStat("\(RedesignMock.headline.pages)", "Pages")
                heroStat("\(RedesignMock.headline.sources)", "Sources")
                heroStat("\(RedesignMock.headline.links)", "Links")
            }
        }
        .padding(SynSpace.x7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SynColor.signatureGradient)
        .clipShape(RoundedRectangle(cornerRadius: SynRadius.xl, style: .continuous))
        .shadow(color: SynColor.accent.opacity(0.28), radius: 20, x: 0, y: 10)
    }

    private func heroStat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title2.weight(.bold)).foregroundStyle(SynColor.onAccent)
            Text(label).font(SynFont.caption).foregroundStyle(SynColor.onAccent.opacity(0.8))
        }
    }

    // Per-domain section insight cards.
    private var domainsGrid: some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            SynSectionHeader(text: "Sections")
            LazyVGrid(
                columns: [GridItem(.flexible(), spacing: SynSpace.x4),
                          GridItem(.flexible(), spacing: SynSpace.x4)],
                spacing: SynSpace.x4
            ) {
                ForEach(RedesignMock.domains) { domain in
                    domainCard(domain)
                }
            }
        }
    }

    private func domainCard(_ domain: RedesignMock.DomainStat) -> some View {
        SynCard(padding: SynSpace.x5) {
            HStack(spacing: SynSpace.x4) {
                SynTypeGlyph(type: domain.type, size: 38)
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(domain.count)")
                        .font(.title3.weight(.bold))
                        .foregroundStyle(SynColor.text)
                    Text(domain.name)
                        .font(SynFont.caption)
                        .foregroundStyle(SynColor.textMuted)
                }
                Spacer(minLength: 0)
            }
        }
    }

    // Recently updated pages.
    private var recentSection: some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            HStack {
                SynSectionHeader(text: "Recently updated")
                Spacer()
                if RedesignMock.headline.review > 0 {
                    SynChip(text: "\(RedesignMock.headline.review) in review",
                            systemImage: "tray.full.fill")
                }
            }
            SynCard(padding: SynSpace.x5) {
                ForEach(Array(RedesignMock.recent.enumerated()), id: \.element.id) { idx, page in
                    SynListRow(title: page.title, subtitle: page.summary, type: page.type)
                    if idx < RedesignMock.recent.count - 1 { SynRowDivider() }
                }
            }
        }
    }

    // Append-only activity feed (log.md lineage).
    private var activitySection: some View {
        VStack(alignment: .leading, spacing: SynSpace.x4) {
            SynSectionHeader(text: "Activity")
            SynCard(padding: SynSpace.x5) {
                ForEach(Array(RedesignMock.activity.enumerated()), id: \.element.id) { idx, item in
                    HStack(spacing: SynSpace.x4) {
                        Image(systemName: item.icon)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(SynColor.accent)
                            .frame(width: 26, height: 26)
                            .background(SynColor.accentSoft)
                            .clipShape(RoundedRectangle(cornerRadius: SynRadius.sm, style: .continuous))
                        Text(item.text)
                            .font(SynFont.subhead)
                            .foregroundStyle(SynColor.text)
                            .lineLimit(2)
                        Spacer(minLength: SynSpace.x3)
                        Text(item.when)
                            .font(SynFont.caption)
                            .foregroundStyle(SynColor.textDim)
                    }
                    .padding(.vertical, SynSpace.x3)
                    if idx < RedesignMock.activity.count - 1 { SynRowDivider(leadingInset: 38) }
                }
            }
        }
    }
}

#Preview("Home — light") {
    NavigationStack { HomeScreen() }.preferredColorScheme(.light)
}
#Preview("Home — dark") {
    NavigationStack { HomeScreen() }.preferredColorScheme(.dark)
}
