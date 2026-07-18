import Observation
import SwiftUI

/// One chat bubble in the transcript. The assistant's `visible` / `think` are
/// kept as raw strings while streaming and rendered as plain `Text` (cheap); the
/// heavy markdown parse happens once, when the stream settles (I3) — never per
/// token.
struct ChatBubble: Identifiable, Equatable {
    enum Role: Equatable { case user, assistant }
    let id: String
    let role: Role
    var visible: String
    var think: String
    var citations: [API.Citation]
    var webCitations: [API.WebCitation]
    var isStreaming: Bool
    var failed: String?
}

@Observable
@MainActor
final class ChatViewModel {
    var bubbles: [ChatBubble] = []
    var input = ""
    var conversations: [API.Conversation] = []
    var conversationID: String?
    var isStreaming = false

    private var streamTask: Task<Void, Never>?

    var canSend: Bool {
        !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isStreaming
    }

    func loadConversations(_ session: SynapseSession) async {
        guard let client = session.client() else { return }
        conversations = (try? await client.conversations(limit: 50))?.items ?? []
    }

    func newConversation() {
        streamTask?.cancel()
        isStreaming = false
        conversationID = nil
        bubbles = []
    }

    func openConversation(_ id: String, _ session: SynapseSession) async {
        guard let client = session.client() else { return }
        streamTask?.cancel(); isStreaming = false
        conversationID = id
        bubbles = []
        guard let list = try? await client.messages(conversationID: id) else { return }
        bubbles = list.items.map { m in
            let (think, visible) = ChatViewModel.splitThink(m.content)
            return ChatBubble(
                id: m.id,
                role: m.role == "user" ? .user : .assistant,
                visible: visible,
                think: think,
                citations: m.citations ?? [],
                webCitations: [],
                isStreaming: false,
                failed: nil)
        }
    }

    func send(_ session: SynapseSession) {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming, let client = session.client() else { return }
        input = ""
        isStreaming = true

        bubbles.append(ChatBubble(id: UUID().uuidString, role: .user, visible: text,
                                  think: "", citations: [], webCitations: [],
                                  isStreaming: false, failed: nil))
        let assistantID = UUID().uuidString
        bubbles.append(ChatBubble(id: assistantID, role: .assistant, visible: "",
                                  think: "", citations: [], webCitations: [],
                                  isStreaming: true, failed: nil))

        // Full history so multi-turn context is preserved.
        let history = bubbles.dropLast().map {
            ChatStreamRequest.Message(role: $0.role == .user ? "user" : "assistant",
                                      content: $0.visible)
        }
        let req = ChatStreamRequest(conversationID: conversationID, messages: Array(history),
                                    vaultID: session.vaultID)

        streamTask = Task {
            do {
                try await client.streamChat(req) { [weak self] event in
                    Task { @MainActor in self?.apply(event, to: assistantID) }
                }
            } catch {
                await MainActor.run {
                    self.fail(assistantID,
                              (error as? SynAPIError)?.errorDescription ?? error.localizedDescription)
                }
            }
            await MainActor.run { self.isStreaming = false }
        }
    }

    func cancel() {
        streamTask?.cancel()
        isStreaming = false
        if let idx = bubbles.lastIndex(where: { $0.isStreaming }) {
            bubbles[idx].isStreaming = false
        }
    }

    // MARK: Stream application

    private func apply(_ event: SynStreamEvent, to id: String) {
        guard let idx = bubbles.firstIndex(where: { $0.id == id }) else { return }
        switch event {
        case .token(let delta):
            bubbles[idx].visible += delta
        case .think(let delta):
            bubbles[idx].think += delta
        case .done(let done):
            bubbles[idx].citations = done.citations
            bubbles[idx].webCitations = done.webCitations
            bubbles[idx].isStreaming = false     // triggers the one-shot markdown parse
            if let cid = done.conversationID { conversationID = cid }
        case .error(_, let message):
            fail(id, message)
        }
    }

    private func fail(_ id: String, _ message: String) {
        guard let idx = bubbles.firstIndex(where: { $0.id == id }) else { return }
        bubbles[idx].isStreaming = false
        bubbles[idx].failed = message
    }

