import Foundation

/// An immutable snapshot of everything needed to reach a Synapse backend:
/// the base URL, the per-device scoped token (PF-AUTH-1), the logical vault,
/// and optional Cloudflare Access service-token creds for the production tunnel.
struct Connection: Equatable {
    let baseURL: URL
    let token: String
    let vaultID: String
    var cfAccessClientID: String = ""
    var cfAccessClientSecret: String = ""
}

/// The redesigned 2.0.0 API client. Every request flows through `send` /
/// `stream`, so the stable error envelope (ADR-0086) is decoded in exactly one
/// place — no ad-hoc per-call error parsing (mirrors the desktop `api/*.ts`).
///
/// A value type built from a `Connection` snapshot; make a fresh one per
/// logical operation (cheap). SSE lives in `EventsClient`; chat streaming is
/// `streamChat` here because it shares the request-building + auth path.
struct APIClient {
    let connection: Connection
    var session: URLSession = .shared

    // MARK: Request building

    private func request(
        _ path: String,
        query: [URLQueryItem] = [],
        method: String = "GET",
        jsonBody: Data? = nil,
        accept: String = "application/json",
        timeout: TimeInterval = 30
    ) throws -> URLRequest {
        guard var comps = URLComponents(
            url: connection.baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)
        else { throw SynAPIError.badURL }
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw SynAPIError.badURL }

        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = timeout
        req.setValue(accept, forHTTPHeaderField: "Accept")
        if let jsonBody {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = jsonBody
        }
        if !connection.token.isEmpty {
            req.setValue("Bearer \(connection.token)", forHTTPHeaderField: "Authorization")
        }
        // Cloudflare Access service-token headers — only when both halves are
        // present, so a plain LAN / Tailscale backend carries nothing extra.
        if !connection.cfAccessClientID.isEmpty, !connection.cfAccessClientSecret.isEmpty {
            req.setValue(connection.cfAccessClientID, forHTTPHeaderField: "CF-Access-Client-Id")
            req.setValue(connection.cfAccessClientSecret,
                         forHTTPHeaderField: "CF-Access-Client-Secret")
        }
        return req
    }

    // MARK: Core send (the one error-decoding path)

    private func send<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let data = try await sendRaw(req)
        do {
            return try API.decoder.decode(T.self, from: data)
        } catch {
            throw SynAPIError.decoding(String(describing: error))
        }
    }

    @discardableResult
    private func sendRaw(_ req: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw SynAPIError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw SynAPIError.transport("No HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw SynAPIError.decode(status: http.statusCode, body: data)
        }
        return data
    }

    // MARK: - Endpoints

    func status() async throws -> API.Status {
        try await send(request("status"), as: API.Status.self)
    }

    func statsOverview() async throws -> API.StatsOverview {
        try await send(request("stats/overview"), as: API.StatsOverview.self)
    }

    func pages(type: String? = nil, limit: Int = 200, offset: Int = 0) async throws -> API.PageList {
        var q = [URLQueryItem(name: "limit", value: "\(limit)"),
                 URLQueryItem(name: "offset", value: "\(offset)")]
        if let type, !type.isEmpty { q.append(URLQueryItem(name: "type", value: type)) }
        return try await send(request("pages", query: q), as: API.PageList.self)
    }

    func pageContent(id: String) async throws -> API.PageContent {
        try await send(request("pages/\(id)/content"), as: API.PageContent.self)
    }

    func relatedPages(id: String, limit: Int = 8) async throws -> API.RelatedList {
        let q = [URLQueryItem(name: "limit", value: "\(limit)")]
        return try await send(request("pages/\(id)/related", query: q), as: API.RelatedList.self)
    }

    /// Resolve a `[[wikilink]]` / citation slug to a full page (K5 navigation).
    func pageBySlug(_ slug: String) async throws -> API.Page {
        try await send(request("pages/by-slug/\(slug)"), as: API.Page.self)
    }

    func search(_ query: String, type: String? = nil, sort: String? = nil, k: Int = 12)
        async throws -> API.SearchResponse
    {
        var q = [URLQueryItem(name: "q", value: query),
                 URLQueryItem(name: "k", value: "\(k)"),
                 URLQueryItem(name: "vault_id", value: connection.vaultID)]
        if let type, !type.isEmpty { q.append(URLQueryItem(name: "type", value: type)) }
        if let sort, !sort.isEmpty { q.append(URLQueryItem(name: "sort", value: sort)) }
        return try await send(request("search", query: q), as: API.SearchResponse.self)
    }

    // MARK: Conversations

    func conversations(limit: Int = 50) async throws -> API.ConversationList {
        let q = [URLQueryItem(name: "limit", value: "\(limit)"),
                 URLQueryItem(name: "vault_id", value: connection.vaultID)]
        return try await send(request("conversations", query: q), as: API.ConversationList.self)
    }

    func messages(conversationID: String) async throws -> API.MessageList {
        try await send(request("conversations/\(conversationID)/messages"),
                       as: API.MessageList.self)
    }

    // MARK: Scoped API tokens (PF-AUTH-1)

    func createToken(label: String, readOnly: Bool, vaultID: String? = nil)
        async throws -> API.TokenCreateResponse
    {
        let body = try API.encoder.encode(
            API.TokenCreateRequest(label: label, vaultID: vaultID, readOnly: readOnly))
        return try await send(
            request("config/api-tokens", method: "POST", jsonBody: body),
            as: API.TokenCreateResponse.self)
    }

    func listTokens() async throws -> API.TokenList {
        try await send(request("config/api-tokens"), as: API.TokenList.self)
    }

    func deleteToken(id: String) async throws {
        _ = try await sendRaw(request("config/api-tokens/\(id)", method: "DELETE"))
    }

    // MARK: Chat streaming (NDJSON)

    /// Streams a chat turn, invoking `onEvent` for each decoded NDJSON line.
    /// Runs until the stream ends or throws. `onEvent` is called off the main
    /// actor — hop inside if updating UI. Parsing is transport-only here; the
    /// heavy markdown/LaTeX parse happens once at stream settle (I3).
    func streamChat(_ body: ChatStreamRequest,
                    onEvent: @escaping (SynStreamEvent) -> Void) async throws {
        let encoded = try API.encoder.encode(body)
        let req = try request("chat/stream", method: "POST", jsonBody: encoded,
                              accept: "application/x-ndjson", timeout: 300)

        let bytes: URLSession.AsyncBytes
        let response: URLResponse
        do {
            (bytes, response) = try await session.bytes(for: req)
        } catch {
            throw SynAPIError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw SynAPIError.transport("No HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            // Drain a little body for the envelope error.
            var buf = Data()
            for try await b in bytes { buf.append(b); if buf.count > 4096 { break } }
            throw SynAPIError.decode(status: http.statusCode, body: buf)
        }

        for try await line in bytes.lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8) else { continue }
            guard let event = SynStreamEvent.decode(data) else { continue }
            onEvent(event)
        }
    }
}
