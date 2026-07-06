import Foundation

// MARK: - Status / health

struct StatusResponse: Codable {
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

// MARK: - Stats overview

struct StatsOverview: Codable {
    let pagesTotal: Int
    let linksTotal: Int
    let reviewPending: Int?
    let communitiesCount: Int?
    let pagesByType: [String: Int]?
    let dataVersion: Int?
    let recentActivity: [RecentActivity]?

    enum CodingKeys: String, CodingKey {
        case pagesTotal = "pages_total"
        case linksTotal = "links_total"
        case reviewPending = "review_pending"
        case communitiesCount = "communities_count"
        case pagesByType = "pages_by_type"
        case dataVersion = "data_version"
        case recentActivity = "recent_activity"
    }
}

struct RecentActivity: Codable, Identifiable, Hashable {
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
}

// MARK: - Pages

struct PageSummary: Codable, Identifiable, Hashable {
    let id: String
    let title: String?
    let type: String?
    let filePath: String?
    let sources: [String]?
    let updatedAt: Date?
    let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, title, type, sources
        case filePath = "file_path"
        case updatedAt = "updated_at"
        case createdAt = "created_at"
    }

    var displayTitle: String {
        if let t = title, !t.isEmpty { return t }
        // Fall back to the filename stem.
        if let p = filePath {
            return (p as NSString).lastPathComponent
                .replacingOccurrences(of: ".md", with: "")
        }
        return "Senza titolo"
    }
}

struct PageListResponse: Codable {
    let items: [PageSummary]
    let total: Int
    let limit: Int?
    let offset: Int?
}

struct PageContent: Codable {
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

struct RelatedPage: Codable, Identifiable, Hashable {
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

struct RelatedPagesResponse: Codable {
    let items: [RelatedPage]
    let total: Int?
}

// MARK: - Search

struct SearchResult: Codable, Identifiable, Hashable {
    let n: Int?
    let id: String
    let title: String?
    let slug: String?
    let score: Double?
    let phase: String?
}

struct SearchResponse: Codable {
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

// MARK: - Graph

struct GraphNode: Codable, Identifiable, Hashable {
    let id: String
    let title: String?
    let type: String?
    let x: Double
    let y: Double
    let size: Double?
    let degree: Int?
    let community: Int?
    let domain: String?
}

struct GraphEdge: Codable, Hashable {
    let source: String
    let target: String
    let weight: Double?
    let kind: String?
}

struct GraphCommunity: Codable, Identifiable, Hashable {
    let id: Int
    let size: Int?
    let label: String?
    let dominantDomain: String?

    enum CodingKeys: String, CodingKey {
        case id, size, label
        case dominantDomain = "dominant_domain"
    }
}

struct GraphResponse: Codable {
    let nodes: [GraphNode]
    let edges: [GraphEdge]
    let dataVersion: Int?
    let communities: [GraphCommunity]?
    let totalNodes: Int?
    let totalEdges: Int?

    enum CodingKeys: String, CodingKey {
        case nodes, edges, communities
        case dataVersion = "data_version"
        case totalNodes = "total_nodes"
        case totalEdges = "total_edges"
    }
}

// MARK: - Chat

/// One decoded NDJSON event from `POST /chat/stream`.
enum ChatStreamEvent {
    case token(String)
    case think(String)
    case done(ChatDone)
    case error(String)
}

struct ChatDone: Decodable {
    let messageID: String?
    let totalCostUSD: Double?
    let citations: [Citation]?

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case totalCostUSD = "total_cost_usd"
        case citations
    }
}

/// Lenient citation decoder — the backend may key the page id as `id`,
/// `page_id`, or nest a slug. All fields optional.
struct Citation: Decodable, Hashable, Identifiable {
    let n: Int?
    let pageID: String?
    let title: String?
    let slug: String?

    var id: String { pageID ?? slug ?? "\(n ?? 0)-\(title ?? "")" }

