# ADR-0031 — OpenAI-compatible embeddings adapter (explicit `EMBEDDING_FORMAT`, optional `EMBEDDING_API_KEY`)

- **Status:** Accepted (owner decided 2026-06-29: explicit `EMBEDDING_FORMAT=ollama|openai`; auto-detect rejected) — implemented
- **Date:** 2026-06-29
- **Sprint:** v0.5 (Feature C — OpenAI-compatible embeddings)
- **Feature:** F17-adjacent (embedding data plane) · builds on ADR-0004 (EMBEDDING_DIM/probe) and the `EmbeddingClient` ABC (`backend/app/embeddings.py`)
- **Invariants owned:** I9 (reuse the same embedding seam; do not add a parallel client) · I6-spirit (no hardcoded backend — request shape is config-driven)
- **Author:** solution-architect
- **Implementers:** ai-agent-engineer (the adapter inside the embedding seam — owns how the AI embeds) · backend-engineer (config keys) · tech-writer (D6b deploy note for the new env)

---

## 1. Context

`HttpEmbeddingClient` (`backend/app/embeddings.py:59`) hardcodes the **Ollama** request/response
shape:

- Request: `POST {"model": <model>, "prompt": <text>}`
- Response: `{"embedding": [<floats>]}`

It sends no auth header (the local bge-m3 service is open on the internal network). The owner
wants `EMBEDDING_URL` to be able to point at an **OpenAI-style** `/v1/embeddings` endpoint:

- Request: `POST {"model": <model>, "input": <text>}`
- Response: `{"data": [{"embedding": [<floats>]}], ...}`

Hosted OpenAI-compatible providers also require `Authorization: Bearer <key>`. The
`EmbeddingClient` ABC (`embed`, `probe_dimension`) is consumed everywhere (orchestrator,
retrieval, MCP) via `get_embedding_client()` — so the ABC must stay stable and the change must
live **inside the seam**, not at every call site.

---

## 2. Decision

### 2.1 Explicit `EMBEDDING_FORMAT=ollama|openai` (recommended over auto-detect)

New env var **`EMBEDDING_FORMAT`** with values `ollama` (default) or `openai`, read in
`config.py`. `HttpEmbeddingClient` branches on it for both request body and response parsing.

**Rationale for explicit over auto-detect:**
- **Determinism / fail-fast.** Auto-detect would mean probing the endpoint and guessing from
  the response shape, or trying one shape and falling back — that introduces an unbounded-ish
  trial and a startup-time network dependency on guessing. Explicit config is unambiguous and
  validated once (ADR-0004 already probes dimension at startup; format is known before the
  probe).
- **No silent wrong-shape.** A URL ending `/api/embeddings` vs `/v1/embeddings` is a weak signal;
  some gateways proxy both. Guessing risks sending `prompt` to an `input` endpoint and getting a
  confusing 400 deep in ingest. Explicit `EMBEDDING_FORMAT` makes the operator state intent.
- **Consistency with the project's config philosophy** (CLAUDE.md §12, ADR-0004): everything is
  explicit env, no magic, fail-fast on mismatch. Auto-detect contradicts that.

**The auto-vs-explicit choice is the one owner decision (§6).** Build proceeds explicit.

### 2.2 Optional `EMBEDDING_API_KEY` → `Authorization: Bearer`

New env var **`EMBEDDING_API_KEY`** (secret, optional, **no default**), read in `config.py`.
When set, `HttpEmbeddingClient` adds `Authorization: Bearer <key>` to every embedding request.
When unset (the local-bge-m3 case), no auth header is sent (unchanged behavior). The key is a
secret: never logged, never returned by `GET /config/embedding`, sourced from env only (§12).

This works for both formats (some local Ollama-compatible gateways also want a key), so the
header is orthogonal to `EMBEDDING_FORMAT`.

### 2.3 One client, two adapters inside — keep the ABC stable

The change lives **inside `HttpEmbeddingClient`**, not as a sibling class:

- `embed()` builds the request body and parses the response per `self._format`:
  - `ollama`: `{"model", "prompt"}` → `payload["embedding"]` (unchanged).
  - `openai`: `{"model", "input"}` → `payload["data"][0]["embedding"]`.