    /// Split persisted content into (reasoning, visible) by extracting
    /// `<think>…</think>` spans (streamed turns arrive already-separated).
    static func splitThink(_ content: String) -> (think: String, visible: String) {
        guard content.contains("<think>") else { return ("", content) }
        var think = ""
        var visible = ""
        var rest = Substring(content)
        while let open = rest.range(of: "<think>") {
            visible += rest[rest.startIndex..<open.lowerBound]
            if let close = rest.range(of: "</think>", range: open.upperBound..<rest.endIndex) {
                think += rest[open.upperBound..<close.lowerBound]
                rest = rest[close.upperBound...]
            } else {
                think += rest[open.upperBound...]
                rest = rest[rest.endIndex...]
                break
            }
        }
        visible += rest
        return (think.trimmingCharacters(in: .whitespacesAndNewlines),
                visible.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

/// Streaming chat (F6/F7) — cited answers with a collapsible `<think>` reasoning
/// block, over multi-conversation persistent history.
struct ChatScreen: View {
    /// When true, opens the most recent conversation on appear (used by the
    /// screenshot harness to capture a populated transcript deterministically).
    var autoOpenFirstConversation = false

    @Environment(SynapseSession.self) private var session
    @Environment(WikiNavigator.self) private var navigator
    @State private var model = ChatViewModel()
    @State private var showConversations = false

    var body: some View {
        VStack(spacing: 0) {
            transcript
            composer
        }
        .synScreenBackground()
        .navigationTitle("Chat")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button { showConversations = true } label: {
                    Image(systemName: "bubble.left.and.bubble.right")
                }.accessibilityLabel("Conversations")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { model.newConversation() } label: {
                    Image(systemName: "square.and.pencil")
                }.accessibilityLabel("New conversation")
            }
        }
        .sheet(isPresented: $showConversations) {
            ConversationPicker(model: model)
        }
        .task {
            await model.loadConversations(session)
            if autoOpenFirstConversation, let first = model.conversations.first {
                await model.openConversation(first.id, session)
            }
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: SynSpace.x5) {
                    if model.bubbles.isEmpty { emptyState }
                    ForEach(model.bubbles) { bubble in
                        MessageBubbleView(bubble: bubble) { citation in
                            if let id = citation.pageID {
                                navigator.push(.page(id: id, title: citation.title))
                            }
                        }
                        .id(bubble.id)
                    }
                }
                .padding(.horizontal, SynSpace.x6)
                .padding(.vertical, SynSpace.x5)
            }
            .onChange(of: model.bubbles.last?.visible) { _, _ in
                if let last = model.bubbles.last?.id {
                    withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(last, anchor: .bottom) }
                }
            }
        }
    }

    private var emptyState: some View {
        SynEmptyState(
            systemImage: "bubble.left.and.text.bubble.right.fill",
            title: "Ask your knowledge base",
            message: "Answers stream in with [n] citations back into the wiki, and a collapsible reasoning trace.")
        .frame(maxWidth: .infinity, minHeight: 420)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: SynSpace.x3) {
            TextField("Message", text: $model.input, axis: .vertical)
                .textFieldStyle(.plain)
                .font(SynFont.body)
                .foregroundStyle(SynColor.text)
                .lineLimit(1...5)
                .padding(.horizontal, SynSpace.x5)
                .padding(.vertical, SynSpace.x4)
                .background(SynColor.inputBg)
                .clipShape(RoundedRectangle(cornerRadius: SynRadius.lg, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: SynRadius.lg, style: .continuous)
                    .strokeBorder(SynColor.border, lineWidth: 1))

            if model.isStreaming {
                Button { model.cancel() } label: {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 30)).foregroundStyle(SynColor.red)
                }
            } else {
                Button { model.send(session) } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 30))
                        .foregroundStyle(model.canSend ? SynColor.accent : SynColor.textDim)
                }
                .disabled(!model.canSend)
            }
        }
        .padding(.horizontal, SynSpace.x6)
        .padding(.vertical, SynSpace.x4)
        .background(SynColor.surface)
        .overlay(Rectangle().fill(SynColor.borderSubtle).frame(height: 1), alignment: .top)
    }
}

