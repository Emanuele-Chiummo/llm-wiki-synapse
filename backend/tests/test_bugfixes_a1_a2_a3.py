"""
Regression tests for the three live-audit bugs (A1/A2/A3).

A1  OllamaProvider must send options.num_ctx (derived from config) on every /api/chat call,
    so Ollama does not silently truncate context to its 4096 default.
A2  _write_ingest_run must persist pages_created / status / error_message from the real run
    outcome (not the stale 0 / "completed" / NULL defaults).
A3  The shared chat answer system prompt must instruct bare [n] citation markers (never
    [[..]], never a title inside the marker, never invented indices).

All tests are infra-free (no live Ollama / Postgres): A1 stubs httpx with a MockTransport that
records the request bodies; A2 calls the pure status helper + asserts the kwargs reaching the
IngestRun construction; A3 inspects the static preamble string.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from app.ingest.provider import ollama as ollama_mod
from app.ingest.provider.config import ProviderSettings
from app.ingest.provider.ollama import (
    _NUM_CTX_CEILING,
    _NUM_CTX_DEFAULT,
    _NUM_CTX_FLOOR,
    OllamaProvider,
    _derive_num_ctx,
)

# ── A1: num_ctx derivation (pure) ────────────────────────────────────────────────


def _cfg(token_budget: int | None) -> ProviderSettings:
    kwargs: dict[str, Any] = {
        "provider_type": "local",
        "model_id": "some-local-model",
        "base_url": "http://ollama.test",
    }
    if token_budget is not None:
        kwargs["token_budget"] = token_budget
    return ProviderSettings(**kwargs)


def test_derive_num_ctx_uses_configured_budget() -> None:
    assert _derive_num_ctx(_cfg(60_000)) == 60_000


def test_derive_num_ctx_defaults_when_unset() -> None:
    # token_budget=0 is treated as "unset" → default.
    assert _derive_num_ctx(_cfg(0)) == _NUM_CTX_DEFAULT


def test_derive_num_ctx_floor_and_ceiling() -> None:
    assert _derive_num_ctx(_cfg(100)) == _NUM_CTX_FLOOR
    assert _derive_num_ctx(_cfg(10_000_000)) == _NUM_CTX_CEILING


# ── A1: num_ctx actually reaches the wire on every call path ─────────────────────


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Force every httpx.AsyncClient built inside ollama.py to use a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.setdefault("transport", transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_analyze_sends_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        analysis = {
            "topics": ["t"],
            "entities": [],
            "language": "en",
            "suggested_pages": [{"title": "P", "type": "concept"}],
            "summary": "s",
        }
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": json.dumps(analysis)},
                "prompt_eval_count": 7,
                "eval_count": 3,
            },
        )

    _patch_transport(monkeypatch, handler)
    provider = OllamaProvider(_cfg(60_000))
    await provider.analyze("source text", "vault ctx")

    assert captured["body"]["options"]["num_ctx"] == 60_000


@pytest.mark.asyncio
async def test_generate_sends_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        pages = {
            "pages": [
                {
                    "title": "P",
                    "type": "concept",
                    "content": "body",
                    "frontmatter": {
                        "type": "concept",
                        "title": "P",
                        "sources": ["raw/sources/x.md"],
                        "lang": "en",
                    },
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": json.dumps(pages)},
                "prompt_eval_count": 7,
                "eval_count": 3,
            },
        )

    _patch_transport(monkeypatch, handler)
    provider = OllamaProvider(_cfg(60_000))
    from app.ingest.schemas import Analysis, PageType, SuggestedPage

    analysis = Analysis(
        topics=["t"],
        entities=[],
        language="en",
        suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
    )
    await provider.generate(analysis, "retrieval ctx")

    assert captured["body"]["options"]["num_ctx"] == 60_000


@pytest.mark.asyncio
async def test_chat_stream_sends_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        lines = [
            json.dumps({"message": {"content": "hello"}, "done": False}),
            json.dumps(
                {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2}
            ),
        ]
        return httpx.Response(200, text="\n".join(lines))

    _patch_transport(monkeypatch, handler)
    provider = OllamaProvider(_cfg(48_000))
    from app.ingest.schemas import Message

    stream = await provider.chat([Message(role="user", content="hi")], "ctx")
    chunks = [c async for c in stream]

    assert "".join(chunks) == "hello"
    assert captured["body"]["options"]["num_ctx"] == 48_000


# ── A2: run status + persisted fields ────────────────────────────────────────────


def test_derive_run_status_completed() -> None:
    from app.ingest.orchestrator import _derive_run_status

    assert _derive_run_status(converged=True, error_message=None) == "completed"


def test_derive_run_status_non_converged() -> None:
    from app.ingest.orchestrator import _derive_run_status

    assert _derive_run_status(converged=False, error_message=None) == "converged_false"


def test_derive_run_status_failed_takes_precedence() -> None:
    from app.ingest.orchestrator import _derive_run_status

    # Even if converged somehow True, an error_message means the run failed.
    assert _derive_run_status(converged=True, error_message="boom") == "failed"
    assert _derive_run_status(converged=False, error_message="boom") == "failed"


@pytest.mark.asyncio
async def test_write_ingest_run_persists_pages_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_write_ingest_run must build an IngestRun with pages_created/status/error_message set."""
    from datetime import UTC, datetime

    import app.ingest.orchestrator as orch

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def add(self, obj: Any) -> None:
            captured["run"] = obj

    monkeypatch.setattr(orch, "get_session", lambda: _FakeSession())

    now = datetime.now(UTC)
    await orch._write_ingest_run(
        page_id=None,
        provider_name="OllamaProvider",
        provider_type="local",
        model_id="m",
        route="orchestrated",
        max_iter_used=2,
        total_tokens=123,
        total_cost_usd=0.0,
        converged=True,
        cost_anomaly=False,
        started_at=now,
        finished_at=now,
        pages_created=4,
    )

    run = captured["run"]
    assert run.pages_created == 4
    assert run.status == "completed"
    assert run.error_message is None


