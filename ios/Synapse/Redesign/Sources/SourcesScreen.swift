import Observation
import SwiftUI

@Observable
@MainActor
final class SourcesModel {
    var state: LoadState<API.SourcesList> = .idle
    var root: String?

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if state.value == nil { state = .loading }
        do {
            state = .loaded(try await client.sources(root: root))
        } catch {
            if state.value == nil {
                state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }
}

/// The raw-sources browser (desktop Sources reader). Lists the vault's
/// `raw/sources/` tree — folders drill in, files show type/size/modified. This is
/// the immutable ingest input layer (K1 `raw/`), distinct from the generated wiki.
struct SourcesScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = SourcesModel()
    var root: String? = nil
    var titleOverride: String? = nil

    var body: some View {
        List {
            content
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle(titleOverride ?? "Sources")
        .navigationBarTitleDisplayMode(root == nil ? .large : .inline)
        .refreshable { await model.load(session) }
        .task {
            model.root = root
            await model.load(session)
        }
        .onChange(of: session.dataVersion) { _, _ in Task { await model.load(session) } }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading where model.state.value == nil:
            ForEach(0..<8, id: \.self) { _ in
                SynSkeletonLine(height: 40)
                    .listRowSeparator(.hidden).listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 4, leading: SynSpace.x6, bottom: 4, trailing: SynSpace.x6))
            }
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
                .listRowSeparator(.hidden).listRowBackground(Color.clear)
        default:
            let list = model.state.value
            let entries = list?.entries ?? []
            if entries.isEmpty {
                SynEmptyState(
                    systemImage: "tray",
                    title: "No sources here",
                    message: "Drop documents into the vault's raw/sources/ folder, or clip a page — they'll appear here and feed ingest.")
                    .listRowSeparator(.hidden).listRowBackground(Color.clear)
            } else {
                ForEach(entries) { entry in entryRow(entry) }
                if list?.truncated == true {
                    Text("List truncated — narrow into a folder to see more.")
                        .font(SynFont.caption).foregroundStyle(SynColor.textDim)
                        .listRowBackground(Color.clear)
                }
            }
        }
    }

    @ViewBuilder private func entryRow(_ entry: API.SourceEntry) -> some View {
        if entry.isDir {
            NavigationLink {
                SourcesScreen(root: entry.path, titleOverride: entry.name)
            } label: { rowLabel(entry) }
                .listRowInsets(EdgeInsets(top: 2, leading: SynSpace.x6, bottom: 2, trailing: SynSpace.x6))
                .listRowSeparatorTint(SynColor.borderSubtle)
                .listRowBackground(Color.clear)
        } else {
            rowLabel(entry)
                .listRowInsets(EdgeInsets(top: 2, leading: SynSpace.x6, bottom: 2, trailing: SynSpace.x6))
                .listRowSeparatorTint(SynColor.borderSubtle)
                .listRowBackground(Color.clear)
        }
    }

    private func rowLabel(_ entry: API.SourceEntry) -> some View {
        HStack(spacing: SynSpace.x4) {
            Image(systemName: icon(for: entry))
                .font(.callout).foregroundStyle(entry.isDir ? SynColor.accent : SynColor.color(forType: "source"))
                .frame(width: 30)
            VStack(alignment: .leading, spacing: 1) {
                Text(entry.name).font(SynFont.rowTitle).foregroundStyle(SynColor.text).lineLimit(1)
                if let sub = subtitle(entry) {
                    Text(sub).font(SynFont.caption).foregroundStyle(SynColor.textMuted).lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, SynSpace.x2)
        .contentShape(Rectangle())
    }

    private func subtitle(_ entry: API.SourceEntry) -> String? {
        if entry.isDir { return "Folder" }
        var bits: [String] = []
        if let e = entry.ext, !e.isEmpty { bits.append(e.uppercased()) }
        if let s = entry.sizeLabel { bits.append(s) }
        return bits.isEmpty ? nil : bits.joined(separator: " · ")
    }

    private func icon(for entry: API.SourceEntry) -> String {
        if entry.isDir { return "folder.fill" }
        switch (entry.ext ?? "").lowercased() {
        case "pdf": return "doc.richtext.fill"
        case "md", "markdown", "txt": return "doc.text.fill"
        case "docx", "doc": return "doc.fill"
        case "pptx", "ppt": return "rectangle.on.rectangle.fill"
        case "xlsx", "xls", "csv": return "tablecells.fill"
        case "png", "jpg", "jpeg", "gif", "webp", "heic": return "photo.fill"
        case "mp3", "wav", "m4a", "mp4", "mov": return "waveform"
        default: return "doc.fill"
        }
    }
}
