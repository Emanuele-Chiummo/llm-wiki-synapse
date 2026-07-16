# ADR-0059 — Chat composer parity: attach-image (vision), web-search toggle, retrieval modes

- **Status:** Accepted
- **Date:** 2026-07-06
- **Sprint:** v1.3 UI-alignment batch **B2 — Chat composer** (`docs/reference/UI-ALIGNMENT-PLAN-2026-07.md` §B2, gaps C1–C4)
- **Amends:** **ADR-0050** (Retrieval scope restricted to `wiki/` pages only). This ADR does **not**
  supersede ADR-0050 — wiki retrieval stays wiki-only. The web-search block of §C2 is an
  **additive, separately-labeled, separately-namespaced** context source that never enters the
  wiki citation path. See §3 for the compatibility argument.
- **Builds on:** ADR-0007 (InferenceProvider ABC, I6) · ADR-0011 (ingest-contract DTOs — `Message`,
  `ProviderCapabilities`) · ADR-0019 (chat streaming transport, `<think>`, G3) · ADR-0022 (F5
  4-phase retrieval + `[n]` citations, `expansion_depth ≤ 2` hard cap) · ADR-0024 (bounded SearXNG
  fetch/strip seam, I9) · R8-2/F12 (`supports_vision` capability + `caption_image` seam, already
  shipped)
- **Features:** F6 (multi-conversation chat) · F5 (retrieval + citations) · F10/I9 (SearXNG web
  search) · F14 (configurable context-window budget) · F17/I6 (capability-aware provider routing —
  vision gating) · R1 (nashsu/llm_wiki `chat-input.tsx` composer parity)
- **Reference:** R1 (nashsu/llm_wiki `chat-input.tsx`: Attach-image with previews + count/size caps ·
  Web-search toggle · AnyTXT toggle · segmented `Fast | Standard | Deep | Local first` mode
  selector) · R8 (SearXNG — the web-search backend, never Tavily)
- **Invariants owned:** **I3** (attached images are static thumbnails — never re-rendered per
  streamed token; markdown/LaTeX parse still happens at stream END) · **I6** (vision is gated on
  `self.capabilities().supports_vision`, NOT on `isinstance`/`type`/`provider_type`; no backend
  hardcoded) · **I7** (every new path is bounded: image count/bytes caps, single-shot web fetch
  with result/char caps, frozen retrieval presets with `expansion_depth ≤ 2`, cost logged) ·
  **I9** (web search is one bounded SearXNG call through the existing `ops/searxng.py` seam — never
  Tavily, never a new search service)
- **Author:** solution-architect

---

## BLOCKING OBJECTIONS

**None.** The pinned C1–C4 contract is invariant-compliant as written. Three **non-blocking
corrections / clarifications** are folded into the decision below (they refine the contract, they do
not reject it):

1. **`supports_vision` already exists.** C1 says `capabilities()` "gains `supports_vision: bool`".
   It is **already present** on `ProviderCapabilities` (`backend/app/ingest/schemas.py`, added for
   R8-2/F12 image captioning, default `False`) and every provider already reports it. C1 must
   **reuse** the existing field for the chat path, not re-add it. No ABC change, no migration on the
   descriptor. This is a strengthening of the contract (the seam already exists), recorded so the
   engineers do not duplicate the field. See §2.1.

2. **Two message DTOs must both carry images, and the field is dropped at the boundary if the
   backend cannot see it.** The wire model is `ChatMessageIn` (`backend/app/routers/chat.py`); the
   backend-neutral provider DTO is `Message` (`backend/app/ingest/schemas.py`). The contract names
   `Message`; `ChatMessageIn` mirrors `Message` (its docstring says so) and therefore must gain the
   same optional `images` field, mapped through in `run_chat_stream`. The **capability check lives
   in the provider's `chat()`**, reading `self.capabilities().supports_vision` (defense-in-depth) —
   the frontend gate is UX, not the security boundary. See §2.1.