    enum CodingKeys: String, CodingKey {
        case n, title, slug
        case pageID = "page_id"
        case idAlt = "id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        n = try? c.decodeIfPresent(Int.self, forKey: .n)
        title = try? c.decodeIfPresent(String.self, forKey: .title)
        slug = try? c.decodeIfPresent(String.self, forKey: .slug)
        let pid = try? c.decodeIfPresent(String.self, forKey: .pageID)
        let alt = try? c.decodeIfPresent(String.self, forKey: .idAlt)
        pageID = pid ?? alt
    }

    init(n: Int?, pageID: String?, title: String?, slug: String?) {
        self.n = n; self.pageID = pageID; self.title = title; self.slug = slug
    }
}

// MARK: - Review queue

struct ReviewItem: Codable, Identifiable, Hashable {
    let id: String
    let itemType: String?
    let status: String?
    let proposedTitle: String?
    let proposedPageType: String?
    let rationale: String?
    let pageTitle: String?
    let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, status, rationale
        case itemType = "item_type"
        case proposedTitle = "proposed_title"
        case proposedPageType = "proposed_page_type"
        case pageTitle = "page_title"
        case createdAt = "created_at"
    }

    /// Best available human title for the card.
    var displayTitle: String {
        proposedTitle ?? pageTitle ?? "Proposta"
    }

    /// Displayed page type (proposed type, else fall back to concept colouring).
    var displayType: String? { proposedPageType }
}

struct ReviewQueueResponse: Codable {
    let items: [ReviewItem]
    let total: Int?
    let limit: Int?
    let offset: Int?
}

// MARK: - Provider config

struct ProviderConfigRow: Codable, Identifiable, Hashable {
    let id: String
    let scope: String?
    let operation: String?
    let providerType: String?
    let modelID: String?
    let baseURL: String?
    let isFallback: Bool?

    enum CodingKeys: String, CodingKey {
        case id, scope, operation
        case providerType = "provider_type"
        case modelID = "model_id"
        case baseURL = "base_url"
        case isFallback = "is_fallback"
    }
}

struct ProviderConfigListResponse: Codable {
    let items: [ProviderConfigRow]
    let total: Int?
}

// MARK: - Ingest

struct IngestRun: Codable, Identifiable, Hashable {
    let id: String
    let status: String?
    let providerType: String?
    let pagesCreated: Int?
    let totalCostUSD: Double?
    let startedAt: Date?
    let completedAt: Date?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case id, status
        case providerType = "provider_type"
        case pagesCreated = "pages_created"
        case totalCostUSD = "total_cost_usd"
        case startedAt = "started_at"
        case completedAt = "completed_at"
        case errorMessage = "error_message"
    }
}

struct IngestRunListResponse: Codable {
    let items: [IngestRun]
    let total: Int?
}

// MARK: - Research

struct ResearchStartResponse: Codable {
    let runID: String
    enum CodingKeys: String, CodingKey { case runID = "run_id" }
}

struct ResearchSource: Codable, Identifiable, Hashable {
    let url: String
    let title: String?
    let relevanceScore: Double?
    let iteration: Int?

    var id: String { url }

    enum CodingKeys: String, CodingKey {
        case url, title, iteration
        case relevanceScore = "relevance_score"
    }
}

struct ResearchRunDetail: Codable {
    let id: String
    let topic: String
    let status: String
    let iterationsUsed: Int?
    let sourcesFetched: Int?
    let totalCostUSD: Double?
    let synthesisText: String?
    let synthesisPageID: String?
    let sources: [ResearchSource]?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case id, topic, status, sources
        case iterationsUsed = "iterations_used"
        case sourcesFetched = "sources_fetched"
        case totalCostUSD = "total_cost_usd"
        case synthesisText = "synthesis_text"
        case synthesisPageID = "synthesis_page_id"
        case errorMessage = "error_message"
    }

    var isDone: Bool { status != "running" }
}
