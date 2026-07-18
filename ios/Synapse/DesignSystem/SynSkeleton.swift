import SwiftUI

/// Shimmering placeholder block (desktop `.syn-skeleton`). A subtle highlight
/// sweeps across a neutral fill so layout appears instantly while data loads.
/// Honours Reduce Motion (falls back to a static fill).
struct SynSkeleton: View {
    var cornerRadius: CGFloat = SynRadius.sm
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = -1

    var body: some View {
        GeometryReader { geo in
            let base = SynColor.surfaceHover
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .fill(base)
                .overlay {
                    if !reduceMotion {
                        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                            .fill(
                                LinearGradient(
                                    colors: [.clear, SynColor.surface.opacity(0.9), .clear],
                                    startPoint: .leading, endPoint: .trailing)
                            )
                            .frame(width: geo.size.width * 0.6)
                            .offset(x: phase * geo.size.width)
                            .mask(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
                    }
                }
        }
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.3).repeatForever(autoreverses: false)) {
                phase = 1.4
            }
        }
    }
}

/// A convenience skeleton line of a given height (rounded).
struct SynSkeletonLine: View {
    var height: CGFloat = 12
    var widthFraction: CGFloat = 1
    var body: some View {
        SynSkeleton(cornerRadius: SynRadius.sm)
            .frame(height: height)
            .frame(maxWidth: .infinity, alignment: .leading)
            .scaleEffect(x: widthFraction, y: 1, anchor: .leading)
    }
}
