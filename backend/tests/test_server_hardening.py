"""Focused security tests for the local/server deployment boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError


def _settings(**overrides: object):
    from app.config import Settings

    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
        "qdrant_url": "http://localhost:6333",
        "embedding_url": "http://localhost:11434/api/embeddings",
        "embedding_dim": 1024,
        "deployment_mode": "local",
        "auth_token": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_local_mode_keeps_zero_config_auth_compatibility() -> None:
    settings = _settings()

    assert settings.deployment_mode == "local"
    assert settings.auth_token == ""


def test_server_mode_rejects_missing_auth_token() -> None:
    with pytest.raises(ValidationError, match="SYNAPSE_AUTH_TOKEN"):
        _settings(deployment_mode="server", auth_token="")


def test_server_mode_rejects_short_auth_token() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        _settings(deployment_mode="server", auth_token="short-token")


def test_server_mode_rejects_low_diversity_auth_token() -> None:
    with pytest.raises(ValidationError, match="random"):
        _settings(deployment_mode="server", auth_token="a" * 64)


def test_server_mode_accepts_strong_auth_token() -> None:
    token = "server-token-with-at-least-32-chars"

    settings = _settings(deployment_mode="server", auth_token=token)

    assert settings.auth_token == token


def test_deployment_mode_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        _settings(deployment_mode="production")


def test_compose_forwards_the_deployment_trust_boundary() -> None:
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "SYNAPSE_DEPLOYMENT_MODE: ${SYNAPSE_DEPLOYMENT_MODE:-local}" in compose
    assert "SYNAPSE_AUTH_TOKEN: ${SYNAPSE_AUTH_TOKEN:-}" in compose
    assert '"${SYNAPSE_BIND_HOST:-127.0.0.1}:8000:8000"' in compose


def test_documented_synapse_env_names_activate_server_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    token = "server-token-with-at-least-32-chars"
    monkeypatch.setenv("SYNAPSE_DEPLOYMENT_MODE", "server")
    monkeypatch.setenv("SYNAPSE_AUTH_TOKEN", token)

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        qdrant_url="http://localhost:6333",
        embedding_url="http://localhost:11434/api/embeddings",
        embedding_dim=1024,
    )

    assert settings.deployment_mode == "server"
    assert settings.auth_token == token


def test_only_minimal_health_endpoint_bypasses_shared_auth() -> None:
    from app.auth import _bypass_auth

    assert _bypass_auth("GET", "/health/live") is True
    assert _bypass_auth("HEAD", "/health/live") is True
    assert _bypass_auth("GET", "/health/detailed") is False
    assert _bypass_auth("HEAD", "/health/detailed") is False


@pytest.mark.asyncio
async def test_health_live_returns_only_minimal_liveness_state() -> None:
    from app.health import router

    app = FastAPI()
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_health_security_matches_runtime_auth_boundary() -> None:
    from app.main import app

    app.openapi_schema = None
    schema = app.openapi()

    live = schema["paths"]["/health/live"]["get"]
    detailed = schema["paths"]["/health/detailed"]["get"]

    assert live["security"] == []
    assert detailed["security"] == [{"BearerAuth": []}]