3. **The retrieval presets map onto EXISTING `retrieve()` knobs — no new retrieval internals.**
   `retrieve()` already accepts `k` and `expansion_depth` with a hard-clamped
   `_MAX_EXPANSION_DEPTH = 2`. C3's four modes are a **frozen preset table** that selects `(k,
   expansion_depth)` and the F14 budget slice — they do **not** add a new retrieval pass, a new
   phase, or agent rounds. `deep` cannot breach the depth-2 clamp even if the preset were
   mis-authored, because `retrieve()` re-clamps. This is exactly the I7-safe shape. See §2.3.

---

## 1. Context

The live nashsu/llm_wiki chat composer (`chat-input.tsx`) exposes a toolbar Synapse's chat composer
does not: an **Attach-image** button (multimodal input with inline previews and count/size caps), a
**Web-search** toggle (the emerald-dot affordance), an **AnyTXT** toggle (Windows-only local-index
search, greyed when unavailable), and a segmented **agent-mode** selector (`Fast | Standard | Deep |
Local first`). B2 (P0 in the UI-alignment plan) closes this composer gap.

Synapse's chat backend already has the right seams to do this **without** loosening any invariant:

- **Vision** is already a first-class provider capability. `ProviderCapabilities.supports_vision`
  (R8-2/F12) and the `InferenceProvider.caption_image()` default-`NotImplementedError` seam already
  exist; the ingest path already gates image captioning on `supports_vision` **explicitly, never by
  `isinstance`** (the I6 rule, enforced in `backend/app/ingest/provider/base.py`). C1 extends the
  *same* capability to the chat surface; it does not invent a new one.
- **Retrieval** is a single bounded pass with explicit `k` / `expansion_depth` caps
  (`backend/app/rag/retrieval.py`, ADR-0022). `expansion_depth` is hard-clamped to
  `_MAX_EXPANSION_DEPTH = 2` at the top of `retrieve()`. C3's user-facing modes are presets over
  these existing caps.
- **Web search** is already a bounded SearXNG seam. `ops/searxng.py::searxng_search()` (I9, the
  emerald search backend) and `ops/deep_research.py::_fetch_and_extract` / `_fetch_max_chars` (the
  bounded fetch/strip helpers, ADR-0024) already exist. C2 makes **one** bounded call through them —
  it does not reinvent search or introduce a fetch loop.

The controlling tension is **ADR-0050**: chat retrieval was deliberately narrowed to `wiki/` pages
only, so the user is never confused about the epistemic status of a citation (raw input vs.
synthesized wiki knowledge). C2 adds an *external* knowledge source. The design must add web context
**without diluting wiki grounding or blurring the citation UX** ADR-0050 protects. §3 makes that
argument in full; the short version is: the web block is a **separate, clearly-labeled block with its
own `[W1]…[Wn]` citation namespace** — the wiki `[n]` namespace and the wiki-only `_load_page_meta`
filter are **untouched**.

The implementation contract (C1–C4) is already pinned; three engineers are building against it in
parallel. This ADR **documents and gates** that contract against I3/I6/I7/I9 and freezes the two
tables (retrieval presets, bounds) so no per-request arbitrary depth or unbounded fetch can slip in.

---

## 2. Decision

### 2.1 C1 — Attach image (vision in chat), capability-aware (I6, I3, I7)

Add multimodal image input to the chat turn, gated on the provider's already-existing vision
capability.

**Contract DTOs.**
- New `MessageImage` model: `{ mime: str, data_base64: str }` (base64-encoded bytes + MIME type).
- `Message` (`backend/app/ingest/schemas.py`) gains **optional** `images: list[MessageImage] = []`.
  Additive and backward-compatible (absent → `[]`), exactly like the `tags` precedent on
  `WikiFrontmatter`.
- `ChatMessageIn` (`backend/app/routers/chat.py`, the wire model that "mirrors the backend-neutral
  `Message` shape") gains the **same** optional `images` field and maps it through to `Message` in
  the `run_chat_stream` call. Both DTOs change together — they are one contract in two layers.

**Capability field — REUSE, do not re-add.** `ProviderCapabilities.supports_vision: bool` **already
exists** (default `False`, shipped for R8-2/F12). Each provider already reports it. C1 does **not**
add it; it reuses it for the chat path. (Correction #1 above.)

**The I6 gate lives in `provider.chat()`, reading `self.capabilities().supports_vision`.**
- `supports_vision == True`  → the provider includes the images in its model call using the backend's
  native multimodal message format (Anthropic image content blocks / OpenAI-compatible image parts /
  Ollama vision input).
- `supports_vision == False` → the provider **drops** the images and sends text only
  (defense-in-depth — a text-only backend never receives an image payload it would choke on or
  silently mis-handle).
- **NO `isinstance` / `type()` / class-name / `provider_type == "…"` branch** anywhere on this path.
  The decision reads the capability descriptor **only** (the I6 rule in `provider/base.py`). This is
  the same idiom the ingest path already uses for `caption_image`.

**Frontend gate (UX, not the boundary).** The composer's Attach-image button is enabled only when the
resolved chat provider reports `supports_vision`. That flag is surfaced to the client via
`GET /status` (a new additive `supports_vision: bool`, resolved from the chat provider's
`capabilities()` — resolved at runtime, never hardcoded). When `False`, the button is disabled with a
tooltip. **The frontend gate is a nicety; the provider `chat()` drop is the guarantee.**

**Bounds (I7).** Two hard caps, both env-configurable structural limits:
- `CHAT_MAX_IMAGES` (default **4**) — max images per turn; over-limit → **422**.
- `CHAT_MAX_IMAGE_BYTES` (default **5 MB**, decoded) — max per-image size; over-limit → **422**.
Enforced at the request boundary (before any provider call). No loop is introduced.

**I3 — images never enter the streaming hot path.** Attached images are rendered as **static
thumbnails** in the composer and in message history. They are decoded/validated **once** at submit,
persisted **once**, and shown as static `<img>` — they are **never** re-rendered per streamed token,
and they do **not** touch the `ThinkScanner` delta loop or the end-of-stream markdown/LaTeX parse
(ADR-0019 §2.4, the G3 gate). The stream still carries text deltas only.

**Persistence (history / regenerate).** The images are persisted on the `messages` row (the same
`ChatMessage` row that already carries `content` + `citations`), so history renders them and
**Regenerate** (ADR-0019, AC-F6-4) re-sends the exact same image payload. The engineers choose the
column shape (a `JSONB images` column mirroring the existing `citations` JSONB, or an equivalent) —
this ADR requires only that (a) the payload round-trips, (b) if a column is added the ER diagram is
regenerated (`make er`, I8), and (c) no image bytes are ever inlined into the streamed NDJSON.

### 2.2 C2 — Web-search toggle (AMENDS ADR-0050 — additive, separately namespaced)

Add an opt-in web-search context source to the chat turn. **Default OFF.**

**Request field.** `use_web_search: bool = False` on `ChatRequest`.

**When ON — exactly one bounded pass (no loop, I7/I9):**
1. **One** SearXNG call via the existing `ops/searxng.py::searxng_search()` seam (I9 — never Tavily,
   never a new search service), capped to `CHAT_WEB_MAX_RESULTS` (default **5**).
2. Fetch + strip the top-N results reusing the existing `ops/deep_research.py` fetch/strip helper
   (`_fetch_and_extract` / `_fetch_max_chars`), each result capped to `CHAT_WEB_FETCH_MAX_CHARS`
   (default **8000**). Concurrency and per-fetch timeout inherit the deep-research seam's existing
   bounds. **Single-shot** — there is NO assess→refine→re-query loop (that is deep-research's job,
   ADR-0024; chat web-search is one pass).
3. Inject the stripped web results as a **SEPARATE, clearly-labeled context block** appended AFTER
   the wiki retrieval block, with its **own citation namespace `[W1]…[Wn]`**. The wiki block and its
   `[n]` markers are **byte-for-byte unchanged** — the web block is concatenated as a distinct,
   headed section (e.g. `## Web results (external)`), never interleaved into the wiki block.
