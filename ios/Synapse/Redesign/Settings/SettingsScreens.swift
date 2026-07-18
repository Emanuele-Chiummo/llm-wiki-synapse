import Observation
import SwiftUI

// MARK: - Providers (F17, view/select active)

@Observable
@MainActor
final class ProvidersModel {
    var state: LoadState<[API.ProviderConfig]> = .idle

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if state.value == nil { state = .loading }
        do {
            state = .loaded(try await client.providerConfigs().items)
        } catch {
            state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
        }
    }
}

/// View the configured inference providers (F17) — which backend + model serves
/// each operation, and which is the fallback. Read-only on iOS by design: pasting
/// API keys / creating configs stays on the desktop; the phone shows the active
/// routing so you always know what's answering ingest and chat.
struct ProvidersScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = ProvidersModel()

    var body: some View {
        List {
            Section {
                Text("Synapse routes each operation to a provider: **Local** (Ollama), **API** (Anthropic / OpenAI-compatible) or **CLI** (claude-agent-sdk). Configure providers and keys on the desktop; this shows what's active.")
                    .font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                    .listRowBackground(Color.clear)
            }
            content
        }
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("Providers")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.load(session) }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading where model.state.value == nil:
            ForEach(0..<3, id: \.self) { _ in SynSkeletonLine(height: 44).listRowBackground(Color.clear) }
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
                .listRowBackground(Color.clear).listRowSeparator(.hidden)
        default:
            let configs = model.state.value ?? []
            if configs.isEmpty {
                Text("No providers configured — set one up on the desktop.")
                    .font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
                    .listRowBackground(Color.clear)
            } else {
                Section("Active routing") {
                    ForEach(configs) { cfg in providerRow(cfg) }
                }
            }
        }
    }

    private func providerRow(_ cfg: API.ProviderConfig) -> some View {
        HStack(spacing: SynSpace.x4) {
            Image(systemName: cfg.providerIcon)
                .font(.callout.weight(.semibold)).foregroundStyle(SynColor.accent)
                .frame(width: 34, height: 34).background(SynColor.accentSoft)
                .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: SynSpace.x2) {
                    Text(cfg.providerLabel).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                    if cfg.isFallback {
                        Text("fallback").font(SynFont.eyebrow).foregroundStyle(SynColor.amber)
                    }
                }
                Text("\(cfg.operationLabel) · \(cfg.modelID)")
                    .font(SynFont.caption).foregroundStyle(SynColor.textMuted).lineLimit(1)
                if cfg.apiKeyConfigured, let masked = cfg.apiKeyMasked {
                    Text("key \(masked)").font(SynFont.caption.monospaced()).foregroundStyle(SynColor.textDim)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, SynSpace.x1)
    }
}

// MARK: - Projects / vaults (switch active)

@Observable
@MainActor
final class ProjectsModel {
    var state: LoadState<API.ProjectsResponse> = .idle
    var switching: String?

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if state.value == nil { state = .loading }
        do {
            state = .loaded(try await client.projects())
        } catch {
            state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
        }
    }

    func activate(_ session: SynapseSession, project: API.Project) async {
        guard let client = session.client(), switching == nil else { return }
        switching = project.id
        defer { switching = nil }
        do {
            try await client.activateProject(id: project.id)
            // Point the app's own vault-scoped calls (search/review/chat) at it too,
            // then reconnect (status + SSE) so live state follows the switch.
            session.vaultID = project.id
            session.reconfigure()
            await load(session)
        } catch {
            state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
        }
    }
}

/// Switch the active project / vault (desktop projects switcher). Activating a
/// project makes it the server's active vault AND repoints the app's vault-scoped
/// calls, then reconnects live state.
struct ProjectsScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = ProjectsModel()

    var body: some View {
        List {
            content
        }
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("Vaults")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await model.load(session) }
        .task { await model.load(session) }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading where model.state.value == nil:
            ForEach(0..<3, id: \.self) { _ in SynSkeletonLine(height: 44).listRowBackground(Color.clear) }
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
                .listRowBackground(Color.clear).listRowSeparator(.hidden)
        default:
            let resp = model.state.value
            let projects = resp?.projects ?? []
            if projects.isEmpty {
                Text("No projects found on the server.")
                    .font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
                    .listRowBackground(Color.clear)
            } else {
                Section("Projects") {
                    ForEach(projects) { p in projectRow(p, activeID: resp?.activeID) }
                }
            }
        }
    }

    private func projectRow(_ project: API.Project, activeID: String?) -> some View {
        let isActive = project.id == activeID
        return Button {
            Task { await model.activate(session, project: project) }
        } label: {
            HStack(spacing: SynSpace.x4) {
                Image(systemName: isActive ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isActive ? SynColor.green : SynColor.textDim)
                VStack(alignment: .leading, spacing: 1) {
                    Text(project.name).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                    Text(project.id).font(SynFont.caption).foregroundStyle(SynColor.textDim).lineLimit(1)
                }
                Spacer(minLength: 0)
                if model.switching == project.id { ProgressView() }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isActive || model.switching != nil)
    }
}

// MARK: - Language (F16 i18n)

struct LanguageScreen: View {
    @Environment(SynapseSession.self) private var session
    var body: some View {
        Form {
            Section {
                Picker("Language", selection: Binding(
                    get: { session.language }, set: { session.language = $0 })) {
                    ForEach(SynapseSession.Language.allCases) { l in Text(l.label).tag(l) }
                }
                .pickerStyle(.inline)
            } footer: {
                Text("“System” follows your device language. Locale changes apply immediately (dates, numbers, system controls). Full Italian translation of the redesign's own copy is being completed progressively.")
            }
        }
        .navigationTitle("Language")
        .navigationBarTitleDisplayMode(.inline)
    }
}
