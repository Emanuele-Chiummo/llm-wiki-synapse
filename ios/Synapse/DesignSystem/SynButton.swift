import SwiftUI

/// Redesign button (ADR-0088). Four intents; the primary uses the brand gradient
/// with restraint. Honours Dynamic Type and provides an instant pressed state so
/// taps never feel dead.
struct SynButton: View {
    enum Kind { case primary, secondary, ghost, destructive }

    let title: String
    var systemImage: String? = nil
    var kind: Kind = .primary
    var fullWidth: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: SynSpace.x3) {
                if let systemImage { Image(systemName: systemImage) }
                Text(title)
            }
            .font(SynFont.button)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .padding(.horizontal, SynSpace.x6)
            .padding(.vertical, SynSpace.x4)
        }
        .buttonStyle(SynButtonStyle(kind: kind))
    }
}

private struct SynButtonStyle: ButtonStyle {
    let kind: SynButton.Kind

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(foreground)
            .background(background(pressed: configuration.isPressed))
            .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous)
                    .strokeBorder(stroke, lineWidth: kind == .secondary || kind == .ghost ? 1 : 0)
            )
            .opacity(configuration.isPressed && kind != .primary ? 0.7 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }

    private var foreground: Color {
        switch kind {
        case .primary: return SynColor.onAccent
        case .secondary: return SynColor.text
        case .ghost: return SynColor.accent
        case .destructive: return SynColor.onAccent
        }
    }

    @ViewBuilder private func background(pressed: Bool) -> some View {
        switch kind {
        case .primary:
            SynColor.signatureGradient.brightness(pressed ? -0.06 : 0)
        case .secondary:
            SynColor.surface
        case .ghost:
            SynColor.accentSoft.opacity(pressed ? 1 : 0)
        case .destructive:
            SynColor.red.brightness(pressed ? -0.06 : 0)
        }
    }

    private var stroke: Color {
        switch kind {
        case .secondary: return SynColor.border
        case .ghost: return .clear
        default: return .clear
        }
    }
}
