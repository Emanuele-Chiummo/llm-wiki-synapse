import SwiftUI

@main
struct SynapseApp: App {
    @StateObject private var settings = AppSettings()
    @StateObject private var app = AppModel()

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(settings)
                .environmentObject(app)
                .tint(Theme.tint)
                .preferredColorScheme(settings.appearance.colorScheme)
        }
    }
}
