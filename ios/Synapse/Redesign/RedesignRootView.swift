import SwiftUI

/// The redesigned app shell (ADR-0088). A native `TabView` over the five primary
/// destinations — Home · Wiki · Chat · Graph · More. Fase B wires live,
/// API-backed data onto Home / Wiki / Chat (and Search, reached from the Wiki
/// toolbar); Graph stays the honest Fase C placeholder (its renderer is gated on
/// an on-device perf check). Home, Wiki and Chat each run inside a `WikiStack`
/// so a citation / wikilink / recent-item tap pushes the shared reading view.
struct RedesignRootView: View {
    @Environment(SynapseSession.self) private var session
    enum Tab: Hashable { case home, wiki, chat, graph, more }
    @State private var selection: Tab = .home

    var body: some View {
        TabView(selection: $selection) {
            WikiStack { HomeScreen() }
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(Tab.home)

            WikiStack { WikiScreen() }
                .tabItem { Label("Wiki", systemImage: "books.vertical.fill") }
                .tag(Tab.wiki)

            WikiStack { ChatScreen() }
                .tabItem { Label("Chat", systemImage: "bubble.left.and.text.bubble.right.fill") }
                .tag(Tab.chat)

            WikiStack { GraphScreen() }
                .tabItem { Label("Graph", systemImage: "point.3.connected.trianglepath.dotted") }
                .tag(Tab.graph)

            NavigationStack { MoreScreen() }
                .tabItem { Label("More", systemImage: "ellipsis") }
                .badge(session.reviewPending)
                .tag(Tab.more)
        }
        .tint(SynColor.accent)
        .task { await session.connect() }
    }
}
