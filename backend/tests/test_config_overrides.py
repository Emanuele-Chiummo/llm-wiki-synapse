"""
Tests for R11-2 config-override layer (ADR-0053).

Acceptance checks:
  AC-R11-2-1 : app_config table supports upsert-by-key (SQLite in-process)
  AC-R11-2-2 : GET /config/app returns correct effective values and sources
  AC-R11-2-3 : PUT /config/app/{key} — valid upsert, invalid key → 400, bad value → 422
  AC-R11-2-4 : DELETE /config/app/{key} reverts to env default
  AC-R11-2-5 : load_overrides + get_effective merge logic (mocked rows)
  EC-M11-13  : empty app_config table ⇒ all sources = "env" (backward-compat)
  Forward-compat: unknown key in table is ignored on load
  Per-key validation rules (ADR-0053 §2.3)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Helpers ──────────────────────────────────────────────────────────────────


async def _noop_lifespan(app_: Any) -> Any:  # noqa: ANN401
    """Suppress real lifespan events in tests."""
    yield


def _make_client() -> AsyncClient:
    from contextlib import asynccontextmanager as acm

    from app.main import app
    from fastapi import FastAPI

    @acm
    async def _test_lifespan(app: FastAPI) -> Any:
        yield

    app.router.lifespan_context = _test_lifespan
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─────────────────────────────────────────────────────────────────────────────
# config_overrides module unit tests (no HTTP)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_overrides_empty_table_is_backward_compat() -> None:
    """EC-M11-13: empty app_config ⇒ get_effective returns env defaults for all keys."""
    import app.config_overrides as co

    # Patch load_overrides to simulate an empty table result
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([]))  # no rows
    mock_session.execute = AsyncMock(return_value=mock_result)

    await co.load_overrides(mock_session)

    # After loading empty table, all keys return their env defaults
    from app.config import settings

    assert co.get_effective("pdf_extractor", settings.pdf_extractor) == settings.pdf_extractor
    assert co.source_of("pdf_extractor") == "env"


@pytest.mark.asyncio
async def test_load_overrides_caches_known_keys() -> None:
    """AC-R11-2-5: load_overrides caches allowed keys; get_effective returns override."""
    import app.config_overrides as co

    mock_session = AsyncMock()
    mock_result = MagicMock()
    # Simulate two rows: one known, one unknown (forward-compat)
    mock_result.__iter__ = MagicMock(
        return_value=iter(
            [
                ("pdf_extractor", "marker"),  # allowed → cached
                ("unknown_future_key", "some_value"),  # not allowed → ignored
            ]
        )
    )
    mock_session.execute = AsyncMock(return_value=mock_result)

    await co.load_overrides(mock_session)

    # Known key: override wins
    assert co.get_effective("pdf_extractor", "pypdf") == "marker"
    assert co.source_of("pdf_extractor") == "override"
    # Unknown key: silently ignored — never applied
    assert co.get_override("unknown_future_key") is None


@pytest.mark.asyncio
async def test_load_overrides_missing_table_tolerates_gracefully() -> None:
    """ADR-0053 §2.6 belt-and-braces: missing table → env governs, no crash."""
    import app.config_overrides as co

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception('relation "app_config" does not exist'))

    # Must not raise
    await co.load_overrides(mock_session)

    # After failure, env default governs
    assert co.get_effective("pdf_extractor", "pypdf") == "pypdf"


@pytest.mark.asyncio
async def test_effective_bool_coercion() -> None:
    """AC-R11-2-5: typed bool accessor coerces stored string correctly."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache["embeddings_enabled"] = "false"
    assert co.effective_bool("embeddings_enabled", True) is False

    async with co._cache_lock:
        co._cache["embeddings_enabled"] = "true"
    assert co.effective_bool("embeddings_enabled", False) is True

    # Missing key → default
    async with co._cache_lock:
        co._cache.pop("embeddings_enabled", None)
    assert co.effective_bool("embeddings_enabled", True) is True


