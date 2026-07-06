import SwiftUI

struct ChatMessage: Identifiable, Equatable {
    enum Role { case user, assistant }
    let id = UUID()
    let role: Role
    var text: String
    var citations: [Citation] = []
    var streaming: Bool = false
}

@MainActor
final class ChatModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var input: String = ""
    @Published var isStreaming = false
    @Published var errorText: String?

    private var task: Task<Void, Never>?

    var isEmpty: Bool { messages.isEmpty }

    func send(_ settings: AppSettings, text overrideText: String? = nil) {
        let content = (overrideText ?? input).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !content.isEmpty, !isStreaming else { return }
        guard let client = settings.makeClient() else {
            errorText = APIError.notConfigured.errorDescription
            return
        }

        errorText = nil
        input = ""
        messages.append(ChatMessage(role: .user, text: content))
        var assistant = ChatMessage(role: .assistant, text: "", streaming: true)
        messages.append(assistant)
        let assistantID = assistant.id
        isStreaming = true

        // Build the full message history for the request.
        let history = messages.dropLast().map {
            ChatRequest.Message(role: $0.role == .user ? "user" : "assistant", content: $0.text)
        }
        let body = ChatRequest(messages: Array(history), vaultID: settings.vaultID)

        task = Task {
            do {
                try await client.streamChat(body) { event in
                    Task { @MainActor in
                        self.apply(event, to: assistantID)
                    }
                }
                await MainActor.run { self.finish(assistantID) }
            } catch {
                await MainActor.run {
                    self.errorText = (error as? APIError)?.errorDescription
                        ?? error.localizedDescription
                    self.finish(assistantID)
                    // Drop an empty assistant bubble on hard failure.
                    if let idx = self.messages.firstIndex(where: { $0.id == assistantID }),
                       self.messages[idx].text.isEmpty {
                        self.messages.remove(at: idx)
                    }
                }
            }
        }
        _ = assistant  // silence unused warning on some toolchains
    }

    private func apply(_ event: ChatStreamEvent, to id: UUID) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        switch event {
        case .token(let t):
            messages[idx].text += t
        case .think:
            break  // reasoning omitted from the bubble for this first version
        case .done(let done):
            if let cites = done.citations { messages[idx].citations = cites }
            messages[idx].streaming = false
        case .error(let e):
            errorText = e
            messages[idx].streaming = false
        }
    }

    private func finish(_ id: UUID) {
        if let idx = messages.firstIndex(where: { $0.id == id }) {
            messages[idx].streaming = false
        }
        isStreaming = false
    }

    func cancel() {
        task?.cancel()
        isStreaming = false
    }
}
