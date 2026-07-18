import SwiftUI

/// Synapse design-system colour tokens (redesign — Track iOS 2.1, Fase A / ADR-0088).
///
/// Ported token-for-token from the desktop source of truth
/// `frontend/src/styles/theme.css` (the 1.9.3 "UI kit"). Colours are **dynamic**
/// (light/dark) so they resolve against the environment's `colorScheme`
/// automatically, matching iOS system behaviour.
///
/// Brand rules honoured here (CLAUDE.md): accent is brand blue `#2563eb`;
/// **never pure black** — light ink is `#0f1729`, the dark ground is deep-navy
/// `#0b1120`. This is now the SOLE source of colour: Fase C retired the legacy
/// `Theme.swift` (Apple-indigo accent + literal `#000000`), so no pure-black
/// token exists anywhere in the app.
enum SynColor {

    // MARK: Surfaces
    /// Page background / content paper — light `#ffffff`, dark deep-navy `#0b1120`.
    static let bg = dyn(light: 0xFFFFFF, dark: 0x0B1120)
    /// Grouped / app ground — cool paper light, navy dark.
    static let bgSoft = dyn(light: 0xF4F6FB, dark: 0x121A2B)
    /// Card surface.
    static let surface = dyn(light: 0xFFFFFF, dark: 0x121A2B)
    /// Raised surface (popovers, elevated cards).
    static let surfaceRaised = dyn(light: 0xFFFFFF, dark: 0x18213A)
    /// Hover / subtle fill (chips, pressed rows).
    static let surfaceHover = dyn(light: 0xEDF1F9, dark: 0x1E2942)
    /// Sunken surface (code blocks, table headers).
    static let surfaceSunken = dyn(light: 0xF4F6FB, dark: 0x0B1120)
    /// Editable field background.
    static let inputBg = dyn(light: 0xF7F9FD, dark: 0x0B1120)

    // MARK: Borders (cool hairlines)
    static let border = dyn(light: 0xE3E8F1, dark: 0x26324C)
    static let borderSubtle = dyn(light: 0xEDF1F8, dark: 0x1C2740)

    // MARK: Text (ink — blue-biased near-black, NEVER pure/GitHub black)
    /// Primary ink.
    static let text = dyn(light: 0x0F1729, dark: 0xE7ECF7)
    /// Muted secondary text.
    static let textMuted = dyn(light: 0x566073, dark: 0x98A3BA)
    /// Dim tertiary text (WCAG-AA tuned on both grounds, per the desktop token).
    static let textDim = dyn(light: 0x68717A, dark: 0x7D8590)
    /// Text drawn on top of the accent / gradient (CTAs).
    static let onAccent = Color.white

    // MARK: Accent (brand blue — reserved for live / interactive / selected)
    static let accent = dyn(light: 0x2563EB, dark: 0x58A6FF)
    static let accentStrong = dyn(light: 0x1D4FD7, dark: 0x79C0FF)
    /// Active / hover tint background.
    static let accentSoft = dyn(light: 0xEAF1FE, dark: 0x1C2C3E)
    static let accent2 = dyn(light: 0x7C3AED, dark: 0xC084FC)

    // MARK: Semantic
    static let green = dyn(light: 0x1A7F37, dark: 0x3FB950)
    static let amber = dyn(light: 0x9A6700, dark: 0xD29922)
    static let red = dyn(light: 0xCF222E, dark: 0xF85149)
    static let success = green
    static let danger = red
    static let warn = amber

    // MARK: Per-type jewel tones (tree glyphs, badges, graph legend)
    // Matches the desktop --syn-type-* set, lightened for the navy ground in dark.
    private static let typeColors: [String: (light: UInt32, dark: UInt32)] = [
        "concept": (0x8B5CF6, 0xA78BFA),      // violet
        "entity": (0x3B82F6, 0x5C9AFF),       // blue
        "source": (0x0E9A92, 0x2CC3B9),       // teal
        "synthesis": (0x6366F1, 0x8B8DF7),    // indigo
        "comparison": (0xCF6A44, 0xE58A63),   // copper
        "query": (0xB9791A, 0xD3A24A),        // amber
        "overview": (0xB8860B, 0xE3B341),     // amber
        "index": (0x9A6700, 0xE3B341),        // amber
        "log": (0x6639BA, 0xA78BFA),          // violet
        "other": (0x6E7781, 0x8B949E),
    ]

    /// Deterministic accent colour for a page `type` (falls back to a stable hue).
    static func color(forType type: String?) -> Color {
        let key = (type ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if let pair = typeColors[key] { return dyn(light: pair.light, dark: pair.dark) }
        guard !key.isEmpty else { return accent }
        let palette = ["concept", "entity", "source", "synthesis", "comparison", "query"]
        let idx = abs(key.hashValue) % palette.count
        return color(forType: palette[idx])
    }

    /// A page type's colour at low opacity, for pill / chip backgrounds.
    static func tintBackground(forType type: String?, scheme: ColorScheme) -> Color {
        color(forType: type).opacity(scheme == .dark ? 0.22 : 0.12)
    }

    /// SF Symbol per page type — makes lists scannable by shape as well as colour.
    private static let typeIcons: [String: String] = [
        "concept": "lightbulb.fill",
        "entity": "cube.fill",
        "source": "doc.text.fill",
        "synthesis": "sparkles",
        "comparison": "arrow.left.arrow.right",
        "query": "magnifyingglass",
        "overview": "square.grid.2x2.fill",
        "index": "list.bullet.rectangle.fill",
        "log": "clock.arrow.circlepath",
    ]

    static func icon(forType type: String?) -> String {
        let key = (type ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        return typeIcons[key] ?? "circle.hexagongrid.fill"
    }

    /// Human-facing capitalised label for a page type.
    static func label(forType type: String?) -> String {
        let t = (type ?? "").trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return "Page" }
        return t.prefix(1).uppercased() + t.dropFirst()
    }

    // MARK: Signature gradient (brand blue → indigo → violet; used with restraint)
    static let gradStart = dyn(light: 0x1D4ED8, dark: 0x2563EB)
    static let gradMid = dyn(light: 0x4338CA, dark: 0x4F46E5)
    static let gradEnd = dyn(light: 0x7C3AED, dark: 0xA855F7)

    static var signatureGradient: LinearGradient {
        LinearGradient(
            colors: [gradStart, gradMid, gradEnd],
            startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    // MARK: Helper
    /// A dynamic colour that resolves the light / dark 24-bit literal per trait.
    static func dyn(light: UInt32, dark: UInt32) -> Color {
        Color(UIColor { traits in
            UIColor(Color(hex: traits.userInterfaceStyle == .dark ? dark : light))
        })
    }
}

extension Color {
    /// Build a colour from a 24-bit `0xRRGGBB` literal. Lives here in the design
    /// system since Fase C retired the legacy `Theme.swift` that used to define it.
    init(hex: UInt32, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha)
    }
}
