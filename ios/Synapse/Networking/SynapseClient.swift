import Foundation

enum APIError: LocalizedError {
    case notConfigured
    case badURL
    case http(status: Int, body: String?)
    case decoding(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "Nessun server configurato. Vai in Impostazioni per indicare l’URL di Synapse."
        case .badURL:
            return "URL del server non valido."
        case .http(let status, let body):
            if status == 401 { return "Non autorizzato (401). Controlla il token di accesso." }
            let extra = (body?.isEmpty == false) ? " — \(body!)" : ""
            return "Il server ha risposto \(status)\(extra)."
        case .decoding:
            return "Risposta del server non riconosciuta."
        case .transport(let e):
            return "Impossibile raggiungere il server: \(e.localizedDescription)"
        }
    }
}

/// Chat request body for `POST /chat/stream`.
struct ChatRequest: Encodable {
    struct Message: Encodable {
        let role: String
        let content: String
    }
    var conversationID: String? = nil
    var messages: [Message]
    var vaultID: String? = nil
    var operation: String = "chat"
    var regenerate: Bool = false
    var useWebSearch: Bool = false
    var retrievalMode: String = "standard"

    enum CodingKeys: String, CodingKey {
        case conversationID = "conversation_id"
        case messages
        case vaultID = "vault_id"
        case operation, regenerate
        case useWebSearch = "use_web_search"
        case retrievalMode = "retrieval_mode"
    }
}

/// Thin async wrapper over the Synapse FastAPI service. A value type built from
/// an `AppSettings` snapshot; create a fresh one per logical operation.
struct SynapseClient {
    let baseURL: URL
    let token: String
    let vaultID: String
    /// Cloudflare Access service-token pair. Both must be non-empty for the
    /// headers to be sent; leave empty for local / Tailscale use.
    var cfAccessClientID: String = ""
    var cfAccessClientSecret: String = ""
    var session: URLSession = .shared

