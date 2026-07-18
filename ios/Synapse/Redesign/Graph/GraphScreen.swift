import Observation
import SwiftUI

@Observable
@MainActor
final class GraphModel {
    var state: LoadState<API.GraphData> = .idle
    var selectedID: String?

    func load(_ session: SynapseSession) async {
        guard let client = session.client() else {
            state = .failed(SynAPIError.notConfigured.errorDescription ?? "Not configured"); return
        }
        if state.value == nil { state = .loading }
        do {
            state = .loaded(try await client.graph())
        } catch {
            if state.value == nil {
                state = .failed((error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }
}

/// The Graph tab (F4). Renders the server-side FA2 layout from `GET /graph` via
/// the swappable `makeGraphRenderer` seam (native Canvas today; the native-vs-
/// WKWebView choice is still gated on an on-device perf check — ADR-0088). Tap a
/// node to inspect it and jump into its wiki page; a legend maps the per-type
/// jewel palette. Live-refreshes on the SSE `data_version` bump (no poll loop).
struct GraphScreen: View {
    @Environment(SynapseSession.self) private var session
    @State private var model = GraphModel()
    @State private var renderKind: GraphRenderKind = .nativeCanvas
    @State private var showLegend = false

    var body: some View {
        content
            .synScreenBackground(false)
            .navigationTitle("Graph")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showLegend.toggle() } label: {
                        Image(systemName: "list.bullet.circle")
                    }
                    .accessibilityLabel("Legend")
                }
            }
            .task { await model.load(session) }
            .onChange(of: session.dataVersion) { _, _ in Task { await model.load(session) } }
            .sheet(isPresented: $showLegend) { GraphLegendSheet() }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading where model.state.value == nil:
            VStack(spacing: SynSpace.x5) {
                ProgressView()
                Text("Loading the knowledge graph…")
                    .font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let message):
            SynErrorState(message: message) { Task { await model.load(session) } }
        default:
            let data = model.state.value ?? API.GraphData(
                nodes: [], edges: [], dataVersion: nil, cached: nil,
                communities: nil, totalNodes: 0, totalEdges: 0)
            if data.nodes.isEmpty {
                SynEmptyState(
                    systemImage: "point.3.connected.trianglepath.dotted",
                    title: "No graph yet",
                    message: "Ingest a few sources — the graph builds itself from the wiki's links and shared sources.")
            } else {
                graphStage(data)
            }
        }
    }

    private func graphStage(_ data: API.GraphData) -> some View {
        ZStack(alignment: .bottom) {
            makeGraphRenderer(kind: renderKind, data: data,
                              selectedID: $model.selectedID)
                .ignoresSafeArea(edges: .bottom)

            // Node count / hint pill (top).
            VStack {
                HStack {
                    countPill(data)
                    Spacer()
                }
                .padding(.horizontal, SynSpace.x6)
                .padding(.top, SynSpace.x3)
                Spacer()
            }

            if let id = model.selectedID,
               let node = data.nodes.first(where: { $0.id == id }) {
                GraphNodeCard(node: node) { model.selectedID = nil }
                    .padding(SynSpace.x5)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.18), value: model.selectedID)
    }

    private func countPill(_ data: API.GraphData) -> some View {
        HStack(spacing: SynSpace.x2) {
            Image(systemName: "circle.grid.hex.fill").font(.caption2)
            Text("\(data.nodes.count) nodes · \(data.edges.count) links")
                .font(SynFont.caption.monospacedDigit())
            if data.cached == true {
                Text("cached").font(SynFont.eyebrow).foregroundStyle(SynColor.textDim)
            }
        }
        .foregroundStyle(SynColor.textMuted)
        .padding(.horizontal, SynSpace.x4)
        .padding(.vertical, SynSpace.x2)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(SynColor.border, lineWidth: 1))
    }
}

// MARK: - Selected-node card

private struct GraphNodeCard: View {
    let node: API.GraphNode
    var onClose: () -> Void
    @Environment(WikiNavigator.self) private var navigator

    var body: some View {
        SynCard(padding: SynSpace.x5, elevated: true) {
            HStack(alignment: .top, spacing: SynSpace.x4) {
                SynTypeGlyph(type: node.type, size: 40)
                VStack(alignment: .leading, spacing: 3) {
                    Text(node.displayTitle).font(SynFont.rowTitle)
                        .foregroundStyle(SynColor.text).lineLimit(2)
                    HStack(spacing: SynSpace.x3) {
                        SynChip(text: SynColor.label(forType: node.type), pageType: node.type)
                        Text("\(node.degree) link\(node.degree == 1 ? "" : "s")")
                            .font(SynFont.caption).foregroundStyle(SynColor.textDim)
                    }
                }
                Spacer(minLength: 0)
                Button { onClose() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3).foregroundStyle(SynColor.textDim)
                }
                .accessibilityLabel("Deselect")
            }
            Button {
                navigator.push(.page(id: node.id, title: node.displayTitle))
            } label: {
                HStack {
                    Text("Open page"); Spacer()
                    Image(systemName: "arrow.right")
                }
                .font(SynFont.button).foregroundStyle(SynColor.onAccent)
                .padding(.vertical, SynSpace.x4).padding(.horizontal, SynSpace.x5)
                .frame(maxWidth: .infinity)
                .background(SynColor.signatureGradient)
                .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
            }
            .padding(.top, SynSpace.x4)
        }
    }
}

// MARK: - Legend

private struct GraphLegendSheet: View {
    @Environment(\.dismiss) private var dismiss
    private let types = ["concept", "entity", "source", "synthesis", "comparison", "query"]

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Node colour is the page type; node size grows with its link count. Positions are the server's precomputed FA2 layout (invariant I2) — the app never runs a force layout on-device.")
                        .font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                        .listRowBackground(Color.clear)
                }
                Section("Page types") {
                    ForEach(types, id: \.self) { t in
                        HStack(spacing: SynSpace.x4) {
                            Circle().fill(SynColor.color(forType: t)).frame(width: 12, height: 12)
                            Text(SynColor.label(forType: t)).font(SynFont.rowTitle)
                                .foregroundStyle(SynColor.text)
                            Spacer()
                            Image(systemName: SynColor.icon(forType: t))
                                .foregroundStyle(SynColor.color(forType: t))
                        }
                    }
                }
                Section {
                    Text("Gestures: drag to pan · pinch to zoom · tap a node to inspect it and open its page.")
                        .font(SynFont.caption).foregroundStyle(SynColor.textDim)
                        .listRowBackground(Color.clear)
                }
            }
            .scrollContentBackground(.hidden)
            .synScreenBackground()
            .navigationTitle("Graph legend")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
}
