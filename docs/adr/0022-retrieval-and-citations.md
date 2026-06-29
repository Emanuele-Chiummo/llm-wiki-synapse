# ADR-0022 — F5 4-phase retrieval + `[n]` citation architecture (M5 Phase 1)

> Status: Accepted
> Date: 2026-06-29
> Sprint: v0.5 (M5 Phase 1)
> Authors: solution-architect (design), tech-writer (formatting)
> Features: F5 (4-phase retrieval), F6 AC-F6-3 (`[n]` citations), F6 AC-F6-5 (save-to-wiki),
> F14 (context-window budget), F17 (`CliAgentProvider.chat()`)
> Invariants in force: I1, I2, I3, I5, I6, I7, I9
> Resolves: AQ-v0.5-1, AQ-v0.5-2, AQ-v0.5-7 (P0/P1 Phase-1 blockers)
> Extends: ADR-0019 (chat: fills the M5-reserved `messages.citations` column + adds `done.citations`;
> replaces the light-context-only retrieval with the real 4-phase pipeline), ADR-0002 (Qdrant point
> id == pages.id), ADR-0007/0011 (provider `chat()` contract unchanged), ADR-0009 (NB-4 cost).
> Supersedes: nothing.

---

## 1. Context

M5 Phase 1 is the **retrieval foundation**. ADR-0019 (M4 Phase 3) shipped chat with a deliberately
minimal context strategy — `build_chat_context()` reads `purpose.md` + `overview.md` and passes one
string as `retrieval_context` — and **explicitly deferred F5 4-phase RAG and `[n]` citations to M5**.
ADR-0019 reserved the `messages.citations` JSONB column (always `[]` in M4) for exactly this work.

This ADR locks the Phase-1 contract so backend-engineer (retrieval pipeline, `GET /search`, chat
wiring, `CliAgentProvider.chat()`) and frontend-engineer (citation rendering, save-to-wiki enable) can
build against a frozen interface. It also unblocks the three M4 carry-forwards (F6 AC-F6-3, F6 AC-F6-5,
F17 `CliAgentProvider.chat()`) that are otherwise empty stubs visible to the user.

**Ground truth that drives the design (verified in code):**

- A `pages` row tracks a **`raw/sources/` source document**, not a `wiki/` page. The Qdrant point
  (`id == pages.id`, ADR-0002) embeds the **raw source text** (`orchestrator.py`:
  `text_for_embedding = raw_bytes.decode(...)`). `pages.file_path` is a `raw/sources/...` path.
- `wiki/*.md` files produced by `write_wiki_page()` are **not** separately rowed in `pages`; they carry
  `sources: [...]` frontmatter back to the source paths (F3 traceability).
- Therefore the **searchable unit is the source page**, and a `[n]` citation must resolve to a `pages`
  row (id + display title + derived slug). No new join table is needed.
- The existing `synapse_pages` Qdrant collection is **single dense vector** (cosine, `EMBEDDING_DIM`).
  There is no sparse/BM25 index configured.
- `provider.chat(messages, retrieval_context: str)` is locked (ADR-0007/0011); it must stay unchanged.
- `chat/stream.py` already bounds a chat turn by `token_budget` + `timeout_seconds` (ADR-0019 §2.2) and
  already consumes both coroutine-returning and async-generator provider shapes.

---

## 2. Decisions

### 2.1 Module & public interface — `backend/app/rag/retrieval.py`

```python
async def retrieve(
    query: str,
    *,
    vault_id: str,
    context_window: int,
    k: int = 8,
    expansion_depth: int = 2,
) -> RetrievalContext: ...
```

`retrieve()` is a **single bounded pass** (I7) that makes **zero inference calls** and **zero `vault/`
walks** (I1): it reads Qdrant (bge-m3) + Postgres (`pages`/`links`/`edges`) + targeted source-file
bodies, and assembles a context string + citation map server-side (I3).

### 2.2 The four phases (in order)

