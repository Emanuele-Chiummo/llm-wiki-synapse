import SwiftUI

struct ResearchView: View {
    @EnvironmentObject private var settings: AppSettings

    @State private var topic = ""
    @State private var run: ResearchRunDetail?
    @State private var runID: String?
    @State private var starting = false
    @State private var pollTask: Task<Void, Never>?
    @State private var errorText: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                BackHeader(
                    title: "Deep research",
                    subtitle: "Un agente cerca sul web, legge le fonti e scrive una pagina citata nel tuo wiki.",
                    backLabel: "Altro")

                inputCard

                if let err = errorText {
                    ErrorState(message: err) { errorText = nil }
                }
                if let run { resultCard(run) }
            }
            .padding(.bottom, 28)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .onDisappear { pollTask?.cancel() }
    }

    private var inputCard: some View {
        VStack(spacing: 10) {
            HStack {
                TextField("Cosa vuoi indagare?", text: $topic)
                    .font(.system(size: 16)).foregroundStyle(Theme.label)
                    .padding(.vertical, 10)
                    .submitLabel(.search)
                    .onSubmit { Task { await start() } }
            }
            .padding(.horizontal, 14)
            .background(Theme.card)
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.separator, lineWidth: 0.5))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

            Button { Task { await start() } } label: {
                HStack {
                    if starting || isRunning { ProgressView().tint(.white) }
                    Text(isRunning ? "Ricerca in corso…" : "Avvia ricerca")
                        .font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 14)
                .background(Theme.tint)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)
            .disabled(topic.trimmingCharacters(in: .whitespaces).isEmpty || isRunning || starting)
        }
        .padding(16)
    }

    private var isRunning: Bool {
        if let run { return !run.isDone }
        return runID != nil && run == nil
    }

    private func resultCard(_ run: ResearchRunDetail) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    statusPill(run.status)
                    Spacer()
                    if let n = run.sourcesFetched { Text("\(n) fonti").font(.system(size: 12)).foregroundStyle(Theme.label2) }
                }
                Text(run.topic).font(.system(size: 20, weight: .bold)).foregroundStyle(Theme.label)
                if let text = run.synthesisText, !text.isEmpty {
                    Text(text)
                        .font(.system(size: 15)).lineSpacing(3)
                        .foregroundStyle(Theme.label)
                        .fixedSize(horizontal: false, vertical: true)
                } else if !run.isDone {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Elaborazione…").font(.system(size: 15)).foregroundStyle(Theme.label2)
                    }
                    .padding(.top, 4)
                }
                if let sources = run.sources, !sources.isEmpty {
                    FlowLayout(spacing: 6) {
                        ForEach(Array(sources.prefix(8).enumerated()), id: \.element.id) { i, s in
                            Text("[\(i + 1)] \(host(s.url))")
                                .font(.system(size: 12))
                                .foregroundStyle(Theme.tint)
                                .padding(.horizontal, 9).padding(.vertical, 5)
                                .background(Theme.fieldBackground)
                                .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                        }
                    }
                    .padding(.top, 4)
                }
            }
            .padding(18)
        }
        .background(Theme.card)
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.separator, lineWidth: 0.5))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .padding(.horizontal, 16)
    }

    private func statusPill(_ status: String) -> some View {
        let done = status != "running"
        return Text(statusLabel(status))
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(done ? Color(hex: 0x0EA5E9) : Theme.tint)
            .padding(.horizontal, 10).padding(.vertical, 3)
            .background((done ? Color(hex: 0x0EA5E9) : Theme.tint).opacity(0.14))
            .clipShape(Capsule())
    }

    private func statusLabel(_ s: String) -> String {
        switch s {
        case "running": return "In corso"
        case "converged": return "Pagina generata"
        case "max_iter_reached": return "Limite iterazioni"
        case "budget_exhausted": return "Budget esaurito"
        case "error": return "Errore"
        default: return s
        }
    }

    private func host(_ url: String) -> String {
        URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") ?? url
    }

    // MARK: Actions

    private func start() async {
        let t = topic.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty, let client = settings.makeClient() else { return }
        starting = true; errorText = nil; run = nil
        do {
            let resp = try await client.startResearch(topic: t)
            runID = resp.runID
            starting = false
            poll(client: client, id: resp.runID)
        } catch {
            errorText = (error as? APIError)?.errorDescription ?? error.localizedDescription
            starting = false
        }
    }

    private func poll(client: SynapseClient, id: String) {
        pollTask?.cancel()
        pollTask = Task {
            for _ in 0..<120 {  // ~4 min cap at 2s intervals
                if Task.isCancelled { return }
                if let detail = try? await client.researchRun(id: id) {
                    await MainActor.run { self.run = detail }
                    if detail.isDone { return }
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }
}
