# ADR-0019 — Chat: streaming transport, persistence, `<think>`/LaTeX, and the G3 gate (M4 Phase 3)

> Status: Accepted
> Date: 2026-06-28
> Sprint: v0.4 (M4 Phase 3)
> Authors: solution-architect (design), tech-writer (formatting)
> Features: F6 (multi-conversation persistent chat), F7 (`<think>` display), F8 (LaTeX→Unicode),
> F14 (context window budget), F16-rest (GFM, multi-provider, timeout), G3 (streaming perf gate)
> Invariants in force: I3, I4, I6, I7
> Supersedes: nothing. Extends: ADR-0007 (`chat()` stub → real body), ADR-0008 (`chat` operation
> already resolvable), ADR-0017/0018 (shell + NavRail Chat slot), ADR-0009 (Usage/cost accounting).

---

## 1. Context

M4 Phase 3 adds the **Chat** feature. The contract surfaces it must fill already exist:

- `InferenceProvider.chat(messages, retrieval_context) -> AsyncIterator[str]` is **locked** in
  `backend/app/ingest/provider/base.py` and **stubbed** (`raise NotImplementedError`) on all three
  backends. ADR-0007 §6 explicitly reserved the v0.4 body change as **non-breaking**: we fill bodies,
  we do not touch the ABC.
- `resolve_provider_config("chat", vault_id)` already resolves a `chat`-operation row by precedence
  (operation+vault > vault > global) in `backend/app/provider_config_service.py`. Chat selection is
  therefore **already** capability/config-driven (I6) — no new resolver code.
- `UsageAccumulator` + `_record_usage()` already give us per-call token/cost accounting out of band
  (ADR-0009). Chat reuses it verbatim.
- The frontend shell (ADR-0017) and NavRail (ADR-0018) already reserve a **disabled `chat` rail item**
  and a `SectionRouter` keyed off `activeSection`. Phase 3 *enables the slot* — it does not restructure
  the shell.

Verified dev-environment ground truth (drives the defaults below):

- **Ollama is reachable** from the backend container at `host.docker.internal:11434` with a real
  generative model **`qwen2.5:3b`** + `bge-m3`. Chat runs **real end-to-end via `OllamaProvider`** in dev.
- `provider_config` has 2 API rows but **no Anthropic key** in dev → the API path won't actually run
  locally. **Local/Ollama is the working dev path** and the default for the Phase-3 human checkpoint.
- There is **no `/chat` endpoint yet**.
- **F5 4-phase retrieval + `[n]` citations is M5** — out of scope here. Phase 3 ships a deliberately
  minimal, honest context strategy and defers citations.

This ADR locks the contract so **ai-agent-engineer** (backend: `/chat` + the three `chat()` bodies) and
**frontend-engineer** (chat UI) can build **in parallel** against a frozen interface.

---

## 2. Decisions

### 2.1 Transport — **NDJSON over a chunked `POST /chat/stream`** (not SSE, not WebSocket)

`POST /chat/stream` returns `200 OK` with `media-type: application/x-ndjson` and a chunked body:
one JSON object per line (`\n`-delimited), flushed as produced. The browser consumes it with
`fetch()` + `response.body.getReader()` (a `ReadableStream`).

**Why NDJSON-over-POST and not SSE:**

- **POST carries a JSON body natively.** The chat request is a non-trivial body (`messages[]`,
  `conversation_id`, `context_window`, scope). The browser `EventSource` API (SSE) is **GET-only and
  cannot send a request body** — it would force the conversation into query params or a side-channel.
  `fetch` + `ReadableStream` reads an SSE/NDJSON stream from a **POST** cleanly.
- **NDJSON is a strict superset of what we need and trivially typed.** Each line is a discriminated-union
  event (`{type: "token"|"think"|"done"|"error", ...}`). No `data:`/`event:` framing rules, no
  reconnection/`Last-Event-ID` semantics (which we do not want — a dropped chat stream is abandoned, not
  resumed). FastAPI emits it with a plain `StreamingResponse` over an `async` generator.
- **WebSocket is rejected:** chat is a request→stream→done lifecycle, not a long-lived bidirectional
  channel. A WS would add connection-state management, a second transport in the stack, and a heavier
  test surface for zero benefit. (ADR-0010 already deferred MCP-HTTP for similar simplicity reasons.)