@pytest.mark.asyncio
async def test_effective_float_coercion() -> None:
    """effective_float coerces stored string correctly; falls back on malformed."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache["marker_timeout_seconds"] = "60.5"
    assert co.effective_float("marker_timeout_seconds", 120.0) == pytest.approx(60.5)

    # Malformed → fallback to default
    async with co._cache_lock:
        co._cache["marker_timeout_seconds"] = "not_a_float"
    assert co.effective_float("marker_timeout_seconds", 120.0) == pytest.approx(120.0)


def test_validate_value_pdf_extractor() -> None:
    """ADR-0053 §2.3: pdf_extractor must be 'pypdf' or 'marker'."""
    from app.config_overrides import validate_value

    assert validate_value("pdf_extractor", "marker") is None
    assert validate_value("pdf_extractor", "pypdf") is None
    assert validate_value("pdf_extractor", "invalid") is not None


def test_validate_value_marker_url() -> None:
    """marker_service_url must start with http:// or https://."""
    from app.config_overrides import validate_value

    assert validate_value("marker_service_url", "http://host.docker.internal:8555") is None
    assert validate_value("marker_service_url", "https://example.com") is None
    assert validate_value("marker_service_url", "ftp://bad") is not None


def test_validate_value_marker_timeout() -> None:
    """marker_timeout_seconds must be float > 0 and <= 3600."""
    from app.config_overrides import validate_value

    assert validate_value("marker_timeout_seconds", "120.0") is None
    assert validate_value("marker_timeout_seconds", "1") is None
    assert validate_value("marker_timeout_seconds", "3600") is None
    assert validate_value("marker_timeout_seconds", "0") is not None
    assert validate_value("marker_timeout_seconds", "3601") is not None
    assert validate_value("marker_timeout_seconds", "abc") is not None


def test_validate_value_cost_threshold() -> None:
    """cost_alert_threshold_usd must be float >= 0."""
    from app.config_overrides import validate_value

    assert validate_value("cost_alert_threshold_usd", "5.0") is None
    assert validate_value("cost_alert_threshold_usd", "0") is None
    assert validate_value("cost_alert_threshold_usd", "-1") is not None


def test_validate_value_bools() -> None:
    """embeddings_enabled and wikilink_enrich_enabled must be 'true' or 'false'."""
    from app.config_overrides import validate_value

    for key in ("embeddings_enabled", "wikilink_enrich_enabled"):
        assert validate_value(key, "true") is None
        assert validate_value(key, "false") is None
        assert validate_value(key, "True") is None  # case-insensitive
        assert validate_value(key, "yes") is None
        assert validate_value(key, "maybe") is not None


def test_validate_value_embedding_format() -> None:
    """embedding_format must be 'ollama' or 'openai'."""
    from app.config_overrides import validate_value

    assert validate_value("embedding_format", "ollama") is None
    assert validate_value("embedding_format", "openai") is None
    assert validate_value("embedding_format", "other") is not None


def test_validate_value_overview_language() -> None:
    """overview_language: non-empty string ok; empty fails (DELETE is the right path)."""
    from app.config_overrides import validate_value

    assert validate_value("overview_language", "en") is None
    assert validate_value("overview_language", "it") is None
    assert validate_value("overview_language", "") is not None


# ─────────────────────────────────────────────────────────────────────────────
# HTTP endpoint tests: GET /config/app, PUT /config/app/{key},
#                     DELETE /config/app/{key}
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_config_app_all_env_sources() -> None:
    """AC-R11-2-2a: with no overrides, all 13 settings have source='env'."""
    import app.config_overrides as co

    # Ensure cache is clean (env-only)
    async with co._cache_lock:
        co._cache.clear()

    async with _make_client() as client:
        resp = await client.get("/config/app")

    assert resp.status_code == 200
    body = resp.json()
    settings_list = body["settings"]
    assert (
        len(settings_list) == 13
    )  # S1..S13 (S9=domain_vocabulary ADR-0054; S10/S11=schedule R12-7/A5; S12=schema_review R12-8; S13=reclassify R12-9)
    for entry in settings_list:
        assert entry["source"] == "env", f"Expected source=env for {entry['key']}"


