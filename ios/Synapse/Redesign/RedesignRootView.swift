import SwiftUI

/// The redesigned app shell (ADR-0088, Track iOS 2.1 Fase A).
///
/// A native `TabView` exposing the five primary destinations — Home · Wiki ·
/// Chat · Graph · More — each its own `NavigationStack`. The desktop 3-panel
/// layout is deliberately NOT copied; this is a phone-native structure. Home and
/// Wiki carry realistic mock content (see `RedesignMock`); Chat, Graph and More
/// are honest placeholders built from the design system so the skeleton builds
/// and demos. Fase B wires live API data onto these surfaces.
struct RedesignRootView: View {
    enum Tab: Hashable { case home, wiki, chat, graph, more }
    @State private var selection: Tab = .home

    var body: some View {
        TabView(selection: $selection) {
            NavigationStack { HomeScreen() }
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(Tab.home)

            NavigationStack { WikiScreen() }
                .tabItem { Label("Wiki", systemImage: "books.vertical.fill") }
                .tag(Tab.wiki)

            NavigationStack { ChatScreen() }
                .tabItem { Label("Chat", systemImage: "bubble.left.and.text.bubble.right.fill") }
                .tag(Tab.chat)

            NavigationStack { GraphScreen() }
                .tabItem { Label("Graph", systemImage: "point.3.connected.trianglepath.dotted") }
                .tag(Tab.graph)

            NavigationStack { MoreScreen() }
                .tabItem { Label("More", systemImage: "ellipsis") }
                .tag(Tab.more)
        }
        .tint(SynColor.accent)
    }
}

#Preview("Redesign — light") {
    RedesignRootView().preferredColorScheme(.light)
}

#Preview("Redesign — dark") {
    RedesignRootView().preferredColorScheme(.dark)
}
