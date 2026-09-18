"""
P3-e — multi-provider web-search seam tests (ADR-0066/ADR-0070).

Covers:
  * provider selection via the ``web_search_provider`` config-override key (default searxng);
  * unknown value fails safe to SearXNG;
  * SearXNG adapter refactor still routes through ops/searxng (behaviour preserved);
  * cloud adapters: unconfigured → no HTTP call + [] (opt-in, off by default, I9);
  * cloud adapter routing with a mocked httpx response → parsed SearchHit list;
  * cloud adapter failure (non-2xx) degrades to [] (best-effort, never raises);
  * search_many bounds + URL-dedup (I7);
  * metadata: cloud providers carry is_cloud/requires_upload_warning=True.
"""

from __future__ import annotations

from typing import Any

import app.config_overrides as _co
import pytest
from app.ops.web_search import (
    PROVIDERS,
    SearchHit,
    get_web_search_provider,
    web_search_many,
)
from app.ops.web_search.brave import BraveProvider
from app.ops.web_search.firecrawl import FirecrawlProvider
from app.ops.web_search.ollama_web import OllamaWebProvider
from app.ops.web_search.searxng import SearxngProvider
from app.ops.web_search.serpapi import SerpApiProvider
from app.ops.web_search.tavily import TavilyProvider


@pytest.fixture(autouse=True)
def _clear_override_cache() -> Any:
    """Each test starts with no web_search_provider override (env default governs)."""
    _co._cache.pop("web_search_provider", None)
    yield
    _co._cache.pop("web_search_provider", None)


# ── Provider selection (I6 — read from config, never hardcoded) ────────────────


def test_default_provider_is_searxng() -> None:
    assert isinstance(get_web_search_provider(), SearxngProvider)


@pytest.mark.parametrize(
    ("value", "cls"),
    [
        ("searxng", SearxngProvider),
        ("tavily", TavilyProvider),
        ("serpapi", SerpApiProvider),
        ("firecrawl", FirecrawlProvider),
        ("brave", BraveProvider),
        ("ollama_web", OllamaWebProvider),
    ],
)
def test_selection_via_config(value: str, cls: type) -> None:
    _co._cache["web_search_provider"] = value
    assert isinstance(get_web_search_provider(), cls)


def test_unknown_value_falls_back_to_searxng() -> None:
    _co._cache["web_search_provider"] = "not-a-real-provider"
    assert isinstance(get_web_search_provider(), SearxngProvider)


def test_registry_matches_config_enum() -> None:
    from app.config_overrides import _WEB_SEARCH_PROVIDER_VALUES

    assert set(PROVIDERS) == set(_WEB_SEARCH_PROVIDER_VALUES)


# ── Metadata (I9 cloud warnings) ──────────────────────────────────────────────


def test_cloud_providers_flag_upload_warning() -> None:
    for cls in (TavilyProvider, SerpApiProvider, FirecrawlProvider, BraveProvider):
        p = cls()
        assert p.is_cloud is True
        assert p.requires_upload_warning is True


def test_local_providers_do_not_warn() -> None:
    for cls in (SearxngProvider, OllamaWebProvider):
        p = cls()
        assert p.is_cloud is False
        assert p.requires_upload_warning is False


# ── configured(): opt-in (off until key/url set) ──────────────────────────────


def test_cloud_provider_unconfigured_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "tavily_api_key", "")
    assert TavilyProvider().configured() is False


def test_cloud_provider_configured_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "tavily_api_key", "tvly-secret")
    assert TavilyProvider().configured() is True


