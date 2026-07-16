"""
1.8.1 regression tests — embedding token-robustness + vector-less degrade.

Root cause fixed: ``embed_max_chars`` bounds CHARACTERS but bge-m3's limit is TOKENS, so token-dense
content under the char cap can still make Ollama return HTTP 500 "input length exceeds the context
length". ``HttpEmbeddingClient.embed`` now catches that specific 500 and retries with the input
halved (bounded), and ``upsert_vector`` degrades an unrecoverable embedding failure to a vector-less
page instead of aborting the whole document ingest.

Infra-free: httpx.MockTransport, no live bge-m3.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import app.embeddings as embeddings_mod
import httpx
import pytest
from app.embeddings import (
    _EMBED_HALVING_MAX_ATTEMPTS,
    _EMBED_HALVING_MIN_CHARS,
    EmbeddingError,
    HttpEmbeddingClient,
)

_CONTEXT_500 = "the input length exceeds the context length"


def _install_length_aware_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_above: int | None = None,
    always_status: int | None = None,
    status_body: str = _CONTEXT_500,
) -> dict[str, Any]:
    """Mock transport whose reply depends on the prompt length.

    - always_status set → every request returns that status with *status_body*.
    - else → 500 (*status_body*) when len(prompt) > fail_above, otherwise 200 with a vector.
    Returns a dict recording call count + the prompt length of each request.
    """
    calls: dict[str, Any] = {"n": 0, "lengths": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        prompt = body.get("prompt") or body.get("input") or ""
        calls["lengths"].append(len(prompt))
        if always_status is not None:
            return httpx.Response(always_status, text=status_body)
        assert fail_above is not None
        if len(prompt) > fail_above:
            return httpx.Response(500, text=status_body)
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(embeddings_mod.httpx, "AsyncClient", _factory)
    return calls


def _client() -> HttpEmbeddingClient:
    return HttpEmbeddingClient(
        embedding_url="http://embed.test/api/embeddings",
        model="bge-m3",
        embedding_format="ollama",
        api_key=None,
    )


@pytest.mark.asyncio
async def test_context_length_500_retries_by_halving_until_it_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Don't let the up-front char cap pre-truncate; exercise the retry loop purely.
    monkeypatch.setattr(embeddings_mod.settings, "embed_max_chars", 8_000)
    calls = _install_length_aware_transport(monkeypatch, fail_above=1_000)

    vector = await _client().embed("x" * 3_000)

    assert vector == [0.1, 0.2, 0.3]
    # 3000 -> 1500 -> 750 (<=1000, succeeds): three requests, each half the previous.
    assert calls["n"] == 3
    assert calls["lengths"] == [3_000, 1_500, 750]
    assert calls["n"] <= _EMBED_HALVING_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_unrecoverable_context_500_raises_and_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embeddings_mod.settings, "embed_max_chars", 8_000)
    calls = _install_length_aware_transport(monkeypatch, always_status=500)

    with pytest.raises(EmbeddingError):
        await _client().embed("x" * 3_000)

    # It must NOT loop forever: bounded attempts, shrinking down to the floor then giving up.
    assert 1 < calls["n"] <= _EMBED_HALVING_MAX_ATTEMPTS
    assert min(calls["lengths"]) >= _EMBED_HALVING_MIN_CHARS
    assert calls["lengths"] == sorted(calls["lengths"], reverse=True)  # monotonically shrinking


@pytest.mark.asyncio
async def test_non_context_500_surfaces_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embeddings_mod.settings, "embed_max_chars", 8_000)
    calls = _install_length_aware_transport(
        monkeypatch, always_status=500, status_body="internal server error"
    )

    with pytest.raises(EmbeddingError):
        await _client().embed("x" * 3_000)

    assert calls["n"] == 1  # a non-context 500 is not the shrinkable error → no retry


@pytest.mark.asyncio
async def test_client_error_400_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_mod.settings, "embed_max_chars", 8_000)
    calls = _install_length_aware_transport(
        monkeypatch, always_status=400, status_body="bad request"
    )

    with pytest.raises(EmbeddingError):
        await _client().embed("x" * 3_000)

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_upsert_vector_degrades_to_vectorless_page_on_embedding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecoverable embedding failure must NOT propagate: the page stays indexed, only the
    Qdrant point is skipped (config.py 'vector-less page' degrade, now actually implemented)."""
    import app.ingest.orchestrator as orch

    class _RaisingClient:
        async def embed(self, text: str) -> list[float]:
            raise EmbeddingError("bge-m3 context 500 after shrink-retries")

    upsert_calls = {"n": 0}

    async def _spy_upsert_point(**kwargs: Any) -> None:
        upsert_calls["n"] += 1

    monkeypatch.setattr(orch, "get_embedding_client", lambda: _RaisingClient())
    monkeypatch.setattr(orch, "upsert_point", _spy_upsert_point)
    # embeddings enabled, without hitting the DB override seam
    monkeypatch.setattr("app.config_overrides.effective_bool", lambda key, fallback: True)

    # Must NOT raise.
    await orch.upsert_vector(
        page_id=uuid.uuid4(),
        text="dense regulatory table",
        file_path="wiki/concepts/x.md",
        title="X",
        page_type="concept",
        vault_id="test-vault",
    )
    assert upsert_calls["n"] == 0  # no Qdrant point written on degrade
