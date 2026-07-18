import SwiftUI

/// The "More" hub — connection status plus links to Server settings, Device
/// tokens, Appearance and the (Fase C) Graph placeholder.
struct MoreScreen: View {
    @Environment(SynapseSession.self) private var session

    var body: some View {
        List {
            Section {
                connectionCard
                    .listRowInsets(EdgeInsets(top: SynSpace.x3, leading: SynSpace.x6,
                                              bottom: SynSpace.x3, trailing: SynSpace.x6))
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }

            Section("Operations") {
                NavigationLink { ReviewScreen() } label: {
                    settingRow("checklist", "Review queue",
                               session.reviewPending > 0
                               ? "\(session.reviewPending) pending" : "Create · Deep-Research · Skip")
                }
                .badge(session.reviewPending)
            }

            Section("Settings & operations") {
                NavigationLink { ServerSettingsScreen() } label: {
                    settingRow("server.rack", "Server", session.serverURLString)
                }
                NavigationLink { TokensScreen() } label: {
                    settingRow("key.fill", "Device tokens", "Scoped, revocable per-device access")
                }
                NavigationLink { AppearanceScreen() } label: {
                    settingRow("circle.lefthalf.filled", "Appearance", session.appearance.label)
                }
            }

            Section {
                Text("Synapse for iOS — redesign (Track 2.1, Fase B). Server \(session.serverVersion ?? "—").")
                    .font(SynFont.caption).foregroundStyle(SynColor.textDim)
                    .listRowBackground(Color.clear)
            }
        }
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("More")
        .navigationBarTitleDisplayMode(.large)
    }

    private var connectionCard: some View {
        SynCard(padding: SynSpace.x5) {
            HStack(spacing: SynSpace.x4) {
                ZStack {
                    Circle().fill(statusColor.opacity(0.15)).frame(width: 44, height: 44)
                    Image(systemName: statusIcon).foregroundStyle(statusColor)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(statusTitle).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                    Text(session.serverURLString).font(SynFont.caption)
                        .foregroundStyle(SynColor.textMuted).lineLimit(1)
                }
                Spacer(minLength: 0)
                if case .connecting = session.reachability { ProgressView() }
            }
        }
    }

    private var statusColor: Color {
        switch session.reachability {
        case .online: return SynColor.green
        case .offline: return SynColor.red
        default: return SynColor.amber
        }
    }
    private var statusIcon: String {
        switch session.reachability {
        case .online: return "checkmark.circle.fill"
        case .offline: return "xmark.circle.fill"
        default: return "clock.fill"
        }
    }
    private var statusTitle: String {
        switch session.reachability {
        case .online: return "Connected"
        case .offline(let m): return m
        case .connecting: return "Connecting…"
        case .unknown: return "Not connected"
        }
    }

    private func settingRow(_ icon: String, _ title: String, _ subtitle: String) -> some View {
        HStack(spacing: SynSpace.x5) {
            Image(systemName: icon)
                .font(.callout.weight(.semibold)).foregroundStyle(SynColor.accent)
                .frame(width: 34, height: 34)
                .background(SynColor.accentSoft)
                .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                Text(subtitle).font(SynFont.caption).foregroundStyle(SynColor.textMuted).lineLimit(1)
            }
        }
    }
}

// MARK: - Server settings

struct ServerSettingsScreen: View {
    @Environment(SynapseSession.self) private var session

    @State private var url = ""
    @State private var vault = ""
    @State private var token = ""
    @State private var cfID = ""
    @State private var cfSecret = ""
    @State private var showCFAccess = false
    @State private var testResult: String?
    @State private var testing = false

    var body: some View {
        Form {
            Section("Server") {
                TextField("https://synapse.example.com", text: $url)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .keyboardType(.URL)
                TextField("Vault id", text: $vault)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            Section {
                SecureField("Access token (optional)", text: $token)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            } header: {
                Text("Authentication")
            } footer: {
                Text("Paste a scoped device token, or generate one under Device tokens once connected. Stored only in the Keychain — never in plain text.")
            }
            Section(isExpanded: $showCFAccess) {
                TextField("CF-Access-Client-Id", text: $cfID)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("CF-Access-Client-Secret", text: $cfSecret)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            } header: {
                Text("Cloudflare Access (optional)")
            }

            Section {
                Button {
                    Task { await testConnection() }
                } label: {
                    HStack {
                        Text("Test connection")
                        Spacer()
                        if testing { ProgressView() }
                    }
                }
                .disabled(testing)
                if let testResult {
                    Text(testResult).font(SynFont.caption)
                        .foregroundStyle(testResult.hasPrefix("Connected") ? SynColor.green : SynColor.red)
                }
            }
        }
        .navigationTitle("Server")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Save") { save() }.bold()
            }
        }
        .onAppear {
            url = session.serverURLString
            vault = session.vaultID
            token = session.token
            cfID = session.cfAccessClientID
            cfSecret = session.cfAccessClientSecret
            showCFAccess = !cfID.isEmpty
        }
    }

    private func save() {
        session.serverURLString = url
        session.vaultID = vault.isEmpty ? "default" : vault
        session.token = token
        session.cfAccessClientID = cfID
        session.cfAccessClientSecret = cfSecret
        session.reconfigure()
    }

    private func testConnection() async {
        testing = true; testResult = nil
        defer { testing = false }
        // Build a throwaway client from the edited fields without persisting yet.
        var s = url.trimmingCharacters(in: .whitespacesAndNewlines)
        while s.hasSuffix("/") { s.removeLast() }
        if !s.contains("://") { s = "http://" + s }
        guard let base = URL(string: s) else { testResult = "Invalid URL"; return }
        let conn = Connection(
            baseURL: base, token: token.trimmingCharacters(in: .whitespacesAndNewlines),
            vaultID: vault.isEmpty ? "default" : vault,
            cfAccessClientID: cfID.trimmingCharacters(in: .whitespacesAndNewlines),
            cfAccessClientSecret: cfSecret.trimmingCharacters(in: .whitespacesAndNewlines))
        do {
            let status = try await APIClient(connection: conn).status()
            testResult = "Connected — server \(status.version ?? "?")"
        } catch {
            testResult = (error as? SynAPIError)?.errorDescription ?? error.localizedDescription
        }
    }
}

// MARK: - Appearance

struct AppearanceScreen: View {
    @Environment(SynapseSession.self) private var session
    var body: some View {
        Form {
            Picker("Theme", selection: Binding(
                get: { session.appearance },
                set: { session.appearance = $0 })) {
                ForEach(SynapseSession.Appearance.allCases) { a in
                    Text(a.label).tag(a)
                }
            }
            .pickerStyle(.inline)
        }
        .navigationTitle("Appearance")
        .navigationBarTitleDisplayMode(.inline)
    }
}
