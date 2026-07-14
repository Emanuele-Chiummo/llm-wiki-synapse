# ADR-0084 — Single-shot complete() transport for one-shot LLM seams and 120 s cold-start timeouts (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Invariants touched:** I6, I7, I8
- **Amends:** ADR-0078 (overview regen now uses complete()); ADR-0079 (review sweep LLM judge now uses complete())
- **Related:** ADR-0076 §2 (InferenceProvider.complete() transport introduced for block ingest; this ADR reuses the same pattern for all one-shot seams)

## Context

Synapse has several one-shot generation seams — a single LLM call that produces a self-contained
output with no tool calls, no agent loop, and no multi-turn exchange:

| Seam | Caller | Method (pre-ADR-0084) | Symptom |
|------|--------|-----------------------|---------|
| Overview regeneration | `orchestrator._overview_chat_collect` | `provider.chat()` | overview.md always a stub; CLI path hung waiting for tool acknowledgement that never arrives |
| Review sweep Pass-2 judge | `review.py::_chat_collect` | `provider.chat()` | Sweep never resolved; 30 s timeout expired before the CLI subprocess even cold-started |
| Propose-reviews LLM call | `review.py::_chat_collect` | `provider.chat()` | Same hang pattern |
| Purpose/schema drift suggestions | `review.py::_chat_collect` | `provider.chat()` | Same hang pattern |

`InferenceProvider.chat()` routes to a full agent loop for `CliAgentProvider`: it spawns a `claude`
subprocess with MCP tool access and expects the agent to call tools iteratively. That is correct for
multi-step ingest (F17, ADR-0076 delegated path) where the agent must write multiple wiki pages.
For one-shot generation ("summarize the vault", "judge these review proposals"), the agent loop
blocks indefinitely waiting for tool acknowledgement that the seam never provides — the review and
overview endpoints do not expose MCP write tools.

The CLI provider also has a non-trivial cold-start: the `claude` subprocess initialization takes
30–60 s on first invocation. The 30 s timeouts on review operations (`review_sweep_timeout_seconds`,
`review_propose_timeout_seconds`) expired before the subprocess finished initializing, producing a
degraded result (empty/absent) on every call with the CLI provider.

`provider.complete()` is the transport introduced in ADR-0076 §2 for the block ingest loop:

```
InferenceProvider.complete(system, prompt, *, max_tokens) -> str
```

It is a single-turn synchronous call. For `CliAgentProvider`, `complete()` uses `claude -p <prompt>`
in non-interactive, non-agentic mode — the subprocess exits after one generation. No tool calls.
No agent loop. Cold-start overhead still applies, but the call completes successfully.

## Decision

### 1. Route all one-shot generation seams through provider.complete()

Replace `provider.chat()` with `provider.complete()` at every seam that requires a single-turn
response:

- **`backend/app/ingest/orchestrator.py::_overview_chat_collect`** — called by `regenerate_overview()`
  (original ADR-0078 manual-op path and the v1.7.0 queue-drain refinement).
- **`backend/app/ops/review.py::_chat_collect`** — shared internal helper called by:
  - `sweep_reviews()` Pass-2 LLM judge (REVIEW_SWEEP_LLM_ENABLED=true path)
  - `propose_reviews()` LLM call
  - Purpose/schema drift suggestion generation

This decision is backend-neutral (I6): all three provider backends (OllamaProvider, ApiProvider,
CliAgentProvider) implement `complete(system, prompt, *, max_tokens) -> str`. The change requires
no isinstance branching, no capability flag, and no provider-specific conditional logic.

### 2. Raise single-call timeouts to 120 s

| Config key | Before | After | Rationale |
|------------|--------|-------|-----------|
| `review_sweep_timeout_seconds` | 30 | 120 | CLI cold-start (30–60 s) + generation time; degrade-safe on timeout |
| `review_propose_timeout_seconds` | 30 | 120 | Same cold-start pattern |
| `overview_timeout_seconds` | 90 | 120 | CLI cold-start + longer generation (full vault summary); degrade-safe |

All three seams are degrade-safe: a timeout returns `degraded` status, never 5xx. The existing
content (overview.md, pending proposals) is retained unchanged. The timeout is a wall-clock bound
satisfying I7.

## Consequences

### Positive

- Overview regeneration and review sweep complete reliably for all three provider backends,
  including the CLI provider.
- I6 preserved: `complete()` is the provider-neutral transport. No isinstance branch, no new
  provider-specific path. OllamaProvider and ApiProvider behaviour is unchanged (they did not
  hang, but `complete()` is equally correct for them).
- I7 preserved: 120 s wall-clock bound with degrade-safe fallback. No unbounded wait introduced.
- Consistent transport contract: all single-shot LLM calls across Synapse (block ingest analysis,
  block ingest generation, overview regen, review sweep judge, propose-reviews) now use `complete()`.
  `chat()` is reserved for the streaming multi-turn chat endpoint and the CLI delegated ingest path.

### Negative / trade-offs

- A 120 s timeout is meaningfully longer than 30 s. Operators running the CLI provider on slow
  hardware may observe extended queue-drain durations on overview regen and review sweep.
- `CliAgentProvider.complete()` spawns a `claude` subprocess per call; cold-start overhead is not
  amortized across calls. High-volume review sweeps with the CLI provider are slower than with
  OllamaProvider or ApiProvider. This is expected behaviour, not a bug.

### Implementation notes

- `backend/app/ingest/orchestrator.py`: `_overview_chat_collect` replaces
  `self.provider.chat(…)` with `self.provider.complete(system, user, max_tokens=4096)`.
- `backend/app/ops/review.py`: `_chat_collect` replaces `provider.chat(…)` with
  `provider.complete(system, prompt, max_tokens=…)`.
- `backend/app/config.py`: `review_sweep_timeout_seconds` and `review_propose_timeout_seconds`
  default 30→120; `overview_timeout_seconds` default 90→120.
- Introduced in commits: `209e971` (`review.py::_chat_collect`), `446e66f`
  (`orchestrator._overview_chat_collect`), `1d29dfb` (timeout config values).
