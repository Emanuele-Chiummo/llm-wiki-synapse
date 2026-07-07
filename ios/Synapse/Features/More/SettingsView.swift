import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var app: AppModel

    @State private var providers: [ProviderConfigRow] = []
    @State private var testResult: TestResult?
    @State private var testing = false

    enum TestResult: Equatable { case ok(String), fail(String) }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                BackHeader(title: "Impostazioni", backLabel: "Altro")

                serverSection
                cloudflareAccessSection
                appearanceSection
                providerSection
                vaultSection
            }
            .padding(.bottom, 28)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task { await loadProviders() }
    }

    // MARK: Server

    private var serverSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Server")
            Card {
                VStack(alignment: .leading, spacing: 6) {
                    Text("URL del server")
                        .font(.system(size: 13)).foregroundStyle(Theme.label2)
                    TextField("http://192.168.1.10:8000", text: $settings.serverURLString)
                        .font(.system(size: 16))
                        .foregroundStyle(Theme.label)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .submitLabel(.done)
                }
                .padding(14)
                RowDivider()
                VStack(alignment: .leading, spacing: 6) {
                    Text("Token di accesso (opzionale)")
                        .font(.system(size: 13)).foregroundStyle(Theme.label2)
                    SecureField("Bearer token", text: $settings.authToken)
                        .font(.system(size: 16))
                        .foregroundStyle(Theme.label)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                .padding(14)
            }
            .padding(.horizontal, 16)

            Button { Task { await testConnection() } } label: {
                HStack {
                    if testing { ProgressView().tint(.white) }
                    Text(testing ? "Verifica…" : "Verifica connessione")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 13)
                .background(Theme.tint)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .disabled(testing)

            if let testResult {
                HStack(spacing: 8) {
                    Image(systemName: testResult.isOK ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(testResult.isOK ? Theme.success : Theme.destructive)
                    Text(testResult.message)
                        .font(.system(size: 14))
                        .foregroundStyle(Theme.label2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.top, 10)
            }
        }
    }

    // MARK: Cloudflare Access (service token)

    private var cloudflareAccessSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Cloudflare Access")
            Card {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Client ID (opzionale)")
                        .font(.system(size: 13)).foregroundStyle(Theme.label2)
                    TextField("xxxxxxxx.access", text: $settings.cfAccessClientID)
                        .font(.system(size: 16))
                        .foregroundStyle(Theme.label)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                }
                .padding(14)
                RowDivider()
                VStack(alignment: .leading, spacing: 6) {
                    Text("Client Secret (opzionale)")
                        .font(.system(size: 13)).foregroundStyle(Theme.label2)
                    SecureField("Service-token secret", text: $settings.cfAccessClientSecret)
                        .font(.system(size: 16))
                        .foregroundStyle(Theme.label)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                .padding(14)
            }
            .padding(.horizontal, 16)

            Text("Necessario solo se il backend è protetto da Cloudflare Access. "
                + "Entrambi i campi vengono inviati come header su ogni richiesta; "
                + "lascia vuoto per l’uso locale / Tailscale.")
                .font(.system(size: 13))
                .foregroundStyle(Theme.label2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 20)
                .padding(.top, 8)

            if !settings.cfAccessClientID.isEmpty || !settings.cfAccessClientSecret.isEmpty {
                Button {
                    settings.cfAccessClientID = ""
                    settings.cfAccessClientSecret = ""
                } label: {
                    Text("Cancella token Cloudflare")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Theme.destructive)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 20)
                .padding(.top, 10)
            }
        }
    }

    // MARK: Appearance

    private var appearanceSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Aspetto")
            HStack(spacing: 6) {
                ForEach(AppSettings.Appearance.allCases) { mode in
                    let active = settings.appearance == mode
                    Button { settings.appearance = mode } label: {
                        Text(mode.label)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(active ? Color.white : Theme.label)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(active ? Theme.tint : Color.clear)
                            .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(8)
            .background(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .padding(.horizontal, 16)
        }
    }

    // MARK: Providers (read-only reflection of provider_config)

    private var providerSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Provider AI")
            Card {
                if providers.isEmpty {
                    HStack {
                        Text("Nessun provider configurato sul server")
                            .font(.system(size: 14)).foregroundStyle(Theme.label2)
                        Spacer()
                    }
                    .padding(14)
                } else {
                    ForEach(Array(providers.enumerated()), id: \.element.id) { idx, p in
                        HStack(spacing: 12) {
                            Text(providerIcon(p.providerType)).font(.system(size: 20))
                            VStack(alignment: .leading, spacing: 1) {
                                Text(providerName(p))
                                    .font(.system(size: 16)).foregroundStyle(Theme.label)
                                Text(providerDetail(p))
                                    .font(.system(size: 13)).foregroundStyle(Theme.label2)
                            }
                            Spacer()
                            if p.isFallback == true {
                                Text("fallback").font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(Theme.label2)
                            }
                        }
                        .padding(14)
                        if idx < providers.count - 1 { RowDivider() }
                    }
                }
            }
            .padding(.horizontal, 16)
        }
    }

    // MARK: Vault

    private var vaultSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Vault")
            Card {
                infoRow("Vault", value: settings.vaultID)
                RowDivider()
                infoRow("Data version",
                        value: app.status?.dataVersion.map(String.init) ?? "—")
                RowDivider()
                infoRow("Server", value: app.serverVersion ?? "—")
            }
            .padding(.horizontal, 16)
        }
    }

    private func infoRow(_ label: String, value: String) -> some View {
        HStack {
            Text(label).font(.system(size: 16)).foregroundStyle(Theme.label)
            Spacer()
            Text(value).font(.system(size: 14)).foregroundStyle(Theme.label2)
        }
        .padding(14)
    }

    // MARK: Actions

    private func testConnection() async {
        testing = true
        testResult = nil
        guard let client = settings.makeClient() else {
            testResult = .fail(APIError.notConfigured.errorDescription ?? "URL mancante")
            testing = false
            return
        }
        do {
            let s = try await client.status()
            let v = s.version ?? "?"
            let pending = s.reviewPending ?? 0
            testResult = .ok("Connesso a Synapse \(v) · \(pending) da rivedere")
            await app.refresh(settings)
            await loadProviders()
        } catch {
            testResult = .fail((error as? APIError)?.errorDescription ?? error.localizedDescription)
        }
        testing = false
    }

    private func loadProviders() async {
        guard let client = settings.makeClient() else { return }
        providers = (try? await client.providerConfig())?.items ?? []
    }

    private func providerIcon(_ type: String?) -> String {
        switch type {
        case "local": return "💻"
        case "api": return "☁️"
        case "cli": return "⌨️"
        default: return "⚙️"
        }
    }
    private func providerName(_ p: ProviderConfigRow) -> String {
        let scope = p.operation ?? p.scope ?? ""
        let base = (p.providerType ?? "provider").capitalized
        return scope.isEmpty ? base : "\(base) · \(scope)"
    }
    private func providerDetail(_ p: ProviderConfigRow) -> String {
        p.modelID ?? p.baseURL ?? "—"
    }
}

private extension SettingsView.TestResult {
    var isOK: Bool { if case .ok = self { return true }; return false }
    var message: String {
        switch self { case .ok(let m), .fail(let m): return m }
    }
}
