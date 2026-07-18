import Foundation

/// The single error type every redesign API call surfaces. It centralises
/// decoding of the Synapse 2.0.0 **stable error envelope** (ADR-0086):
///
///     { "error": { "code": "not_found", "message": "…", "status": 404, "details": … } }
///
/// mirroring how the desktop frontend centralises this in `api/errors.ts` —
/// one decode path, reused by every call site, never ad-hoc per call.
///
/// One documented divergence the decoder tolerates: the auth middleware's 401
/// short-circuits *before* the envelope handlers and returns a different body
/// (ADR-0052 §2.4): `{ "error": "unauthorized", "hint": "…" }` — here `error`
/// is a **string**, not an object. `decode(...)` handles both shapes.
enum SynAPIError: LocalizedError, Equatable {
    /// No server URL configured yet (first run).
    case notConfigured
    /// The configured server URL could not be parsed.
    case badURL
    /// A structured server error decoded from the stable envelope.
    case server(code: String, message: String, status: Int)
    /// An HTTP error whose body was NOT the envelope (fallback to status text).
    case http(status: Int, message: String)
    /// The response body could not be decoded into the expected type.
    case decoding(String)
    /// The request never reached the server (offline, DNS, TLS, timeout…).
    case transport(String)
    /// The stream ended or failed mid-flight.
    case stream(String)

    // MARK: Semantic helpers used by call sites / UI

    /// True for a 401 (missing/invalid token) — drives the "connect / set token"
    /// affordance instead of a generic error banner.
    var isUnauthorized: Bool {
        switch self {
        case .server(_, _, 401), .http(401, _): return true
        case .server(let code, _, _): return code == "authentication"
        default: return false
        }
    }

    /// The stable snake_case code when available (`not_found`, `rate_limited`…).
    var code: String? {
        if case .server(let code, _, _) = self { return code }
        return nil
    }

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "No server configured. Open Settings to set your Synapse server URL."
        case .badURL:
            return "The server URL is not valid."
        case .server(_, let message, let status):
            if status == 401 { return "Not authorized. Check your access token in Settings." }
            return message
        case .http(let status, let message):
            return message.isEmpty ? "The server responded \(status)." : message
        case .decoding(let detail):
            return "The server response could not be read. \(detail)"
        case .transport(let detail):
            return "Could not reach the server. \(detail)"
        case .stream(let detail):
            return detail
        }
    }

    // MARK: Central decode path

    /// Build an `SynAPIError` from a non-2xx response body. Tries the ADR-0086
    /// envelope, then the auth-401 `{error:string}` shape, then falls back to
    /// the raw body / status. This is the ONE place error bodies are parsed.
    static func decode(status: Int, body: Data) -> SynAPIError {
        // 1) The stable envelope: { "error": { code, message, status, details } }
        if let env = try? JSONDecoder().decode(Envelope.self, from: body) {
            return .server(code: env.error.code, message: env.error.message,
                           status: env.error.status)
        }
        // 2) Auth-middleware 401: { "error": "unauthorized", "hint": "…" }
        if let auth = try? JSONDecoder().decode(AuthEnvelope.self, from: body) {
            let msg = auth.hint.map { "\(auth.error) — \($0)" } ?? auth.error
            return .server(code: auth.error, message: msg, status: status)
        }
        // 3) Fallback: raw text (or nothing).
        let text = String(data: body, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return .http(status: status, message: text)
    }

    private struct Envelope: Decodable {
        struct Inner: Decodable {
            let code: String
            let message: String
            let status: Int
        }
        let error: Inner
    }

    private struct AuthEnvelope: Decodable {
        let error: String
        let hint: String?
    }
}
