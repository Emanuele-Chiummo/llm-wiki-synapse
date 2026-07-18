import SwiftUI

@main
struct SynapseApp: App {
    @StateObject private var settings = AppSettings()
    @StateObject private var app = AppModel()

    var body: some Scene {
        WindowGroup {
            // Track iOS 2.1, Fase A (ADR-0088): the redesigned native shell is the
            // runtime entry. The legacy `RootTabView` and its screens remain
            // compiled (available for Fase B to port real data onto the new
            // design system) but are no longer the entry point.
            rootView
                .environmentObject(settings)
                .environmentObject(app)
                .tint(SynColor.accent)
                .preferredColorScheme(settings.appearance.colorScheme)
        }
    }

    @ViewBuilder private var rootView: some View {
        #if DEBUG
        if let screen = ScreenshotHarness.requestedScreen {
            ScreenshotHarness.view(for: screen)
        } else {
            RedesignRootView()
        }
        #else
        RedesignRootView()
        #endif
    }
}
