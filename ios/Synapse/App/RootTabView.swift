import SwiftUI

struct RootTabView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var app: AppModel
    @State private var selection: Tab = .wiki

    enum Tab: Hashable { case wiki, search, chat, graph, more }

    var body: some View {
        TabView(selection: $selection) {
            NavigationStack {
                WikiListView()
                    .navigationDestination(for: PageRef.self) { WikiDetailView(page: $0) }
            }
            .tabItem { Label("Wiki", systemImage: "books.vertical") }
            .tag(Tab.wiki)

            NavigationStack {
                SearchView()
                    .navigationDestination(for: PageRef.self) { WikiDetailView(page: $0) }
            }
            .tabItem { Label("Cerca", systemImage: "magnifyingglass") }
            .tag(Tab.search)

            NavigationStack {
                ChatView()
                    .navigationDestination(for: PageRef.self) { WikiDetailView(page: $0) }
            }
            .tabItem { Label("Chat", systemImage: "bubble.left.and.text.bubble.right") }
            .tag(Tab.chat)

            NavigationStack {
                GraphView()
                    .navigationDestination(for: PageRef.self) { WikiDetailView(page: $0) }
            }
            .tabItem { Label("Grafo", systemImage: "point.3.filled.connected.trianglepath.dotted") }
            .tag(Tab.graph)

            NavigationStack {
                MoreView()
                    .navigationDestination(for: PageRef.self) { WikiDetailView(page: $0) }
            }
            .tabItem { Label("Altro", systemImage: "ellipsis") }
            .badge(app.reviewCount)
            .tag(Tab.more)
        }
        .task { await app.refresh(settings) }
        .onChange(of: settings.serverURLString) { _, _ in
            Task { await app.refresh(settings) }
        }
        .onChange(of: settings.authToken) { _, _ in
            Task { await app.refresh(settings) }
        }
    }
}
