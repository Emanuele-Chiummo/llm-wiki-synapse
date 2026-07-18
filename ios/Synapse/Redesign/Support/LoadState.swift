import SwiftUI

/// The four states every async-loaded surface moves through. Keeps screens from
/// re-implementing the loading / empty / error branches by hand. Deliberately
/// NOT `Equatable` — the wrapped DTOs aren't, and Observation doesn't need it.
enum LoadState<Value> {
    case idle
    case loading
    case loaded(Value)
    case failed(String)

    var value: Value? {
        if case .loaded(let v) = self { return v }
        return nil
    }

    /// True while first-loading with nothing to show yet (drives the skeleton).
    var isInitialLoading: Bool {
        if case .loading = self { return true }
        return false
    }
}

/// A compact, brand-correct error state with a Retry affordance — the redesign
/// counterpart to the desktop `ErrorState`. Uses the `SynColor` tokens (never
/// pure black) and offers a single primary action.
struct SynErrorState: View {
    let message: String
    var retry: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: SynSpace.x4) {
            ZStack {
                Circle().fill(SynColor.red.opacity(0.12)).frame(width: 64, height: 64)
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 24, weight: .regular))
                    .foregroundStyle(SynColor.red)
            }
            Text("Something went wrong")
                .font(SynFont.title)
                .foregroundStyle(SynColor.text)
            Text(message)
                .font(SynFont.subhead)
                .foregroundStyle(SynColor.textMuted)
                .multilineTextAlignment(.center)
            if let retry {
                SynButton(title: "Retry", systemImage: "arrow.clockwise", kind: .secondary,
                          action: retry)
                    .padding(.top, SynSpace.x2)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, SynSpace.x8)
        .padding(.vertical, SynSpace.x9)
    }
}
