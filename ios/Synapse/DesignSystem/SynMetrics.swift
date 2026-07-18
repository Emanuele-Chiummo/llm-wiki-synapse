import SwiftUI

/// Spacing ramp (desktop `--syn-space-*`: 4 / 6 / 8 / 10 / 12 / 16).
/// Use these instead of raw literals for new redesign code.
enum SynSpace {
    static let x1: CGFloat = 4
    static let x2: CGFloat = 6
    static let x3: CGFloat = 8
    static let x4: CGFloat = 10
    static let x5: CGFloat = 12
    static let x6: CGFloat = 16
    static let x7: CGFloat = 20
    static let x8: CGFloat = 24
    static let x9: CGFloat = 32
}

/// Corner radii (desktop `--syn-radius-*`: sm 7 / md 9 / lg 12 / pill).
enum SynRadius {
    static let sm: CGFloat = 7
    static let md: CGFloat = 9
    static let lg: CGFloat = 12
    static let xl: CGFloat = 16
    static let pill: CGFloat = 999
}

/// Typography — built on the native text styles so **Dynamic Type** works, with
/// the wordmark tightened to echo the desktop Geist logotype.
enum SynFont {
    /// Large screen title (navigation large-title equivalent).
    static let largeTitle = Font.system(.largeTitle, design: .default).weight(.bold)
    /// Section / card title.
    static let title = Font.system(.title3, design: .default).weight(.semibold)
    /// Prominent body (reading).
    static let body = Font.system(.body)
    /// Standard row label.
    static let rowTitle = Font.system(.callout).weight(.medium)
    /// Secondary / subtitle text.
    static let subhead = Font.system(.subheadline)
    /// Small meta / caption.
    static let caption = Font.system(.caption)
    /// Uppercase eyebrow / section header.
    static let eyebrow = Font.system(.caption2).weight(.heavy)
    /// Button label.
    static let button = Font.system(.callout).weight(.semibold)

    /// The "Synapse" wordmark style — tightened tracking, semibold.
    static func wordmark(_ size: CGFloat = 20) -> Font {
        .system(size: size, weight: .semibold, design: .default)
    }
}

extension View {
    /// Fill the screen with the redesign ground behind scrolling content.
    func synScreenBackground(_ soft: Bool = true) -> some View {
        background((soft ? SynColor.bgSoft : SynColor.bg).ignoresSafeArea())
    }
}
