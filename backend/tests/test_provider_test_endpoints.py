"""
Provider-test endpoint tests (W1 / F17, I7): /provider/test/connection + /provider/test/function.

Covers:
    - connection happy path (endpoint responds → ok)
    - function happy path (reply contains OK → ok) and mismatch (→ not ok)
    - bounded failure modes: timeout and HTTP error map to ok=false + safe detail
    - the inline api_key is NEVER echoed in the response
    - inline validation: neither config_id nor provider_type+model → 422
    - key resolution precedence unit test (inline > decrypted stored > env)
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.main import app
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _patch_one_shot(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    """Patch app.routers.config._one_shot_chat to return `result` or raise it if it's an Exception."""

    async def _fake(*_a: Any, **_k: Any) -> str:
        if isinstance(result, Exception):
            raise result
        return str(result)

    monkeypatch.setattr("app.routers.config._one_shot_chat", _fake)


_INLINE_API = {
    "provider_type": "api",
    "base_url": "https://api.example.com/v1",
    "model": "test-model",
    "api_key": "sk-super-secret-should-never-leak",
}


def test_connection_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_one_shot(monkeypatch, "OK")
    resp = _client().post("/provider/test/connection", json=_INLINE_API)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["detail"] == "endpoint responded"
    assert isinstance(body["latency_ms"], int)


def test_function_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_one_shot(monkeypatch, "OK")
    resp = _client().post("/provider/test/function", json=_INLINE_API)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_function_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_one_shot(monkeypatch, "I refuse")
    resp = _client().post("/provider/test/function", json=_INLINE_API)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_timeout_maps_to_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_one_shot(monkeypatch, httpx.TimeoutException("boom"))
    resp = _client().post("/provider/test/connection", json=_INLINE_API)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "timeout" in body["detail"].lower()


def test_http_error_maps_to_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    _patch_one_shot(monkeypatch, err)
    resp = _client().post("/provider/test/connection", json=_INLINE_API)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "401" in body["detail"]


def test_api_key_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_one_shot(monkeypatch, "OK")
    resp = _client().post("/provider/test/function", json=_INLINE_API)
    assert resp.status_code == 200
    assert "sk-super-secret-should-never-leak" not in resp.text


def test_missing_target_is_422() -> None:
    resp = _client().post("/provider/test/connection", json={})
    assert resp.status_code == 422


def test_cli_posture_no_live_call() -> None:
    # CLI backend is never live-probed; returns a posture-derived ok + descriptive detail.
    resp = _client().post(
        "/provider/test/connection", json={"provider_type": "cli", "model": "claude-opus-4-8"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["ok"], bool)
    assert body["latency_ms"] == 0


def test_key_precedence_inline_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers.config import _resolve_probe_key

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert _resolve_probe_key("api", "https://x/v1", "inline-key", None) == "inline-key"


def test_key_precedence_stored_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers.config import _resolve_probe_key

    monkeypatch.setenv("SYNAPSE_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    from app import secrets_crypto

    stored = secrets_crypto.encrypt("stored-key")
    # Anthropic-native (no base_url) → stored decrypted key wins over env.
    assert _resolve_probe_key("api", None, None, stored) == "stored-key"


def test_key_precedence_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers.config import _resolve_probe_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert _resolve_probe_key("api", None, None, None) == "env-key"
