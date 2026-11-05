import SwiftUI
import UIKit

/// A `UITextView`-backed replacement for `Text(AttributedString)` when the string may contain
/// `.link` runs (wikilinks).
///
/// Background (Track iOS 2.1 — wikilink tap-to-navigate bug, found during live verification):
/// plain SwiftUI `Text` built from an `AttributedString` containing a `.link` attribute did not
/// dispatch taps to the environment's `openURL` in this app's view hierarchy (ScrollView >
/// VStack > ForEach > `@ViewBuilder` switch in `MarkdownView.view(for:)`), even after ruling out
/// three explanations (`.foregroundStyle` view-level override, `.lineSpacing` shifting the tap
/// hit-test region, an explicit per-run `foregroundColor`) via live rebuild-tap-log-check cycles
/// on a real Simulator. `UITextView` has always had reliable, first-class link-tap handling via
/// its delegate — this sidesteps the SwiftUI-side gap entirely rather than trying to explain it.
///
/// `sizeThatFits(_:uiView:context:)` (iOS 16+) lets this participate in SwiftUI's layout like a
/// native view — no manual height plumbing needed. Deployment target is iOS 17 (project.yml).
struct LinkableText: UIViewRepresentable {
    let attributedString: AttributedString
    let font: UIFont
    var onLinkTap: (URL) -> Void

    func makeUIView(context: Context) -> UITextView {
        let tv = UITextView()
        tv.isEditable = false
        tv.isScrollEnabled = false
        tv.isSelectable = true
        tv.backgroundColor = .clear
        tv.textContainerInset = .zero
        tv.textContainer.lineFragmentPadding = 0
        tv.dataDetectorTypes = []
        tv.adjustsFontForContentSizeCategory = true
        tv.delegate = context.coordinator
        // Let the text wrap and grow vertically only — width comes from the SwiftUI parent.
        tv.setContentCompressionResistancePriority(.required, for: .vertical)
        return tv
    }

    func updateUIView(_ uiView: UITextView, context: Context) {
        uiView.attributedText = renderedAttributedString()
        context.coordinator.onLinkTap = onLinkTap
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onLinkTap: onLinkTap)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UITextView, context: Context) -> CGSize? {
        let width = proposal.width ?? UIView.layoutFittingCompressedSize.width
        return uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
    }

    /// Apply the default font to runs that don't already carry an explicit one (headings/bold
    /// inline markdown set their own), preserving every other attribute (color, link) as-is.
    private func renderedAttributedString() -> NSAttributedString {
        var attr = attributedString
        for run in attr.runs where run.uiKit.font == nil {
            attr[run.range].uiKit.font = font
        }
        return NSAttributedString(attr)
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var onLinkTap: (URL) -> Void
        init(onLinkTap: @escaping (URL) -> Void) { self.onLinkTap = onLinkTap }

        func textView(
            _ textView: UITextView,
            shouldInteractWith url: URL,
            in characterRange: NSRange,
            interaction: UITextItemInteraction
        ) -> Bool {
            guard interaction == .invokeDefaultAction else { return false }
            onLinkTap(url)
            return false
        }
    }
}