/// One transcript bubble. Assistant bodies render as `MarkdownView` once settled,
/// as plain `Text` while streaming (I3). Reasoning is collapsed by default (F7).
private struct MessageBubbleView: View {
    let bubble: ChatBubble
    let onCitationTap: (API.Citation) -> Void

    var body: some View {
        if bubble.role == .user {
            HStack {
                Spacer(minLength: SynSpace.x9)
                Text(bubble.visible)
                    .font(SynFont.body)
                    .foregroundStyle(SynColor.onAccent)
                    .padding(.horizontal, SynSpace.x5)
                    .padding(.vertical, SynSpace.x4)
                    .background(SynColor.accent)
                    .clipShape(RoundedRectangle(cornerRadius: SynRadius.lg, style: .continuous))
            }
        } else {
            VStack(alignment: .leading, spacing: SynSpace.x3) {
                if !bubble.think.isEmpty { ReasoningDisclosure(text: bubble.think) }

                if let failed = bubble.failed {
                    HStack(spacing: SynSpace.x2) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(SynColor.red)
                        Text(failed).font(SynFont.subhead).foregroundStyle(SynColor.textMuted)
                    }
                } else if bubble.isStreaming {
                    if bubble.visible.isEmpty {
                        TypingIndicator()
                    } else {
                        // Plain text while streaming — NO markdown parse per token.
                        Text(bubble.visible)
                            .font(SynFont.body).foregroundStyle(SynColor.text).lineSpacing(5)
                    }
                } else {
                    MarkdownView(bubble.visible)   // parsed once, on settle
                }

                if !bubble.citations.isEmpty { citations }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var citations: some View {
        VStack(alignment: .leading, spacing: SynSpace.x2) {
            SynSectionHeader(text: "Citations")
            FlowLayout(spacing: SynSpace.x2) {
                ForEach(bubble.citations) { c in
                    Button { onCitationTap(c) } label: {
                        HStack(spacing: SynSpace.x1) {
                            Text("[\(c.n ?? 0)]").font(SynFont.caption.monospacedDigit())
                                .foregroundStyle(SynColor.accent)
                            Text(c.title ?? c.slug ?? "page").font(SynFont.caption)
                                .foregroundStyle(SynColor.text).lineLimit(1)
                        }
                        .padding(.horizontal, SynSpace.x3)
                        .padding(.vertical, SynSpace.x2)
                        .background(SynColor.accentSoft)
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.top, SynSpace.x2)
    }
}

private struct ReasoningDisclosure: View {
    let text: String
    @State private var expanded = false   // collapsed by default (F7)
    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            Text(text)
                .font(SynFont.caption)
                .foregroundStyle(SynColor.textMuted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, SynSpace.x2)
        } label: {
            HStack(spacing: SynSpace.x2) {
                Image(systemName: "brain").font(.caption2)
                Text("Reasoning").font(SynFont.eyebrow)
            }
            .foregroundStyle(SynColor.textMuted)
        }
        .padding(SynSpace.x4)
        .background(SynColor.surfaceSunken)
        .clipShape(RoundedRectangle(cornerRadius: SynRadius.md, style: .continuous))
    }
}

private struct TypingIndicator: View {
    @State private var on = false
    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { i in
                Circle().fill(SynColor.textDim).frame(width: 7, height: 7)
                    .opacity(on ? 1 : 0.3)
                    .animation(.easeInOut(duration: 0.6).repeatForever().delay(Double(i) * 0.2), value: on)
            }
        }
        .onAppear { on = true }
    }
}

/// Conversation switcher sheet.
private struct ConversationPicker: View {
    @Bindable var model: ChatViewModel
    @Environment(SynapseSession.self) private var session
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Button {
                    model.newConversation(); dismiss()
                } label: {
                    Label("New conversation", systemImage: "square.and.pencil")
                        .foregroundStyle(SynColor.accent)
                }
                ForEach(model.conversations) { c in
                    Button {
                        Task { await model.openConversation(c.id, session); dismiss() }
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(c.displayTitle).font(SynFont.rowTitle).foregroundStyle(SynColor.text)
                            if let p = c.preview, !p.isEmpty {
                                Text(p).font(SynFont.caption).foregroundStyle(SynColor.textMuted)
                                    .lineLimit(1)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Conversations")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await model.loadConversations(session) }
        }
    }
}
