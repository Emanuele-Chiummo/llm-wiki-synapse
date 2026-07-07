import SwiftUI

/// User-configurable connection + appearance settings, persisted in
/// `UserDefaults`. Injected as an `@EnvironmentObject` so any screen can read
/// the current server config or flip the theme.
@MainActor
final class AppSettings: ObservableObject {

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
            case .system: return "Sistema"
            case .light: return "Light"
            case .dark: return "Dark"
            }
        }
    }

    /// Base URL of the Synapse FastAPI server, e.g. `http://localhost:8000`.
    @Published var serverURLString: String {
        didSet { defaults.set(serverURLString, forKey: Keys.server) }
    }

    /// Optional bearer token (only needed if the server sets `SYNAPSE_AUTH_TOKEN`).
    @Published var authToken: String {
        didSet { defaults.set(authToken, forKey: Keys.token) }
    }

    /// Cloudflare Access service-token Client ID (e.g. `<id>.access`). Sent as
    /// `CF-Access-Client-Id` when both halves are present, so the app can pass
    /// the Cloudflare Access gate in front of the production backend. Leave both
    /// empty for local / Tailscale use without Cloudflare Access.
    @Published var cfAccessClientID: String {
        didSet { defaults.set(cfAccessClientID, forKey: Keys.cfClientID) }
    }

    /// Cloudflare Access service-token Client Secret. Sent as
    /// `CF-Access-Client-Secret`. Sensitive — masked in the UI.
    @Published var cfAccessClientSecret: String {
        didSet { defaults.set(cfAccessClientSecret, forKey: Keys.cfClientSecret) }
    }

    @Published var appearance: Appearance {
        didSet { defaults.set(appearance.rawValue, forKey: Keys.appearance) }
    }

    /// Logical vault id; the backend defaults to `default` for single-vault use.
    @Published var vaultID: String {
        didSet { defaults.set(vaultID, forKey: Keys.vault) }
    }

    private let defaults: UserDefaults

    private enum Keys {
        static let server = "synapse.serverURL"
        static let token = "synapse.authToken"
        static let appearance = "synapse.appearance"
        static let vault = "synapse.vaultID"
        static let cfClientID = "synapse.cfAccessClientID"
        static let cfClientSecret = "synapse.cfAccessClientSecret"
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.serverURLString = defaults.string(forKey: Keys.server) ?? "http://localhost:8000"
        self.authToken = defaults.string(forKey: Keys.token) ?? ""
        self.appearance = Appearance(rawValue: defaults.string(forKey: Keys.appearance) ?? "")
            ?? .system
        self.vaultID = defaults.string(forKey: Keys.vault) ?? "default"
        self.cfAccessClientID = defaults.string(forKey: Keys.cfClientID) ?? ""
        self.cfAccessClientSecret = defaults.string(forKey: Keys.cfClientSecret) ?? ""
    }

    /// The normalised base URL (trailing slash stripped), or nil if unparseable.
    var baseURL: URL? {
        var s = serverURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        while s.hasSuffix("/") { s.removeLast() }
        guard !s.isEmpty else { return nil }
        // Tolerate a bare host:port by assuming http.
        if !s.contains("://") { s = "http://" + s }
        return URL(string: s)
    }
}