4. **Cost logged (I7).** The web pass is fetch/strip only — no inference call of its own — but the
   run's structured log line records that web-search ran and how many results were fetched, so the
   turn's cost/behaviour is auditable (consistent with the ADR-0024 cost-logging discipline).

**`done` event gains `web_citations`.** The terminal `done` NDJSON event gains an additive
`web_citations: [{ index, title, url }]` field (parallel to the existing `citations` field, but a
**distinct** field with a **distinct** namespace). Existing clients that ignore unknown keys are
unaffected (non-breaking, ADR-0019/0022 convention). The `citations` field (wiki `[n]`) is
unchanged.

**ADR-0050 compatibility — see §3 for the full argument.** In one line: the wiki-only
`_load_page_meta` filter (`file_path NOT LIKE 'raw/%'`) and the wiki `[n]` namespace are untouched;
the web block is *additional* external context with its own `[W]` namespace and its own labeled
block, so it can never be mistaken for a wiki citation and never dilutes wiki grounding.

### 2.3 C3 — Retrieval modes (FROZEN preset table — I7, no arbitrary per-request depth)

Add a user-facing retrieval-depth selector that maps to a **fixed, frozen table** of presets over
the **existing** `retrieve()` knobs. Synapse keeps its ⭐ deterministic single-pass pipeline;
llm_wiki uses agent decision-rounds. **We mirror the user-facing MODES via presets, NOT the
internals** — there are no agent rounds, no extra retrieval pass, no per-request arbitrary depth.

