import Foundation

/// Wire DTOs + fetch for the knowledge graph (F4). The coordinates are the
/// **server-side, FA2-precomputed** `x`/`y` from `GET /graph` — the client only
/// renders them, it NEVER runs a force layout (invariant I2). The renderer is a
/// swappable seam (`makeGraphRenderer`) so the still-open native-vs-WKWebView
/// decision (ADR-0088, pending an on-device perf check) can flip without
/// touching the models or the Graph tab.
extension API {
    struct GraphNode: Decodable, Identifiable, Hashable {
        let id: String
        let title: String?
        let type: String?
        let x: Double
        let y: Double
        let size: Double
        let degree: Int
        let community: Int
        let domain: String?

        var displayTitle: String {
            if let t = title, !t.isEmpty { return t }
            return "Untitled"
        }
    }

    struct GraphEdge: Decodable, Hashable {
        let source: String
        let target: String
        let weight: Double
        let kind: String
    }

    struct GraphCommunity: Decodable, Identifiable, Hashable {
        let id: Int
        let size: Int
        let cohesion: Double
        let label: String
        let dominantDomain: String?

        enum CodingKeys: String, CodingKey {
            case id, size, cohesion, label
            case dominantDomain = "dominant_domain"
        }
    }

    struct GraphData: Decodable {
        let nodes: [GraphNode]
        let edges: [GraphEdge]
        let dataVersion: Int?
        let cached: Bool?
        let communities: [GraphCommunity]?
        let totalNodes: Int?
        let totalEdges: Int?

        enum CodingKeys: String, CodingKey {
            case nodes, edges, cached, communities
            case dataVersion = "data_version"
            case totalNodes = "total_nodes"
            case totalEdges = "total_edges"
        }
    }
}

extension APIClient {
    /// Fetch the precomputed graph (nodes carry server-side FA2 coords — I2).
    /// The endpoint renders the server's active vault (no vault_id param, per
    /// `docs/api/openapi.json`), matching the desktop graph viewer.
    func graph() async throws -> API.GraphData {
        try await send(request("graph", timeout: 60), as: API.GraphData.self)
    }
}
