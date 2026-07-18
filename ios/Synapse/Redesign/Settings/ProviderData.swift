import Foundation

/// Wire DTOs + calls for the inference-provider config (F17) and projects/vaults.
/// iOS *views* which providers are configured and which vault is active, and can
/// switch the active vault; provider creation/keys stay on desktop (the phone
/// isn't where you paste API keys). All reuse the single APIClient error path.
extension API {
    struct ProviderConfig: Decodable, Identifiable, Hashable {
        let id: String
        let scope: String
        let operation: String?
        let vaultID: String?
        let providerType: String
        let modelID: String
        let baseURL: String?
        let apiKeyConfigured: Bool
        let apiKeyMasked: String?
        let reasoningEffort: String?
        let maxIter: Int
        let tokenBudget: Int
        let isFallback: Bool

        enum CodingKeys: String, CodingKey {
            case id, scope, operation
            case vaultID = "vault_id"
            case providerType = "provider_type"
            case modelID = "model_id"
            case baseURL = "base_url"
            case apiKeyConfigured = "api_key_configured"
            case apiKeyMasked = "api_key_masked"
            case reasoningEffort = "reasoning_effort"
            case maxIter = "max_iter"
            case tokenBudget = "token_budget"
            case isFallback = "is_fallback"
        }

        /// The operation this row governs (ingest / chat / …) or "all" when global.
        var operationLabel: String {
            (operation?.isEmpty == false ? operation! : "all").capitalized
        }
    }

    struct ProviderConfigList: Decodable {
        let items: [ProviderConfig]
        let total: Int
    }

    struct Vendor: Decodable, Identifiable, Hashable {
        let id: String
        let displayName: String
        let providerType: String
        let needsAPIKey: Bool

        enum CodingKeys: String, CodingKey {
            case id
            case displayName = "display_name"
            case providerType = "provider_type"
            case needsAPIKey = "needs_api_key"
        }
    }

    struct VendorList: Decodable { let vendors: [Vendor] }

    // MARK: Projects / vaults

    struct Project: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let path: String
        let lastOpenedAt: Date?

        enum CodingKeys: String, CodingKey {
            case id, name, path
            case lastOpenedAt = "last_opened_at"
        }
    }

    struct ProjectsResponse: Decodable {
        let projects: [Project]
        let activeID: String?

        enum CodingKeys: String, CodingKey {
            case projects
            case activeID = "active_id"
        }
    }
}

extension APIClient {
    func providerConfigs() async throws -> API.ProviderConfigList {
        try await send(request("provider/config"), as: API.ProviderConfigList.self)
    }

    func providerVendors() async throws -> API.VendorList {
        try await send(request("provider/vendors"), as: API.VendorList.self)
    }

    func projects() async throws -> API.ProjectsResponse {
        try await send(request("projects"), as: API.ProjectsResponse.self)
    }

    /// Switch the server's active project/vault (desktop projects/activate).
    func activateProject(id: String) async throws {
        _ = try await sendRaw(request("projects/\(id)/activate", method: "POST"))
    }
}

extension API.ProviderConfig {
    /// Friendly provider label for the F17 backends.
    var providerLabel: String {
        switch providerType {
        case "ollama", "local": return "Local (Ollama)"
        case "api", "anthropic", "openai": return "API"
        case "cli": return "CLI (claude-agent-sdk)"
        default: return providerType.capitalized
        }
    }
    var providerIcon: String {
        switch providerType {
        case "ollama", "local": return "desktopcomputer"
        case "cli": return "terminal"
        default: return "cloud"
        }
    }
}
