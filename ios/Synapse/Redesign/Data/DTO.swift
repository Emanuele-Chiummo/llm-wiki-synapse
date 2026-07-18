import Foundation

/// Wire DTOs for the Synapse **2.0.0** API, consumed by the redesigned surfaces
/// (Track iOS 2.1, Fase B). Namespaced under `API.*` so they never collide with
/// the legacy `Networking/SynapseModels.swift` types (same module) — the legacy
/// client stays compiling for not-yet-migrated screens, exactly as ADR-0088
/// keeps `Theme.swift` alongside `SynColor`.
///
/// Field names and `CodingKeys` are taken verbatim from `docs/api/openapi.json`
/// and the router/schema source. Watch-outs honoured: the backend serialises
/// `page_type` as **`type`** on the wire; list responses carry
/// `total`/`limit`/`offset`; chat citations arrive only inside the `done` event.
enum API {}

// MARK: - Shared JSON coding

extension API {
    /// One decoder for every response — tolerant ISO-8601 (with / without
    /// fractional seconds), matching the backend's timestamp formats.
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let s = try decoder.singleValueContainer().decode(String.self)
            if let date = iso8601Frac.date(from: s) ?? iso8601Plain.date(from: s) {
                return date
            }
            throw DecodingError.dataCorrupted(
                .init(codingPath: decoder.codingPath,
                      debugDescription: "Unrecognised ISO-8601 date: \(s)"))
        }
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        return e
    }()

    private static let iso8601Frac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let iso8601Plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
}

// MARK: - Status

extension API {
    struct Status: Decodable {
        let vaultID: String?
        let dataVersion: Int?
        let version: String?
        let reviewPending: Int?
        let supportsVision: Bool?
        let uptimeSeconds: Double?

        enum CodingKeys: String, CodingKey {
            case vaultID = "vault_id"
            case dataVersion = "data_version"
            case version
            case reviewPending = "review_pending"
            case supportsVision = "supports_vision"
            case uptimeSeconds = "uptime_seconds"
        }
    }
}

// MARK: - Stats overview (F18 home dashboard)

extension API {
    struct StatsOverview: Decodable {
        let pagesTotal: Int
        let pagesByType: [String: Int]
        let linksTotal: Int
        let communitiesCount: Int?
        let reviewPending: Int?
        let lintOpen: Int?
        let monthlyCostUSD: Double?
        let dataVersion: Int?
        let recentActivity: [RecentActivity]

        enum CodingKeys: String, CodingKey {
            case pagesTotal = "pages_total"
            case pagesByType = "pages_by_type"
            case linksTotal = "links_total"
            case communitiesCount = "communities_count"
            case reviewPending = "review_pending"
            case lintOpen = "lint_open"
            case monthlyCostUSD = "monthly_cost_usd"
            case dataVersion = "data_version"
            case recentActivity = "recent_activity"
        }
    }

    struct RecentActivity: Decodable, Identifiable, Hashable {
        let pageID: String
        let title: String?
        let slug: String?
        let updatedAt: Date?

        var id: String { pageID }

        enum CodingKeys: String, CodingKey {
            case pageID = "page_id"
            case title, slug
            case updatedAt = "updated_at"
        }

        var displayTitle: String {
            if let t = title, !t.isEmpty { return t }
            return slug?.replacingOccurrences(of: "-", with: " ").capitalized ?? "Untitled"
        }
    }
}

// MARK: - Pages

extension API {
    struct Page: Decodable, Identifiable, Hashable {
        let id: String
        let vaultID: String?
        let filePath: String?
        let title: String?
        let type: String?
        let sources: [String]?
        let updatedAt: Date?
        let createdAt: Date?
        let domain: String?

        enum CodingKeys: String, CodingKey {
            case id, title, type, sources, domain
            case vaultID = "vault_id"
            case filePath = "file_path"
            case updatedAt = "updated_at"
            case createdAt = "created_at"
        }

        /// The slug used by citations / `by-slug` resolution (matches the
        /// backend `slugify(title)`: lowercase, non-alnum → `-`, trimmed).
        var slug: String { API.slugify(title ?? displayTitle) }

        var displayTitle: String {
            if let t = title, !t.isEmpty { return t }
            if let p = filePath {
                return (p as NSString).lastPathComponent.replacingOccurrences(of: ".md", with: "")
            }
            return "Untitled"
        }
    }

    struct PageList: Decodable {
        let items: [Page]
        let total: Int
        let limit: Int?
        let offset: Int?
    }

    struct PageContent: Decodable {
        let id: String
        let title: String?
        let filePath: String?
        let content: String
        let contentHash: String?
        let updatedAt: Date?
        let type: String?
        let sources: [String]?
        let tags: [String]?

        enum CodingKeys: String, CodingKey {
            case id, title, content, type, sources, tags
            case filePath = "file_path"
            case contentHash = "content_hash"
            case updatedAt = "updated_at"
        }
    }