1. **Tokenized / vector search (I9).** Embed `query` via the existing `get_embedding_client()` (bge-m3),
   `client.query_points(collection_name="synapse_pages", query=vector, limit=k, with_payload=True)`,
   reading `response.points`. Note: `qdrant_client.QdrantClient.search()` was removed in
   qdrant-client 1.18; `query_points()` is the current dense top-k API (semantically identical —
   same cosine top-k, same single-dense `synapse_pages` collection, same `ScoredPoint` where
   `point.id == pages.id`). Point ids are `pages.id`.
   **Dense-only** (see §3, AQ-v0.5-1). Score = cosine similarity.
2. **Graph-expansion (I2).** BFS over the **`edges` table** (the F4 4-signal output) from the seed
   pages, `expansion_depth ≤ 2` (hard cap), ordered by edge `weight DESC`; also follow resolved
   `links.target_page_id`. This **reads `edges`** — it does **NOT** call the GraphEngine or FA2.
   `data_version` is unchanged across the call (AC-F5-5).
3. **Token-budget allocation (F14).** `budget_tokens = int(context_window * 0.20)` (the "retrieved"
   slice of 60/20/5/15); `budget_chars = budget_tokens * 4` (char/4, AQ-v0.5-2). Candidates ranked:
   vector seeds (by cosine) then expansions (by edge weight).
4. **Context assembly (I3).** Walk candidates in rank order while budget remains; load each passage
   (source-file body, per-passage capped), assign the next 1-based `n`, append `[n] <title>\n<passage>`
   to `text`, and record the matching `Citation`. Lowest-ranked candidates that don't fit are **dropped**
   (never mid-sentence truncate-without-drop, AC-F5-4).

### 2.3 Data structures (the contract)

```python
class PageRef(BaseModel):
    id: str         # str(uuid) of the pages row (== Qdrant point id)
    title: str      # frontmatter title, else filename stem (never empty)
    slug: str       # slugify(title) — derived in code, NOT a DB column (§2.6)

class Citation(BaseModel):
    n: int          # 1-based, contiguous from 1
    ref: PageRef
    score: float
    phase: Literal["vector", "expansion"]

class RetrievalContext(BaseModel):
    query: str
    text: str                  # assembled context WITH inline [n] markers (≤ budget)
    citations: list[Citation]  # len == count of distinct [n] in text (single authority)
    token_budget: int
    approx_tokens: int         # char/4 of text, ≤ token_budget
    data_version: int          # snapshot read before assembly (AC-F5-5 proof)
```

### 2.4 The citation contract (F5 ↔ F6 ↔ frontend)

- **Stored:** on a chat turn with non-empty `citations`, `chat/stream.py` writes the serialized list
  into the assistant `messages.citations` JSONB column (reserved by ADR-0019).
- **Streamed:** the `done` NDJSON event gains an **additive** `citations` field — the compact
  projection `[{"n","id","title","slug"}, ...]` (score/phase stored, not streamed). Additive →
  non-breaking for existing clients.
- **Resolved:** the frontend reads the message's `citations`, builds `n → {title, slug}`, and
  post-processes the **already-parsed-once** settled markdown (I3) to turn each `[n]` text token into
  `<sup role="link" title="{title}">[n]</sup>` (hover = title; click = navigate by slug). This runs
  **once** on the settled message (memoized on raw + citations), never per streaming token.
- **Authority:** the assembler emits each `[n]` and its `Citation` together →
  `len(citations) == count of distinct [n]`; they cannot drift.

### 2.5 `GET /search` (D4)

```
GET /search?q=<query>&vault_id=<id>&k=<int>  → 200
{ "query", "context", "results":[{n,id,title,slug,score,phase}], "data_version", "approx_tokens", "token_budget" }
```

0-hit query → 200 with empty `results` + empty `context` (AC-F5-7a). Read-only; never bumps
`data_version` (AC-F5-5). Documented in openapi.json (`make openapi`, I8).

### 2.6 F5 adds NO migration

`messages.citations` already exists (reserved, ADR-0019) — F5 **reuses** it. `slug` is **derived**
(`slugify(title or file_stem)`) in `rag/retrieval.py`, **not** a new `pages.slug` column (no backfill,
no migration). Migration **0009** is reserved for Phase 3 `review_items` — Phase 1 is migration-free.
Qdrant usage is **unchanged** (same collection, same dense vector, read-only search — no new collection,
no sparse vector).

