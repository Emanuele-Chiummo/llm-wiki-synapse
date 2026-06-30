"""
Unit tests for the embedding seam (ADR-0031, Feature C — OpenAI-compatible adapter).

Infra-free: no live bge-m3 / no network. We intercept the outgoing httpx request with
httpx.MockTransport (patched onto the module's httpx.AsyncClient), so we can assert the
exact request body + headers and control the response shape.

Covers:
  - ollama mode (default): request body {"model","prompt"}, parse {"embedding":[...]}.
  - openai mode: request body {"model","input"}, parse data[0].embedding.
  - Authorization: Bearer header present only when EMBEDDING_API_KEY is set, absent otherwise.
  - malformed openai response raises EmbeddingError (no silent empty vector).
  - probe_dimension() stays format-agnostic (ADR-0004).
  GAP-EMB-KEY-LEAK (ADR-0031 C-AC-6): EMBEDDING_API_KEY never appears in GET /config/embedding.
  GAP-EMB-ABC-SIG  (ADR-0031 C-AC-5): EmbeddingClient ABC + factory signatures are frozen.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any
from unittest.mock import patch

import app.embeddings as embeddings_mod
import httpx
import pytest
from app.embeddings import EmbeddingClient, EmbeddingError, HttpEmbeddingClient
from httpx import ASGITransport, AsyncClient


class _Capture:
    """Records the last request seen by the mock transport."""

    def __init__(self) -> None:
        self.body: dict[str, Any] | None = None
        self.headers: httpx.Headers | None = None


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    capture: _Capture,
    response: dict[str, Any] | str,
    status_code: int = 200,
) -> None:
    """Patch httpx.AsyncClient (as seen by embeddings.py) to use a MockTransport.

    The mock handler records the request body + headers into *capture* and replies with
    *response* (dict → JSON; str → raw text) and *status_code*.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        capture.headers = request.headers
        try:
            capture.body = json.loads(request.content)
        except json.JSONDecodeError:
            capture.body = None
        if isinstance(response, str):
            return httpx.Response(status_code, text=response)
        return httpx.Response(status_code, json=response)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(embeddings_mod.httpx, "AsyncClient", _factory)


# ── ollama mode (default) — must stay byte-identical to historical behavior ──────


@pytest.mark.asyncio
async def test_ollama_mode_request_body_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"embedding": [0.1, 0.2, 0.3]})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/api/embeddings",
        model="bge-m3",
        embedding_format="ollama",
        api_key=None,
    )
    vector = await client.embed("hello")

    assert vector == [0.1, 0.2, 0.3]
    assert capture.body == {"model": "bge-m3", "prompt": "hello"}
    # No "input" key in ollama mode.
    assert "input" not in (capture.body or {})


