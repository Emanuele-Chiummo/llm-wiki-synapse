import SwiftUI

/// Design tokens ported from the Synapse mobile design handoff (`Synapse.dc.html`).
/// Colors are defined as dynamic (light/dark) so they resolve against the
/// environment's `colorScheme` automatically — matching iOS system behaviour.
enum Theme {

    // MARK: Semantic surface / label tokens

    /// Page background — light `#F2F2F7`, dark `#000`.
    static let background = dynamic(light: 0xF2F2F7, dark: 0x000000)
    /// Card / grouped-cell background — light `#FFF`, dark `#1C1C1E`.
    static let card = dynamic(light: 0xFFFFFF, dark: 0x1C1C1E)
    /// Primary label — light black, dark white.
    static let label = dynamic(light: 0x000000, dark: 0xFFFFFF)
    /// Secondary label (`--label2`).
    static let label2 = dynamicRGBA(
        light: (60, 60, 67, 0.6), dark: (235, 235, 245, 0.6))
    /// Tertiary label (`--label3`).
    static let label3 = dynamicRGBA(
        light: (60, 60, 67, 0.3), dark: (235, 235, 245, 0.3))
    /// Hairline separator (`--sep`).
    static let separator = dynamicRGBA(
        light: (60, 60, 67, 0.12), dark: (84, 84, 88, 0.55))
    /// Accent / tint (`--tint`) — indigo, lighter in dark mode.
    static let tint = dynamic(light: 0x4F46E5, dark: 0x8B85F5)
    /// Field / chip background (`--fieldbg`).
    static let fieldBackground = dynamicRGBA(
        light: (118, 118, 128, 0.12), dark: (118, 118, 128, 0.24))
    /// Translucent bar background (`--barbg`).
    static let barBackground = dynamicRGBA(
        light: (255, 255, 255, 0.82), dark: (30, 30, 32, 0.82))
    /// Graph canvas background (`--graphbg`).
    static let graphBackground = dynamic(light: 0xF0F0F5, dark: 0x0B0B0F)

    static let destructive = Color(hex: 0xFF3B30)
    static let success = Color(hex: 0x10B981)

    // MARK: Type → colour mapping

    /// Page types → accent colour. The backend vocabulary is
    /// `entity/concept/source/synthesis/comparison/query`; the Italian design
    /// labels (Concetto/Progetto/…) and common English words are also accepted
    /// so the palette stays stable whatever the API returns.
    private static let typeColors: [String: UInt32] = [
        // Backend vocabulary (source of truth)
        "concept": 0x4F46E5,
        "entity": 0x10B981,
        "source": 0x0EA5E9,
        "synthesis": 0xF59E0B,
        "comparison": 0xA855F7,
        "query": 0x14B8A6,
        // Design labels / English synonyms
        "concetto": 0x4F46E5,
        "persona": 0x10B981, "person": 0x10B981, "people": 0x10B981,
        "documento": 0x0EA5E9, "document": 0x0EA5E9, "doc": 0x0EA5E9,
        "progetto": 0xF59E0B, "project": 0xF59E0B,
        "riunione": 0xA855F7, "meeting": 0xA855F7,
        "strumento": 0x14B8A6, "tool": 0x14B8A6,
    ]

    /// Human-facing label for a page type (capitalised backend value).
    static func label(forType type: String?) -> String {
        let t = (type ?? "").trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return "Pagina" }
        return t.prefix(1).uppercased() + t.dropFirst()
    }

    /// The backend page-type vocabulary, in display order (for filter chips).
    static let pageTypes = ["entity", "concept", "source", "synthesis", "comparison", "query"]

    /// Deterministic accent colour for a page `type`. Falls back to a stable
    /// hash-derived hue so unknown types still get a consistent colour.
    static func color(forType type: String?) -> Color {
        let key = (type ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if let hex = typeColors[key] { return Color(hex: hex) }
        guard !key.isEmpty else { return tint }
        let palette: [UInt32] = [
            0x4F46E5, 0xF59E0B, 0x10B981, 0x0EA5E9, 0xA855F7, 0x14B8A6,
            0xEF4444, 0xEC4899,
        ]
        let idx = abs(key.hashValue) % palette.count
        return Color(hex: palette[idx])
    }

    /// A page type's colour at low opacity, for pill / chip backgrounds.
    static func tintBackground(forType type: String?, scheme: ColorScheme) -> Color {
        color(forType: type).opacity(scheme == .dark ? 0.22 : 0.12)
    }

    // MARK: Helpers

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(UIColor { traits in
            UIColor(Color(hex: traits.userInterfaceStyle == .dark ? dark : light))
        })
    }

    private static func dynamicRGBA(
        light: (r: Double, g: Double, b: Double, a: Double),
        dark: (r: Double, g: Double, b: Double, a: Double)
    ) -> Color {
        Color(UIColor { traits in
            let c = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: c.r / 255, green: c.g / 255, blue: c.b / 255, alpha: c.a)
        })
    }
}

extension Color {
    /// Build a `Color` from a 24-bit `0xRRGGBB` literal.
    init(hex: UInt32, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha)
    }

    /// Parse `#RRGGBB` / `RRGGBB` strings (used for colours coming from the API).
    init?(hexString: String?) {
        guard var s = hexString?.trimmingCharacters(in: .whitespaces), !s.isEmpty
        else { return nil }
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(hex: v)
    }
}
