import Foundation

/// Wire DTOs + calls for the raw sources browser and the ingest-activity view
/// (the desktop Sources reader + ActivityBar / ingest-queue). All reuse the
/// single APIClient error path (ADR-0086).
extension API {
    struct SourceEntry: Decodable, Identifiable, Hashable {
        let path: String
        let name: String
        let isDir: Bool
        let ext: String?
        let sizeBytes: Int?
        let mtime: String?

        var id: String { path }

        enum CodingKeys: String, CodingKey {
            case path, name, ext, mtime
            case isDir = "is_dir"
            case sizeBytes = "size_bytes"
        }
    }

    struct SourcesList: Decodable {
        let entries: [SourceEntry]
        let total: Int
        let truncated: Bool
    }

    // MARK: Ingest activity

    struct QueueTask: Decodable, Identifiable, Hashable {
        let runID: String?
        let sourcePath: String
        let filename: String
        let status: String
        let retryCount: Int
        let error: String?
        let phase: String?
        let progress: Double?
        let elapsedSeconds: Int?
        let etaSeconds: Int?

        var id: String { runID ?? sourcePath }

        enum CodingKeys: String, CodingKey {
            case status, error, phase, progress
            case runID = "run_id"
            case sourcePath = "source_path"
            case filename
            case retryCount = "retry_count"
            case elapsedSeconds = "elapsed_seconds"
            case etaSeconds = "eta_seconds"
        }
    }

    struct QueueSnapshot: Decodable {
        let paused: Bool
        let pending: Int
        let processing: Int
        let failed: Int
        let completedSinceIdle: Int
        let total: Int
        let tasks: [QueueTask]

        enum CodingKeys: String, CodingKey {
            case paused, pending, processing, failed, total, tasks
            case completedSinceIdle = "completed_since_idle"
        }
    }

    struct IngestRun: Decodable, Identifiable, Hashable {
        let id: String
        let status: String
        let providerType: String
        let pagesCreated: Int
        let iterationsUsed: Int
        let totalCostUSD: Double
        let startedAt: Date
        let completedAt: Date?
        let errorMessage: String?

        enum CodingKeys: String, CodingKey {
            case id, status
            case providerType = "provider_type"
            case pagesCreated = "pages_created"
            case iterationsUsed = "iterations_used"
            case totalCostUSD = "total_cost_usd"
            case startedAt = "started_at"
            case completedAt = "completed_at"
            case errorMessage = "error_message"
        }
    }

    struct IngestRunList: Decodable {
        let items: [IngestRun]
        let total: Int
    }
}

extension APIClient {
    /// List raw sources under `root` (defaults to the vault's `raw/sources/`).
    func sources(root: String? = nil) async throws -> API.SourcesList {
        var q: [URLQueryItem] = []
        if let root, !root.isEmpty { q.append(URLQueryItem(name: "root", value: root)) }
        return try await send(request("sources", query: q), as: API.SourcesList.self)
    }

    /// The live ingest-queue snapshot (per-task phase/progress).
    func ingestQueue() async throws -> API.QueueSnapshot {
        try await send(request("ingest/queue"), as: API.QueueSnapshot.self)
    }

    /// Recent ingest-run history (status, provider, pages, cost — I7 cost log).
    func ingestRuns(limit: Int = 30) async throws -> API.IngestRunList {
        let q = [URLQueryItem(name: "limit", value: "\(limit)")]
        return try await send(request("ingest/runs", query: q), as: API.IngestRunList.self)
    }
}

extension API.SourceEntry {
    /// Human byte size, e.g. "12 KB".
    var sizeLabel: String? {
        guard let b = sizeBytes else { return nil }
        return ByteCountFormatter.string(fromByteCount: Int64(b), countStyle: .file)
    }
}
