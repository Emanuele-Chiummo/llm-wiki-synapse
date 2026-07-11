"""
P3-e — web-search cloud provider API-key store (ADR-0071).

Unit-level coverage of the key resolver + posture + write guards (no DB round-trip):
  - DB-cached key wins over the env fallback
  - env fallback when nothing is cached
  - posture reports configured/source without ever exposing the value
  - set requires SYNAPSE_SECRET_KEY (SecretsNotConfiguredError when absent)
  - the 4 cloud adapters' configured() now reads through the resolver (DB wins over env)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the module cache around each test."""
    from app.ops.web_search import keys as k

    k._cache.replace({})
    yield
    k._cache.replace({})


class TestResolver:
    def test_env_fallback_when_nothing_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ops.web_search import keys as k

        monkeypatch.setattr(cfg.settings, "tavily_api_key", "env-tav", raising=False)
        assert k.get_web_search_api_key("tavily") == "env-tav"
        assert k.key_source("tavily") == "env"

    def test_db_cache_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ops.web_search import keys as k

        monkeypatch.setattr(cfg.settings, "tavily_api_key", "env-tav", raising=False)
        k._cache.replace({"tavily": "db-tav"})
        assert k.get_web_search_api_key("tavily") == "db-tav"
        assert k.key_source("tavily") == "db"

    def test_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ops.web_search import keys as k

        monkeypatch.setattr(cfg.settings, "brave_api_key", "", raising=False)
        assert k.get_web_search_api_key("brave") is None
        assert k.key_source("brave") == "none"


class TestPosture:
    def test_posture_shape_never_leaks_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ops.web_search import keys as k

        for p in ("tavily", "serpapi", "firecrawl", "brave"):
            monkeypatch.setattr(cfg.settings, f"{p}_api_key", "", raising=False)
        k._cache.replace({"tavily": "secret-value"})

        posture = k.get_key_posture()
        assert set(posture.keys()) == {"tavily", "serpapi", "firecrawl", "brave"}
        assert posture["tavily"] == {"configured": True, "source": "db"}
        assert posture["brave"] == {"configured": False, "source": "none"}
        # the plaintext must never appear anywhere in the posture
        assert "secret-value" not in str(posture)


class TestWriteGuards:
    @pytest.mark.asyncio
    async def test_set_requires_secret_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import secrets_crypto
        from app.ops.web_search import keys as k

        monkeypatch.setattr(secrets_crypto, "is_configured", lambda: False)
        with pytest.raises(secrets_crypto.SecretsNotConfiguredError):
            await k.set_web_search_api_key("tavily", "abc")

    @pytest.mark.asyncio
    async def test_set_rejects_unknown_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import secrets_crypto
        from app.ops.web_search import keys as k

        monkeypatch.setattr(secrets_crypto, "is_configured", lambda: True)
        with pytest.raises(ValueError):
            await k.set_web_search_api_key("searxng", "abc")  # searxng takes no key

    @pytest.mark.asyncio
    async def test_load_cache_noop_without_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import secrets_crypto
        from app.ops.web_search import keys as k

        k._cache.replace({"tavily": "stale"})
        monkeypatch.setattr(secrets_crypto, "is_configured", lambda: False)
        await k.load_cache_from_db()
        assert k.get_web_search_api_key("tavily") is None  # cache cleared, no env


class TestAdapterIntegration:
    def test_adapters_read_through_resolver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A DB-cached key makes each cloud adapter report configured() True."""
        from app import config as cfg
        from app.ops.web_search import brave, firecrawl, keys as k, serpapi, tavily

        for p in ("tavily", "serpapi", "firecrawl", "brave"):
            monkeypatch.setattr(cfg.settings, f"{p}_api_key", "", raising=False)
        k._cache.replace({"tavily": "t", "serpapi": "s", "firecrawl": "f", "brave": "b"})
        assert tavily.TavilyProvider().configured() is True
        assert serpapi.SerpApiProvider().configured() is True
        assert firecrawl.FirecrawlProvider().configured() is True
        assert brave.BraveProvider().configured() is True
