import SwiftUI

#if DEBUG
/// A DEBUG-only screenshot harness (D5 lineage). When the app is launched with
/// `-synScreen <name>` (e.g. `xcrun simctl launch <dev> ai.synapse.mobile
/// -synScreen wikiReading -synArg <pageId>`), the app renders that single
/// redesign surface — inside a `WikiStack` where navigation is needed — instead
/// of the tab shell, so any screen (including pushed detail views) can be
/// captured deterministically against the live backend. Absent the flag, the
/// normal `RedesignRootView` shell is shown. Never compiled into release builds.
enum ScreenshotHarness {
    static var requestedScreen: String? {
        arg(after: "-synScreen")
    }

    /// A generic string argument (a page id for wikiReading, a query for search).
    static var extraArg: String? {
        arg(after: "-synArg")
    }

    private static func arg(after flag: String) -> String? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    @ViewBuilder static func view(for name: String) -> some View {
        switch name {
        case "home": WikiStack { HomeScreen() }
        case "wiki": WikiStack { WikiScreen() }
        case "wikiReading":
            WikiStack { WikiReadingScreen(pageID: extraArg ?? "", title: nil) }
        case "search":
            WikiStack { SearchScreen(initialQuery: extraArg ?? "network") }
        case "chat": WikiStack { ChatScreen(autoOpenFirstConversation: true) }
        case "graph": WikiStack { GraphScreen() }
        case "more": NavigationStack { MoreScreen() }
        case "review": NavigationStack { ReviewScreen() }
        case "tokens": NavigationStack { TokensScreen() }
        default: RedesignRootView()
        }
    }
}
#endif
