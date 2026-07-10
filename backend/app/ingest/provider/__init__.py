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
    # W1 (F17): present on the ProviderConfig ORM row; read structurally via getattr so this
    # package stays ORM-free. api_key_encrypted is Fernet ciphertext (or None); reasoning_effort
    # is auto|off|low|medium|high|max|custom (or None).
    api_key_encrypted: bytes | None
    reasoning_effort: str | None


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
    reasoning_effort = getattr(row, "reasoning_effort", None)
    return ProviderSettings(
        provider_type=provider_type,  # type: ignore[arg-type]
        model_id=str(row.model_id),
        base_url=getattr(row, "base_url", None),
        max_iter=int(getattr(row, "max_iter", None) or DEFAULT_MAX_ITER),
        token_budget=int(getattr(row, "token_budget", None) or default_budget),
        is_fallback=bool(getattr(row, "is_fallback", False)),
        # W1 (F17, §12 amendment): decrypt the stored UI key at build time. A missing master key
        # or a tampered/foreign ciphertext degrades to None → ApiProvider falls back to env keys
        # (I6 — all 3 backends keep working). Only meaningful for the API backend.
        api_key=_resolve_api_key(getattr(row, "api_key_encrypted", None)),
        reasoning_effort=str(reasoning_effort) if reasoning_effort else None,
    )


def _resolve_api_key(api_key_encrypted: bytes | None) -> str | None:
    """
    Decrypt a stored provider key ciphertext, or return None (env-var fallback).

    Fail-closed and NEVER raises: no master key (SecretsNotConfiguredError) or a
    tampered/foreign ciphertext (InvalidToken) both degrade to None so the provider uses its
    env-var key. The plaintext is returned only into ProviderSettings.api_key and NEVER logged.
    """
    if not api_key_encrypted:
        return None
    # Deferred import: keep the provider package importable without app.secrets_crypto in
    # infra-free unit tests that never touch stored keys.
    from app import secrets_crypto  # noqa: PLC0415

    try:
        return secrets_crypto.decrypt(bytes(api_key_encrypted))
    except (secrets_crypto.SecretsNotConfiguredError, secrets_crypto.InvalidToken):
        return None


def resolve_provider(provider_config_row: ProviderConfigRow | Any) -> InferenceProvider:
    """
    Build the concrete InferenceProvider for a resolved provider_config row.

    Raises ValueError on a missing row or an unknown provider_type — never silently defaults
    to a backend (I6: "never hardcode a provider"). The caller (ConfigResolver) is responsible
    for selecting the row by precedence (operation>vault>global, ADR-0008).

    ADR-0043: when provider_type == 'cli', stamps ProviderSettings.subscription_token with
    the DB-cached OAuth token (via cli_auth.resolve_subscription_token()). Non-CLI providers
    always receive subscription_token=None. Import is deferred to avoid a cycle.
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
    settings = _settings_from_row(provider_config_row)

    # ADR-0043: inject DB-resolved subscription token for CLI only (never for local/api).
    # Deferred import to avoid circular imports (cli_auth → config only; safe here).
    if provider_type == "cli":
        from app import cli_auth  # noqa: PLC0415 — deferred to break any potential cycle

        token = cli_auth.resolve_subscription_token()
        if token is not None:
            # ProviderSettings is frozen — rebuild with the token field set.
            import dataclasses  # noqa: PLC0415

            settings = dataclasses.replace(settings, subscription_token=token)

    return cls(settings)


__all__ = [
    "InferenceProvider",
    "ProviderConfigRow",
    "ProviderSettings",
    "UsageAccumulator",
    "resolve_provider",
]
