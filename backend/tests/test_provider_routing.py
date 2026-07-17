"""
Capability-aware routing tests (I6, ADR-0007 §3).

2.0.0 (ADR-0076): The legacy JSON/delegated routing tests were removed because
``run_ingest_pipeline`` no longer branches on ``supports_agentic_loop`` or
``ingest_pipeline_format`` — every provider runs the block loop.  The static
source-inspection guardrail (``test_routing_has_no_class_or_type_check_in_source``) was
also removed for the same reason (the routing region no longer exists).

The single surviving test verifies that ``resolve_provider`` never silently defaults to a
hardcoded backend (I6).
"""

from __future__ import annotations

import pytest


class _Row:
    """Minimal duck-typed provider_config row."""

    def __init__(self, provider_type: str) -> None:
        self.provider_type = provider_type
        self.model_id = "dummy-model"
        self.base_url = None
        self.max_iter = 3
        self.token_budget = 60_000
        self.is_fallback = False


def test_resolve_provider_has_no_hardcoded_default() -> None:
    """resolve_provider must reject a missing/unknown row, never default a backend (I6)."""
    from app.ingest.provider import resolve_provider

    with pytest.raises(ValueError):
        resolve_provider(None)
    with pytest.raises(ValueError):
        resolve_provider(_Row("bogus"))