**Request field.** `retrieval_mode: "fast" | "standard" | "deep" | "local_first" = "standard"` on
`ChatRequest`. An enum — any other value → **422**. There is **no** raw `k` / `expansion_depth` on
the chat request (that would be the I7 hole this ADR forbids — see Do-NOT #4).

**FROZEN preset table** (this is the authoritative source; the code MUST match it exactly):

| `retrieval_mode` | `k` | `expansion_depth` | F14 retrieval budget slice | Web-search behaviour | Notes |
|---|---|---|---|---|---|
| `fast` | 4 | **0** (no graph expansion) | standard (0.20) | as requested | Vector/lexical seeds only; skips Phase 2 BFS. |
| `standard` | 8 | 1 | standard (0.20) | as requested | **CURRENT defaults — behaviour UNCHANGED.** Backward-compatible baseline. |
| `deep` | 12 | **2** (HARD cap — never exceed) | larger slice (see below) | as requested | Widest retrieval; `expansion_depth` is at the ADR-0022/I2 ceiling and cannot go higher. |
| `local_first` | 8 | 1 | standard (0.20) | **gated**: fetch web only if local hits `< LOCAL_FIRST_MIN_HITS` (3) | Retrieval == `standard`; the *only* effect is suppressing the web fetch when local grounding already suffices. Meaningful only with `use_web_search=True`. |

Constraints on the table:
- **`expansion_depth` NEVER exceeds 2** for any mode. `retrieve()` re-clamps to
  `_MAX_EXPANSION_DEPTH = 2` regardless, so even a mis-authored preset cannot breach the ADR-0022/I2
  ceiling — but the table itself must never *specify* > 2 (Do-NOT #1).
- **`standard` is exactly today's behaviour.** `k=8, expansion_depth=1` is the current
  `run_chat_stream` call shape; existing chat tests stay green with `retrieval_mode` defaulting to
  `standard`.
- **`deep`'s "larger slice"** raises the F14 retrieval fraction from the default 0.20 to a **fixed,
  frozen** larger constant (e.g. 0.30) — it is a **constant in the preset table**, never a
  per-request value, and it stays within the F14 60/20/5/15 envelope (it borrows from the model's
  own generation slice, never exceeds the configured `context_window`). The exact constant is fixed
  in code to match this row; if it is ever tuned, that is an amendment to this ADR, not a runtime
  knob.
- **`local_first`** does not change retrieval at all; it changes only whether the C2 web fetch
  fires. `LOCAL_FIRST_MIN_HITS` (default **3**) is the local-hit threshold below which web fetch is
  allowed. With `use_web_search=False`, `local_first` behaves identically to `standard`.

The selected mode is persisted as a per-conversation default in the frontend settings store
(presentation state); the backend is stateless per request (it reads `retrieval_mode` off the
request each turn).

### 2.4 C4 — AnyTXT: DO-NOT-MIRROR (documented decision)

The llm_wiki composer's AnyTXT toggle drives **AnyTXT Searcher**, a **Windows-only** local
full-text indexing *service* (a background Windows daemon exposing a local search API). Synapse's
deployment targets are **TrueNAS SCALE / Docker + macOS** (CLAUDE.md §1) — AnyTXT does not run there
and has no cross-platform equivalent. Mirroring it would mean shipping a dead, permanently-greyed
toggle on every supported platform, or coupling Synapse to a Windows-only dependency (a direct I9
violation — "do not reinvent / respect existing infra").

**Decision: DO-NOT-MIRROR.** No toggle, no code. The parity doc (`SYNAPSE-VS-LLMWIKI-PARITY.md`) gets
a ⛔/N-A row with this rationale (already staged in the UI-alignment plan's "Decisions &
do-not-mirror" table). An **optional future** ripgrep-based full-text search over `raw/sources/`
could deliver the *user intent* (local raw-file search) cross-platform — but it is **out of scope**
for B2 and would be its own ADR if pursued. It would also live in the **Search** surface, not chat,
and chat citations would remain wiki-only per ADR-0050.

---

## 3. ADR-0050 compatibility — why the web block does not dilute wiki-only retrieval

ADR-0050 restricted chat/search **retrieval** to `wiki/` pages so citations never surface raw input
material as if it were synthesized knowledge, and so the `[n]` citation UX always resolves to a
navigable wiki page. Its mechanism is a SQL filter (`file_path NOT LIKE 'raw/%'`) in
`_load_page_meta` / `_phase1_lexical_search`, and its intent is: **the wiki is the primary,
unambiguous grounding for chat.**

The C2 web block is consistent with that intent, on four grounds:

1. **The wiki-only filter is untouched.** C2 adds **zero** rows to the retrieval SQL path. The
   `wiki/`-only `_load_page_meta` gate, the `[n]` numbering, and the F5 pipeline are byte-for-byte
   unchanged. The web block is assembled by a *separate* function and *concatenated* as a distinct
   section after the wiki block — it never flows through `retrieve()`.

2. **The wiki remains PRIMARY; web is OPT-IN and SUPPLEMENTARY.** `use_web_search` defaults **OFF**,
   so the default chat behaviour is identical to today (wiki-only). Web context appears only when the
   user explicitly asks for it, and even then it is appended *after* the wiki grounding, not in place
   of it. The wiki is never diluted; it is (optionally) *supplemented*.

3. **Distinct namespace, distinct label — no epistemic confusion.** ADR-0050's core worry was the
   user being unable to tell input material from synthesized knowledge. The web block has its **own**
   `[W1]…[Wn]` namespace and its **own** labeled section (`## Web results (external)`), and its
   citations ride a **separate** `web_citations` field in `done`. A `[W3]` marker resolves to an
   external URL; a `[3]` marker resolves to a wiki page. The two can never be confused, and a web
   result can never masquerade as a wiki citation. This *strengthens* ADR-0050's clarity principle
   rather than eroding it.

4. **Raw/ stays excluded from BOTH namespaces.** The web block is external URLs, not `raw/` vault
   files; ADR-0050's raw-exclusion is orthogonal and remains fully in force for the wiki path.

**Conclusion:** ADR-0050 is **amended, not superseded** — its wiki-only *retrieval* scope stands
verbatim. This ADR records that chat may, on explicit opt-in, present a **clearly-separated external
web context** alongside (never inside) the wiki grounding.

---

## 4. Bounds — the I7 contract (new keys)

Every new path is bounded. All keys are structural env-configurable limits with the fixed defaults
below; none introduces a loop.

| Bound / key | Default | Applies to | Enforcement |
|---|---|---|---|
| `CHAT_MAX_IMAGES` | **4** | C1 attach-image | count > cap → **422** at request boundary |
| `CHAT_MAX_IMAGE_BYTES` | **5 MB** | C1 attach-image | decoded per-image size > cap → **422** |
| `CHAT_WEB_MAX_RESULTS` | **5** | C2 web search | SearXNG call `max_results` cap (I9) |
| `CHAT_WEB_FETCH_MAX_CHARS` | **8000** | C2 web fetch | per-result strip cap (reuses deep-research helper) |
| C2 web pass | **single-shot** | C2 web search | ONE call → fetch → inject; NO assess/refine loop |
| `retrieval_mode` presets | frozen (§2.3) | C3 retrieval | enum → fixed `(k, expansion_depth, budget)`; no raw depth on request |
| `expansion_depth` | **≤ 2 (HARD)** | C3 `deep` | `_MAX_EXPANSION_DEPTH` re-clamp in `retrieve()` (ADR-0022/I2) |
| `LOCAL_FIRST_MIN_HITS` | **3** | C3 `local_first` | local-hit threshold below which web fetch may fire |
| cost logging | per turn | C2 | structured log records web-search ran + result count (ADR-0024 discipline) |

No new *loop* is introduced by any of the above. The existing chat `token_budget` + `timeout_seconds`
bounds (ADR-0019 §2.2) still cap the streaming turn end-to-end; the web pass runs **before**
streaming (like `retrieve()`), so it is inside the turn's overall wall-clock budget.

---

## 5. Consequences

- **llm_wiki composer parity reached** for the three portable affordances (attach-image, web-search
  toggle, retrieval modes), backed by real endpoints and real provider plumbing — no UI stubs.
  AnyTXT is a documented, principled decline.
- **Vision is now available in chat, capability-aware.** The same `supports_vision` capability that
  gates ingest captioning (R8-2/F12) now gates chat images — one abstraction, extended not forked.
  A text-only backend degrades gracefully (images dropped in `chat()`), and the composer button is
  disabled with a tooltip so the user is never surprised. **I6 holds:** the decision is a capability
  read, never a class/type branch.
- **ADR-0050 stands; web is additive and unambiguous.** Wiki retrieval is still wiki-only and still
  the primary grounding. Opt-in web context is a separate, labeled, separately-namespaced block —
  the `[n]` citation UX and the raw-exclusion filter are untouched.
- **Retrieval depth is user-facing but I7-bounded.** Four frozen presets replace any temptation to
  expose raw `k`/`expansion_depth` on the wire. `deep` sits at the ADR-0022/I2 depth-2 ceiling and
  cannot breach it. `standard` is exactly today's behaviour, so existing tests stay green.
- **I3 preserved.** Images are static thumbnails decoded once; they never touch the per-token
  streaming loop or the end-of-stream parse (G3). The stream still carries text deltas only.
- **I9 preserved.** Web search is one bounded call through the existing SearXNG seam — never Tavily,
  never a new search service; fetch/strip reuses the deep-research helper.
- **Docs debt tracked (I8).** `GET /status` gains `supports_vision`; `ChatRequest` gains
  `use_web_search` / `retrieval_mode`; `ChatMessageIn` / `Message` gain `images`; the `done` event
  gains `web_citations` — all must land in a regenerated `docs/api/openapi.json`. If a `messages`
  column is added for image persistence, the ER diagram MUST be regenerated (`make er`) so
  `docs/er/schema.mmd` matches the live models. B2 is not Done until these D-artifacts are updated
  and consistent (this ADR records the requirement; the docs gate enforces it).

---

## 6. Do-NOT list (this ADR)

1. Do **not** exceed `expansion_depth = 2` in any retrieval mode. `deep` is capped at 2; `retrieve()`
   re-clamps to `_MAX_EXPANSION_DEPTH` regardless (ADR-0022/I2). Never author a preset row > 2.
2. Do **not** expose raw `k` / `expansion_depth` (or any arbitrary depth) on the chat request. Depth
   is chosen ONLY by the frozen `retrieval_mode` preset table (§2.3) — no per-request arbitrary
   depth (I7).
3. Do **not** loop the web fetch. C2 is **single-shot**: one SearXNG call → fetch top-N → inject.
   The assess→refine→re-query loop belongs to deep-research (ADR-0024), not to chat web-search (I7).
4. Do **not** send images to a backend when `capabilities().supports_vision` is `False`. The provider
   `chat()` **drops** them (defense-in-depth); the frontend button-disable is UX, not the boundary.
5. Do **not** branch on `isinstance` / `type()` / class name / `provider_type == "…"` to decide
   vision. The decision reads `self.capabilities().supports_vision` ONLY (I6 — the `provider/base.py`
   rule).
6. Do **not** mix the web `[W]` and wiki `[n]` citation namespaces. They are **distinct** namespaces
   in **distinct**, **distinctly-labeled** context blocks and **distinct** `done` fields
   (`web_citations` vs `citations`). A `[W3]` is external; a `[3]` is a wiki page. Never interleave,
   never renumber one into the other.
7. Do **not** modify the wiki-only retrieval filter. The `_load_page_meta` /
   `_phase1_lexical_search` `file_path NOT LIKE 'raw/%'` gate and the F5 `[n]` numbering are
   untouched (ADR-0050 stands). The web block is assembled and concatenated separately, never through
   `retrieve()`.
8. Do **not** re-render attached images per streamed token, and do **not** route image bytes through
   the NDJSON stream or the `ThinkScanner` delta loop. Images are static thumbnails, decoded and
   persisted once; the stream carries text deltas only (I3 / G3, ADR-0019).
9. Do **not** re-add `supports_vision` to `ProviderCapabilities` — it already exists (R8-2/F12).
   Reuse it for the chat path.
10. Do **not** exceed `CHAT_MAX_IMAGES` (4) or `CHAT_MAX_IMAGE_BYTES` (5 MB) — over-limit requests
    are rejected with **422** at the boundary, before any provider call (I7).
11. Do **not** use Tavily or any search backend other than SearXNG for C2. One bounded call through
    the existing `ops/searxng.py` seam (I9).
12. Do **not** ship an AnyTXT toggle. It is a Windows-only service, N/A on Synapse's
    TrueNAS/Docker+macOS targets — documented decline, no code (C4 / I9).