async def test_unconfigured_cloud_provider_returns_empty_no_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in: with no API key, the adapter never makes an HTTP call and returns [] (I9)."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "tavily_api_key", "")

    def _boom(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not be called
        raise AssertionError("httpx.AsyncClient must not be constructed when unconfigured")

    monkeypatch.setattr("app.ops.web_search.tavily.httpx.AsyncClient", _boom)
    assert await TavilyProvider().search_many(["q"]) == []


def test_ollama_web_configured_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert OllamaWebProvider().configured() is False
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    assert OllamaWebProvider().configured() is True


# ── Cloud adapter routing with a mocked httpx response ────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Minimal async context-manager httpx.AsyncClient stand-in."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def post(self, *_a: Any, **_k: Any) -> _FakeResponse:
        return self._response

    async def get(self, *_a: Any, **_k: Any) -> _FakeResponse:
        return self._response


async def test_tavily_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "tavily_api_key", "tvly-secret")
    payload = {
        "results": [
            {"url": "https://a.example/1", "title": "A", "content": "about a"},
            {"url": "https://a.example/2", "title": "B", "content": "about b"},
            {"title": "no-url"},  # dropped
        ]
    }
    monkeypatch.setattr(
        "app.ops.web_search.tavily.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse(payload)),
    )
    hits = await TavilyProvider().search_many(["q"])
    assert [h.url for h in hits] == ["https://a.example/1", "https://a.example/2"]
    assert all(isinstance(h, SearchHit) for h in hits)
    assert hits[0].engine == "tavily"


async def test_serpapi_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "serpapi_api_key", "serp-secret")
    payload = {"organic_results": [{"link": "https://s.example/1", "title": "S", "snippet": "x"}]}
    monkeypatch.setattr(
        "app.ops.web_search.serpapi.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse(payload)),
    )
    hits = await SerpApiProvider().search_many(["q"])
    assert [h.url for h in hits] == ["https://s.example/1"]


async def test_brave_parses_nested_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "brave_api_key", "brave-secret")
    payload = {
        "web": {"results": [{"url": "https://b.example/1", "title": "B", "description": "d"}]}
    }
    monkeypatch.setattr(
        "app.ops.web_search.brave.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse(payload)),
    )
    hits = await BraveProvider().search_many(["q"])
    assert [h.url for h in hits] == ["https://b.example/1"]


async def test_cloud_adapter_non_2xx_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "firecrawl_api_key", "fc-secret")
    monkeypatch.setattr(
        "app.ops.web_search.firecrawl.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse({}, status=500)),
    )
    assert await FirecrawlProvider().search_many(["q"]) == []


# ── search_many bounds + dedup (I7) ───────────────────────────────────────────


async def test_search_many_dedupes_by_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "tavily_api_key", "tvly-secret")
    payload = {"results": [{"url": "https://dup.example", "title": "Dup", "content": "c"}]}
    monkeypatch.setattr(
        "app.ops.web_search.tavily.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse(payload)),
    )
    hits = await TavilyProvider().search_many(["q1", "q2", "q3"])
    assert len(hits) == 1  # same URL across all three queries → deduped


async def test_empty_queries_short_circuits() -> None:
    assert await TavilyProvider().search_many([]) == []


# ── Dispatcher wiring + SearXNG refactor preserved ────────────────────────────


async def test_web_search_many_routes_to_selected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _co._cache["web_search_provider"] = "searxng"
    called: dict[str, Any] = {}

    async def _fake_searxng_many(queries: list[str]) -> list[SearchHit]:
        called["queries"] = queries
        return [SearchHit(url="https://sx.example", title="SX")]

    monkeypatch.setattr("app.ops.web_search.searxng.searxng_search_many", _fake_searxng_many)
    hits = await web_search_many(["alpha", "beta"])
    assert called["queries"] == ["alpha", "beta"]
    assert [h.url for h in hits] == ["https://sx.example"]


# ── SearXNG honours the same best-effort contract as every other backend ──────
#
# Regression for 2.1.14. ``WebSearchProvider._search_one`` documents, for EVERY backend,
# "on ANY failure return [] and log a WARNING — never raise into the caller", and the five
# opt-in cloud adapters implement it with a blanket ``except Exception``. The DEFAULT
# backend did not: ``ops/searxng.searxng_search`` named three httpx types, of which
# ConnectError is already a NetworkError — so everything else under TransportError, plus
# the non-TransportError httpx errors and any malformed payload, escaped. The escape is
# not contained: it propagates through ``searxng_search_many``'s gather (cancelling the
# other in-flight queries) into ``run_deep_research``'s terminal handler, which fails the
# whole research run.


