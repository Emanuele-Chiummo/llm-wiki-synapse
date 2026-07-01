"""
CLI subscription OAuth token resolver — ADR-0043 §2.4.

Holds the in-process cache (_CliAuthConfigCache) for vault_state.cli_oauth_token and
exposes resolve_subscription_token() for the provider factory.

Design constraints (ADR-0043 §2.4):
- Imports ONLY stdlib + app.config (never models/main/provider) — cycle-free.
- The DB token is loaded once at startup via _load_cli_auth_config_cache() and cached
  in a module-level singleton; provider factory reads it O(1) per build (no DB round-trip).
- resolve_subscription_token() returns the DB token if set (non-empty), else None.
  The env tiers (CLAUDE_CODE_OAUTH_TOKEN, CLAUDE_CODE_USE_SUBSCRIPTION) remain the
  responsibility of cli.py's _resolve_cli_auth_mode() — this module surfaces only tier 1.
- NEVER logs or returns the token value.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class _CliAuthConfigCache:
    """
    In-process cache for vault_state.cli_oauth_token (ADR-0043 §2.4).

    Mirrors _ClipConfigCache in main.py: loaded once at startup; refreshed on
    PUT /provider/cli-auth writes. The provider factory reads it O(1) per build
    (no DB round-trip on every resolve_provider() call).

    Token storage — plaintext (ADR-0043 §2.1): the CLI subscription token is
    replayed outbound into the spawned claude CLI; it cannot be hashed.

    NEVER logs or returns the token value. get_token() is for injection only.
    """

    def __init__(self) -> None:
        self._token: str | None = None  # None = no DB token; env governs in cli.py
        self._loaded: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Cache management ──────────────────────────────────────────────────────

    async def load(self, token: str | None) -> None:
        """Load (or reload) from DB value. token is the plaintext or None."""
        async with self._lock:
            # Treat empty string as unset (ADR-0043 §2.3 / ADR-0042 empty=unset rule).
            self._token = token if token else None
            self._loaded = True

    async def set_token(self, token: str | None) -> None:
        """Update cached token after a PUT /provider/cli-auth DB write. NEVER log."""
        async with self._lock:
            self._token = token if token else None

    # ── Read-only accessors ───────────────────────────────────────────────────

    def get_token(self) -> str | None:
        """
        Return the stored plaintext DB token, or None if not set.

        CALLER CONTRACT: do NOT log, return, or render this value.
        Intended use: injection into the spawned CLI child env (cli.py).
        """
        return self._token

    def token_configured(self) -> bool:
        """True iff any credential is available (DB token OR any env signal)."""
        return self.token_source() != "none"

    def token_source(self) -> str:
        """
        'db' | 'env' | 'none'.

        'db'  — vault_state.cli_oauth_token is set (non-empty).
        'env' — no DB token, but ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN or
                CLAUDE_CODE_USE_SUBSCRIPTION is present in the process environment.
        'none' — nothing configured.
        """
        if self._token:
            return "db"
        # Check any env signal (presence only — do not read values except truthiness).
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        use_sub = os.environ.get("CLAUDE_CODE_USE_SUBSCRIPTION", "")
        if api_key or oauth_token or use_sub:
            return "env"
        return "none"

    def auth_mode(self) -> str:
        """
        'api-key' | 'subscription' | 'unconfigured'.

        Derived from the ADR-0043 §2.3 precedence (presence-only, no injection):
          tier 1: DB token set                          → 'subscription'
          tier 2: env ANTHROPIC_API_KEY non-empty       → 'api-key'
          tier 3: env CLAUDE_CODE_OAUTH_TOKEN non-empty → 'subscription'
          tier 4: env CLAUDE_CODE_USE_SUBSCRIPTION truthy → 'subscription'
          else                                          → 'unconfigured'
        """
        if self._token:
            return "subscription"
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            return "api-key"
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if oauth_token:
            return "subscription"
        use_sub = os.environ.get("CLAUDE_CODE_USE_SUBSCRIPTION", "").strip().lower()
        if use_sub in {"1", "true", "yes", "on"}:
            return "subscription"
        return "unconfigured"


# ── Module-level singleton — loaded in lifespan (main.py) ────────────────────
_cli_auth_config_cache: _CliAuthConfigCache = _CliAuthConfigCache()


def resolve_subscription_token() -> str | None:
    """
    Return the DB-set CLI subscription OAuth token if available, else None.

    Called by resolve_provider() in app/ingest/provider/__init__.py to stamp
    ProviderSettings.subscription_token only when provider_type == 'cli'.

    Returns None if no DB token is set; env tiers are handled inside cli.py's
    _resolve_cli_auth_mode(), which now gains the DB tier from settings.subscription_token.

    NEVER log the returned value.
    """
    return _cli_auth_config_cache.get_token()


async def _load_cli_auth_config_cache(session: Any) -> None:
    """
    Startup loader: read vault_state.cli_oauth_token and populate the cache.

    Called from main.py lifespan after _load_clip_config_cache().
    The `session` parameter is a live AsyncSession (already yielded by get_session()).
    NEVER logs the token value.
    """
    from sqlalchemy import select  # local import — avoid top-level SQLAlchemy dep

    from app.config import settings  # noqa: PLC0415

    # Import inside the function to keep this module free of top-level model imports
    # while still being usable from main.py (which owns the DB session).
    from app.models import VaultState  # noqa: PLC0415 — intentional local import

    row = await session.execute(select(VaultState).where(VaultState.vault_id == settings.vault_id))
    state = row.scalar_one_or_none()
    if state is not None:
        # getattr with None default guards against old DB schemas (pre-migration 0017).
        token: str | None = getattr(state, "cli_oauth_token", None)
    else:
        token = None

    await _cli_auth_config_cache.load(token)
    logger.info(
        "CliAuthConfigCache loaded from DB: token_source=%s (ADR-0043)",
        _cli_auth_config_cache.token_source(),
        # NEVER log the token value
    )