**Trade-off accepted:** NDJSON has no built-in auto-reconnect (SSE does). We explicitly **do not want**
mid-stream resume for chat (a partial assistant turn is discarded; the user re-sends or hits Regenerate).
This keeps the server stateless per stream and matches I7 (a bounded, abandonable run).

**Headers:** `X-Accel-Buffering: no` and `Cache-Control: no-cache` are set so no proxy buffers the
stream. CORS already exposes what the browser needs (no new exposed header required).

### 2.2 Endpoint contract

```
POST /chat/stream
Content-Type: application/json
→ 200 OK, Content-Type: application/x-ndjson  (chunked, one JSON event per line)
→ 404 if conversation_id is provided but unknown
→ 422 on body validation failure
→ 503 if no chat provider_config resolves (I6 — never silently default a backend)
```

**Request body (`ChatRequest`):**

| Field | Type | Notes |
|-------|------|-------|
| `conversation_id` | `uuid \| null` | `null` = start a new conversation (server creates one, returns its id in the `done` event). |
| `messages` | `list[ChatMessageIn]` | The turn(s) to send. Each `{role: "user"\|"assistant"\|"system", content: str}`. Reuses the existing backend-neutral `Message` shape (`ingest/schemas.py`) — keeps I6. |
| `vault_id` | `str \| null` | Defaults to `settings.vault_id`. Used for `chat`-op provider resolution + light context (§2.3). |
| `context_window` | `int \| null` | F14. Overrides the configured window for this turn (4096..1_000_000). If null, falls back to `provider_config` / 32K default (AC-F14-2). |
| `operation` | `Literal["chat"]` | Fixed to `"chat"`; present so the same abstraction can route ingest-vs-chat differently (CLAUDE.md §5: "ingest via Local, chat via API"). |

The server **never** accepts `provider_type` / `model_id` in the body. The backend chooses the provider
via `resolve_provider_config("chat", vault_id)` (I6). The active provider shown in the UI (providerStore)
is informational; the backend is the source of truth.

**Streamed event schema (one per NDJSON line — discriminated on `type`):**

```jsonc
// 0..N content deltas (raw text, appended verbatim to the buffer; NO parse here — I3)
{ "type": "token", "delta": "the next chunk of visible text" }

// 0..N reasoning deltas, emitted ONLY while inside a <think> span (F7, §2.4)
{ "type": "think", "delta": "the next chunk of reasoning text" }

// exactly ONE terminal event on success
{ "type": "done",
  "conversation_id": "uuid",          // echoes/returns the (possibly new) conversation id
  "message_id": "uuid",               // id of the persisted assistant message
  "input_tokens": 812,
  "output_tokens": 256,
  "total_cost_usd": 0.0000,           // 0.0 for local/cli (ADR-0009); 4dp display on FE (I7)
  "iterations_used": 1,               // 1 for a plain chat turn; reserved for future fallback
  "finish_reason": "stop" | "length" | "timeout"
}

// emitted INSTEAD of done on failure (and the stream then closes)
{ "type": "error",
  "code": "provider_timeout" | "provider_error" | "no_provider" | "budget_exceeded",
  "message": "human-readable detail",
  "total_cost_usd": 0.0000            // cost incurred before the failure (I7 still logged)
}
```

**Bounding (I7).** The chat run is bounded by **two** caps, both from the resolved `chat`
`provider_config` row (never hardcoded):

- `token_budget` — the server stops the stream and emits `error.budget_exceeded` (or `done` with
  `finish_reason: "length"` if the model stops first) when cumulative tokens reach the budget.
- `timeout_seconds` — the **F16 chat timeout (default 60s)**. On timeout the server cancels the upstream
  request, emits `error.provider_timeout`, and logs the call as a provider failure (feeds the ADR-0009
  bounded single-fallback mechanism; chat fallback is **at most one** retry, same as ingest).

Every chat turn writes **one `ingest_runs`-style cost log line** at minimum (`total_cost_usd` logged per
run, I7). Chat does **not** create `ingest_runs` rows (those are the F1-INGEST-VIEW ledger for ingest);
chat cost is logged to the structured logger and returned in the `done` event. **A dedicated
`chat_runs` table is explicitly NOT created in M4** — the per-message token/cost columns on the
`messages` table (§2.5) are the persistent chat-cost record.

### 2.3 Retrieval strategy for Phase 3 — **Option (b)-lite: single light system context, no 4-phase pipeline, no citations**

