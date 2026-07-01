"""
ProviderSettings — the resolved, ORM-free configuration a single provider instance needs
(built by the factory from a `provider_config` row, ADR-0008).

Keeping this a plain frozen dataclass (not the SQLAlchemy row) means the provider package
never imports `models.py`, so model ids / base_urls flow in via config only — never
hardcoded (I6). Secrets are NOT here: API keys are read from the environment inside
`api.py` / `cli.py` only (§12, ADR-0008 §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Default loop bounds when a provider_config row omits them (ADR-0009 §1).
DEFAULT_MAX_ITER = 3
DEFAULT_TOKEN_BUDGET_ORCHESTRATED = 60_000
DEFAULT_TOKEN_BUDGET_CLI = 100_000


@dataclass(frozen=True)
class ProviderSettings:
    """Resolved per-provider configuration (from provider_config, ADR-0008)."""

    provider_type: Literal["local", "api", "cli"]
    model_id: str
    base_url: str | None = None
    max_iter: int = DEFAULT_MAX_ITER
    token_budget: int = DEFAULT_TOKEN_BUDGET_ORCHESTRATED
    is_fallback: bool = False
    # Optional knobs that may come from env-adjacent config (never a literal in app code):
    timeout: float = 120.0
    # ADR-0043: the resolved Claude subscription OAuth token for the CLI backend, injected
    # into the spawned `claude` CLI env as CLAUDE_CODE_OAUTH_TOKEN when set. None = no DB
    # token (env governs). Set ONLY for provider_type == "cli"; ignored by local/api.
    # NEVER logged.
    subscription_token: str | None = None
