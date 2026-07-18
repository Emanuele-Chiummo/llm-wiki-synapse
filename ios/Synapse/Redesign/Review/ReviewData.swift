import Foundation

/// Wire DTOs + calls for the HITL review queue (F9). Mirrors the desktop review
/// model: pre-generated proposals a human resolves with one of three actions —
/// **Create**, **Deep-Research**, **Skip**. All calls reuse the single APIClient
/// error path (ADR-0086).
extension API {
    struct ReviewItem: Decodable, Identifiable, Hashable {
        let id: String
        let vaultID: String
        let itemType: String
        let status: String
        let proposedTitle: String?
        let proposedPageType: String?
        let proposalOrigin: String
        let rationale: String?
        let pageID: String?
        let pageTitle: String?
        let createdPageID: String?
        let resolution: String?
        let searchQueries: [String]?
        let createdAt: Date

        enum CodingKeys: String, CodingKey {
            case id, status, rationale, resolution
            case vaultID = "vault_id"
            case itemType = "item_type"
            case proposedTitle = "proposed_title"
            case proposedPageType = "proposed_page_type"
            case proposalOrigin = "proposal_origin"
            case pageID = "page_id"
            case pageTitle = "page_title"
            case createdPageID = "created_page_id"
            case searchQueries = "search_queries"
            case createdAt = "created_at"
        }

        var displayTitle: String {
            if let t = proposedTitle, !t.isEmpty { return t }
            if let t = pageTitle, !t.isEmpty { return t }
            return "Untitled proposal"
        }
    }

    struct ReviewQueue: Decodable {
        let items: [ReviewItem]
        let total: Int
        let limit: Int?
        let offset: Int?
    }
}

extension APIClient {
    /// The review queue for the session vault (F9). `status` defaults to
    /// `pending` — the actionable items — matching the desktop queue view.
    func reviewQueue(status: String? = "pending", limit: Int = 100, offset: Int = 0)
        async throws -> API.ReviewQueue
    {
        var q = [URLQueryItem(name: "vault_id", value: connection.vaultID),
                 URLQueryItem(name: "limit", value: "\(limit)"),
                 URLQueryItem(name: "offset", value: "\(offset)")]
        if let status, !status.isEmpty { q.append(URLQueryItem(name: "status", value: status)) }
        return try await send(request("review/queue", query: q), as: API.ReviewQueue.self)
    }

    /// F9 action — accept the proposal and create the wiki page.
    func reviewCreate(itemID: String) async throws {
        _ = try await sendRaw(request("review/queue/\(itemID)/create", method: "POST"))
    }

    /// F9 action — spin the proposal into a bounded Deep-Research run (F10) that
    /// auto-ingests its synthesis. Returns immediately; progress shows in Activity.
    func reviewDeepResearch(itemID: String) async throws {
        _ = try await sendRaw(request("review/queue/\(itemID)/deep-research", method: "POST"))
    }

    /// F9 action — dismiss the proposal without creating anything.
    func reviewSkip(itemID: String) async throws {
        _ = try await sendRaw(request("review/queue/\(itemID)/skip", method: "POST"))
    }
}
