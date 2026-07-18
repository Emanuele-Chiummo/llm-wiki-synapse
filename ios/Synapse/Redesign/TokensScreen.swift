import Observation
import SwiftUI

@Observable
@MainActor
final class TokensModel {
    var tokens: LoadState<[API.TokenListItem]> = .idle
    var creating = false
    /// The plaintext secret of a just-created token — shown ONCE, then cleared.
    var revealedSecret: API.TokenCreateResponse?

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            tokens = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if tokens.value == nil { tokens = .loading }
        do {
            tokens = .loaded(try await client.listTokens().tokens)
        } catch {
            tokens = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
        }
    }

    /// Create a scoped token and — if it's a read/write token scoped to make
    /// this device work — adopt it as this device's stored token.
    func create(_ session: SynapseSession, label: String, readOnly: Bool,
                scopeToVault: Bool, adopt: Bool) async {
        guard let client = session.client() else { return }
        creating = true
        defer { creating = false }
        do {
            let resp = try await client.createToken(
                label: label, readOnly: readOnly,
                vaultID: scopeToVault ? session.vaultID : nil)
            revealedSecret = resp
            if adopt && !readOnly {
                // Swap this device onto the freshly-minted scoped token.
                session.token = resp.token
                session.reconfigure()
            }
            await load(session)
        } catch {
            tokens = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
        }
    }

    func delete(_ session: SynapseSession, id: String) async {
        guard let client = session.client() else { return }
        try? await client.deleteToken(id: id)
        await load(session)
    }
}

/// Manage per-device scoped API tokens (PF-AUTH-1). Generate a token scoped to
/// this device (optionally read-only / vault-scoped), see the secret once, and
/// revoke any token independently of the others.
struct TokensScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = TokensModel()
    @State private var showCreate = false

    var body: some View {
        List {
            Section {
                Text("Tokens authenticate this app with **Authorization: Bearer**. Create one per device so it can be revoked on its own; the secret is shown only once and stored in the Keychain.")
                    .font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                    .listRowBackground(Color.clear)
            }
            content
        }
        .scrollContentBackground(.hidden)
        .synScreenBackground()
        .navigationTitle("Device tokens")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showCreate = true } label: { Image(systemName: "plus") }
                    .accessibilityLabel("New token")
            }
        }
        .task { await model.load(session) }
        .sheet(isPresented: $showCreate) {
            CreateTokenSheet(model: model)
        }
        .sheet(item: Binding(get: { model.revealedSecret }, set: { model.revealedSecret = $0 })) { resp in
            RevealSecretSheet(response: resp)
        }
    }

    @ViewBuilder private var content: some View {
        switch model.tokens {
        case .idle, .loading where model.tokens.value == nil:
            ForEach(0..<3, id: \.self) { _ in
                SynSkeletonLine(height: 40).listRowBackground(Color.clear)
            }
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
                .listRowBackground(Color.clear).listRowSeparator(.hidden)
        default:
            let tokens = model.tokens.value ?? []
            if tokens.isEmpty {
                Text("No device tokens yet.").font(SynFont.subhead)
                    .foregroundStyle(SynColor.textMuted).listRowBackground(Color.clear)
            } else {
                ForEach(tokens) { t in tokenRow(t) }
            }
        }
    }

    private func tokenRow(_ t: API.TokenListItem) -> some View {
        HStack(spacing: SynSpace.x4) {
            Image(systemName: t.readOnly ? "lock.fill" : "key.fill")
                .foregroundStyle(SynColor.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(t.label).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                HStack(spacing: SynSpace.x2) {
                    if t.readOnly { Text("read-only").font(SynFont.eyebrow).foregroundStyle(SynColor.amber) }
                    Text(t.vaultID.map { "vault: \($0)" } ?? "global")
                        .font(SynFont.caption).foregroundStyle(SynColor.textDim)
                }
            }
            Spacer(minLength: 0)
        }
        .swipeActions {
            Button(role: .destructive) {
                Task { await model.delete(session, id: t.id) }
            } label: { Label("Revoke", systemImage: "trash") }
        }
    }
}

private struct CreateTokenSheet: View {
    @Bindable var model: TokensModel
    @Environment(SynapseSession.self) private var session
    @Environment(\.dismiss) private var dismiss

    @State private var label = "iPhone"
    @State private var readOnly = false
    @State private var scopeToVault = true
    @State private var adopt = true

    var body: some View {
        NavigationStack {
            Form {
                Section("Label") {
                    TextField("This device", text: $label)
                }
                Section {
                    Toggle("Read-only", isOn: $readOnly)
                    Toggle("Scope to vault “\(session.vaultID)”", isOn: $scopeToVault)
                    if !readOnly {
                        Toggle("Use on this device", isOn: $adopt)
                    }
                } footer: {
                    Text(readOnly
                         ? "A read-only token can browse and search but not ingest or edit."
                         : "“Use on this device” stores the new token in the Keychain and reconnects with it.")
                }
                Section {
                    Button {
                        Task {
                            await model.create(session, label: label, readOnly: readOnly,
                                               scopeToVault: scopeToVault, adopt: adopt)
                            dismiss()
                        }
                    } label: {
                        HStack { Text("Generate token"); Spacer(); if model.creating { ProgressView() } }
                    }
                    .disabled(label.trimmingCharacters(in: .whitespaces).isEmpty || model.creating)
                }
            }
            .navigationTitle("New device token")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } } }
        }
    }
}

private struct RevealSecretSheet: View {
    let response: API.TokenCreateResponse
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: SynSpace.x6) {
                SynSectionHeader(text: "Save this token now", accent: true)
                Text("This secret is shown only once. If you didn't choose “Use on this device”, copy it somewhere safe — it can't be retrieved again.")
                    .font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
                SynCard(padding: SynSpace.x5) {
                    Text(response.token)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(SynColor.text)
                        .textSelection(.enabled)
                }
                SynButton(title: "Copy token", systemImage: "doc.on.doc", fullWidth: true) {
                    UIPasteboard.general.string = response.token
                }
                Spacer()
            }
            .padding(SynSpace.x6)
            .synScreenBackground()
            .navigationTitle(response.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
}