F5 (tokenized → graph-expansion → budget → assembly) and `[n]` citation resolution are **M5**. For
Phase 3 we choose the simplest **honest** option that yields somewhat-grounded answers:

- The server builds **one** `retrieval_context` string injected as a **system message** ahead of the
  user turn. It is the concatenation, in priority order and **budget-capped** (§2.6), of:
  1. `vault/purpose.md` (if present) — the vault goal/scope (this is the F2 idea but used only as a
     light grounding header here; full F2 injection is M6, so we keep it to a short prefix, not the
     full provider-context contract).
  2. `vault/wiki/overview.md` (if present) — the auto-generated catalogue summary (K3/F3).
  3. The **currently-selected page** body, **iff** the client passed a `selected_page_id` — deferred:
     **not** in M4 (no content API is wired into chat yet; ADR-0017 left `GET /pages/{id}/content` as a
     reserved fast-follow). For M4 we inject **only** purpose.md + overview.md.

- **No vector search, no graph expansion, no `[n]` citations** in chat for M4. The `done` event carries
  **no `citations[]`** field in M4; the column is reserved on `messages` (§2.5) but always `[]`.

**Explicitly deferred to M5 (do not build in M4):** Qdrant/bge-m3 retrieval for chat, graph-expansion of
retrieved seeds, the 60/20/5/15 *retrieval* slice being filled from real RAG, `[n]` citation markers +
hover-to-source, and "save-to-wiki **with** citations". (Save-to-wiki itself — F6 — is in M4 but routes
the raw message text through `POST /ingest/trigger`; see §2.7.)

**Why this is the honest minimum:** purpose.md + overview.md are cheap file reads already on disk, give
the model real vault grounding, and require **zero** new retrieval machinery. Pretending to do RAG (e.g.
a half-built vector call) would create a fake seam we'd rip out in M5.

### 2.4 `<think>` detection — server-side span split, streaming-safe (F7)

Reasoning models (qwen2.5:3b *may* emit `<think>…</think>`; design for when a reasoning model does) wrap
their chain-of-thought in `<think>…</think>`. We split it **on the server**, during streaming, with a
tiny stateful scanner so the **client never parses tags**:

- The server maintains a 2-state machine (`OUTSIDE` / `INSIDE_THINK`) over the raw token stream.
- Text outside `<think>…</think>` → emitted as `{type:"token"}`. Text inside → `{type:"think"}`.
- **Partial-tag safety:** the scanner buffers a trailing fragment that *could* be the start of a tag
  (up to `len("</think>")-1 = 7` chars) and does not emit it until it can decide. This guarantees a tag
  split across two model chunks (`...<thi` | `nk>...`) is never mis-emitted as visible content.
- The **full raw content (including the literal `<think>…</think>`) is persisted** to the assistant
  `messages` row (AC-F7-2: stored un-mutated, re-derivable). The split is a **transport convenience**,
  not a data mutation. On reload, the client re-derives think-vs-content from the stored raw string with
  the same trivial split (a pure string scan at render time — not per token).

**Frontend:** the `<think>` deltas accumulate into a `ThinkBlock` that is **collapsed by default**
(AC-F7-1) and "closes" when the first `{type:"token"}` arrives (i.e., `</think>` was seen server-side).
No regex-per-token on the client.

### 2.5 Conversation persistence — **Postgres `conversations` + `messages` tables (Alembic 0007)**

F6 says "multi-conversation **persistent**" and AC-F6-1 requires "a page refresh restores the last active
conversation". localStorage cannot satisfy multi-device LiveSync, and the cost ledger (I7) belongs in the
system of record. We therefore persist in **Postgres** (consistent with ADR-0002: Postgres is the system
of record).

**New tables (Alembic migration `0007_chat_tables.py`, added to `models.py` — D2 regen via `make er`):**