### 2.7 Chat & provider integration (carry-forwards)

- **Chat (F6):** `chat/stream.py` extracts the latest user message as the query, calls `retrieve()`
  **once before streaming** (I3 — not per-token), prepends the existing light file-context
  (`build_chat_context`, the 5% grounding header) to `RetrievalContext.text`, and passes the combined
  string as `retrieval_context`. The provider `chat()` signature is **unchanged** (I6); citations travel
  out of band in the orchestration layer and are stamped onto the assistant message + `done` event.
- **Save-to-wiki (F6 AC-F6-5):** wire the existing disabled button to the existing
  `POST /ingest/from-text` seam (ADR-0019 §2.7) — no new endpoint, no new ingest logic (I1/I6).
- **`CliAgentProvider.chat()` (F17, AQ-v0.5-7):** implement the M4 stub as a **delegated streaming
  chat** via `claude-agent-sdk` (mirroring `delegate_ingest`): `retrieval_context` injected as the
  system/leading context (read-only chat — the agent is **not** granted `write_page`); yields text
  deltas in the existing shape; cost per NB-4. **Bounded by three caps, no new DB column:**
  `token_budget` + `timeout_seconds` (both already flow through `run_chat_stream`) + an env
  `CHAT_AGENT_MAX_TURNS` (default 8) on SDK agent turns. With no `ANTHROPIC_API_KEY` it raises a clean
  pre-stream config error — never a fake stream (dev default stays Ollama; mocked SDK in tests).

---

## 3. Resolved ambiguities (the P0/P1 Phase-1 blockers)

- **AQ-v0.5-1 (P0) — dense-only, no BM25.** The `synapse_pages` collection is single-dense; adding a
  named sparse vector is a collection-shape change we do not take on the dependency-root feature. The
  keyword phase = bge-m3 dense top-k via `client.query_points()` (qdrant-client ≥ 1.12; `.search()`
  was removed in 1.18). Hybrid (named sparse vector + RRF fusion) is a **measured post-M5
  enhancement**, not a guess. Recall is widened by graph-expansion (Phase 2).
- **AQ-v0.5-2 (P0) — char/4, not tiktoken.** Reuse the existing `_CHARS_PER_TOKEN=4` convention
  (`chat/context.py`/`stream.py`). tiktoken is OpenAI-specific (mis-estimates Anthropic/Ollama), a new
  heavy dependency (I9), and unnecessary for a *drop-lowest-ranked* safety cap. Under-fill is the safe
  direction (never over-fill the window).
- **AQ-v0.5-7 (P1) — no `chat_max_iter` column.** CLI chat is bounded by `token_budget` +
  `timeout_seconds` (existing) + `CHAT_AGENT_MAX_TURNS` env (new, default 8). A schema change for one
  provider's turn cap is not warranted; I7 is satisfied by three bounds.

(AQ-v0.5-3/-4/-5/-6 are Phase 2–4 decisions, resolved in v0.5-architecture.md §1 and finalized in
ADR-0024/0025/0026 at their phase gates.)

---

## 4. The one accepted limitation

`[n]` resolves to the **source-document `pages` row**, not the synthesized `wiki/` page, because `pages`
index `raw/sources/` documents (§1). This is correct, honest grounding (the answer came from that
indexed text), but a user clicking `[n]` lands on the source rather than necessarily a polished wiki
page. Indexing `wiki/` pages as first-class searchable units (needs a `page_kind` discriminator on
`pages` or a parallel index) is the clean fix and is **explicitly deferred post-M5**. This is a scope
boundary, **not** an invariant violation.

---

## 5. Do-NOT list (rejection triggers in PR review)

1. **Do NOT make an inference call inside `retrieve()`.** It is pure store reads + assembly (I1/I7).
2. **Do NOT walk `vault/` or trigger a rescan in retrieval or `GET /search`** (I1). Read Qdrant +
   Postgres + targeted source files only. `GET /search` must leave `data_version` unchanged (AC-F5-5).
