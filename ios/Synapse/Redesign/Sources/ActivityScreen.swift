import Observation
import SwiftUI

@Observable
@MainActor
final class ActivityModel {
    var queue: LoadState<API.QueueSnapshot> = .idle
    var runs: [API.IngestRun] = []

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            queue = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if queue.value == nil { queue = .loading }
        do {
            async let q = client.ingestQueue()
            async let r = client.ingestRuns(limit: 30)
            let (snap, runList) = try await (q, r)
            queue = .loaded(snap)
            runs = runList.items
        } catch {
            if queue.value == nil {
                queue = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }
}

/// Ingest activity (desktop ActivityBar). The live counters come from the SSE
/// `queue` channel already wired in Fase B (`session.queue`) — no poll loop; the
/// per-task phase/progress detail and the run history are fetched from
/// `/ingest/queue` + `/ingest/runs` on appear, on pull-to-refresh, and when the
/// SSE data_version bumps.
struct ActivityScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = ActivityModel()

    var body: some View {
        List {
            liveSection
            tasksSection
            historySection
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("Activity")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await model.load(session) }
        .task { await model.load(session) }
        // The SSE queue counters change live; refetch task/run detail when they do.
        .onChange(of: session.queue) { _, _ in Task { await model.load(session) } }
        .onChange(of: session.dataVersion) { _, _ in Task { await model.load(session) } }
    }

    // MARK: Live counters (from SSE)

    private var liveSection: some View {
        Section {
            SynCard(padding: SynSpace.x5) {
                HStack {
                    SynSectionHeader(text: "Live queue")
                    Spacer()
                    HStack(spacing: 5) {
                        Circle().fill(session.streamHealthy ? SynColor.green : SynColor.amber)
                            .frame(width: 7, height: 7)
                        Text(session.streamHealthy ? "Live" : "Reconnecting")
                            .font(SynFont.eyebrow).foregroundStyle(SynColor.textDim)
                    }
                }
                HStack(spacing: SynSpace.x3) {
                    counter("Pending", session.queue.pending, SynColor.amber)
                    counter("Running", session.queue.processing, SynColor.accent)
                    counter("Failed", session.queue.failed, SynColor.red)
                    counter("Done", session.queue.completedSinceIdle, SynColor.green)
                }
                .padding(.top, SynSpace.x4)
                if session.queue.paused {
                    Label("Queue paused", systemImage: "pause.circle.fill")
                        .font(SynFont.caption).foregroundStyle(SynColor.amber)
                        .padding(.top, SynSpace.x3)
                }
            }
            .listRowInsets(EdgeInsets(top: SynSpace.x2, leading: SynSpace.x6, bottom: SynSpace.x2, trailing: SynSpace.x6))
            .listRowBackground(Color.clear)
        }
    }

    private func counter(_ label: String, _ value: Int, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text("\(value)").font(.title3.weight(.bold).monospacedDigit()).foregroundStyle(color)
            Text(label).font(SynFont.eyebrow).foregroundStyle(SynColor.textMuted)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: In-flight tasks (phase / progress)

    @ViewBuilder private var tasksSection: some View {
        let tasks = model.queue.value?.tasks ?? []
        if !tasks.isEmpty {
            Section("In progress") {
                ForEach(tasks) { task in taskRow(task) }
            }
        }
    }

    private func taskRow(_ task: API.QueueTask) -> some View {
        VStack(alignment: .leading, spacing: SynSpace.x2) {
            HStack {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.caption).foregroundStyle(SynColor.accent)
                Text(task.filename).font(SynFont.rowTitle).foregroundStyle(SynColor.text).lineLimit(1)
                Spacer()
                if let eta = task.etaSeconds { Text("~\(eta)s").font(SynFont.caption).foregroundStyle(SynColor.textDim) }
            }
            if let p = task.progress {
                ProgressView(value: min(max(p, 0), 1)).tint(SynColor.accent)
            } else {
                ProgressView().tint(SynColor.accent)
            }
            HStack(spacing: SynSpace.x2) {
                if let phase = task.phase, !phase.isEmpty {
                    Text(phase.capitalized).font(SynFont.eyebrow).foregroundStyle(SynColor.accent)
                }
                Text(task.status).font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                if task.retryCount > 0 {
                    Text("retry \(task.retryCount)").font(SynFont.caption).foregroundStyle(SynColor.amber)
                }
            }
        }
        .padding(.vertical, SynSpace.x2)
    }

    // MARK: Run history

    @ViewBuilder private var historySection: some View {
        Section("Recent runs") {
            switch model.queue {
            case .idle, .loading where model.queue.value == nil:
                ForEach(0..<4, id: \.self) { _ in SynSkeletonLine(height: 40) }
            case .failed(let message):
                SynErrorState(message: message) { Task { await model.load(session) } }
                    .listRowBackground(Color.clear)
            default:
                if model.runs.isEmpty {
                    Text("No ingest runs yet.").font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
                } else {
                    ForEach(model.runs) { run in runRow(run) }
                }
            }
        }
    }

    private func runRow(_ run: API.IngestRun) -> some View {
        HStack(spacing: SynSpace.x4) {
            Image(systemName: statusIcon(run.status))
                .foregroundStyle(statusColor(run.status)).frame(width: 26)
            VStack(alignment: .leading, spacing: 1) {
                Text("\(run.pagesCreated) page\(run.pagesCreated == 1 ? "" : "s") · \(run.providerType)")
                    .font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                HStack(spacing: SynSpace.x3) {
                    Text(run.status.capitalized).font(SynFont.caption).foregroundStyle(statusColor(run.status))
                    if run.totalCostUSD > 0 {
                        Text(String(format: "$%.4f", run.totalCostUSD))
                            .font(SynFont.caption.monospacedDigit()).foregroundStyle(SynColor.textDim)
                    }
                    Text(relativeTime(run.startedAt)).font(SynFont.caption).foregroundStyle(SynColor.textDim)
                }
                if let err = run.errorMessage, !err.isEmpty {
                    Text(err).font(SynFont.caption).foregroundStyle(SynColor.red).lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, SynSpace.x1)
    }

    private func statusIcon(_ s: String) -> String {
        switch s.lowercased() {
        case "completed", "success", "done": return "checkmark.circle.fill"
        case "failed", "error": return "xmark.octagon.fill"
        case "running", "processing": return "arrow.triangle.2.circlepath"
        default: return "clock.fill"
        }
    }
    private func statusColor(_ s: String) -> Color {
        switch s.lowercased() {
        case "completed", "success", "done": return SynColor.green
        case "failed", "error": return SynColor.red
        case "running", "processing": return SynColor.accent
        default: return SynColor.amber
        }
    }
    private func relativeTime(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter(); f.unitsStyle = .abbreviated
        return f.localizedString(for: date, relativeTo: Date())
    }
}