@pytest.mark.asyncio
async def test_get_config_app_stable_order() -> None:
    """GET /config/app returns keys in ORDERED_KEYS order (stable for FE snapshot tests)."""
    import app.config_overrides as co
    from app.config_overrides import ORDERED_KEYS

    async with co._cache_lock:
        co._cache.clear()

    async with _make_client() as client:
        resp = await client.get("/config/app")

    assert resp.status_code == 200
    keys = [s["key"] for s in resp.json()["settings"]]
    assert keys == ORDERED_KEYS


@pytest.mark.asyncio
async def test_put_config_app_valid_key_upserts() -> None:
    """AC-R11-2-3: PUT with valid key and value → 204; subsequent GET shows source='override'."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache.clear()

    # Mock the DB session for the upsert
    mock_row = MagicMock()
    mock_row.value = "pypdf"
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none.return_value = None  # no existing row
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_scalar)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.main.get_session", return_value=mock_ctx):
        async with _make_client() as client:
            resp = await client.put(
                "/config/app/pdf_extractor",
                json={"value": "marker"},
            )

    assert resp.status_code == 204
    # Cache was refreshed by set_override
    assert co.get_override("pdf_extractor") == "marker"
    assert co.source_of("pdf_extractor") == "override"


@pytest.mark.asyncio
async def test_put_config_app_invalid_key_returns_400() -> None:
    """AC-R11-2-3: PUT with a non-allowed key → 400 with error:invalid_key."""
    async with _make_client() as client:
        resp = await client.put(
            "/config/app/DATABASE_URL",  # excluded key
            json={"value": "postgresql://evil"},
        )

    assert resp.status_code == 400
    body = resp.json()
    # FastAPI may wrap in 'detail' or return directly; handle both
    if "detail" in body:
        content = body["detail"]
    else:
        content = body
    assert "invalid_key" in str(content)


@pytest.mark.asyncio
async def test_put_config_app_invalid_value_returns_422() -> None:
    """AC-R11-2-3: PUT with valid key but invalid value → 422, no write."""
    import app.config_overrides as co

    prev_value = co.get_override("pdf_extractor")

    async with _make_client() as client:
        resp = await client.put(
            "/config/app/pdf_extractor",
            json={"value": "not_a_valid_extractor"},
        )

    assert resp.status_code == 422
    # Cache unchanged (no write happened)
    assert co.get_override("pdf_extractor") == prev_value


@pytest.mark.asyncio
async def test_delete_config_app_reverts_to_env() -> None:
    """AC-R11-2-4: DELETE removes override row; setting reverts to env default."""
    import app.config_overrides as co
    from app.config import settings

    # Manually inject an override
    async with co._cache_lock:
        co._cache["pdf_extractor"] = "marker"

    assert co.source_of("pdf_extractor") == "override"

    # Mock the DB session for the DELETE
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.main.get_session", return_value=mock_ctx):
        async with _make_client() as client:
            resp = await client.delete("/config/app/pdf_extractor")

    assert resp.status_code == 204
    # Cache no longer has this key — reverts to env
    assert co.get_override("pdf_extractor") is None
    assert co.source_of("pdf_extractor") == "env"
    assert co.get_effective("pdf_extractor", settings.pdf_extractor) == settings.pdf_extractor


@pytest.mark.asyncio
async def test_delete_config_app_invalid_key_returns_400() -> None:
    """DELETE for a non-allowed key → 400."""
    async with _make_client() as client:
        resp = await client.delete("/config/app/VAULT_PATH")

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_config_app_shows_override_after_put() -> None:
    """AC-R11-2-2b: after PUT, affected key flips to source='override' with new value."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache.clear()
        co._cache["embedding_format"] = "openai"  # inject override directly

    async with _make_client() as client:
        resp = await client.get("/config/app")

    assert resp.status_code == 200
    settings_list = resp.json()["settings"]
    ef_entry = next(s for s in settings_list if s["key"] == "embedding_format")
    assert ef_entry["source"] == "override"
    assert ef_entry["value"] == "openai"
    # All others remain env
    others = [s for s in settings_list if s["key"] != "embedding_format"]
    for entry in others:
        assert entry["source"] == "env"
