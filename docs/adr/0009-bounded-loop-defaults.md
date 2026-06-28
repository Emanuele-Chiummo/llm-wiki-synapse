# ADR-0009 — Bounded ingest loop: defaults, cost accounting, token-usage normalization (I7)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.2
- Decider: solution-architect
- Invariants: I7 (every loop bounded by max_iter + token_budget; cost logged), I6 (per-provider accounting stays inside providers)
- Related: CLAUDE.md §7 (canonical loops), v0.2-scope §6, ADR-0007, ADR-0008
- Resolves: AQ-v0.2-4 (per-backend token accounting), AQ-v0.2-8 (cost-anomaly WARNING site)

## Context

v0.2 makes the first real LLM calls. I7 requires every loop to carry a `max_iter` cap, a
`token_budget` cap, and a `total_cost_usd` log on completion. Two specifics had to be pinned:
1. How is token usage measured uniformly across three backends that expose it differently?
   (AQ-v0.2-4)
2. Where is the $1.00/run cost-anomaly WARNING emitted? (AQ-v0.2-8)

## Decision

### 1. Loop bounds and defaults (ratifies scope §6)

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `max_iter` (orchestrated) | 3 | 1 happy-path pass + 2 retries covers malformed JSON / missing-frontmatter recovery. Beyond 3 is a provider-quality problem, not a retry problem. |
| `token_budget` (orchestrated) | 60 000 | analysis (5–10k) + generation of 3–5 pages (20–30k) + 2 retries headroom. Well inside API context windows. |
| `token_budget` (CLI delegated) | 100 000 | the CLI runs its own agent loop (browse + write); higher headroom is appropriate. |
| `provider_fallback_max` | 1 | primary fail/timeout → one fallback attempt → surface error. No chained fallbacks (CLAUDE.md §7). |
| cost-anomaly threshold | $1.00 / run | a single ~500-word doc producing 3–5 pages should cost cents on Sonnet; $1 signals a runaway or misconfiguration. |

Both bounds are enforced **together**: the loop exits when **either** `iteration == max_iter`
**or** `accumulated_tokens >= token_budget`, whichever comes first. Both are read from the
resolved `provider_config` (ADR-0008), falling back to the table above only if absent.

The token_budget is checked **before** each provider call: if the projected/accumulated usage
would exceed the budget, the loop stops with `converged=False` rather than making a call it
cannot afford. AC-K2-5 (non-converging provider) stops at `max_iter=3`; a separate test stops
on the token_budget branch.

### 2. Uniform Usage normalization (resolves AQ-v0.2-4)

A single internal type normalizes per-backend accounting:

```python
@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    total_cost_usd: float   # 0.0 for local and cli
```

Each provider populates `Usage` from its native fields, inside `provider/` only (I6):

| Backend | input_tokens | output_tokens | total_cost_usd |
|---------|--------------|---------------|----------------|
| Ollama | `prompt_eval_count` | `eval_count` | **0.00** always (local, RTX 3060, zero marginal cost) |
| Anthropic API | `response.usage.input_tokens` | `response.usage.output_tokens` | `input×in_price + output×out_price`, prices per model from a seeded price map keyed by `model_id` (in `api.py`, sourced from provider_config-adjacent seed, never a literal in app code) |
| OpenAI-compatible | `usage.prompt_tokens` | `usage.completion_tokens` | same pricing formula; price map keyed by model_id |
| CLI (claude-agent-sdk) | from SDK result metadata if present, else best-effort | same | **0.00 by convention** — the CLI uses build-time agent credits, not runtime per-token billing. If the SDK exposes a cost, it is logged for visibility but `total_cost_usd` is recorded as 0.00 and a note is logged. If the SDK exposes no token counts, log a WARNING and record tokens=0. |

The orchestrator owns a run-scoped accumulator; each provider call returns its domain object
and **adds** its `Usage` to the accumulator (the providers do not return `Usage` in their
public method signatures — ADR-0007 §1 — they push it to the run accumulator the orchestrator
passes in). At run end, the accumulator yields `total_tokens` and `total_cost_usd` for the
`ingest_runs` row (ADR-0008) and the structured log line.

**CLI = $0.00 convention** is explicit and architect-agreed: the CliAgentProvider path is the
build-time / Karpathy-lineage agent; its spend is not Synapse-runtime billing, so it is
recorded as $0.00 to keep the cost ledger comparable across backends while still logging the
raw token counts when available.

### 3. Cost-anomaly WARNING — inline in the orchestrator (resolves AQ-v0.2-8)

The $1.00 threshold check is performed **inline in `orchestrator.py`**, immediately after the
run accumulator is finalized and **after** the `ingest_runs` row / structured log line is
written. Option (a) from AQ-v0.2-8 — not a separate hook (over-engineered for one threshold)
and not in the provider (the provider does not know per-run totals across iterations). When
`total_cost_usd > 1.00`, the orchestrator logs a `WARNING` containing `total_cost_usd`,
`provider_name`, and `page_id`, and sets `ingest_runs.cost_anomaly = True`. The test mocks the
cost computation to return 1.01 and asserts the WARNING is emitted.

### 4. Provider fallback (bounded, exactly once)

If the primary provider raises a timeout / 503 during `analyze()` or `generate()`, the
orchestrator resolves the single fallback row (`is_fallback=True` for the scope, ADR-0008) and
retries the **whole** ingest once with it. If the fallback also fails, an `IngestError` is
surfaced (HTTP 500 from the REST path). No recursion, no chains (AC-K2-7). The fallback's
`provider_name` is recorded in `ingest_runs` with `converged=False` on double failure.

## Consequences

- (+) I7 fully satisfied: dual bounds on the loop, fallback bounded to one, cost logged and
  persisted per run, anomaly flagged.
- (+) AQ-v0.2-4 and AQ-v0.2-8 resolved; AC-K2-6 (structured run record) and the anomaly test
  are unblocked.
- (+) The uniform `Usage` type makes cost comparable across heterogeneous backends without
  leaking backend-specific field names out of `provider/` (I6).
- (−) The CLI=$0.00 convention means the cost ledger understates true cost when the SDK does
  bill. Accepted and documented: it is build-time credit, not runtime billing; raw tokens are
  still logged for visibility.
- (−) Per-model pricing must be maintained as the price map drifts. Mitigation: prices live in
  seeded data keyed by model_id (alongside provider_config seeding), updatable without code
  changes, never as literals in `backend/app/` (AC-F17-8).
