# ADR-0007 — InferenceProvider ABC and capability-aware routing (I6)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.2
- Decider: solution-architect
- Invariants: I6 (pluggable inference — never hardcode a provider), I7 (bounded loops), I8 (D1 must show provider layer)
- Related: CLAUDE.md §5 (F17), ADR-0003 (thin ingest seam), v0.2-scope §2/§5, v0.2-architecture.md
- Resolves: AQ-v0.2-1 (retry strategy), AQ-v0.2-7 (validator contract)

## Context

F17 is the defining feature of Synapse: the analysis/classification/ingest AI must be
user-selectable at runtime per vault/operation, with three backends — Local (Ollama),
API (Anthropic / OpenAI-compatible), CLI (claude-agent-sdk). ADR-0003 reserved the
extension point inside `ingest_file()`; v0.2 fills it. The contract between the
orchestrator and every backend must be locked **before** any provider code is written,
or the three implementations will diverge and break I6.

Three design questions had to be settled first:
1. What is the abstraction shape — ABC, `Protocol`, or duck typing?
2. How does the orchestrator decide between the orchestrated loop and CLI delegation
   without ever naming a backend (I6)?
3. On a validation retry, is `analyze()` re-run or only `generate()`? (AQ-v0.2-1)

## Decision

### 1. Abstract Base Class, not Protocol or duck typing

`InferenceProvider` is an `abc.ABC` with four `@abstractmethod`s. Rationale:
- An ABC fails **at instantiation** if a backend forgets a method (`TypeError`), giving an
  early, loud signal. A `Protocol` only fails at the call site, possibly in production.
- The set of abstract methods is introspectable (`__isabstractmethod__`), which the I6
  enforcement test in `test_code_quality.py` asserts is exactly
  `{analyze, generate, chat, capabilities}` (AC-F17-1).
- mypy strict treats abstract methods as a hard contract; a missing override is a type error.

Locked signatures (in `backend/app/ingest/provider/base.py`):

```python
class InferenceProvider(abc.ABC):
    @abstractmethod
    async def analyze(self, source_text: str, vault_context: str) -> Analysis: ...
    @abstractmethod
    async def generate(self, analysis: Analysis, retrieval_context: str) -> list[WikiPage]: ...
    @abstractmethod
    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]: ...
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...
```

`analyze`/`generate`/`chat` are `async`; `capabilities()` is sync (a pure descriptor read,
no I/O). `Usage` is returned **out of band** (see ADR-0009) — the methods return domain
objects, and the provider records token usage on a run-scoped accumulator the orchestrator
owns, so the public return types stay clean (a WikiPage is not polluted with billing data).

### 2. Three backends — not two, not four

The three correspond to the three distinct execution models a homelab user actually has:
zero-cost local (Ollama, RTX 3060), pay-per-token cloud (Anthropic / OpenAI-compatible),
and the Karpathy-lineage agentic CLI (claude-agent-sdk). A fourth (e.g. a separate
OpenAI-native provider) is unnecessary because the API backend already takes a configurable
`base_url`, making any OpenAI-compatible endpoint a configuration value, not a new class
(I6: backends are config, not code). Two would force a false choice between local privacy
and agentic quality.

### 3. Capability-aware routing — read `capabilities()`, never the type

The orchestrator branches **only** on `provider.capabilities().supports_agentic_loop`:

```python
caps = provider.capabilities()
if caps.supports_agentic_loop:        # CLI: provider runs its own agent loop
    await delegate_ingest(provider, ...)
else:                                  # Local / API: run the orchestrated loop
    await orchestrated_loop(provider, ...)
```

It is a **hard I6 rule** that the routing branch never uses `isinstance()`, `type(...)`,
a class-name string, or a `provider_type == "cli"` test. AC-K2-4 proves this with a custom
`CustomAgentic` provider (not `CliAgentProvider`) whose `supports_agentic_loop=True`; the
orchestrator must still delegate. A static grep in `test_code_quality.py` forbids
`isinstance(`/`type(`/class-name literals in the routing region of `orchestrator.py`.

`ProviderCapabilities` is a **frozen dataclass** (not Pydantic — no parsing/validation of
external input is involved; it is an internal descriptor) with fields:
`mode: Literal["local","api","cli"]`, `supports_tools: bool`,
`supports_agentic_loop: bool`, `max_context: int`, `name: str`.

### 4. Retry strategy — analyze ONCE, retry generate with augmented context (resolves AQ-v0.2-1)

The orchestrated loop runs `analyze()` exactly **once** per ingest run. On a validation
failure, only `generate()` is retried, with the validation errors appended to
`retrieval_context` as an augmentation block. Rationale:
- §5 phrases the loop as "if invalid **augment & retry**" — augmentation targets generation,
  not re-analysis. The source's topics/entities/language do not change between attempts; a
  malformed-JSON or missing-frontmatter failure is a generation defect, not an analysis one.
- Cheaper: analysis tokens are spent once, leaving more of the `token_budget` for retries.
- Makes AC-K2-5 deterministic: `analyze.call_count == 1`, `generate.call_count == max_iter`
  on a non-converging provider.

### 5. Validator contract — what makes a WikiPage "invalid" (resolves AQ-v0.2-7)

A generated batch is **invalid** (triggers a retry) if **any** page fails:
- `type` present AND in the enum `{entity, concept, source, synthesis, comparison}`;
- `title` a non-empty string;
- `frontmatter.sources` a **non-empty** list containing at least the originating source's
  relative path (F3 traceability — empty `sources[]` is invalid);
- `frontmatter.lang` a non-empty ISO-639-1 string;
- `content` a non-empty Markdown body.

Retry is **whole-batch**, not per-page (simpler orchestrator state; a partial-batch retry
would require diffing and merging partial outputs — deferred indefinitely as YAGNI). Dangling
wikilinks do **not** invalidate a page (K5 stores them with `dangling=True`; AC-K5-5).
The validator lives in `orchestrator.py` and consumes the locked `WikiPage` schema
(v0.2-architecture.md §schemas).

### 6. chat() is stubbed, not omitted

All three backends implement `chat()` to `raise NotImplementedError` immediately (no network
call). The signature is locked **now** so adding the real implementation in v0.4 (F6) is a
non-breaking body change, not an ABC change. Omitting it now would be a breaking ABC change
at v0.4. AC-F17-2 asserts the method exists on all three and raises on call.

## Consequences

- (+) I6 fully honoured: backends are swappable config; routing is capability-driven; no
  backend name appears in the orchestrator. The static grep test is the machine guardrail.
- (+) The contract (ABC + schemas + validator) is frozen before provider code starts, so the
  three implementations cannot drift.
- (+) AQ-v0.2-1 and AQ-v0.2-7 resolved deterministically, unblocking AC-K2-5 and AC-F3-7.
- (−) `analyze`-once means a pathological source that needs re-analysis to succeed will
  exhaust `max_iter` without ever re-analyzing. Accepted: that is a provider-quality problem
  (per §6), correctly surfaced as `converged=False`, not papered over with extra analysis spend.
- (−) Returning `Usage` out of band (run-scoped accumulator) is slightly less obvious than a
  return-value tuple. Justified: it keeps domain return types clean and lets the CLI delegated
  path — which produces no per-call `Usage` — use the same accounting surface (ADR-0009).
- Review rule: any PR whose routing branch inspects provider class/type, or whose provider
  module leaks a model id / endpoint / key outside `provider/`, is rejected on sight (I6).
