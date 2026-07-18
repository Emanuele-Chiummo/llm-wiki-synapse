import SwiftUI

@main
struct SynapseApp: App {
    // The redesign's connection + live-state source (Track 2.1). Since Fase C
    // retired the legacy Theme/Feature screens, this is the ONLY app state — there
    // is no second visual language or connection stack left.
    @State private var session = SynapseSession()

    init() {
        // Brand rule (CLAUDE.md): never pure black. The system large-title /
        // inline-title default is #000 in light mode — repaint navigation titles
        // with the brand ink token (a dynamic colour, so dark mode stays correct).
        let ink = UIColor { $0.userInterfaceStyle == .dark
            ? UIColor(red: 0.906, green: 0.925, blue: 0.969, alpha: 1)   // #E7ECF7
            : UIColor(red: 0.059, green: 0.090, blue: 0.161, alpha: 1) } // #0F1729
        let appearance = UINavigationBarAppearance()
        appearance.configureWithDefaultBackground()
        appearance.largeTitleTextAttributes = [.foregroundColor: ink]
        appearance.titleTextAttributes = [.foregroundColor: ink]
        UINavigationBar.appearance().standardAppearance = appearance
        UINavigationBar.appearance().scrollEdgeAppearance = appearance
        UINavigationBar.appearance().compactAppearance = appearance
    }

    var body: some Scene {
        WindowGroup {
            // Track iOS 2.1 (ADR-0088): the redesigned native shell is the runtime
            // entry. Fase B wires it to live API data via `SynapseSession`.
            rootView
                .environment(session)
                .tint(SynColor.accent)
                .preferredColorScheme(session.appearance.colorScheme)
                .modifier(LocaleOverride(locale: session.localeOverride))
        }
    }

    /// Applies the user's language override (F16) app-wide, or leaves the device
    /// locale untouched when following the system.
    private struct LocaleOverride: ViewModifier {
        let locale: Locale?
        func body(content: Content) -> some View {
            if let locale { content.environment(\.locale, locale) } else { content }
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
