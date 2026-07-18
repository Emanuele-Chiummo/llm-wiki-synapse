import Foundation

/// SSE consumer for `GET /events` (1.9.3 flagship push channel). Streams
/// `data_version` bumps and ingest-queue counters so Home / list views update
/// live instead of polling — the iOS mirror of the desktop `eventsStore.ts`.
///
/// Apple ships no `EventSource` system API, and native `EventSource` couldn't
/// send our custom auth headers anyway, so this parses the SSE wire by hand
/// over `URLSession.bytes`. Reconnect mirrors the desktop reference: exponential
/// backoff `1→2→4→8→16→30s`, carrying `Last-Event-ID` so the server re-syncs
/// current state immediately. The loop retries forever (bounded per-attempt by
/// the backoff cap); after 3 consecutive failures `onHealth(false)` fires — the
/// only effect, exactly as on desktop, is that callers may un-relax any polling
/// fallback. There is no permanent give-up.
actor EventsClient {
    enum Event {
        case dataVersion(Int)
        case queue(QueueState)
    }

    struct QueueState: Equatable {
        var paused = false
        var pending = 0
        var processing = 0
        var failed = 0
        var completedSinceIdle = 0
        var total = 0
    }

    private static let backoffScheduleMs: [UInt64] = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]
    private static let unhealthyAfterFailures = 3

    private let connection: Connection
    private var task: Task<Void, Never>?

    init(connection: Connection) {
        self.connection = connection
    }

    /// Start the reconnect loop. `onEvent` / `onHealth` are called on an
    /// arbitrary executor — hop to `@MainActor` inside if updating UI.
    func start(onEvent: @escaping @Sendable (Event) -> Void,
               onHealth: @escaping @Sendable (Bool) -> Void) {
        task?.cancel()
        task = Task { [connection] in
            var attempt = 0
            var failures = 0
            var lastEventID: String?

            while !Task.isCancelled {
                do {
                    try await Self.consumeOnce(
                        connection: connection,
                        lastEventID: lastEventID,
                        onEvent: onEvent,
                        onFrameID: { lastEventID = $0 })
                    // A clean end (server's max-stream cap) is a healthy cycle.
                    attempt = 0
                    failures = 0
                    onHealth(true)
                } catch is CancellationError {
                    break
                } catch {
                    failures += 1
                    if failures >= Self.unhealthyAfterFailures { onHealth(false) }
                    let idx = min(attempt, Self.backoffScheduleMs.count - 1)
                    let delay = Self.backoffScheduleMs[idx] * 1_000_000
                    attempt += 1
                    try? await Task.sleep(nanoseconds: delay)
                }
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    // MARK: One connection lifecycle

    private static func consumeOnce(
        connection: Connection,
        lastEventID: String?,
        onEvent: @escaping @Sendable (Event) -> Void,
        onFrameID: @escaping (String) -> Void
    ) async throws {
        guard var comps = URLComponents(
            url: connection.baseURL.appendingPathComponent("events"),
            resolvingAgainstBaseURL: false) else { throw SynAPIError.badURL }
        guard let url = comps.url else { throw SynAPIError.badURL }
        _ = comps

        var req = URLRequest(url: url)
        req.timeoutInterval = 3600
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let lastEventID { req.setValue(lastEventID, forHTTPHeaderField: "Last-Event-ID") }
        if !connection.token.isEmpty {
            req.setValue("Bearer \(connection.token)", forHTTPHeaderField: "Authorization")
        }
        if !connection.cfAccessClientID.isEmpty, !connection.cfAccessClientSecret.isEmpty {
            req.setValue(connection.cfAccessClientID, forHTTPHeaderField: "CF-Access-Client-Id")
            req.setValue(connection.cfAccessClientSecret,
                         forHTTPHeaderField: "CF-Access-Client-Secret")
        }

        // A dedicated session so an idle SSE stream never blocks the shared pool.
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 3600
        let session = URLSession(configuration: cfg)

        let (bytes, response) = try await session.bytes(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw SynAPIError.transport("No HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw SynAPIError.http(status: http.statusCode, message: "SSE connect failed")
        }

        // Accumulate lines into frames delimited by a blank line.
        var eventName: String?
        var dataLines: [String] = []
        var frameID: String?

        for try await line in bytes.lines {
            if Task.isCancelled { throw CancellationError() }

            if line.isEmpty {
                // Frame boundary — dispatch what we gathered.
                if let ev = decodeFrame(event: eventName, data: dataLines.joined(separator: "\n")) {
                    if let frameID { onFrameID(frameID) }
                    onEvent(ev)
                }
                eventName = nil
                dataLines.removeAll(keepingCapacity: true)
                frameID = nil
                continue
            }
            // SSE comment (heartbeat) — ignore.
            if line.hasPrefix(":") { continue }

            if let range = line.range(of: ":") {
                let field = String(line[line.startIndex..<range.lowerBound])
                var value = String(line[range.upperBound...])
                if value.hasPrefix(" ") { value.removeFirst() }
                switch field {
                case "event": eventName = value
                case "data": dataLines.append(value)
                case "id": frameID = value
                default: break
                }
            }
        }
    }

    private static func decodeFrame(event: String?, data: String) -> Event? {
        guard let event, let payload = data.data(using: .utf8) else { return nil }
        switch event {
        case "data_version":
            if let dv = try? JSONDecoder().decode(DataVersionFrame.self, from: payload) {
                return .dataVersion(dv.dataVersion)
            }
        case "queue":
            if let q = try? JSONDecoder().decode(QueueFrame.self, from: payload) {
                return .queue(QueueState(
                    paused: q.paused, pending: q.pending, processing: q.processing,
                    failed: q.failed, completedSinceIdle: q.completedSinceIdle, total: q.total))
            }
        default:
            return nil
        }
        return nil
    }

    private struct DataVersionFrame: Decodable {
        let dataVersion: Int
        enum CodingKeys: String, CodingKey { case dataVersion = "data_version" }
    }

    private struct QueueFrame: Decodable {
        let paused: Bool
        let pending: Int
        let processing: Int
        let failed: Int
        let completedSinceIdle: Int
        let total: Int
        enum CodingKeys: String, CodingKey {
            case paused, pending, processing, failed, total
            case completedSinceIdle = "completed_since_idle"
        }
    }
}
