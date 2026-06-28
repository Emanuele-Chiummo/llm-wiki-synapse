"""
Provider package + factory (F17, I6).

`resolve_provider()` maps a resolved `provider_config` row (ADR-0008) to a concrete
`InferenceProvider`. The mapping is driven entirely by the row's `provider_type` value —
there is NO hardcoded default provider (a missing/unknown type is a configuration error, not
a silent fallback). Model ids, base_urls and loop bounds flow in from the row; secrets never
do (keys are env-only inside api.py/cli.py, §12).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.ingest.provider.api import ApiProvider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.provider.cli import CliAgentProvider
from app.ingest.provider.config import (
    DEFAULT_MAX_ITER,
    DEFAULT_TOKEN_BUDGET_CLI,
    DEFAULT_TOKEN_BUDGET_ORCHESTRATED,
    ProviderSettings,
)
from app.ingest.provider.ollama import OllamaProvider


class ProviderConfigRow(Protocol):
    """
    Structural type for a resolved provider_config row (ADR-0008). Both the SQLAlchemy
    `ProviderConfig` ORM model and a plain object expose these attributes — the factory does
    not import models.py, keeping the provider package free of ORM coupling.
    """

    provider_type: str
    model_id: str
    base_url: str | None
    max_iter: int
    token_budget: int
    is_fallback: bool


# provider_type → concrete provider class. The ONLY place a string maps to a class; it is a
# config-value dispatch (the row's provider_type), NOT a routing decision (I6 routing is by
# capabilities().supports_agentic_loop, done in the orchestrator).
_REGISTRY: dict[str, Callable[[ProviderSettings], InferenceProvider]] = {
    "local": OllamaProvider,
    "api": ApiProvider,
    "cli": CliAgentProvider,
}


def _settings_from_row(row: ProviderConfigRow) -> ProviderSettings:
    provider_type = str(row.provider_type)
    default_budget = (
        DEFAULT_TOKEN_BUDGET_CLI if provider_type == "cli" else DEFAULT_TOKEN_BUDGET_ORCHESTRATED
    )
    return ProviderSettings(
        provider_type=provider_type,  # type: ignore[arg-type]
        model_id=str(row.model_id),
        base_url=getattr(row, "base_url", None),
        max_iter=int(getattr(row, "max_iter", None) or DEFAULT_MAX_ITER),
        token_budget=int(getattr(row, "token_budget", None) or default_budget),
        is_fallback=bool(getattr(row, "is_fallback", False)),
    )


def resolve_provider(provider_config_row: ProviderConfigRow | Any) -> InferenceProvider:
    """
    Build the concrete InferenceProvider for a resolved provider_config row.

    Raises ValueError on a missing row or an unknown provider_type — never silently defaults
    to a backend (I6: "never hardcode a provider"). The caller (ConfigResolver) is responsible
    for selecting the row by precedence (operation>vault>global, ADR-0008).
    """
    if provider_config_row is None:
        raise ValueError(
            "No provider_config resolved — cannot select a backend (I6: no hardcoded default)."
        )
    provider_type = str(getattr(provider_config_row, "provider_type", "")).strip()
    cls = _REGISTRY.get(provider_type)
    if cls is None:
        raise ValueError(
            f"Unknown provider_type {provider_type!r}; expected one of {sorted(_REGISTRY)}."
        )
    return cls(_settings_from_row(provider_config_row))


__all__ = [
    "InferenceProvider",
    "ProviderConfigRow",
    "ProviderSettings",
    "UsageAccumulator",
    "resolve_provider",
]
