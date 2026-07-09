import SwiftUI

/// Header for pushed sub-screens: a back button plus a large title and an
/// optional subtitle, matching the design's detail headers.
struct BackHeader: View {
    let title: String
    var subtitle: String? = nil
    var backLabel: String = "Indietro"
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { dismiss() } label: {
                HStack(spacing: 3) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 16, weight: .semibold))
                    Text(backLabel).font(.system(size: 16))
                }
                .foregroundStyle(Theme.tint)
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.vertical, 6)

            Text(title)
                .font(.system(size: 30, weight: .bold))
                .foregroundStyle(Theme.label)
                .padding(.horizontal, 22)
                .padding(.top, 4)
            if let subtitle {
                Text(subtitle)
                    .font(.system(size: 15))
                    .foregroundStyle(Theme.label2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 22)
                    .padding(.top, 4)
            }
        }
        // Without this the VStack hugs its content width and the parent
        // (a centered VStack) centres the whole header, leaving it visibly
        // offset from the full-width sections below. Pin it left.
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
