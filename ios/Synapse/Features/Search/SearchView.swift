import SwiftUI

struct SearchView: View {
    @EnvironmentObject private var settings: AppSettings

    @State private var query = ""
    @State private var selectedType: String? = nil   // nil == "Tutti"
    @State private var results: [SearchResult] = []
    @State private var loadError: String?
    @State private var searching = false
    @State private var didSearch = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                LargeHeader(title: "Cerca").padding(.top, 8)
                searchField
                typeChips
                content
            }
            .padding(.bottom, 24)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task(id: searchKey) { await runSearch() }
    }

    private var searchKey: String { "\(query)|\(selectedType ?? "all")" }

    private var searchField: some View {
        HStack(spacing: 7) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 15))
                .foregroundStyle(Theme.label2)
            TextField("Concetti, persone, progetti…", text: $query)
                .font(.system(size: 16))
                .foregroundStyle(Theme.label)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.label3)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 38)
        .background(Theme.fieldBackground)
        .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.top, 6)
    }

    private var typeChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                chip(label: "Tutti", type: nil)
                ForEach(Theme.pageTypes, id: \.self) { t in
                    chip(label: Theme.label(forType: t), type: t)
                }
            }
            .padding(.horizontal, 16)
        }
        .padding(.top, 12)
    }

    private func chip(label: String, type: String?) -> some View {
        let active = selectedType == type
        let color = type.map { Theme.color(forType: $0) } ?? Theme.tint
        return Button {
            selectedType = type
        } label: {
            HStack(spacing: 6) {
                if let type {
                    Circle().fill(Theme.color(forType: type)).frame(width: 8, height: 8)
                }
                Text(label).font(.system(size: 14, weight: .medium))
            }
            .foregroundStyle(active ? (type == nil ? Color.white : color) : Theme.label)
            .padding(.horizontal, 13).padding(.vertical, 7)
            .background(
                active
                    ? (type == nil ? Theme.tint : color.opacity(0.16))
                    : Theme.card)
            .overlay(
                Capsule().stroke(active ? Color.clear : Theme.separator, lineWidth: 0.5))
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var content: some View {
        if let loadError {
            ErrorState(message: loadError) { Task { await runSearch() } }
        } else if query.trimmingCharacters(in: .whitespaces).isEmpty {
            EmptyState(systemImage: "magnifyingglass",
                       title: "Interroga la tua conoscenza",
                       message: "Cerca tra concetti, entità, fonti e altro.")
                .padding(.top, 20)
        } else if searching && results.isEmpty {
            LoadingState()
        } else if didSearch && results.isEmpty {
            Text("Nessun risultato per “\(query)”.")
                .font(.system(size: 15)).foregroundStyle(Theme.label2)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40).padding(.vertical, 30)
        } else {
            Text("\(results.count) risultat\(results.count == 1 ? "o" : "i")")
                .font(.system(size: 13)).foregroundStyle(Theme.label2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 24).padding(.top, 10).padding(.bottom, 6)
            Card {
                ForEach(Array(results.enumerated()), id: \.element.id) { idx, r in
                    NavigationLink(value: PageRef(id: r.id, title: r.title, type: selectedType)) {
                        PageRow(title: r.title ?? r.slug ?? "Pagina",
                                subtitle: r.phase.map { phaseLabel($0) },
                                type: selectedType) {
                            if let n = r.n {
                                Text("[\(n)]").font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Theme.label3)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    if idx < results.count - 1 { RowDivider() }
                }
            }
            .padding(.horizontal, 16)
        }
    }

    private func phaseLabel(_ phase: String) -> String {
        switch phase {
        case "vector": return "Corrispondenza vettoriale"
        case "expansion": return "Espansione sul grafo"
        default: return phase
        }
    }

    private func runSearch() async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { results = []; didSearch = false; return }
        // Debounce.
        try? await Task.sleep(for: .milliseconds(300))
        if Task.isCancelled { return }
        guard let client = settings.makeClient() else {
            loadError = APIError.notConfigured.errorDescription; return
        }
        searching = true; loadError = nil
        do {
            let resp = try await client.search(q, type: selectedType)
            results = resp.results
            didSearch = true
        } catch {
            loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        searching = false
    }
}