@pytest.mark.asyncio
async def test_ollama_mode_is_the_default_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit format, the client defaults to ollama (settings default)."""
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"embedding": [1.0]})

    client = HttpEmbeddingClient(embedding_url="http://embed.test/api/embeddings")
    await client.embed("x")

    assert capture.body is not None
    assert "prompt" in capture.body
    assert "input" not in capture.body


@pytest.mark.asyncio
async def test_ollama_malformed_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"embedding": []})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/api/embeddings", embedding_format="ollama"
    )
    with pytest.raises(EmbeddingError):
        await client.embed("x")


# ── openai mode ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_mode_request_body_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(
        monkeypatch,
        capture,
        {"data": [{"embedding": [0.5, 0.6]}], "model": "text-embedding-3-small"},
    )

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/v1/embeddings",
        model="text-embedding-3-small",
        embedding_format="openai",
        api_key=None,
    )
    vector = await client.embed("world")

    assert vector == [0.5, 0.6]
    assert capture.body == {"model": "text-embedding-3-small", "input": "world"}
    # No "prompt" key in openai mode.
    assert "prompt" not in (capture.body or {})


@pytest.mark.asyncio
async def test_openai_mode_format_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"data": [{"embedding": [9.0]}]})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/v1/embeddings", embedding_format="OpenAI"
    )
    vector = await client.embed("y")

    assert vector == [9.0]
    assert capture.body is not None and "input" in capture.body


@pytest.mark.parametrize(
    "bad_payload",
    [
        {},  # no "data"
        {"data": []},  # empty list
        {"data": [{}]},  # missing nested "embedding"
        {"data": [{"embedding": []}]},  # empty nested vector
        {"data": "not-a-list"},  # wrong type
        {"data": ["not-a-dict"]},  # element not a dict
    ],
)
@pytest.mark.asyncio
async def test_openai_malformed_response_raises(
    monkeypatch: pytest.MonkeyPatch, bad_payload: dict[str, Any]
) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, bad_payload)

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/v1/embeddings", embedding_format="openai"
    )
    with pytest.raises(EmbeddingError):
        await client.embed("x")


# ── Authorization header (orthogonal to format) ──────────────────────────────


@pytest.mark.asyncio
async def test_bearer_header_present_when_key_set_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"data": [{"embedding": [1.0]}]})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/v1/embeddings",
        embedding_format="openai",
        api_key="sk-secret-123",
    )
    await client.embed("x")

    assert capture.headers is not None
    assert capture.headers.get("authorization") == "Bearer sk-secret-123"


@pytest.mark.asyncio
async def test_bearer_header_present_when_key_set_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header is orthogonal to format — sent in ollama mode too when a key is set."""
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"embedding": [1.0]})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/api/embeddings",
        embedding_format="ollama",
        api_key="sk-secret-456",
    )
    await client.embed("x")

    assert capture.headers is not None
    assert capture.headers.get("authorization") == "Bearer sk-secret-456"


@pytest.mark.asyncio
async def test_bearer_header_absent_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"embedding": [1.0]})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/api/embeddings",
        embedding_format="ollama",
        api_key=None,
    )
    await client.embed("x")

    assert capture.headers is not None
    assert "authorization" not in capture.headers


# ── probe_dimension stays format-agnostic (ADR-0004 startup validation) ──────────


@pytest.mark.asyncio
async def test_probe_dimension_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(
        monkeypatch, capture, {"data": [{"embedding": [0.0] * 1024}]}
    )

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/v1/embeddings", embedding_format="openai"
    )
    dim = await client.probe_dimension()

    assert dim == 1024
    # probe just calls embed("probe")
    assert capture.body == {"model": client._model, "input": "probe"}


@pytest.mark.asyncio
async def test_probe_dimension_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"embedding": [0.0] * 768})

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/api/embeddings", embedding_format="ollama"
    )
    dim = await client.probe_dimension()

    assert dim == 768
    assert capture.body == {"model": client._model, "prompt": "probe"}


# ── http error wrapping unchanged ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_error_wrapped_as_embedding_error(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _Capture()
    _install_mock_transport(monkeypatch, capture, {"error": "boom"}, status_code=500)

    client = HttpEmbeddingClient(
        embedding_url="http://embed.test/v1/embeddings", embedding_format="openai"
    )
    with pytest.raises(EmbeddingError):
        await client.embed("x")


# ── GAP-EMB-KEY-LEAK (ADR-0031 C-AC-6) ───────────────────────────────────────
# GET /config/embedding must NEVER expose EMBEDDING_API_KEY in the response body.


async def _noop_lifespan(app_: Any) -> Any:  # type: ignore[type-arg]
    """No-op async context manager to bypass real startup in lifespan tests."""
    yield