@pytest.mark.asyncio
async def test_write_ingest_run_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    import app.ingest.orchestrator as orch

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def add(self, obj: Any) -> None:
            captured["run"] = obj

    monkeypatch.setattr(orch, "get_session", lambda: _FakeSession())

    now = datetime.now(UTC)
    await orch._write_ingest_run(
        page_id=None,
        provider_name="ApiProvider",
        provider_type="api",
        model_id="m",
        route="orchestrated",
        max_iter_used=1,
        total_tokens=10,
        total_cost_usd=0.0,
        converged=False,
        cost_anomaly=False,
        started_at=now,
        finished_at=now,
        pages_created=0,
        error_message="connection reset",
    )

    run = captured["run"]
    assert run.status == "failed"
    assert run.error_message == "connection reset"
    assert run.pages_created == 0


@pytest.mark.asyncio
async def test_write_ingest_run_records_converged_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    _write_ingest_run with converged=False + no error_message must persist status='converged_false'.

    This is the non-convergence path: the loop ran but never produced a valid batch
    (max_iter / token_budget exhausted). It is distinct from both "completed" (converged=True)
    and "failed" (error_message set). Previously untested as an integration path through
    _write_ingest_run (the unit test for _derive_run_status covered the pure function;
    this test closes the gap for the full persist path — A2 / ADR-0018 §7).
    """
    from datetime import UTC, datetime

    import app.ingest.orchestrator as orch

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def add(self, obj: Any) -> None:
            captured["run"] = obj

    monkeypatch.setattr(orch, "get_session", lambda: _FakeSession())

    now = datetime.now(UTC)
    await orch._write_ingest_run(
        page_id=None,
        provider_name="OllamaProvider",
        provider_type="local",
        model_id="m",
        route="orchestrated",
        max_iter_used=3,
        total_tokens=45_000,
        total_cost_usd=0.0,
        converged=False,
        cost_anomaly=False,
        started_at=now,
        finished_at=now,
        pages_created=0,
        # error_message deliberately absent (None) — non-convergence, not a failure
    )

    run = captured["run"]
    assert run.status == "converged_false", (
        f"Non-converged run must get status='converged_false'; got {run.status!r}"
    )
    assert run.error_message is None, (
        f"Non-converged run must NOT have error_message set; got {run.error_message!r}"
    )
    assert run.pages_created == 0
    assert run.converged is False


# ── A3: citation marker prompt ───────────────────────────────────────────────────


def test_chat_preamble_demands_bare_bracket_citations() -> None:
    from app.chat.context import _SYSTEM_PREAMBLE

    text = _SYSTEM_PREAMBLE
    # Must demand bare [n] markers and forbid the local-model variants seen in the audit.
    assert "[1]" in text
    assert "[[3]]" in text  # explicitly forbidden form is named
    assert "never invent" in text.lower()
    # Must forbid putting the title inside the marker.
    assert "title inside the marker" in text.lower()
