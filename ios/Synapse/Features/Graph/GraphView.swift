import SwiftUI

struct GraphView: View {
    /// If set, the graph pre-selects and centers on this page.
    var focusPageID: String? = nil

    @EnvironmentObject private var settings: AppSettings

    @State private var graph: GraphResponse?
    @State private var loadError: String?
    @State private var isLoading = true

    // Viewport state
    @State private var zoom: CGFloat = 1
    @State private var pan: CGSize = .zero
    @GestureState private var pinch: CGFloat = 1
    @GestureState private var dragTranslation: CGSize = .zero

    @State private var selectedID: String?
    /// Ids of the highest-degree nodes — only these get always-on labels.
    @State private var topLabelIDs: Set<String> = []

    var body: some View {
        ZStack {
            Theme.graphBackground.ignoresSafeArea()

            if let loadError {
                ErrorState(message: loadError) { Task { await load() } }
            } else if isLoading {
                LoadingState(text: "Costruzione del grafo…")
            } else if let graph, !graph.nodes.isEmpty {
                canvas(graph)
                overlays(graph)
            } else {
                EmptyState(systemImage: "point.3.connected.trianglepath.dotted",
                           title: "Grafo vuoto",
                           message: "Importa pagine e collegamenti per vedere il grafo.")
            }
        }
        .toolbar(.hidden, for: .navigationBar)
        .task { await load() }
    }

    // MARK: Canvas