@pytest.mark.asyncio
async def test_gap_emb_key_leak_api_key_not_in_config_response() -> None:
    """
    GAP-EMB-KEY-LEAK (ADR-0031 C-AC-6): the raw EMBEDDING_API_KEY sentinel value
    must never appear in the GET /config/embedding response body, and the response
    must contain neither an 'embedding_api_key' nor an 'api_key' field.

    Modelled after test_mcp_http.py::test_mcp_info_does_not_expose_token.
    """
    sentinel = "super-secret-embedding-api-key-do-not-leak"
    saved = os.environ.get("EMBEDDING_API_KEY")
    os.environ["EMBEDDING_API_KEY"] = sentinel
    try:
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as ac:
                resp = await ac.get("/config/embedding")
    finally:
        if saved is None:
            os.environ.pop("EMBEDDING_API_KEY", None)
        else:
            os.environ["EMBEDDING_API_KEY"] = saved

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    # The sentinel must NOT appear anywhere in the raw response text.
    raw_body = resp.text
    assert sentinel not in raw_body, (
        "EMBEDDING_API_KEY sentinel leaked into GET /config/embedding response body "
        "(ADR-0031 C-AC-6)"
    )

    # The parsed JSON must not contain 'embedding_api_key' or 'api_key' at the top level.
    data = resp.json()
    assert "embedding_api_key" not in data, (
        "GET /config/embedding response must not include 'embedding_api_key' field "
        "(ADR-0031 C-AC-6)"
    )
    assert "api_key" not in data, (
        "GET /config/embedding response must not include 'api_key' field (ADR-0031 C-AC-6)"
    )


# ── GAP-EMB-ABC-SIG (ADR-0031 C-AC-5) ────────────────────────────────────────
# Pin the public contract of EmbeddingClient and the factory functions.


def test_gap_emb_abc_sig_embed_is_abstract() -> None:
    """
    GAP-EMB-ABC-SIG (ADR-0031 C-AC-5): EmbeddingClient.embed is an abstract method
    with the exact signature (self, text: str) -> list[float].
    """
    method = EmbeddingClient.embed
    # Confirm it is marked abstract.
    assert getattr(method, "__isabstractmethod__", False), (
        "EmbeddingClient.embed must be abstract (ADR-0031 C-AC-5)"
    )
    sig = inspect.signature(method)
    params = list(sig.parameters)
    assert params == ["self", "text"], (
        f"EmbeddingClient.embed signature must be (self, text); got {params}"
    )


def test_gap_emb_abc_sig_probe_dimension_is_abstract() -> None:
    """
    GAP-EMB-ABC-SIG (ADR-0031 C-AC-5): EmbeddingClient.probe_dimension is an abstract
    method with the exact signature (self) -> int.
    """
    method = EmbeddingClient.probe_dimension
    assert getattr(method, "__isabstractmethod__", False), (
        "EmbeddingClient.probe_dimension must be abstract (ADR-0031 C-AC-5)"
    )
    sig = inspect.signature(method)
    params = list(sig.parameters)
    assert params == ["self"], (
        f"EmbeddingClient.probe_dimension signature must be (self); got {params}"
    )


def test_gap_emb_abc_sig_get_embedding_client_factory() -> None:
    """
    GAP-EMB-ABC-SIG (ADR-0031 C-AC-5): get_embedding_client() takes no parameters
    (except the module-level default) and returns an EmbeddingClient.
    """
    from app.embeddings import get_embedding_client

    sig = inspect.signature(get_embedding_client)
    params = list(sig.parameters)
    # No parameters — the factory is a zero-arg call.
    assert params == [], (
        f"get_embedding_client() must take no parameters; got {params}"
    )


def test_gap_emb_abc_sig_set_embedding_client_factory() -> None:
    """
    GAP-EMB-ABC-SIG (ADR-0031 C-AC-5): set_embedding_client(client) takes exactly
    one positional parameter named 'client'.
    """
    from app.embeddings import set_embedding_client

    sig = inspect.signature(set_embedding_client)
    params = list(sig.parameters)
    assert params == ["client"], (
        f"set_embedding_client() must take exactly one parameter 'client'; got {params}"
    )