- Both paths reuse the same httpx call, error wrapping (`EmbeddingError`), and the auth header
  from §2.2. Two tiny private helpers (`_build_request`, `_parse_response`) keyed on
  `self._format` keep it readable; a sibling class would duplicate the httpx/error machinery for
  no gain.
- `probe_dimension()` is unchanged — it calls `embed("probe")` and measures length, so it works
  for both formats automatically (ADR-0004 startup validation keeps working).
- The **`EmbeddingClient` ABC, `get_embedding_client()`, `set_embedding_client()`, and
  `FakeEmbeddingClient` are untouched.** No call site changes. I9 holds: still one seam.

**Validation:** on `openai` parse, if `payload["data"]` is missing/empty or the nested
`embedding` is not a non-empty list, raise `EmbeddingError` with the offending payload (mirrors
the existing ollama-shape guard at `embeddings.py:98`). No silent empty vector.

### 2.4 Interaction with ADR-0030 (embeddings off)

Orthogonal. When `EMBEDDINGS_ENABLED=false` (ADR-0030), `HttpEmbeddingClient` is not called at
all, so `EMBEDDING_FORMAT`/`EMBEDDING_API_KEY` are simply unused. When enabled, the adapter
applies. No coupling beyond both touching the same file.

---

## 3. New config / env / schema

| Kind | Name | Type / default | Read in | Notes |
|------|------|----------------|---------|-------|
| env | `EMBEDDING_FORMAT` | `ollama` \| `openai`, default `ollama` | `config.py` | Selects request/response adapter. Validated to the two values. |
| env | `EMBEDDING_API_KEY` | secret str, optional, no default | `config.py` | Adds `Authorization: Bearer` when set. Never logged/returned. |

**No DB schema change. No migration. No D2 (ER) change.** Pure config + in-seam adapter.

---

## 4. Acceptance check (DoD)

1. `EMBEDDING_FORMAT=openai` + an OpenAI-style endpoint: `embed("x")` posts
   `{"model", "input"}` and parses `data[0].embedding`; `probe_dimension()` returns the right
   length and ADR-0004 startup validation passes.
2. `EMBEDDING_FORMAT=ollama` (default): byte-for-byte identical to today (`{"model","prompt"}` →
   `embedding`). No regression for the existing bge-m3 deployment.
3. `EMBEDDING_API_KEY` set: requests carry `Authorization: Bearer <key>`; unset: no auth header.
4. A malformed `openai` response (no `data`, empty list) raises `EmbeddingError`, not a silent
   bad vector.
5. The `EmbeddingClient` ABC and `get_embedding_client()` signatures are unchanged; no call site
   outside `embeddings.py` was modified.
6. `GET /config/embedding` does **not** expose `EMBEDDING_API_KEY`.

---

## 5. Consequences

**Positive** — Synapse can use hosted/OpenAI-compatible embedding providers (and authed
gateways) without touching ingest/retrieval/MCP; the seam stays single (I9); existing bge-m3
deployments are unaffected (default `ollama`).

**Trade-offs (explicit)** — explicit `EMBEDDING_FORMAT` is one more env var the operator must set
correctly (mitigated: clear default, two values, fail-fast on bad response). Only the
single-string `input` form is supported (not batch arrays) — fine, since callers embed one text
at a time; batch is a non-blocking future optimisation. A leaked `EMBEDDING_API_KEY` would expose
the embedding provider — mitigated by the never-log/never-return rule (§12).

**Invariant check** — I9: one embedding seam, reused; no parallel client, no new service
abstraction. I6-spirit: backend/request-shape is config-driven, never hardcoded. I1/I2/I3/I4/I5/
I7/I8: untouched. **No invariant is traded for convenience.**

## 6. Decision the owner must make before coding

**Format detection.** RECOMMENDED: **explicit `EMBEDDING_FORMAT=ollama|openai`** (deterministic,
fail-fast, matches the project's explicit-config philosophy). Alternative: **auto-detect** by
probing response shape — rejected as the default (non-deterministic, risks wrong-shape errors
deep in ingest), but the owner may override. Build proceeds explicit.
