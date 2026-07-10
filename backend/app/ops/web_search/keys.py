"""
P3-e — web-search cloud provider API-key store (ADR-0071).

Keys for the opt-in cloud providers (tavily/serpapi/firecrawl/brave) can be set from the UI and
are stored **Fernet-encrypted at rest** in ``vault_state.web_search_api_keys_encrypted`` (a JSON
map {provider: key}). The DB value wins over the env ``{PROVIDER}_API_KEY`` fallback.

Resolution is cache-backed so the adapters' ``configured()`` / key reads stay SYNCHRONOUS:
  - ``load_cache_from_db()`` runs at startup (and after each write) to populate the cache.
  - ``get_web_search_api_key(provider)`` reads the cache (DB) first, else the env setting.

Plaintext is NEVER logged or returned by any endpoint — only a masked posture is exposed.
Writing requires SYNAPSE_SECRET_KEY (secrets_crypto.is_configured()); otherwise PUT returns 400.
"""

from __future__ import annotations

import json
import logging

from app import secrets_crypto
from app.config import settings

logger = logging.getLogger(__name__)

# Cloud providers that take an API key (ollama_web is local — no key; searxng needs no key).
CLOUD_KEY_PROVIDERS: frozenset[str] = frozenset({"tavily", "serpapi", "firecrawl", "brave"})


class _WebSearchKeyCache:
    """In-memory cache of decrypted {provider: key}. Populated at startup + on write."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def replace(self, keys: dict[str, str]) -> None:
        self._keys = {k: v for k, v in keys.items() if isinstance(v, str) and v}

    def get(self, provider: str) -> str | None:
        return self._keys.get(provider) or None

    def has(self, provider: str) -> bool:
        return bool(self._keys.get(provider))


_cache = _WebSearchKeyCache()


def _env_key(provider: str) -> str | None:
    """Env fallback: settings.{provider}_api_key (e.g. TAVILY_API_KEY)."""
    return (getattr(settings, f"{provider}_api_key", "") or "") or None


def get_web_search_api_key(provider: str) -> str | None:
    """Resolve a provider's API key: DB-stored (cache) wins over the env var."""
    return _cache.get(provider) or _env_key(provider)


def key_source(provider: str) -> str:
    """Where the effective key comes from: 'db' | 'env' | 'none' (never the value)."""
    if _cache.has(provider):
        return "db"
    if _env_key(provider):
        return "env"
    return "none"


async def _load_encrypted_map(session: object) -> dict[str, str]:
    """Read + decrypt the stored JSON map from vault_state (empty on any issue — fail-closed)."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import VaultState  # noqa: PLC0415

    row = await session.execute(  # type: ignore[attr-defined]
        select(VaultState).where(VaultState.vault_id == settings.vault_id)
    )
    state = row.scalar_one_or_none()
    blob: bytes | None = getattr(state, "web_search_api_keys_encrypted", None) if state else None
    if not blob:
        return {}
    try:
        data = json.loads(secrets_crypto.decrypt(blob))
        return {k: v for k, v in data.items() if isinstance(v, str) and v}
    except Exception:  # noqa: BLE001 — tampered/undecryptable → treat as empty (fail-closed)
        logger.warning("web_search keys: could not decrypt stored blob — ignoring (fail-closed)")
        return {}


async def load_cache_from_db() -> None:
    """Populate the module cache from vault_state (startup + post-write refresh)."""
    import app.db as _db  # noqa: PLC0415

    if not secrets_crypto.is_configured():
        _cache.replace({})
        return
    try:
        async with _db.get_session() as session:
            _cache.replace(await _load_encrypted_map(session))
    except Exception:  # noqa: BLE001 — never let a cache load crash startup
        logger.warning("web_search keys: cache load failed — env vars will govern")
        _cache.replace({})


async def set_web_search_api_key(provider: str, key: str) -> None:
    """Store (encrypt) a provider's API key. Requires SYNAPSE_SECRET_KEY (else raises)."""
    if provider not in CLOUD_KEY_PROVIDERS:
        raise ValueError(f"{provider!r} does not take an API key")
    if not key.strip():
        raise ValueError("key must be non-empty")
    if not secrets_crypto.is_configured():
        raise secrets_crypto.SecretsNotConfiguredError("SYNAPSE_SECRET_KEY is not set")

    from sqlalchemy import select  # noqa: PLC0415

    import app.db as _db  # noqa: PLC0415
    from app.models import VaultState  # noqa: PLC0415

    async with _db.get_session() as session:
        current = await _load_encrypted_map(session)
        current[provider] = key.strip()
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            state.web_search_api_keys_encrypted = secrets_crypto.encrypt(json.dumps(current))
    await load_cache_from_db()


async def clear_web_search_api_key(provider: str) -> None:
    """Remove a provider's stored key (env fallback resumes). No-op if the master key is absent."""
    if not secrets_crypto.is_configured():
        await load_cache_from_db()
        return

    from sqlalchemy import select  # noqa: PLC0415

    import app.db as _db  # noqa: PLC0415
    from app.models import VaultState  # noqa: PLC0415

    async with _db.get_session() as session:
        current = await _load_encrypted_map(session)
        current.pop(provider, None)
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            state.web_search_api_keys_encrypted = (
                secrets_crypto.encrypt(json.dumps(current)) if current else None
            )
    await load_cache_from_db()


def get_key_posture() -> dict[str, dict[str, object]]:
    """Masked posture per cloud provider — NEVER the plaintext key."""
    posture: dict[str, dict[str, object]] = {}
    for provider in sorted(CLOUD_KEY_PROVIDERS):
        src = key_source(provider)
        posture[provider] = {"configured": src != "none", "source": src}
    return posture
