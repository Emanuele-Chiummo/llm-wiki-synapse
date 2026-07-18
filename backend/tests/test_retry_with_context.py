"""Tests for ADR-0085 §4 retry-with-context feature.

Two independent surfaces:

1. ``IngestQueueManager.pop_retry_context`` — verifies the store/pop lifecycle:
   - context stored by ``request_retry(..., prior_failure_context=...)`` is returned once by
     ``pop_retry_context`` and then cleared (consumed exactly once).
   - when no context was stored, ``pop_retry_context`` returns None (no regression).

2. ``run_block_loop(prior_failure_context=...)`` — verifies injection semantics:
   - when a non-None prior_failure_context is supplied, the first-iteration generation user
     message contains the context text (the model sees "prior run failed because ...").
   - when prior_failure_context is None (normal / first-ever run), the generation user message
     is NOT augmented with cross-run context (no regression, no spurious text).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from app.ingest.block_loop import run_block_loop
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import (
    Analysis,
    Message,
    ProviderCapabilities,
    Usage,
    WikiPage,
)
from app.routers.ingest import _format_retry_failure_context

# ── helpers shared with test_block_loop.py ───────────────────────────────────

ANALYSIS = "## Key Entities\n- Acme Corp\n\n## Recommendations\n- Create entity pages."

GEN_VALID = """---FILE: wiki/entities/acme.md---
---
type: entity
title: Acme Corp
created: 2026-07-17
updated: 2026-07-17
sources: [doc.md]
---

# Acme Corp

Acme builds the [[Widget Platform]].
---END FILE---

---FILE: wiki/concepts/widget-platform.md---
---
type: concept
title: Widget Platform
created: 2026-07-17
updated: 2026-07-17
sources: [doc.md]
---

# Widget Platform

The platform maintained by [[Acme Corp]].
---END FILE---
"""


class _RecordingProvider(InferenceProvider):
    """Scripted provider that records every complete() call for assertion."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.generation_user_messages: list[str] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,
            supports_agentic_loop=False,
            max_context=8192,
            name="Recording",
        )

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:  # pragma: no cover
        raise NotImplementedError

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError

    async def complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
        self._record_usage(Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.0))
        response = self._responses.pop(0)
        # Generation calls (not analysis): record the user message.
        # We detect them by position: call 0 is analysis, calls 1+ are generation.
        self.generation_user_messages.append(prompt)
        return response


def _run_loop(provider: _RecordingProvider, **overrides: object):
    kwargs: dict[str, object] = {
        "provider": provider,
        "accumulator": UsageAccumulator(),
        "source_text": "The Acme Corp report.",
        "purpose": "",
        "schema": "",
        "index": "",
        "source_filename": "doc.md",
        "origin_source": "raw/sources/doc.md",
        "language_name": None,
        "max_iter": 3,
        "token_budget": 60_000,
        "review_stage_min_chars": 10_000,
        "review_stage_min_file_blocks": 4,
    }
    kwargs.update(overrides)
    return run_block_loop(**kwargs)  # type: ignore[arg-type]


# ── 1. Queue manager: store / pop lifecycle ───────────────────────────────────


def _make_failed_entry(run_id: uuid.UUID, source_path: str) -> object:
    """Build a FailedEntry with only the fields needed for retry testing."""
    from datetime import UTC, datetime

    from app.ingest.queue_manager import FailedEntry

    return FailedEntry(
        run_id=run_id,
        source_path=source_path,
        error="test error",
        retry_count=0,
        started_at=datetime.now(UTC),
    )


def test_pop_retry_context_returns_stored_context() -> None:
    """request_retry(..., prior_failure_context=...) stores context; pop_retry_context returns it."""
    from app.ingest.queue_manager import IngestQueueManager

    manager = IngestQueueManager()
    source_path = "/vault/raw/sources/test.md"
    run_id = uuid.uuid4()

    manager._recent_failed[source_path] = _make_failed_entry(run_id, source_path)
    manager._retry_counts[source_path] = 0

    ctx = "Stop reason: max_iter after 3 iterations\nValidation errors:\n- no FILE blocks"
    manager.request_retry(run_id, prior_failure_context=ctx)

    # pop_retry_context should return the stored context
    result = manager.pop_retry_context(source_path)
    assert result == ctx


def test_pop_retry_context_consumed_exactly_once() -> None:
    """pop_retry_context returns None on a second call (consumed on first pop)."""
    from app.ingest.queue_manager import IngestQueueManager

    manager = IngestQueueManager()
    source_path = "/vault/raw/sources/test.md"
    run_id = uuid.uuid4()
    manager._recent_failed[source_path] = _make_failed_entry(run_id, source_path)
    manager._retry_counts[source_path] = 0

    ctx = "prior context"
    manager.request_retry(run_id, prior_failure_context=ctx)

    manager.pop_retry_context(source_path)  # first pop: consumed
    second = manager.pop_retry_context(source_path)  # second pop: gone
    assert second is None


