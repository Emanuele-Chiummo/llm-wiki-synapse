import SwiftUI

/// Shared, lightweight connection state: reachability, server version, and the
/// pending-review count that drives the tab-bar badge. Polls `GET /status`
/// (auth-exempt) so it works even before a token is set.
@MainActor
final class AppModel: ObservableObject {
    enum Reachability: Equatable {
        case unknown, connecting, online, offline(String)
    }

    @Published var reachability: Reachability = .unknown
    @Published var status: StatusResponse?

    var reviewCount: Int { status?.reviewPending ?? 0 }
    var serverVersion: String? { status?.version }

    func refresh(_ settings: AppSettings) async {
        guard let client = settings.makeClient() else {
            reachability = .offline("Nessun server configurato")
            return
        }
        if case .online = reachability {} else { reachability = .connecting }
        do {
            let s = try await client.status()
            status = s
            reachability = .online
        } catch {
            let msg = (error as? APIError)?.errorDescription ?? error.localizedDescription
            reachability = .offline(msg)
        }
    }
}
