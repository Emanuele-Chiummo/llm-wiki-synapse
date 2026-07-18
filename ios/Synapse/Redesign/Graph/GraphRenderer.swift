import SwiftUI

/// The **swappable rendering seam** for the knowledge graph (ADR-0088).
///
/// The ADR recommends a native SwiftUI `Canvas` renderer over a WKWebView-sigma
/// embed, but flags that recommendation as *pending an on-device performance
/// check* (fps / memory / gesture latency) that no Simulator-only environment can
/// run. So the concrete renderer is chosen in exactly ONE place — `makeGraphRenderer`
/// — behind this thin factory. If the owner's eventual device test says the
/// WKWebView embed is actually better, adding a `.webViewSigma` case here (plus a
/// `SigmaWebGraphView`) swaps it in **without touching** `GraphScreen`, the view
/// model, the DTOs, or anything else in the app.
///
/// Both candidates consume the same server-precomputed FA2 coordinates, so
/// **I2 holds regardless of which one is active**.
enum GraphRenderKind: String, CaseIterable, Identifiable {
    /// Native SwiftUI Canvas — the ADR's recommended (not yet device-locked) path.
    case nativeCanvas
    // case webViewSigma  // I2-safe fallback; add when/if the device perf check picks it.

    var id: String { rawValue }
    var label: String {
        switch self {
        case .nativeCanvas: return "Native (Canvas)"
        }
    }
}

/// The single factory. `GraphScreen` calls only this; nothing else knows which
/// renderer is live.
@ViewBuilder
func makeGraphRenderer(
    kind: GraphRenderKind,
    data: API.GraphData,
    selectedID: Binding<String?>
) -> some View {
    switch kind {
    case .nativeCanvas:
        NativeCanvasGraphView(data: data, selectedID: selectedID)
    }
}

// MARK: - Native Canvas renderer

/// Draws the precomputed graph with a SwiftUI `Canvas` (GPU-composited, no
/// main-thread force layout — I2). Pinch to zoom, drag to pan, tap a node to
/// select it. Colour is the desktop F4 per-type jewel palette via `SynColor`.
/// The background is `SynColor.bg` (deep-navy in dark) — **never pure black**.
struct NativeCanvasGraphView: View {
    let data: API.GraphData
    @Binding var selectedID: String?

    // Persistent (committed) transform; the gesture deltas below are layered on top.
    @State private var scale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @GestureState private var pinch: CGFloat = 1
    @GestureState private var drag: CGSize = .zero

    // Precomputed model bounds — coords are static, so compute once.
    private let bounds: CGRect
    private let nodeByID: [String: API.GraphNode]

    init(data: API.GraphData, selectedID: Binding<String?>) {
        self.data = data
        self._selectedID = selectedID
        self.nodeByID = Dictionary(uniqueKeysWithValues: data.nodes.map { ($0.id, $0) })
        self.bounds = Self.computeBounds(data.nodes)
    }

    var body: some View {
        GeometryReader { geo in
            let fit = fitTransform(in: geo.size)
            let liveScale = scale * pinch
            let liveOffset = CGSize(width: offset.width + drag.width,
                                    height: offset.height + drag.height)

            Canvas { ctx, _ in
                draw(in: &ctx, fit: fit, scale: liveScale, offset: liveOffset)
            }
            .background(SynColor.bg)
            .contentShape(Rectangle())
            .gesture(
                DragGesture()
                    .updating($drag) { value, state, _ in state = value.translation }
                    .onEnded { value in
                        offset.width += value.translation.width
                        offset.height += value.translation.height
                    }
            )
            .simultaneousGesture(
                MagnificationGesture()
                    .updating($pinch) { value, state, _ in state = value }
                    .onEnded { value in
                        scale = (scale * value).clamped(to: 0.25...6)
                    }
            )
            .onTapGesture { location in
                selectNode(at: location, fit: fit, scale: liveScale, offset: liveOffset)
            }
        }
    }

    // MARK: Drawing