```
conversations
  id            UUID  PK  default gen_random_uuid()
  vault_id      String      NOT NULL                      -- scope, matches pages/edges pattern
  title         Text        NULL                          -- user-set or first-prompt-derived
  created_at    TIMESTAMPTZ NOT NULL  default now()
  updated_at    TIMESTAMPTZ NOT NULL  default now() onupdate now()
  deleted_at    TIMESTAMPTZ NULL                          -- soft-delete (matches ADR-0005 pattern)
  INDEX (vault_id, updated_at DESC)  WHERE deleted_at IS NULL

messages
  id              UUID  PK  default gen_random_uuid()
  conversation_id UUID  FK → conversations.id  NOT NULL
  role            Text        NOT NULL                    -- 'user' | 'assistant' | 'system'
  content         Text        NOT NULL                    -- RAW content, incl. literal <think>…</think>
  citations       JSONB       NULL                        -- RESERVED (M5); always [] in M4
  provider_type   Text        NULL                        -- audit: which backend produced an assistant msg
  model_id        Text        NULL                        -- audit
  input_tokens    Integer     NOT NULL default 0          -- I7 persistent cost record
  output_tokens   Integer     NOT NULL default 0
  total_cost_usd  Numeric(10,4) NOT NULL default 0        -- 0.0000 for local/cli (ADR-0009)
  created_at      TIMESTAMPTZ NOT NULL  default now()
  INDEX (conversation_id, created_at ASC)
```

