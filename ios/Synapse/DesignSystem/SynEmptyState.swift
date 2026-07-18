import SwiftUI

/// Empty / informational state (desktop `.syn-empty-state`): eyebrow, SF Symbol,
/// title, body, and an optional primary action.
struct SynEmptyState: View {
    let systemImage: String
    let title: String
    var eyebrow: String? = nil
    var message: String? = nil
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: SynSpace.x4) {
            ZStack {
                Circle()
                    .fill(SynColor.accentSoft)
                    .frame(width: 64, height: 64)
                Image(systemName: systemImage)
                    .font(.system(size: 26, weight: .regular))
                    .foregroundStyle(SynColor.accent)
            }
            if let eyebrow {
                SynSectionHeader(text: eyebrow, accent: true)
                    .multilineTextAlignment(.center)
            }
            Text(title)
                .font(SynFont.title)
                .foregroundStyle(SynColor.text)
                .multilineTextAlignment(.center)
            if let message {
                Text(message)
                    .font(SynFont.subhead)
                    .foregroundStyle(SynColor.textMuted)
                    .multilineTextAlignment(.center)
            }
            if let actionTitle, let action {
                SynButton(title: actionTitle, kind: .primary, action: action)
                    .padding(.top, SynSpace.x2)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, SynSpace.x9)
        .padding(.vertical, SynSpace.x9)
    }
}
