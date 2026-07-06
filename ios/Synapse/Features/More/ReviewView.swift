import SwiftUI

struct ReviewView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var app: AppModel

    @State private var items: [ReviewItem] = []
    @State private var loadError: String?
    @State private var isLoading = true
    @State private var busyID: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                BackHeader(
                    title: "Coda di revisione", subtitle: "Proposte generate dall’AI. Approva per applicarle al vault.",
                    backLabel: "Altro")
                    .padding(.bottom, 4)

                if let loadError {
                    ErrorState(message: loadError) { Task { await load() } }
                } else if isLoading {
                    LoadingState()
                } else if items.isEmpty {
                    EmptyState(
                        systemImage: "checkmark.seal.fill",
                        title: "Tutto revisionato",
                        message: "Nessuna proposta in attesa.",
                        tint: Theme.success)
                        .padding(.top, 30)
                } else {
                    ForEach(items) { item in card(item) }
                        .padding(.horizontal, 16)
                }
            }
            .padding(.bottom, 24)
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task { await load() }
    }

    private func card(_ item: ReviewItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                if let kind = item.itemType {
                    Text(kindLabel(kind))
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.label2)
                        .padding(.horizontal, 9).padding(.vertical, 3)
                        .background(Theme.fieldBackground)
                        .clipShape(Capsule())
                }
                if item.displayType != nil { TypePill(type: item.displayType) }
            }
            Text(item.displayTitle)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Theme.label)
                .padding(.top, 8)
            if let summary = item.rationale, !summary.isEmpty {
                Text(summary)
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.label2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 5)
            }
            HStack(spacing: 10) {
                Button { Task { await resolve(item, approve: false) } } label: {
                    label("Rifiuta", color: Theme.destructive, filled: false)
                }
                .buttonStyle(.plain)
                Button { Task { await resolve(item, approve: true) } } label: {
                    label("Approva", color: .white, filled: true)
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 14)
            .opacity(busyID == item.id ? 0.4 : 1)
            .overlay { if busyID == item.id { ProgressView() } }
        }
        .padding(16)
        .background(Theme.card)
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.separator, lineWidth: 0.5))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func label(_ text: String, color: Color, filled: Bool) -> some View {
        Text(text)
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(filled ? Color.white : color)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 11)
            .background(filled ? Theme.tint : Theme.card)
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(filled ? Color.clear : Theme.separator, lineWidth: 0.5))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func kindLabel(_ raw: String) -> String {
        switch raw {
        case "missing-page": return "Nuova pagina"
        case "suggestion": return "Suggerimento"
        case "contradiction": return "Contraddizione"
        case "duplicate": return "Duplicato"
        case "confirm": return "Conferma"
        case "purpose-suggestion": return "Scopo"
        default: return raw.capitalized
        }
    }

    private func load() async {
        guard let client = settings.makeClient() else {
            loadError = APIError.notConfigured.errorDescription; isLoading = false; return
        }
        isLoading = true; loadError = nil
        do {
            items = try await client.reviewQueue().items
        } catch {
            loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        isLoading = false
    }

    private func resolve(_ item: ReviewItem, approve: Bool) async {
        guard let client = settings.makeClient() else { return }
        busyID = item.id
        do {
            if approve { try await client.reviewApprove(id: item.id) }
            else { try await client.reviewSkip(id: item.id) }
            items.removeAll { $0.id == item.id }
            await app.refresh(settings)
        } catch {
            loadError = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        busyID = nil
    }
}