class _RaisingClient:
    """httpx.AsyncClient stand-in whose GET raises the configured exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> _RaisingClient:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def get(self, *_a: Any, **_k: Any) -> Any:
        raise self._exc


def _searxng_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the resolver at a URL so searxng_search gets as far as the HTTP call."""
    from app import config as cfg
    from app import runtime_state

    monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng.local:8080")
    monkeypatch.setattr(runtime_state.web_search_config_cache, "_url_db", None, raising=False)


def _transport_failures() -> list[BaseException]:
    import httpx

    return [
        # Server closed the connection mid-response — a ProtocolError, NOT a NetworkError.
        httpx.RemoteProtocolError("server disconnected without sending a response"),
        # Operator saved a scheme-less base URL through PUT /web-search/config.
        httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol"),
        httpx.ProxyError("proxy refused the tunnel"),
        httpx.DecodingError("failed to decode the response body"),
        httpx.InvalidURL("malformed URL"),
    ]


@pytest.mark.parametrize("exc", _transport_failures(), ids=lambda e: type(e).__name__)
async def test_searxng_transport_failure_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    """Each of these used to propagate out of searxng_search instead of returning []."""
    from app.ops.searxng import searxng_search

    _searxng_configured(monkeypatch)
    monkeypatch.setattr("app.ops.searxng.httpx.AsyncClient", lambda *a, **k: _RaisingClient(exc))

    assert await searxng_search("q") == []


async def test_searxng_transport_failure_does_not_fail_the_whole_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The blast radius, not just the single call: one failing query inside
    searxng_search_many's gather must not take the other queries (or the caller) down.
    """
    import httpx
    from app.ops.searxng import searxng_search_many

    _searxng_configured(monkeypatch)

    good = _FakeResponse({"results": [{"url": "https://ok.example", "title": "OK"}]})

    def _client_for(*_a: Any, **kwargs: Any) -> Any:
        # First query raises, the rest succeed — the gather must still return the good hits.
        if calls["n"] == 0:
            calls["n"] += 1
            return _RaisingClient(httpx.RemoteProtocolError("server disconnected"))
        calls["n"] += 1
        return _FakeClient(good)

    calls = {"n": 0}
    monkeypatch.setattr("app.ops.searxng.httpx.AsyncClient", _client_for)

    hits = await searxng_search_many(["boom", "fine", "fine-too"])
    assert [h.url for h in hits] == ["https://ok.example"]


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "a", "dict"],  # top-level array → data.get was an AttributeError
        {"results": {"not": "a list"}},  # non-list results → the slice was a TypeError
        {"results": [{"url": 12345, "title": "numeric url"}]},  # → pydantic ValidationError
    ],
    ids=["top-level-array", "results-not-a-list", "non-string-url"],
)
async def test_searxng_malformed_payload_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    """A remote service's JSON is not a validated schema — a bad shape returns [], not 500."""
    from app.ops.searxng import searxng_search

    _searxng_configured(monkeypatch)
    monkeypatch.setattr(
        "app.ops.searxng.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse(payload)),
    )

    assert await searxng_search("q") == []


async def test_searxng_well_formed_results_still_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-regression: the hardening must not change the happy path."""
    from app.ops.searxng import searxng_search

    _searxng_configured(monkeypatch)
    payload = {
        "results": [
            {"url": "https://a.example", "title": "A", "content": "ca", "engine": "duckduckgo"},
            {"link": "https://b.example", "title": "B", "snippet": "cb"},
            {"title": "no url at all"},  # dropped, as before
        ]
    }
    monkeypatch.setattr(
        "app.ops.searxng.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(_FakeResponse(payload)),
    )

    hits = await searxng_search("q")
    assert [h.url for h in hits] == ["https://a.example", "https://b.example"]
    assert hits[0].snippet == "ca"
    assert hits[0].engine == "duckduckgo"
    assert hits[1].snippet == "cb"
