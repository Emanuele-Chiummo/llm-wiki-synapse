import Foundation

/// Request body for `POST /chat/stream`. **Invariant I6**: the client never
/// sends `provider_type` / `model_id` — the backend resolves the provider from
/// `provider_config`. Only the fields the UI actually varies are exposed here.
struct ChatStreamRequest: Encodable {
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

/// One decoded NDJSON line from `POST /chat/stream`. The wire discriminator is
/// the `type` field: `token` | `think` | `done` | `error` (ADR-0019 §2.2 frozen
/// schema). There is no separate `citation` line — citations arrive inside `done`.
enum SynStreamEvent {
    case token(String)
    case think(String)
    case done(Done)
    case error(code: String?, message: String)

    struct Done {
        let conversationID: String?
        let messageID: String?
        let inputTokens: Int?
        let outputTokens: Int?
        let totalCostUSD: Double?
        let finishReason: String?
        let citations: [API.Citation]
        let webCitations: [API.WebCitation]
    }

    /// Decode one NDJSON object. Returns nil for unknown / malformed lines
    /// (defensive: a stray line never aborts the stream).
    static func decode(_ data: Data) -> SynStreamEvent? {
        guard let env = try? API.decoder.decode(Envelope.self, from: data) else { return nil }
        switch env.type {
        case "token":
            return env.delta.map { .token($0) }
        case "think":
            return env.delta.map { .think($0) }
        case "done":
            guard let done = try? API.decoder.decode(DoneWire.self, from: data) else { return nil }
            return .done(Done(
                conversationID: done.conversationID,
                messageID: done.messageID,
                inputTokens: done.inputTokens,
                outputTokens: done.outputTokens,
                totalCostUSD: done.totalCostUSD,
                finishReason: done.finishReason,
                citations: done.citations ?? [],
                webCitations: done.webCitations ?? []))
        case "error":
            return .error(code: env.code, message: env.message ?? "The answer failed.")
        default:
            return nil
        }
    }

    private struct Envelope: Decodable {
        let type: String?
        let delta: String?
        let message: String?
        let code: String?
    }

    private struct DoneWire: Decodable {
        let conversationID: String?
        let messageID: String?
        let inputTokens: Int?
        let outputTokens: Int?
        let totalCostUSD: Double?
        let finishReason: String?
        let citations: [API.Citation]?
        let webCitations: [API.WebCitation]?

        enum CodingKeys: String, CodingKey {
            case conversationID = "conversation_id"
            case messageID = "message_id"
            case inputTokens = "input_tokens"
            case outputTokens = "output_tokens"
            case totalCostUSD = "total_cost_usd"
            case finishReason = "finish_reason"
            case citations
            case webCitations = "web_citations"
        }
    }
}