3. **Do NOT call the GraphEngine / FA2 from the expansion phase.** Read the `edges` table (I2).
4. **Do NOT introduce a new embedding service, a new Qdrant collection, a sparse/BM25 index, tiktoken,
   or sentence_transformers** (I9 / AQ-v0.5-1 / AQ-v0.5-2). Reuse `get_embedding_client` + the existing
   `synapse_pages` collection.
5. **Do NOT add a `pages.slug` column or any F5 migration.** `slug` is derived; `messages.citations`
   is reused (§2.6).
6. **Do NOT resolve `[n]` per streaming token, and do NOT add a second markdown parse.** Citation
   `<sup>` decoration runs once on the settled, already-parsed message (I3 / G3, ADR-0019 §2.6).
7. **Do NOT change `provider.chat(messages, retrieval_context)`.** Citations travel out of band in the
   orchestration layer (I6).
8. **Do NOT hardcode a provider/model and do NOT add an unbounded loop.** Chat resolves via
   `resolve_provider_config("chat", ...)`; `CliAgentProvider.chat()` is bounded by `token_budget` +
   `timeout_seconds` + `CHAT_AGENT_MAX_TURNS` (I6/I7).
9. **Do NOT fake a CLI chat stream when no API key is set.** Raise a clean pre-stream config error;
   cost follows NB-4 (`Decimal("0.00")` + WARNING when the SDK reports none).
10. **Do NOT skip D4.** `make openapi` must include `GET /search` with zero drift (I8). (No D2 change
    expected in Phase 1.)

---

## 6. AC mapping

| AC | Satisfied by |
|----|--------------|
| AC-F5-1 (4 phases in order) | §2.2 |
| AC-F5-2 (`[n]` markers + `PageRef{id,title,slug}`) | §2.3, §2.4 |
| AC-F5-3 (Qdrant/bge-m3 only, no new service) | §2.1, §3 (AQ-1/2), Do-NOT #4 |
| AC-F5-4 (budget respected; drop lowest-ranked) | §2.2 phase 3–4 |
| AC-F5-5 (`data_version` unchanged) | §2.2 phase 2 + `RetrievalContext.data_version` (§2.3) |
| AC-F5-6 (`GET /search` + openapi) | §2.5 |
| AC-F5-7 (0/1/multi/overflow cases) | §2.2 (BFS expansion + budget drop) |
| AC-F5-8 (context passed to provider.chat for all 3 backends) | §2.7 |
| AC-F6-3 (citations stored + rendered `<sup>` + hover) | §2.4 |
| AC-F6-5 (save-to-wiki) | §2.7 (reuse `POST /ingest/from-text`) |
| AC-F17-CHAT-1..3 (CLI chat, bounded, cost) | §2.7 (AQ-v0.5-7 + NB-4) |

---

## 7. Consequences

**Positive.** F5 is **migration-free** (reuses the reserved `citations` column, derives `slug`) and
**inference-free** in the hot path (pure store reads), so it is cheap, deterministic, and easy to test.
The provider `chat()` contract is filled, not changed (I6 honored). I2/I3/G3 are preserved by
construction — retrieval runs once before streaming, the expansion phase only reads `edges`, and
citation decoration is a single settled-message pass. The three M4 carry-forwards land together,
turning empty stubs into working features.

**Negative / accepted.** Dense-only retrieval may under-recall exact-keyword queries (mitigated by
graph-expansion; hybrid reserved post-M5). char/4 may slightly under-fill the 20% slice (safe
direction). `[n]` points at source documents, not wiki pages (§4 — deferred post-M5). CLI chat is
exercised only via a mocked SDK in dev (no key) — documented, not faked.

**Follow-ups (later in M5 / post-M5).** Phase 2 (F10) feeds auto-ingested deep-research synthesis into
the same retrieval corpus; Phase 3/4 (F9/F13) add `review_items` (migration 0009) and cascade-delete.
Post-M5: hybrid BM25 retrieval and `wiki/`-page-as-citation-target (`page_kind`).