    struct RelatedPage: Decodable, Identifiable, Hashable {
        let pageID: String
        let title: String?
        let type: String?
        let score: Double?

        var id: String { pageID }

        enum CodingKeys: String, CodingKey {
            case pageID = "page_id"
            case title, type, score
        }
    }

    struct RelatedList: Decodable {
        let items: [RelatedPage]
        let total: Int?
    }
}

// MARK: - Search

extension API {
    struct SearchResult: Decodable, Identifiable, Hashable {
        let n: Int?
        let id: String
        let title: String?
        let slug: String?
        let score: Double?
        let phase: String?
    }

    struct SearchResponse: Decodable {
        let query: String?
        let results: [SearchResult]
        let dataVersion: Int?
        let approxTokens: Int?
        let tokenBudget: Int?

        enum CodingKeys: String, CodingKey {
            case query, results
            case dataVersion = "data_version"
            case approxTokens = "approx_tokens"
            case tokenBudget = "token_budget"
        }
    }
}

// MARK: - Chat

extension API {
    struct Conversation: Decodable, Identifiable, Hashable {
        let id: String
        let title: String?
        let createdAt: Date?
        let updatedAt: Date?
        let preview: String?

        enum CodingKeys: String, CodingKey {
            case id, title, preview
            case createdAt = "created_at"
            case updatedAt = "updated_at"
        }

        var displayTitle: String {
            if let t = title, !t.isEmpty { return t }
            return "New conversation"
        }
    }

    struct ConversationList: Decodable {
        let items: [Conversation]
        let total: Int?
    }

    /// A persisted message from `GET /conversations/{id}/messages`. `content`
    /// still contains literal `<think>…</think>` spans and `[n]` markers — the
    /// renderer strips/derives those (I3: parsed once, not per token).
    struct Message: Decodable, Identifiable, Hashable {
        let id: String
        let role: String
        let content: String
        let citations: [Citation]?
        let createdAt: Date?

        enum CodingKeys: String, CodingKey {
            case id, role, content, citations
            case createdAt = "created_at"
        }
    }

    struct MessageList: Decodable {
        let items: [Message]
        let total: Int?
    }

    /// Compact wiki citation `{n, id, title, slug}` — arrives inside the chat
    /// `done` event and on persisted messages (ADR-0022 §2.4).
    struct Citation: Decodable, Hashable, Identifiable {
        let n: Int?
        let pageID: String?
        let title: String?
        let slug: String?

        var id: String { pageID ?? slug ?? "\(n ?? 0)" }

        enum CodingKeys: String, CodingKey {
            case n, title, slug
            case pageID = "id"
        }
    }

    /// Web citation `{index, title, url}` from a SearXNG-backed answer (`[Wn]`).
    struct WebCitation: Decodable, Hashable, Identifiable {
        let index: Int?
        let title: String?
        let url: String

        var id: String { url }
    }
}

// MARK: - Scoped API tokens (PF-AUTH-1)

extension API {
    struct TokenCreateRequest: Encodable {
        let label: String
        let vaultID: String?
        let readOnly: Bool

        enum CodingKeys: String, CodingKey {
            case label
            case vaultID = "vault_id"
            case readOnly = "read_only"
        }
    }

    /// Create response — `token` is the plaintext secret, returned EXACTLY ONCE.
    struct TokenCreateResponse: Decodable {
        let id: String
        let label: String
        let vaultID: String?
        let readOnly: Bool
        let createdAt: Date?
        let token: String

        enum CodingKeys: String, CodingKey {
            case id, label, token
            case vaultID = "vault_id"
            case readOnly = "read_only"
            case createdAt = "created_at"
        }
    }

    struct TokenListItem: Decodable, Identifiable, Hashable {
        let id: String
        let label: String
        let vaultID: String?
        let readOnly: Bool
        let createdAt: Date?
        let lastUsedAt: Date?

        enum CodingKeys: String, CodingKey {
            case id, label
            case vaultID = "vault_id"
            case readOnly = "read_only"
            case createdAt = "created_at"
            case lastUsedAt = "last_used_at"
        }
    }

    struct TokenList: Decodable {
        let tokens: [TokenListItem]
    }
}

// MARK: - Helpers

extension API {
    /// Mirror of the backend `slugify(title)` used for citations / by-slug
    /// resolution: lowercase, runs of non-`[a-z0-9]` → single `-`, trimmed.
    static func slugify(_ title: String) -> String {
        let lowered = title.lowercased()
        var out = ""
        var lastDash = false
        for ch in lowered {
            if ch.isLetter || ch.isNumber {
                out.append(ch)
                lastDash = false
            } else if !lastDash {
                out.append("-")
                lastDash = true
            }
        }
        return out.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }
}
