import Observation
import SwiftUI

/// The redesign's single source of connection state + live push signals.
///
/// Owns the connection config — server URL, vault id and the Cloudflare Access
/// client id live in `UserDefaults` (non-secret); the **scoped API token** and
/// the CF Access *secret* live only in the **Keychain** (hard security
/// requirement, PF-AUTH-1). Vends an `APIClient` per operation, and runs the
/// `GET /events` SSE lifecycle so `dataVersion` / `queue` / `reviewPending`
/// update live app-wide (the desktop `statusStore` + `eventsStore` equivalent).
@Observable
@MainActor
final class SynapseSession {
    enum Reachability: Equatable {
        case unknown, connecting, online, offline(String)
    }

    enum Appearance: String, CaseIterable, Identifiable {
        case system, light, dark
        var id: String { rawValue }
        var colorScheme: ColorScheme? {
            switch self {
            case .system: return nil
            case .light: return .light
            case .dark: return .dark
            }
        }
        var label: String {
            switch self {
            case .system: return "System"
            case .light: return "Light"
            case .dark: return "Dark"
            }
        }
    }

    // MARK: Persisted, non-secret config
    var serverURLString: String {
        didSet { UserDefaults.standard.set(serverURLString, forKey: Keys.server) }
    }
    var vaultID: String {
        didSet { UserDefaults.standard.set(vaultID, forKey: Keys.vault) }
    }
    var cfAccessClientID: String {
        didSet { UserDefaults.standard.set(cfAccessClientID, forKey: Keys.cfID) }
    }

    // MARK: Secrets (Keychain-backed; never UserDefaults)
    /// The per-device scoped API token. Setting writes straight to the Keychain.
    var token: String {
        didSet { Keychain.set(token, account: Keychain.Account.apiToken) }
    }
    var cfAccessClientSecret: String {
        didSet { Keychain.set(cfAccessClientSecret, account: Keychain.Account.cfAccessSecret) }
    }

    // MARK: Appearance (non-secret)
    var appearance: Appearance {
        didSet { UserDefaults.standard.set(appearance.rawValue, forKey: Keys.appearance) }
    }

    // MARK: Live state (fed by SSE + a status probe)
    var reachability: Reachability = .unknown
    var serverVersion: String?
    var dataVersion: Int = 0
    var reviewPending: Int = 0
    var supportsVision: Bool = false
    var queue = EventsClient.QueueState()
    /// False after 3 consecutive SSE reconnect failures (see `EventsClient`).
    var streamHealthy = true

    private var events: EventsClient?

    private enum Keys {
        static let server = "syn.redesign.serverURL"
        static let vault = "syn.redesign.vaultID"
        static let cfID = "syn.redesign.cfClientID"
        static let appearance = "syn.redesign.appearance"
    }

    init() {
        let d = UserDefaults.standard
        self.serverURLString = d.string(forKey: Keys.server) ?? "http://localhost:8000"
        // `-synVault <id>` launch override (screenshot harness / testing only);
        // otherwise the persisted vault, defaulting to "default".
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-synVault"), i + 1 < args.count {
            self.vaultID = args[i + 1]
        } else {
            self.vaultID = d.string(forKey: Keys.vault) ?? "default"
        }
        self.cfAccessClientID = d.string(forKey: Keys.cfID) ?? ""
        self.appearance = Appearance(rawValue: d.string(forKey: Keys.appearance) ?? "") ?? .system
        self.token = Keychain.get(account: Keychain.Account.apiToken) ?? ""
        self.cfAccessClientSecret = Keychain.get(account: Keychain.Account.cfAccessSecret) ?? ""
    }

    // MARK: Connection

    /// True once a server URL is set (a token may still be required by the server).
    var isConfigured: Bool { baseURL != nil }

    var baseURL: URL? {
        var s = serverURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        while s.hasSuffix("/") { s.removeLast() }
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "http://" + s }
        return URL(string: s)
    }

    var connection: Connection? {
        guard let baseURL else { return nil }
        return Connection(
            baseURL: baseURL,
            token: token.trimmingCharacters(in: .whitespacesAndNewlines),
            vaultID: vaultID,
            cfAccessClientID: cfAccessClientID.trimmingCharacters(in: .whitespacesAndNewlines),
            cfAccessClientSecret: cfAccessClientSecret.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// A fresh client for one operation, or nil if not configured.
    func client() -> APIClient? {
        guard let connection else { return nil }
        return APIClient(connection: connection)
    }

    // MARK: Lifecycle

    /// Probe `/status` and (re)start the SSE stream. Call on appear and after a
    /// settings change.
    func connect() async {
        guard let client = client() else {
            reachability = .offline("No server configured")
            return
        }
        if case .online = reachability {} else { reachability = .connecting }
        do {
            let s = try await client.status()
            serverVersion = s.version
            supportsVision = s.supportsVision ?? false
            if let dv = s.dataVersion { dataVersion = dv }
            if let rp = s.reviewPending { reviewPending = rp }
            reachability = .online
            startStream()
        } catch {
            let msg = (error as? SynAPIError)?.errorDescription ?? error.localizedDescription
            reachability = .offline(msg)
        }
    }

    /// Restart everything after the user changes server/token in Settings.
    func reconfigure() {
        stopStream()
        reachability = .unknown
        Task { await connect() }
    }

    private func startStream() {
        guard let connection else { return }
        let events = EventsClient(connection: connection)
        self.events = events
        Task {
            await events.start(
                onEvent: { [weak self] ev in
                    Task { @MainActor in self?.apply(ev) }
                },
                onHealth: { [weak self] healthy in
                    Task { @MainActor in self?.streamHealthy = healthy }
                })
        }
    }

    private func stopStream() {
        let events = self.events
        self.events = nil
        Task { await events?.stop() }
    }

    private func apply(_ event: EventsClient.Event) {
        switch event {
        case .dataVersion(let dv):
            // Monotonic guard (mirrors statusStore.setDataVersion, FE bug #6):
            // a late REST value must never clobber a fresher pushed one.
            if dv > dataVersion { dataVersion = dv }
        case .queue(let q):
            queue = q
        }
    }
}