    // MARK: JSON coding

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let s = try decoder.singleValueContainer().decode(String.self)
            if let date = iso8601Frac.date(from: s) ?? iso8601Plain.date(from: s) {
                return date
            }
            throw DecodingError.dataCorrupted(
                .init(codingPath: decoder.codingPath,
                      debugDescription: "Bad ISO-8601 date: \(s)"))
        }
        return d
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

    // MARK: Request building

    private func makeRequest(path: String, query: [URLQueryItem] = [], method: String = "GET")
        throws -> URLRequest
    {
        guard var comps = URLComponents(
            url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)
        else { throw APIError.badURL }
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 30
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        // Cloudflare Access service-token headers — sent only when both halves
        // are present, so requests to a plain local / Tailscale backend (no
        // Cloudflare Access) carry nothing extra.
        if !cfAccessClientID.isEmpty, !cfAccessClientSecret.isEmpty {
            req.setValue(cfAccessClientID, forHTTPHeaderField: "CF-Access-Client-Id")
            req.setValue(cfAccessClientSecret, forHTTPHeaderField: "CF-Access-Client-Secret")
        }
        return req
    }

    private func send<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(status: -1, body: nil)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(status: http.statusCode,
                                body: String(data: data, encoding: .utf8))
        }
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    // MARK: Endpoints

    func status() async throws -> StatusResponse {
        try await send(try makeRequest(path: "status"), as: StatusResponse.self)
    }

    func statsOverview() async throws -> StatsOverview {
        try await send(try makeRequest(path: "stats/overview"), as: StatsOverview.self)
    }

    func pages(limit: Int = 100, offset: Int = 0) async throws -> PageListResponse {
        let q = [URLQueryItem(name: "limit", value: "\(limit)"),
                 URLQueryItem(name: "offset", value: "\(offset)")]
        return try await send(try makeRequest(path: "pages", query: q), as: PageListResponse.self)
    }

    func pageContent(id: String) async throws -> PageContent {
        try await send(try makeRequest(path: "pages/\(id)/content"), as: PageContent.self)
    }

    func relatedPages(id: String, limit: Int = 12) async throws -> RelatedPagesResponse {
        let q = [URLQueryItem(name: "limit", value: "\(limit)")]
        return try await send(
            try makeRequest(path: "pages/\(id)/related", query: q), as: RelatedPagesResponse.self)
    }

    func pageBySlug(_ slug: String) async throws -> PageSummary {
        try await send(try makeRequest(path: "pages/by-slug/\(slug)"), as: PageSummary.self)
    }

    func search(_ query: String, type: String? = nil, sort: String? = nil, k: Int = 8)
        async throws -> SearchResponse
    {
        var q = [URLQueryItem(name: "q", value: query),
                 URLQueryItem(name: "k", value: "\(k)")]
        if let type, !type.isEmpty { q.append(URLQueryItem(name: "type", value: type)) }
        if let sort { q.append(URLQueryItem(name: "sort", value: sort)) }
        return try await send(try makeRequest(path: "search", query: q), as: SearchResponse.self)
    }

    func graph() async throws -> GraphResponse {
        try await send(try makeRequest(path: "graph"), as: GraphResponse.self)
    }

    func reviewQueue(status: String = "pending", limit: Int = 50) async throws
        -> ReviewQueueResponse
    {
        let q = [URLQueryItem(name: "vault_id", value: vaultID),
                 URLQueryItem(name: "status", value: status),
                 URLQueryItem(name: "limit", value: "\(limit)")]
        return try await send(
            try makeRequest(path: "review/queue", query: q), as: ReviewQueueResponse.self)
    }

    @discardableResult
    func reviewApprove(id: String) async throws -> ReviewItem {
        try await send(
            try makeRequest(path: "review/queue/\(id)/approve", method: "POST"),
            as: ReviewItem.self)
    }

    func reviewSkip(id: String) async throws {
        _ = try await sendRaw(try makeRequest(path: "review/queue/\(id)/skip", method: "POST"))
    }

    func providerConfig() async throws -> ProviderConfigListResponse {
        try await send(
            try makeRequest(path: "provider/config"), as: ProviderConfigListResponse.self)
    }

    func ingestRuns(limit: Int = 20) async throws -> IngestRunListResponse {
        let q = [URLQueryItem(name: "limit", value: "\(limit)")]
        return try await send(
            try makeRequest(path: "ingest/runs", query: q), as: IngestRunListResponse.self)
    }

    // MARK: Research (start + poll)

    func startResearch(topic: String) async throws -> ResearchStartResponse {
        var req = try makeRequest(path: "research/start", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(
            withJSONObject: ["vault_id": vaultID, "topic": topic])
        return try await send(req, as: ResearchStartResponse.self)
    }

    func researchRun(id: String) async throws -> ResearchRunDetail {
        try await send(try makeRequest(path: "research/runs/\(id)"), as: ResearchRunDetail.self)
    }

    // MARK: Chat streaming (NDJSON)

    private struct RawStreamEvent: Decodable {
        let token: String?
        let think: String?
        let error: String?
        let done: ChatDone?
    }

    /// Streams a chat turn, invoking `onEvent` for each NDJSON line. Runs until
    /// the stream ends or throws. `onEvent` is called on an arbitrary task; hop
    /// to the main actor inside if updating UI.
    func streamChat(_ body: ChatRequest, onEvent: @escaping (ChatStreamEvent) -> Void)
        async throws
    {
        var req = try makeRequest(path: "chat/stream", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 300
        req.httpBody = try JSONEncoder().encode(body)

        let (bytes, response): (URLSession.AsyncBytes, URLResponse)
        do {
            (bytes, response) = try await session.bytes(for: req)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(status: -1, body: nil)
        }
        guard (200..<300).contains(http.statusCode) else {
            // Drain a little body for the error message.
            var body = ""
            for try await line in bytes.lines { body += line; if body.count > 500 { break } }
            throw APIError.http(status: http.statusCode, body: body)
        }

        for try await line in bytes.lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8) else { continue }
            guard let raw = try? Self.decoder.decode(RawStreamEvent.self, from: data) else {
                continue
            }
            if let t = raw.token { onEvent(.token(t)) }
            else if let th = raw.think { onEvent(.think(th)) }
            else if let d = raw.done { onEvent(.done(d)) }
            else if let e = raw.error { onEvent(.error(e)) }
        }
    }

    @discardableResult
    private func sendRaw(_ req: URLRequest) async throws -> Data {
        let (data, response): (Data, URLResponse)
        do { (data, response) = try await session.data(for: req) }
        catch { throw APIError.transport(error) }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(status: -1, body: nil)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(status: http.statusCode,
                                body: String(data: data, encoding: .utf8))
        }
        return data
    }
}

extension AppSettings {
    /// Build a client from the current settings, or nil if the server URL is
    /// missing / unparseable.
    func makeClient() -> SynapseClient? {
        guard let url = baseURL else { return nil }
        return SynapseClient(
            baseURL: url,
            token: authToken,
            vaultID: vaultID,
            cfAccessClientID: cfAccessClientID.trimmingCharacters(in: .whitespacesAndNewlines),
            cfAccessClientSecret: cfAccessClientSecret.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}
