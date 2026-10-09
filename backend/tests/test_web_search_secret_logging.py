"""
Regression tests for the web-search credential-logging invariant (2.1.17).

THE INVARIANT (``app/ops/web_search/keys.py``, module docstring): "Plaintext is NEVER logged
or returned by any endpoint — only a masked posture is exposed."

WHY SERPAPI IS THE ONE THAT BROKE IT: of the five opt-in backends, four carry their
credential in a request HEADER (``brave``: ``X-Subscription-Token``; ``firecrawl``:
``Authorization: Bearer``) or in a JSON BODY (``tavily``). SerpApi's API takes no auth header,
so its key is a QUERY PARAMETER — part of the request URL. ``httpx`` formats
``HTTPStatusError`` as::

    Client error '401 Unauthorized' for url 'https://serpapi.com/search.json?...&api_key=KEY'

so the adapter's best-effort ``except`` clause, which interpolated ``exc`` into a WARNING to
explain the degrade, wrote the operator's key into the log in plaintext. The triggering
responses are the ordinary ones: 401 for a wrong or expired key, 429 for a RATE-LIMITED VALID
key — i.e. the valid credential leaks too, and nothing about it is adversarial.

Each test here FAILS without the fix: the key appears verbatim in the captured log record.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from app.ops.web_search import serpapi as serpapi_mod
from app.ops.web_search.serpapi import SerpApiProvider, _redact_key

_KEY = "sk-serpapi-abcdef0123456789-SECRET"


@pytest.fixture
def _key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the adapter believe a key is configured, without touching the real env."""
    monkeypatch.setattr(serpapi_mod, "get_web_search_api_key", lambda _provider: _KEY)


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route the adapter's short-lived AsyncClient through a MockTransport."""
    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(serpapi_mod.httpx, "AsyncClient", _factory)


# ── _redact_key unit behaviour ────────────────────────────────────────────────


class TestRedactKey:
    def test_replaces_the_raw_key(self) -> None:
        assert _redact_key(f"url '...api_key={_KEY}'", _KEY) == "url '...api_key=***'"

    def test_replaces_the_percent_encoded_key(self) -> None:
        """httpx builds the URL from params=, so a URL-unsafe byte arrives encoded."""
        key = "abc/def+ghi"
        text = "for url 'https://serpapi.com/search.json?api_key=abc%2Fdef%2Bghi'"
        assert key not in _redact_key(text, key)
        assert "abc%2Fdef%2Bghi" not in _redact_key(text, key)

    def test_empty_key_is_not_substituted(self) -> None:
        """str.replace("", x) splices x between every character — never do that to a log line."""
        assert _redact_key("connection refused", "") == "connection refused"


# ── The leak path: a non-2xx response ─────────────────────────────────────────


class TestSerpApiNeverLogsTheKey:
    async def test_401_does_not_log_the_key(
        self, _key_set: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A wrong/expired key is the single most likely failure — and it leaked the key."""
        _install_transport(monkeypatch, lambda _req: httpx.Response(401, text="unauthorized"))

        with caplog.at_level(logging.WARNING):
            hits = await SerpApiProvider()._search_one("some query")

        assert hits == [], "a non-2xx must still degrade to [] (best-effort contract)"
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "401" in logged, f"the degrade should still be explained: {logged!r}"
        assert _KEY not in logged, f"API KEY LEAKED into the log: {logged!r}"

    async def test_429_does_not_log_the_key(
        self, _key_set: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Rate-limiting leaks a key that is perfectly VALID — the worse of the two cases."""
        _install_transport(monkeypatch, lambda _req: httpx.Response(429, text="slow down"))

        with caplog.at_level(logging.WARNING):
            await SerpApiProvider()._search_one("some query")

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert _KEY not in logged, f"API KEY LEAKED into the log: {logged!r}"

    async def test_transport_error_does_not_log_the_key(
        self, _key_set: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Belt and braces: any exception text is redacted, not just HTTPStatusError's."""

        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        _install_transport(monkeypatch, _boom)

        with caplog.at_level(logging.WARNING):
            await SerpApiProvider()._search_one("some query")

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert _KEY not in logged, f"API KEY LEAKED into the log: {logged!r}"

    async def test_a_successful_search_still_parses_hits(
        self, _key_set: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-regression: the redaction must not disturb the happy path."""
        _install_transport(
            monkeypatch,
            lambda _req: httpx.Response(
                200,
                json={
                    "organic_results": [
                        {"link": "https://example.com/a", "title": "A", "snippet": "sa"},
                        {"link": "", "title": "skipped — no url"},
                    ]
                },
            ),
        )

        hits = await SerpApiProvider()._search_one("some query")

        assert [h.url for h in hits] == ["https://example.com/a"]
        assert hits[0].engine == "serpapi"
        assert hits[0].snippet == "sa"
