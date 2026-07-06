import SwiftUI

struct ChatView: View {
    /// Optional question to auto-send when the screen appears (used by the
    /// "Chiedi alla chat" deep link from a wiki page).
    var seedQuestion: String? = nil

    @EnvironmentObject private var settings: AppSettings
    @StateObject private var model = ChatModel()
    @FocusState private var inputFocused: Bool

    private let suggestions = [
        "Come funziona il retrieval a 4 fasi?",
        "Riassumi gli OKR del Q3",
        "Quali pagine parlano di Qdrant?",
    ]

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollViewReader { proxy in
                ScrollView {
                    if model.isEmpty {
                        emptyState
                    } else {
                        LazyVStack(spacing: 12) {
                            ForEach(model.messages) { message in
                                bubble(message).id(message.id)
                            }
                            if let err = model.errorText {
                                Text(err)
                                    .font(.system(size: 13))
                                    .foregroundStyle(Theme.destructive)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 6)
                        .padding(.bottom, 12)
                    }
                }
                .onChange(of: model.messages.last?.text) { _, _ in
                    if let last = model.messages.last {
                        withAnimation(.easeOut(duration: 0.15)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }
            composer
        }
        .screenBackground()
        .toolbar(.hidden, for: .navigationBar)
        .task {
            if let seed = seedQuestion, model.isEmpty {
                model.send(settings, text: seed)
            }
        }
    }

    private var header: some View {
        HStack(alignment: .bottom) {
            Text("Chat").font(.system(size: 33, weight: .bold)).foregroundStyle(Theme.label)
            Spacer()
            HStack(spacing: 6) {
                Circle().fill(Theme.success).frame(width: 7, height: 7)
                Text("Con citazioni").font(.system(size: 13)).foregroundStyle(Theme.label2)
            }
            .padding(.horizontal, 11).padding(.vertical, 5)
            .background(Theme.fieldBackground)
            .clipShape(Capsule())
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
        .padding(.bottom, 10)
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 0) {
            Image(systemName: "bubble.left.and.text.bubble.right.fill")
                .font(.system(size: 26)).foregroundStyle(.white)
                .frame(width: 56, height: 56)
                .background(Theme.tint)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .padding(.bottom, 16)
            Text("Interroga la tua conoscenza")
                .font(.system(size: 22, weight: .bold)).foregroundStyle(Theme.label)
            Text("Le risposte citano le pagine wiki di origine. Tocca un riferimento per aprirle.")
                .font(.system(size: 15)).lineSpacing(2).foregroundStyle(Theme.label2)
                .padding(.top, 6)
            VStack(spacing: 8) {
                ForEach(suggestions, id: \.self) { s in
                    Button { model.send(settings, text: s) } label: {
                        HStack(spacing: 10) {
                            Image(systemName: "magnifyingglass")
                                .font(.system(size: 14)).foregroundStyle(Theme.tint)
                            Text(s).font(.system(size: 15)).foregroundStyle(Theme.label)
                            Spacer()
                        }
                        .padding(.horizontal, 14).padding(.vertical, 13)
                        .background(Theme.card)
                        .overlay(RoundedRectangle(cornerRadius: 13, style: .continuous).stroke(Theme.separator, lineWidth: 0.5))
                        .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 20)
        }
        .padding(.horizontal, 24)
        .padding(.top, 24)
    }

    @ViewBuilder
    private func bubble(_ message: ChatMessage) -> some View {
        if message.role == .user {
            HStack {
                Spacer(minLength: 40)
                Text(message.text)
                    .font(.system(size: 16)).foregroundStyle(.white)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Theme.tint)
                    .clipShape(BubbleShape(isUser: true))
            }
        } else {
            HStack {
                VStack(alignment: .leading, spacing: 8) {
                    (Text(message.text) + caret(message))
                        .font(.system(size: 16)).lineSpacing(3)
                        .foregroundStyle(Theme.label)
                        .padding(.horizontal, 15).padding(.vertical, 12)
                        .background(Theme.card)
                        .overlay(BubbleShape(isUser: false).stroke(Theme.separator, lineWidth: 0.5))
                        .clipShape(BubbleShape(isUser: false))
                    if !message.citations.isEmpty {
                        FlowLayout(spacing: 6) {
                            ForEach(message.citations) { c in citationChip(c) }
                        }
                        .padding(.leading, 4)
                    }
                }
                Spacer(minLength: 30)
            }
        }
    }

    private func caret(_ message: ChatMessage) -> Text {
        message.streaming && message.text.isEmpty ? Text(" ▍").foregroundColor(Theme.tint) : Text("")
    }

    @ViewBuilder
    private func citationChip(_ c: Citation) -> some View {
        let ref = c.pageID.map { PageRef(id: $0, title: c.title, type: nil) }
        let chip = HStack(spacing: 5) {
            Text("\(c.n ?? 0)")
                .font(.system(size: 10, weight: .bold)).foregroundStyle(.white)
                .frame(width: 15, height: 15)
                .background(Theme.tint)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
            Text(c.title ?? c.slug ?? "Fonte")
                .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.tint)
                .lineLimit(1)
        }
        .padding(.horizontal, 9).padding(.vertical, 5)
        .background(Theme.fieldBackground)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

        if let ref {
            NavigationLink(value: ref) { chip }.buttonStyle(.plain)
        } else {
            chip
        }
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            HStack {
                TextField("Chiedi qualcosa…", text: $model.input, axis: .vertical)
                    .font(.system(size: 16)).foregroundStyle(Theme.label)
                    .lineLimit(1...5)
                    .focused($inputFocused)
                    .onSubmit { model.send(settings) }
            }
            .padding(.horizontal, 15).padding(.vertical, 9)
            .background(Theme.fieldBackground)
            .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))

            Button { model.send(settings) } label: {
                Image(systemName: model.isStreaming ? "stop.fill" : "arrow.up")
                    .font(.system(size: 18, weight: .bold)).foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(Theme.tint)
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(!model.isStreaming && model.input.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(.ultraThinMaterial)
        .overlay(Rectangle().fill(Theme.separator).frame(height: 0.5), alignment: .top)
    }
}

/// Asymmetric chat bubble corners (sharp on the sender's side).
struct BubbleShape: Shape {
    let isUser: Bool
    func path(in rect: CGRect) -> Path {
        UnevenRoundedRectangle(
            cornerRadii: .init(
                topLeading: 20,
                bottomLeading: isUser ? 20 : 5,
                bottomTrailing: isUser ? 5 : 20,
                topTrailing: 20),
            style: .continuous
        ).path(in: rect)
    }
}