**REST (added to `main.py`):**

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/conversations` | List conversations for a vault, `updated_at DESC`, paginated (limit/offset). Excludes soft-deleted. |
| `POST` | `/conversations` | Create an empty conversation `{vault_id, title?}` → `201` with the row. (Also implicitly created by `/chat/stream` when `conversation_id` is null.) |
| `GET`  | `/conversations/{id}/messages` | Ordered (`created_at ASC`) message history for one conversation; `404` if unknown/deleted. |
| `DELETE` | `/conversations/{id}` | Soft-delete (set `deleted_at`); `204`; `404` if unknown. |

**Write path inside `/chat/stream`:** the server persists the **user** message immediately, streams the
assistant turn, and persists the **assistant** message (full raw content + token/cost columns) on
`done`. On `error`, the assistant message is **not** persisted (the user message remains so Regenerate
works). `conversations.updated_at` is bumped on each turn (drives "last active conversation" restore).

**Regenerate (AC-F6-4):** client calls `DELETE`-of-last-assistant semantics by **re-POSTing
`/chat/stream`** with the same `conversation_id` and the prior user turn; the server **deletes the last
assistant message row** for that conversation before streaming the replacement. (A small
`regenerate: true` flag on `ChatRequest` triggers this server-side delete-then-stream; no separate
endpoint.)

### 2.6 F8 LaTeX→Unicode + GFM — **parse exactly once, at stream END (I3 / G3)**

This is the I3-critical decision. **During streaming the client shows raw text, fast.** No markdown
parse, no LaTeX conversion, no syntax highlight per token. On the `done` event (stream end) **and only
then**, the completed message is rendered:

- **GFM:** rendered with **`marked`** + `marked-gfm-heading-id` (or `remark` + `remark-gfm` — engineer's
  choice, but **one** library, no custom parser; AC-F16-gfm-1). Output sanitized with **DOMPurify**
  before injection.
- **LaTeX→Unicode:** a **pure lookup-table converter** (a small in-repo `latexToUnicode(s)` util) covers
  AC-F8-2: Greek (`\alpha`→α … `\Omega`→Ω), operators (`\sum`→∑ `\prod`→∏ `\int`→∫ `\partial`→∂
  `\nabla`→∇), arrows (`\to`→→ `\leftarrow`→← `\leftrightarrow`→↔ `\Rightarrow`→⇒ `\Leftrightarrow`→⇔),
  and common sub/superscripts (`x^2`→x², `H_2O`→H₂O via Unicode super/subscript ranges). **Inline math
  only** (`\( … \)` / single-`$`). **Unconvertible / display math (`$$…$$`, `\[…\]`, matrices) is left as
  a fenced code block, never silently dropped** (AC-F8-3). No KaTeX/MathJax dependency — the F8 spec is
  "LaTeX→**Unicode**", and a lookup table is sufficient, dependency-free, and trivially testable.

**The single-parse guarantee (AC-G3-2):** the chatStore holds the streaming text in a **raw string
buffer**. The `MarkdownView` for a completed message calls `renderMarkdown(raw)` =
`marked(latexToUnicode(raw))` **once**, memoized on the (immutable, post-`done`) raw string. The
**streaming** message is rendered by `StreamingMessage`, which prints the raw buffer in a
`white-space: pre-wrap` block with **no parser at all**. When `done` fires, the message moves from
"streaming" to "settled" and is rendered once by `MarkdownView`. Parse count over a stream of N tokens =
**exactly 1** (verified by a vitest spy, AC-G3-2).

### 2.7 Save-to-wiki (F6, AC-F6-5)

A "Save to wiki" button on an assistant message POSTs the message **content** to the **existing**
`POST /ingest/trigger` path — but trigger today takes a `file_path`, not inline text. **Decision:** add a
thin **`POST /ingest/from-text`** companion (or extend `/ingest/trigger` with an optional `text` field)
that writes the message body to `vault/raw/sources/chat-<message_id>.md` and runs the **same**
`ingest_file` seam (ADR-0003). This reuses the entire orchestrated ingest loop (I1/I6) with **zero** new
ingest logic — it only adds a file-materialization step. The ingest result (page title + link) is shown
inline. **`[n]` citations carried into the saved page are M5** (M4 saves plain text).

---

## 3. Frontend components & store

All chat UI lives under `frontend/src/components/chat/`. The NavRail `chat` item is **enabled**
(remove `disabled: true` in `nav/NavRail.tsx`) and `Section` in `graphStore.ts` gains `"chat"`;
`SectionRouter.tsx` adds an `activeSection === "chat"` branch rendering `<ChatSection/>`.

| Component | File | Responsibility |
|-----------|------|----------------|
| `ChatSection` | `chat/ChatSection.tsx` | Section root: `ConversationList` (left) + active conversation (center: `MessageList` + `MessageInput`). Plugs into `SectionRouter`. |
| `ConversationList` | `chat/ConversationList.tsx` | `GET /conversations`; create/switch/delete; **virtualized if >50** (I4). |
| `MessageList` | `chat/MessageList.tsx` | **VIRTUALIZED (TanStack Virtual, I4)** — ≤30 mounted DOM rows regardless of length (AC-F6-6). Renders settled messages via `MarkdownView`, the in-flight one via `StreamingMessage`. |
| `StreamingMessage` | `chat/StreamingMessage.tsx` | Renders the **raw** streaming buffer in `pre-wrap` — **no parser** (I3). Hosts the live `ThinkBlock`. |
| `MarkdownView` | `chat/MarkdownView.tsx` | Settled-message render: `marked(latexToUnicode(raw))` → DOMPurify → `dangerouslySetInnerHTML`. Memoized on `raw` (parse-once). |
| `ThinkBlock` | `chat/ThinkBlock.tsx` | Collapsible "Reasoning" section, **collapsed by default** (F7). |
| `MessageInput` | `chat/MessageInput.tsx` | **Plain `<textarea>`** (I4: CodeMirror is reserved for the *editor*, **not** the chat input; no WYSIWYG). Enter=send, Shift+Enter=newline. Pre-fillable by ScenarioTemplates (F1). |
| `latexToUnicode` | `chat/latexToUnicode.ts` | Pure lookup-table converter (F8). Unit-tested. |

**`chatStore` (NEW, separate Zustand store — `frontend/src/store/chatStore.ts`):**

Separate from `graphStore`/`providerStore`/`ingestStore` so **streaming never re-renders the graph,
tree, ingest list, or settings** (I3). Shape (selector-only access, shallow on collections):

```ts
interface ChatState {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  messages: ChatMessage[];          // settled messages for the active conversation
  // ── streaming buffers (the ONLY fields that mutate per token) ──
  streamingContent: string;         // raw visible-text buffer (append-only; no parse)
  streamingThink: string;           // raw reasoning buffer (append-only)
  isStreaming: boolean;
  streamError: string | null;
  lastUsage: { inputTokens: number; outputTokens: number; totalCostUsd: number } | null;
}
```

**I3 streaming discipline (AC-G3-3 / AC-G3-4):**

- `appendToken(delta)` / `appendThink(delta)` do `set(s => ({ streamingContent: s.streamingContent + delta }))`.
  Only `StreamingMessage` subscribes (via `selectStreamingContent`); the settled `messages` array and the
  whole `MessageList` virtualizer **do not** re-subscribe to the streaming buffer → existing messages do
  not re-render per token (AC-G3-4).
- No selector derives parsed markdown from the streaming buffer → **zero parser selector recomputes
  during stream** (AC-G3-3: 100 appends ⇒ 0 parse-selector calls; 1 after `done`).
- On `done`: the buffer is committed as one new entry in `messages` (settled), buffers cleared,
  `isStreaming=false`. `MarkdownView` parses **once**.

**Streaming consumption** lives in a `chat/useChatStream.ts` hook: `fetch('/chat/stream', {method:'POST', body})`
→ `res.body.getReader()` → a `TextDecoder` + line-buffer that splits on `\n`, `JSON.parse`s each line,
and dispatches `token`/`think`/`done`/`error` to the store. An `AbortController` cancels on unmount /
new send (F16 timeout surfaces as an `error` event; a client-side abort is also wired for safety).

**Provider tie-in:** the chat UI reads the **active provider name** from `providerStore`
(`selectActiveProvider`) for display only. It does **not** send provider info to `/chat/stream` (I6 — the
backend resolves `chat`-op config). Changing the provider in the existing `ProviderSelector` (writing a
`scope=vault`/`global` row) changes which backend the **next** chat turn uses, with no reload (AC-F17-UI-5).

---

## 4. G3 streaming performance gate — what it asserts & how the impl satisfies it

| Assertion | Mechanism | Why the design satisfies it |
|-----------|-----------|-----------------------------|
| **AC-G3-1** No main-thread long task **>50ms** during stream | Playwright + `PerformanceObserver({entryTypes:['longtask']})` over a live Ollama (`qwen2.5:3b`) stream | Per-token work is `string += delta` + one Zustand `set` on a leaf component. No parse, no layout thrash, no graph touch. |
| **AC-G3-2** Markdown+LaTeX parse fires **exactly once** (on `done`) | vitest spy on `renderMarkdown` | `StreamingMessage` has no parser; `MarkdownView` parses once, memoized on the immutable settled `raw`. |
| **AC-G3-3** **Zero** parser-selector recomputes per token; 1 after end | vitest: 100 simulated `appendToken` calls | Streaming buffer is a raw string; no selector derives parsed output from it. |
| **AC-G3-4** Existing messages don't re-render on a new token | vitest render-count spy | `MessageList` virtualizer + settled `messages` array do not subscribe to streaming buffers; only `StreamingMessage` does. |
| Bounded DOM | TanStack Virtual (I4) | ≤30 mounted message rows (AC-F6-6); `ConversationList` virtualized >50. |

G3 is **mandatory at end of Phase 3** (not deferred-to-live without explicit orchestrator waiver, per
PM scope §4 Phase 3 and DoD gate #2). The dev path for the live assertion is **Local/Ollama qwen2.5:3b**.

---

## 5. Build order (parallelizable across two engineers)

**ai-agent-engineer (backend, F17 domain):**

1. Alembic `0007_chat_tables.py` + `Conversation`/`Message` models in `models.py` → `make er` (D2).
2. `chat/` service module: light-context builder (purpose.md + overview.md, budget-capped §2.6),
   the `<think>` span scanner (§2.4), the bounded-stream wrapper (token_budget + timeout, I7).
3. Implement the three `chat()` bodies (`ollama.py` streaming `/api/chat` `stream:true`; `api.py`
   Anthropic SSE + OpenAI-compat SSE; `cli.py` may remain `NotImplementedError` in M4 if delegated chat
   is out of dev scope — **document it, don't fake it**). Usage pushed via `_record_usage` (ADR-0009).
4. `POST /chat/stream` (NDJSON `StreamingResponse`) + `GET/POST /conversations`,
   `GET /conversations/{id}/messages`, `DELETE /conversations/{id}`, `POST /ingest/from-text` →
   `make openapi` (D4).

**frontend-engineer (chat UI):**

1. `chatStore.ts` + selectors + `useChatStream.ts` (can be built against the §2.2 schema before the
   backend is live, using a mock NDJSON reader).
2. Components in the §3 table; enable NavRail `chat` item; add `"chat"` to `Section` + `SectionRouter`.
3. `latexToUnicode.ts` + `MarkdownView` (parse-once) + `ThinkBlock`.
4. i18n keys for chat (`nav.chat` already exists; add `chat.*` to `en.json`/`it.json`, key parity).

**Shared gate:** vitest (G3-2/3/4, F7, F8) green; Playwright G3-1 green on live Ollama; D5
`shell-chat-streaming.png`; architect review for I3/I4/I6/I7.

---

## 6. AC mapping

| AC | Satisfied by |
|----|--------------|
| AC-F6-1 (persistent, restore last) | §2.5 Postgres tables + `updated_at DESC` restore |
| AC-F6-2 (multi-conversation, switch <100ms) | `ConversationList` + `messages` swap from store |
| AC-F6-3 (`[n]` citations) | **Deferred to M5** (§2.3); `citations` column reserved, `[]` in M4 |
| AC-F6-4 (regenerate) | §2.5 `regenerate:true` → server delete-last-assistant + re-stream |
| AC-F6-5 (save-to-wiki) | §2.7 `POST /ingest/from-text` reusing `ingest_file` |
| AC-F6-6 (virtualized ≤30 DOM) | §3 `MessageList` TanStack Virtual |
| AC-F7-1..4 (`<think>`) | §2.4 server span split + collapsed `ThinkBlock` + raw persisted |
| AC-F8-1..4 (LaTeX→Unicode) | §2.6 `latexToUnicode` lookup table, parse-once on `done` |
| AC-F14-1..5 (context window) | §2.2 `context_window`; §2.6 60/20/5/15 budget; truncate oldest |
| AC-F16-gfm-1 | §2.6 `marked`/`remark`-gfm + DOMPurify |
| AC-F16-timeout-1 | §2.2 `timeout_seconds` (60s chat default) → `error.provider_timeout` |
| AC-F17-UI-5 (no reload on provider change) | §3 backend resolves `chat`-op config; next turn uses it |
| AC-G3-1..4 | §4 |

---

## 7. Do-NOT list (rejection triggers in PR review)

1. **Do NOT parse markdown/LaTeX per token.** Parse fires exactly once on `done` (I3 / AC-G3-2). A
   `marked(...)`/`latexToUnicode(...)` call inside a token handler is an automatic PR rejection.
2. **Do NOT subscribe the message list or settled `messages` to the streaming buffer.** Only
   `StreamingMessage` reads `streamingContent`/`streamingThink` (I3 / AC-G3-4).
3. **Do NOT use SSE `EventSource` or WebSocket.** Transport is NDJSON over `POST /chat/stream` (§2.1).
4. **Do NOT send `provider_type`/`model_id` from the client, and do NOT hardcode a provider/model in the
   backend.** Chat resolves via `resolve_provider_config("chat", vault_id)` (I6).
5. **Do NOT add an unbounded stream.** Every chat run is capped by `token_budget` + `timeout_seconds`
   from `provider_config`; `total_cost_usd` is logged + returned (I7).
6. **Do NOT use CodeMirror or any WYSIWYG/contentEditable for the chat input.** It is a plain
   `<textarea>` (I4; CodeMirror is reserved for the editor).
7. **Do NOT strip `<think>` from stored content.** Persist the raw assistant string un-mutated; the
   split is transport-only (AC-F7-2).
8. **Do NOT build F5 retrieval, vector search for chat, or `[n]` citation resolution.** M5. M4 context =
   purpose.md + overview.md only (§2.3).
9. **Do NOT create a `chat_runs` table or write chat turns into `ingest_runs`.** Chat cost lives on the
   `messages` columns + the `done` event (§2.2).
10. **Do NOT skip D-artifacts:** `make er` (D2, new tables) and `make openapi` (D4, new endpoints) must
    be regenerated with zero drift (I8).

---

## 8. Consequences

**Positive.** Chat runs **real end-to-end in dev via Ollama/qwen2.5:3b** with no mock dependency. The
NDJSON+POST transport is one `StreamingResponse` + one `ReadableStream` reader — minimal surface, fully
typed, no reconnect/state machine. The `chat()` ABC is filled, not changed (ADR-0007 §6 honored). I3 is
satisfied **by construction** (raw buffer + parse-once + separate store). Persistence reuses the
Postgres-as-system-of-record principle and gives I7 a durable per-message cost record.

**Negative / accepted.** No mid-stream resume (NDJSON has none; we don't want it for chat). qwen2.5:3b
may not emit `<think>` — the F7 path is built and tested against a fixture so it's correct when a
reasoning model is selected. Chat answers are only lightly grounded (purpose.md + overview.md) until M5
brings real retrieval + citations — this is an explicit, documented M4→M5 seam, not a hidden gap.

**Follow-ups (M5).** F5 4-phase retrieval feeds `retrieval_context`; `[n]` citations populate the
reserved `messages.citations` column and the `done` event; save-to-wiki carries citations; the
`selected_page_id` context injection (needs `GET /pages/{id}/content`, ADR-0017 fast-follow) lands.
