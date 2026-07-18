import SwiftUI

#if DEBUG
/// A DEBUG-only screenshot harness (D5 lineage). When the app is launched with
/// `-synScreen <name>` (e.g. `xcrun simctl launch <dev> ai.synapse.mobile
/// -synScreen wikiReading`), the app renders that single redesign surface inside
/// its own `NavigationStack` instead of the tab shell — so any screen, including
/// pushed detail views, can be captured deterministically for docs/screens/ios.
/// Absent the flag, the normal `RedesignRootView` shell is shown. Never compiled
/// into release builds.
enum ScreenshotHarness {
    static var requestedScreen: String? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-synScreen"), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    @ViewBuilder static func view(for name: String) -> some View {
        switch name {
        case "home": NavigationStack { HomeScreen() }
        case "wiki": NavigationStack { WikiScreen() }
        case "wikiReading": NavigationStack { WikiReadingScreen() }
        case "chat": NavigationStack { ChatScreen() }
        case "graph": NavigationStack { GraphScreen() }
        case "more": NavigationStack { MoreScreen() }
        default: RedesignRootView()
        }
    }
}
#endif
