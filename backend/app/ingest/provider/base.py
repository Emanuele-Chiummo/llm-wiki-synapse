"""
InferenceProvider ABC — the single abstraction behind which ALL analysis/ingest/chat AI
sits (I6 / F17, ADR-0007).

THE I6 RULE (enforced in review + CI):
  Routing reads `provider.capabilities().supports_agentic_loop` ONLY. The orchestrator MUST
  NEVER branch on `isinstance(...)`, `type(...)`, a class-name string, or
  `provider_type == "cli"`. A backend is selected from `provider_config` and resolved by the
  factory in `provider/__init__.py`; it is NEVER hardcoded. No model id / API key / endpoint
  URL may appear outside this `provider/` package.

Usage is returned OUT OF BAND (ADR-0007 §1): the domain methods return clean domain objects
(Analysis / list[WikiPage]); each provider PUSHES its per-call `Usage` onto the run-scoped
`UsageAccumulator` the orchestrator passes in via `bind_accumulator()`. This keeps WikiPage
free of billing data and lets the CLI delegated path share the same accounting surface.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from pathlib import Path

from app.ingest.schemas import (
    Analysis,
    Message,
    ProviderCapabilities,
    Usage,
    WikiPage,
)


class UsageAccumulator:
    """
    Run-scoped token/cost ledger owned by the orchestrator and bound onto a provider for the
    duration of one ingest run (ADR-0009). Providers call `add()`; the orchestrator reads the
    finalized totals for the `ingest_runs` row and the cost-anomaly check.
    """

    __slots__ = ("input_tokens", "output_tokens", "total_cost_usd", "calls")

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_cost_usd = 0.0
        self.calls = 0

    def add(self, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.total_cost_usd += usage.total_cost_usd
        self.calls += 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def snapshot(self) -> Usage:
        return Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_cost_usd=self.total_cost_usd,
        )


class InferenceProvider(abc.ABC):
    """
    Abstract inference backend (ADR-0007). Three concrete subclasses ship in v0.2:
    OllamaProvider (local), ApiProvider (api), CliAgentProvider (cli). Backends are config,
    not new classes — an OpenAI-compatible endpoint is a `base_url` on ApiProvider (I6).

    Concrete providers SHOULD accept an optional `UsageAccumulator` (typically via
    `bind_accumulator`) and push a `Usage` after each LLM call so the orchestrator can
    bound the loop by `token_budget` and log `total_cost_usd` (I7).
    """

    # Bound by the orchestrator for the duration of a run; providers push Usage here.
    _accumulator: UsageAccumulator | None = None

    def bind_accumulator(self, accumulator: UsageAccumulator) -> None:
        """Attach the run-scoped Usage ledger (called by the orchestrator before a run)."""
        self._accumulator = accumulator

    def _record_usage(self, usage: Usage) -> None:
        """Push *usage* to the bound accumulator if one is attached (no-op otherwise)."""
        if self._accumulator is not None:
            self._accumulator.add(usage)

    # ── Abstract contract (locked signatures, ADR-0007 §1) ──────────────────────

    @abc.abstractmethod
    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        """
        Step 1 of the two-step CoT (F3): classify the source into topics/entities/language and
        propose pages. Called ONCE per ingest run (AQ-v0.2-1). Records Usage out of band.
        """

    @abc.abstractmethod
    async def generate(
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        """
        Step 2 of the two-step CoT (F3): produce schema-valid WikiPage(s) from the analysis +
        retrieval context + the ORIGINAL source document. Retried (whole-batch) on validation
        failure with errors appended to `retrieval_context` (ADR-0007 §4/§5). Records Usage out
        of band.

        `source_text` (D1, ADR-0063 §9, nashsu/llm_wiki parity — ingest.ts:1000-1016) is the raw
        source, budget-trimmed inside the shared `build_generate_prompt` builder so pages are
        written from the source text, not only the lossy Analysis summary. Defaults to "" so the
        contract stays back-compatible for callers/fakes that omit it (Analysis-only fallback);
        the orchestrated loop always threads the run's source_text.
        """

    @abc.abstractmethod
    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]:
        """
        STUBBED in v0.2 — raises NotImplementedError immediately on every backend (no network
        call). Signature locked now so the real F6 implementation at v0.4 is a non-breaking
        body change, not an ABC change (ADR-0007 §6).
        """

    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """
        Return the immutable capability descriptor. The orchestrator reads ONLY
        `supports_agentic_loop` from this for routing (I6). Pure descriptor read — no I/O.
        """

    # ── Raw text completion (block-based pipeline, ADR-0076) ────────────────────

    async def complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
        """
        One bounded, non-streaming call returning the model's RAW TEXT (no JSON mode). This is
        the transport the block-based ingest loop uses (ADR-0076): the loop assembles the
        provider-neutral markdown-analysis / FILE-block prompts (app.ingest.prompts) and parses
        the returned text with app.ingest.blocks — providers stay transport-only (I6). Records
        Usage out of band like analyze()/generate() so the run ledger stays truthful (I7).

        The DEFAULT raises NotImplementedError: the orchestrated backends (Ollama, API) override
        it; the CLI backend runs its own agent loop (delegated) and never needs it. A provider
        without it must not be routed through the orchestrated block loop.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement complete() "
            "(orchestrated block pipeline requires it)"
        )

    # ── Vision (R8-2 / F12) ─────────────────────────────────────────────────────

    async def caption_image(self, path_or_bytes: str | Path | bytes, context: str) -> str:
        """
        Describe an image for a knowledge-base entry (R8-2 / F12) — one bounded, non-streaming
        provider call, no agent loop (I3 not applicable: no per-token DOM/parse work; the caption
        is consumed as plain text by the normal analyze→generate flow). Records Usage out of band
        exactly like analyze()/generate() so the orchestrator's run-scoped ledger stays truthful
        (I7).

        `path_or_bytes` is a filesystem path (str/Path) or the raw image bytes. `context` is a
        short instruction / vault-context string (e.g. purpose.md excerpt) the provider may fold
        into the prompt.

        The DEFAULT raises NotImplementedError so a backend that does not (or cannot) see images
        never silently returns a bogus caption — providers with `capabilities().supports_vision`
        True MUST override this. The orchestrator only calls this after checking `supports_vision`,
        and falls back to the extract.py placeholder on NotImplementedError / any error (R8-2).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support vision captioning "
            "(capabilities().supports_vision is False)"
        )