def test_pop_retry_context_returns_none_when_no_context_stored() -> None:
    """pop_retry_context returns None when request_retry was called without diagnostics (no regression)."""
    from app.ingest.queue_manager import IngestQueueManager

    manager = IngestQueueManager()
    source_path = "/vault/raw/sources/test.md"
    run_id = uuid.uuid4()
    manager._recent_failed[source_path] = _make_failed_entry(run_id, source_path)
    manager._retry_counts[source_path] = 0

    manager.request_retry(run_id)  # no prior_failure_context

    result = manager.pop_retry_context(source_path)
    assert result is None


# ── 2. block_loop: prior_failure_context injection ───────────────────────────


async def test_prior_failure_context_appears_in_first_generation_call() -> None:
    """When prior_failure_context is set, the first generation user message contains it."""
    prior_ctx = "Stop reason: max_iter after 3 iterations\n- no FILE blocks emitted"
    provider = _RecordingProvider([ANALYSIS, GEN_VALID])
    result = await _run_loop(provider, prior_failure_context=prior_ctx)

    assert result.converged is True
    # provider.generation_user_messages[0] = analysis call user
    # provider.generation_user_messages[1] = first generation call user (index 1)
    assert len(provider.generation_user_messages) >= 2
    first_gen_user = provider.generation_user_messages[1]
    assert prior_ctx in first_gen_user
    assert "previous ingest attempt" in first_gen_user


async def test_prior_failure_context_only_on_first_iteration() -> None:
    """Context is injected ONLY on the first iteration; subsequent iterations use within-run errors."""
    prior_ctx = "prior run context"
    # First generation yields no blocks (triggers within-run retry), second succeeds.
    provider = _RecordingProvider([ANALYSIS, "no blocks here", GEN_VALID])
    result = await _run_loop(provider, prior_failure_context=prior_ctx, max_iter=3)

    assert result.converged is True
    assert len(provider.generation_user_messages) >= 3

    first_gen_user = provider.generation_user_messages[1]  # first gen after analysis
    second_gen_user = provider.generation_user_messages[2]  # retry after within-run error

    # First iteration: prior context injected (with the header from _augment_prior_failure_context)
    assert prior_ctx in first_gen_user
    assert "previous ingest attempt" in first_gen_user
    # Second iteration: within-run validation error injected (not the prior context again)
    assert "Validation errors from the previous attempt" in second_gen_user
    # Prior context should NOT appear again in the second-iteration user message
    # (it's not re-injected; the elif guard prevents double-firing)
    assert prior_ctx not in second_gen_user


async def test_no_prior_failure_context_no_injection() -> None:
    """When prior_failure_context is None, the first generation user message is unaugmented (no regression)."""
    provider = _RecordingProvider([ANALYSIS, GEN_VALID])
    result = await _run_loop(provider, prior_failure_context=None)

    assert result.converged is True
    first_gen_user = provider.generation_user_messages[1]
    # Neither prior-context header nor within-run error header should appear on a clean first run
    assert "previous ingest attempt" not in first_gen_user
    assert "Validation errors from the previous attempt" not in first_gen_user


# ── 3. _format_retry_failure_context helper ───────────────────────────────────


def test_format_retry_failure_context_none_on_empty_diagnostics() -> None:
    assert _format_retry_failure_context(None) is None
    assert _format_retry_failure_context({}) is None


def test_format_retry_failure_context_none_when_converged() -> None:
    # A converged run being retried should not inject stale "errors"
    diag = {"stop_reason": "converged", "last_errors": [], "iterations": 1}
    assert _format_retry_failure_context(diag) is None


def test_format_retry_failure_context_none_when_no_last_errors() -> None:
    # token_budget exhausted before any generation ran — no errors to inject
    diag = {"stop_reason": "token_budget", "last_errors": [], "iterations": 0}
    assert _format_retry_failure_context(diag) is None


def test_format_retry_failure_context_formats_errors() -> None:
    diag = {
        "stop_reason": "max_iter",
        "iterations": 3,
        "last_errors": ["no FILE blocks emitted", 'FILE "wiki/x.md": title is empty'],
        "tokens_used": 45_000,
        "token_budget": 60_000,
    }
    ctx = _format_retry_failure_context(diag)
    assert ctx is not None
    assert "max_iter" in ctx
    assert "3" in ctx
    assert "no FILE blocks emitted" in ctx
    assert 'FILE "wiki/x.md": title is empty' in ctx
    assert "45000" in ctx
    assert "60000" in ctx