    private func draw(in ctx: inout GraphicsContext, fit: FitTransform,
                      scale: CGFloat, offset: CGSize) {
        func project(_ x: Double, _ y: Double) -> CGPoint {
            let px = (CGFloat(x) - fit.center.x) * fit.scale * scale + fit.viewCenter.x + offset.width
            let py = (CGFloat(y) - fit.center.y) * fit.scale * scale + fit.viewCenter.y + offset.height
            return CGPoint(x: px, y: py)
        }

        // Edges first (hairlines), so nodes sit on top.
        var edgePath = Path()
        for e in data.edges {
            guard let a = nodeByID[e.source], let b = nodeByID[e.target] else { continue }
            edgePath.move(to: project(a.x, a.y))
            edgePath.addLine(to: project(b.x, b.y))
        }
        ctx.stroke(edgePath, with: .color(SynColor.border.opacity(0.55)),
                   lineWidth: 0.6)

        // Nodes.
        for n in data.nodes {
            let p = project(n.x, n.y)
            let r = nodeRadius(n) * min(max(scale, 0.6), 2.2)
            let rect = CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2)
            let color = SynColor.color(forType: n.type)
            if n.id == selectedID {
                let halo = rect.insetBy(dx: -5, dy: -5)
                ctx.fill(Circle().path(in: halo), with: .color(color.opacity(0.28)))
                ctx.stroke(Circle().path(in: rect), with: .color(SynColor.accent), lineWidth: 2)
            }
            ctx.fill(Circle().path(in: rect), with: .color(color))
        }

        // Selected node label (drawn last, on top).
        if let id = selectedID, let n = nodeByID[id] {
            let p = project(n.x, n.y)
            let text = Text(n.displayTitle).font(.caption2.weight(.semibold))
                .foregroundStyle(SynColor.text)
            ctx.draw(text, at: CGPoint(x: p.x, y: p.y - nodeRadius(n) - 12), anchor: .center)
        }
    }

    private func nodeRadius(_ n: API.GraphNode) -> CGFloat {
        // Map the server size (roughly degree-weighted) to a legible radius.
        let base = 3.0 + min(n.size, 40) * 0.18
        return CGFloat(base)
    }

    // MARK: Hit testing

    private func selectNode(at location: CGPoint, fit: FitTransform,
                            scale: CGFloat, offset: CGSize) {
        func project(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: (CGFloat(x) - fit.center.x) * fit.scale * scale + fit.viewCenter.x + offset.width,
                    y: (CGFloat(y) - fit.center.y) * fit.scale * scale + fit.viewCenter.y + offset.height)
        }
        var best: (id: String, dist: CGFloat)?
        for n in data.nodes {
            let p = project(n.x, n.y)
            let d = hypot(p.x - location.x, p.y - location.y)
            let hitR = max(nodeRadius(n) * min(max(scale, 0.6), 2.2) + 10, 16)
            if d <= hitR, best == nil || d < best!.dist { best = (n.id, d) }
        }
        // Toggle off if tapping the already-selected node again; else select / clear.
        if let hit = best {
            selectedID = (selectedID == hit.id) ? nil : hit.id
        } else {
            selectedID = nil
        }
    }

    // MARK: Fitting

    struct FitTransform {
        var center: CGPoint       // model-space centre
        var scale: CGFloat        // model→view base scale (fit)
        var viewCenter: CGPoint   // view-space centre
    }

    private func fitTransform(in size: CGSize) -> FitTransform {
        let pad: CGFloat = 32
        let w = max(bounds.width, 1), h = max(bounds.height, 1)
        let s = min((size.width - pad * 2) / w, (size.height - pad * 2) / h)
        return FitTransform(
            center: CGPoint(x: bounds.midX, y: bounds.midY),
            scale: s.isFinite && s > 0 ? s : 1,
            viewCenter: CGPoint(x: size.width / 2, y: size.height / 2))
    }

    private static func computeBounds(_ nodes: [API.GraphNode]) -> CGRect {
        guard let first = nodes.first else { return CGRect(x: 0, y: 0, width: 1, height: 1) }
        var minX = first.x, maxX = first.x, minY = first.y, maxY = first.y
        for n in nodes {
            minX = min(minX, n.x); maxX = max(maxX, n.x)
            minY = min(minY, n.y); maxY = max(maxY, n.y)
        }
        return CGRect(x: minX, y: minY, width: max(maxX - minX, 1), height: max(maxY - minY, 1))
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