    private func canvas(_ graph: GraphResponse) -> some View {
        GeometryReader { geo in
            let layout = Layout(graph: graph, size: geo.size,
                                zoom: zoom * pinch,
                                pan: CGSize(width: pan.width + dragTranslation.width,
                                            height: pan.height + dragTranslation.height))
            Canvas { ctx, _ in
                let eff = zoom * pinch
                // Edges — fainter so the nodes read clearly.
                for edge in graph.edges {
                    guard let a = layout.point(edge.source), let b = layout.point(edge.target)
                    else { continue }
                    var path = Path()
                    path.move(to: a); path.addLine(to: b)
                    ctx.stroke(path, with: .color(.gray.opacity(0.16)), lineWidth: 0.7)
                }
                // Nodes
                for node in graph.nodes {
                    let p = layout.screen(node)
                    let r = layout.radius(node)
                    let selected = node.id == selectedID
                    let color = Theme.color(forType: node.type)
                    if selected {
                        ctx.fill(Circle().path(in: CGRect(x: p.x - r - 7, y: p.y - r - 7,
                                                           width: (r + 7) * 2, height: (r + 7) * 2)),
                                 with: .color(color.opacity(0.32)))
                    }
                    ctx.fill(Circle().path(in: CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2)),
                             with: .color(color))
                }
                // Labels — hub nodes + the selection (more appear as you zoom
                // in). Placed greedily in priority order, skipping any that would
                // overlap an already-drawn label so the canvas stays readable.
                let candidates = graph.nodes
                    .filter { node in
                        node.title != nil
                            && (node.id == selectedID
                                || (eff >= 0.6 && topLabelIDs.contains(node.id))
                                || (eff > 1.9 && (node.degree ?? 0) >= 3))
                    }
                    .sorted { a, b in
                        if a.id == selectedID { return true }
                        if b.id == selectedID { return false }
                        return (a.degree ?? 0) > (b.degree ?? 0)
                    }
                var placed: [CGRect] = []
                for node in candidates {
                    let p = layout.screen(node)
                    let r = layout.radius(node)
                    let resolved = ctx.resolve(
                        Text(shorten(node.title ?? ""))
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Theme.label))
                    let sz = resolved.measure(in: CGSize(width: 240, height: 40))
                    let ty = p.y - r - 9
                    let bg = CGRect(x: p.x - sz.width / 2 - 5, y: ty - sz.height / 2 - 1,
                                    width: sz.width + 10, height: sz.height + 2)
                    let selected = node.id == selectedID
                    if !selected && placed.contains(where: { $0.insetBy(dx: -3, dy: -2).intersects(bg) }) {
                        continue
                    }
                    placed.append(bg)
                    ctx.fill(Path(roundedRect: bg, cornerRadius: 5, style: .continuous),
                             with: .color(Theme.card.opacity(0.92)))
                    ctx.draw(resolved, at: CGPoint(x: p.x, y: ty), anchor: .center)
                }
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture()
                    .updating($dragTranslation) { value, state, _ in state = value.translation }
                    .onEnded { value in
                        // Distinguish tap (small movement) from pan.
                        if abs(value.translation.width) < 6, abs(value.translation.height) < 6 {
                            selectNearest(to: value.location, layout: layout, graph: graph)
                        } else {
                            pan.width += value.translation.width
                            pan.height += value.translation.height
                        }
                    }
            )
            .simultaneousGesture(
                MagnifyGesture()
                    .updating($pinch) { value, state, _ in state = value.magnification }
                    .onEnded { value in
                        zoom = clampZoom(zoom * value.magnification)
                    }
            )
        }
    }

    // MARK: Overlays (title bar, legend, zoom controls, node sheet)

    @ViewBuilder
    private func overlays(_ graph: GraphResponse) -> some View {
        VStack {
            HStack {
                Text("Grafo della conoscenza")
                    .font(.system(size: 17, weight: .bold)).foregroundStyle(Theme.label)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                Spacer()
                ThemeToggleButton(size: 38)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)

            HStack {
                FlowLayout(spacing: 6) {
                    ForEach(Theme.pageTypes, id: \.self) { t in
                        HStack(spacing: 5) {
                            Circle().fill(Theme.color(forType: t)).frame(width: 8, height: 8)
                            Text(Theme.label(forType: t))
                                .font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.label)
                        }
                        .padding(.horizontal, 9).padding(.vertical, 3)
                        .background(.ultraThinMaterial)
                        .clipShape(Capsule())
                    }
                }
                .frame(maxWidth: 230, alignment: .leading)
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)

            Spacer()

            HStack(alignment: .bottom) {
                Button {
                    withAnimation(.easeOut(duration: 0.2)) { zoom = 1; pan = .zero; selectedID = nil }
                } label: {
                    Text("Reimposta · \(Int(zoom * 100))%")
                        .font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.label)
                        .padding(.horizontal, 13).padding(.vertical, 9)
                        .background(.ultraThinMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                .buttonStyle(.plain)
                Spacer()
                VStack(spacing: 0) {
                    zoomButton("plus") { zoom = clampZoom(zoom * 1.2) }
                    Divider().frame(width: 42)
                    zoomButton("minus") { zoom = clampZoom(zoom / 1.2) }
                }
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .padding(.horizontal, 16)
            .padding(.bottom, selectedID == nil ? 24 : 8)
        }

        if let id = selectedID, let node = graph.nodes.first(where: { $0.id == id }) {
            VStack {
                Spacer()
                nodeSheet(node)
            }
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    private func zoomButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: { withAnimation(.easeOut(duration: 0.15), action) }) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold)).foregroundStyle(Theme.label)
                .frame(width: 42, height: 42)
        }
        .buttonStyle(.plain)
    }

    private func nodeSheet(_ node: GraphNode) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                TypePill(type: node.type)
                Spacer()
                Button { withAnimation { selectedID = nil } } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.label3)
                }
                .buttonStyle(.plain)
            }
            Text(node.title ?? "Pagina")
                .font(.system(size: 21, weight: .bold)).foregroundStyle(Theme.label)
                .padding(.top, 10)
            Text(nodeSubtitle(node))
                .font(.system(size: 13)).foregroundStyle(Theme.label3)
                .padding(.top, 6)
            NavigationLink(value: PageRef(id: node.id, title: node.title, type: node.type)) {
                Text("Apri pagina")
                    .font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                    .frame(maxWidth: .infinity).padding(.vertical, 13)
                    .background(Theme.tint)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)
            .padding(.top, 14)
        }
        .padding(18)
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        .shadow(color: .black.opacity(0.18), radius: 20, y: -4)
        .padding(.horizontal, 12)
        .padding(.bottom, 28)
    }

    private func nodeSubtitle(_ node: GraphNode) -> String {
        var parts: [String] = []
        if let d = node.degree { parts.append("\(d) collegamenti") }
        if let dom = node.domain { parts.append(dom) }
        return parts.joined(separator: " · ")
    }

    // MARK: Interaction helpers

    private func selectNearest(to location: CGPoint, layout: Layout, graph: GraphResponse) {
        var best: (id: String, dist: CGFloat)?
        for node in graph.nodes {
            let p = layout.screen(node)
            let d = hypot(p.x - location.x, p.y - location.y)
            let hit = max(layout.radius(node) + 12, 20)
            if d < hit, best == nil || d < best!.dist { best = (node.id, d) }
        }
        withAnimation(.easeOut(duration: 0.2)) { selectedID = best?.id }
    }

    private func clampZoom(_ z: CGFloat) -> CGFloat { min(3.5, max(0.25, z)) }

    private func shorten(_ s: String) -> String {
        s.count > 14 ? String(s.prefix(13)) + "…" : s
    }

    // MARK: Data

    private func load() async {
        guard let client = settings.makeClient() else {
            loadError = APIError.notConfigured.errorDescription; isLoading = false; return
        }
        isLoading = true; loadError = nil
        do {
            let g = try await client.graph()
            graph = g
            // Label only the ~14 most-connected nodes by default.
            topLabelIDs = Set(
                g.nodes.sorted { ($0.degree ?? 0) > ($1.degree ?? 0) }
                    .prefix(14).map(\.id))
            if let focus = focusPageID, g.nodes.contains(where: { $0.id == focus }) {
                selectedID = focus
            }
        } catch {
            loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        isLoading = false
    }

    // MARK: Coordinate layout

    /// Maps FA2 data coordinates into screen space, fitting all nodes with a
    /// margin, then applying user zoom + pan.
    private struct Layout {
        let size: CGSize
        let zoom: CGFloat
        let pan: CGSize
        let dataCenter: CGPoint
        let fit: CGFloat
        let index: [String: GraphNode]
        let maxDegree: Int

        init(graph: GraphResponse, size: CGSize, zoom: CGFloat, pan: CGSize) {
            self.size = size; self.zoom = zoom; self.pan = pan
            var idx: [String: GraphNode] = [:]
            var minX = Double.greatestFiniteMagnitude, minY = Double.greatestFiniteMagnitude
            var maxX = -Double.greatestFiniteMagnitude, maxY = -Double.greatestFiniteMagnitude
            var maxDeg = 1
            for n in graph.nodes {
                idx[n.id] = n
                minX = min(minX, n.x); maxX = max(maxX, n.x)
                minY = min(minY, n.y); maxY = max(maxY, n.y)
                maxDeg = max(maxDeg, n.degree ?? 1)
            }
            index = idx
            maxDegree = maxDeg
            dataCenter = CGPoint(x: (minX + maxX) / 2, y: (minY + maxY) / 2)
            let spanX = max(maxX - minX, 0.001)
            let spanY = max(maxY - minY, 0.001)
            // Leave breathing room around the cluster so it doesn't touch the
            // edges and labels near the border stay readable.
            let margin: CGFloat = 72
            fit = min((size.width - margin) / spanX, (size.height - margin) / spanY) * 0.82
        }

        func screen(_ node: GraphNode) -> CGPoint {
            let sx = (CGFloat(node.x) - dataCenter.x) * fit * zoom
            let sy = (CGFloat(node.y) - dataCenter.y) * fit * zoom
            return CGPoint(x: size.width / 2 + sx + pan.width,
                           y: size.height / 2 + sy + pan.height)
        }

        func point(_ id: String) -> CGPoint? {
            index[id].map(screen)
        }

        func radius(_ node: GraphNode) -> CGFloat {
            let deg = CGFloat(node.degree ?? 1)
            let base = 2.2 + 5 * sqrt(deg / CGFloat(maxDegree))
            // Scale dots with zoom so zooming out actually shrinks them
            // (Obsidian-style), floor low enough to reach a tiny overview.
            return base * min(max(zoom, 0.3), 1.6)
        }
    }
}
