import SwiftUI

/// Read-only view over ingest activity. Uploading files from the phone is a
/// follow-up; this first version surfaces the recent-runs history so you can see
/// the pipeline working against your server.
struct IngestView: View {
    @EnvironmentObject private var settings: AppSettings

    @State private var runs: [IngestRun] = []
    @State private var loadError: String?
    @State private var isLoading = true

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                BackHeader(
                    title: "Importa",
                    subtitle: "Un LLM analizza ogni fonte e scrive pagine wiki tipizzate e interconnesse.",
                    backLabel: "Altro")

                dropCard

                SectionHeader(text: "Import recenti")
                if let loadError {
                    ErrorState(message: loadError) { Task { await load() } }
                } else if isLoading {
                    LoadingState()
                } else if runs.isEmpty {
                    EmptyState(systemImage: "tray.and.arrow.down",
                               title: "Nessun import",
                               message: "Gli import avviati sul server appariranno qui.")
                } else {
                    Card {
                        ForEach(Array(runs.enumerated()), id: \.element.id) { idx, run in
                            runRow(run)
                            if idx < runs.count - 1 { RowDivider() }
                        }
                    }
                    .padding(.horizontal, 16)
                }
            }
            .padding(.bottom, 24)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task { await load() }
        .refreshable { await load() }
    }

    private var dropCard: some View {
        VStack(spacing: 8) {
            Image(systemName: "arrow.up.doc")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 46, height: 46)
                .background(Theme.tint)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            Text("Carica documenti dal server")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.label)
            Text("Markdown, PDF, DOCX, PPTX, XLSX · via file watcher o web clipper")
                .font(.system(size: 13))
                .foregroundStyle(Theme.label2)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26).padding(.horizontal, 16)
        .background(Theme.fieldBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
                .foregroundStyle(Theme.tint))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .padding(16)
    }

    private func runRow(_ run: IngestRun) -> some View {
        HStack(spacing: 12) {
            Text(statusEmoji(run.status)).font(.system(size: 20))
            VStack(alignment: .leading, spacing: 1) {
                Text(run.providerType.map { "Provider: \($0)" } ?? "Import")
                    .font(.system(size: 16)).foregroundStyle(Theme.label)
                Text(detail(run)).font(.system(size: 13)).foregroundStyle(Theme.label2)
            }
            Spacer()
            statusBadge(run.status)
        }
        .padding(.horizontal, 14).padding(.vertical, 13)
    }

    private func detail(_ run: IngestRun) -> String {
        var parts: [String] = []
        if let n = run.pagesCreated { parts.append("\(n) pagine") }
        if let c = run.totalCostUSD, c > 0 { parts.append(String(format: "$%.4f", c)) }
        if let d = run.startedAt { parts.append(RelativeDate.string(d)) }
        return parts.joined(separator: " · ")
    }

    private func statusEmoji(_ s: String?) -> String {
        switch s {
        case "completed": return "📄"
        case "running": return "⏳"
        case "failed": return "⚠️"
        default: return "📝"
        }
    }

    @ViewBuilder
    private func statusBadge(_ s: String?) -> some View {
        let (text, color): (String, Color) = {
            switch s {
            case "completed": return ("Completato", Theme.success)
            case "running": return ("In corso", Theme.tint)
            case "failed": return ("Errore", Theme.destructive)
            default: return (s ?? "—", Theme.label2)
            }
        }()
        Text(text)
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(color.opacity(0.14))
            .clipShape(Capsule())
    }

    private func load() async {
        guard let client = settings.makeClient() else {
            loadError = APIError.notConfigured.errorDescription; isLoading = false; return
        }
        isLoading = true; loadError = nil
        do { runs = try await client.ingestRuns().items }
        catch { loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription }
        isLoading = false
    }
}
