"""System self-update (R12-3, B1) — update-status check + Watchtower trigger.

Infra-free: httpx is faked, so no network / no real Watchtower / GitHub call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import app.ops.system_update as su
import httpx
import pytest
from app.config import settings


class _FakeResp:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )


def _fake_client_cls(
    *,
    get_resp: _FakeResp | None = None,
    post_resp: _FakeResp | None = None,
    capture: dict[str, Any] | None = None,
) -> type:
    """A stand-in for httpx.AsyncClient whose instances are async context managers."""

    class _Client:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResp:
            if capture is not None:
                capture["get_url"] = url
            assert get_resp is not None
            return get_resp

        async def post(self, url: str, headers: dict[str, str] | None = None) -> _FakeResp:
            if capture is not None:
                capture["post_url"] = url
                capture["post_headers"] = headers
            assert post_resp is not None
            return post_resp

    return _Client


def test_parse_semver() -> None:
    assert su._parse_semver("v1.7.2") == (1, 7, 2)
    assert su._parse_semver("1.7") == (1, 7, 0)
    assert su._parse_semver("1.7.2-rc1") == (1, 7, 2)  # pre-release suffix ignored
    assert su._parse_semver("dev") is None


@pytest.mark.asyncio
async def test_update_available_when_latest_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    su._latest_cache = (0.0, None)
    monkeypatch.setattr(su, "_current_version", lambda: "1.7.1")
    monkeypatch.setattr(
        httpx, "AsyncClient", _fake_client_cls(get_resp=_FakeResp(200, {"tag_name": "v1.7.2"}))
    )
    monkeypatch.setattr(settings, "watchtower_url", None, raising=False)
    monkeypatch.setattr(settings, "watchtower_http_api_token", None, raising=False)

    st = await su.get_update_status()
    assert st.current_version == "1.7.1"
    assert st.latest_version == "1.7.2"
    assert st.update_available is True
    assert st.update_supported is False  # Watchtower not configured → button hidden


@pytest.mark.asyncio
async def test_no_update_when_current_is_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    su._latest_cache = (0.0, None)
    monkeypatch.setattr(su, "_current_version", lambda: "1.7.2")
    monkeypatch.setattr(
        httpx, "AsyncClient", _fake_client_cls(get_resp=_FakeResp(200, {"tag_name": "v1.7.2"}))
    )
    st = await su.get_update_status()
    assert st.update_available is False


@pytest.mark.asyncio
async def test_update_check_survives_github_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate-limit / outage must never break the endpoint — latest is None, not an exception."""
    su._latest_cache = (0.0, None)
    monkeypatch.setattr(su, "_current_version", lambda: "1.7.1")
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client_cls(get_resp=_FakeResp(403)))
    st = await su.get_update_status()
    assert st.latest_version is None
    assert st.update_available is False


@pytest.mark.asyncio
async def test_update_supported_reflects_config(monkeypatch: pytest.MonkeyPatch) -> None:
    su._latest_cache = (0.0, None)
    monkeypatch.setattr(su, "_current_version", lambda: "1.7.2")
    monkeypatch.setattr(
        httpx, "AsyncClient", _fake_client_cls(get_resp=_FakeResp(200, {"tag_name": "v1.7.2"}))
    )
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080", raising=False)
    monkeypatch.setattr(settings, "watchtower_http_api_token", "tok", raising=False)
    st = await su.get_update_status()
    assert st.update_supported is True


@pytest.mark.asyncio
async def test_trigger_not_configured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "watchtower_url", None, raising=False)
    monkeypatch.setattr(settings, "watchtower_http_api_token", None, raising=False)
    with pytest.raises(su.UpdateNotConfiguredError):
        await su.trigger_system_update()


@pytest.mark.asyncio
async def test_trigger_posts_to_watchtower_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080", raising=False)
    monkeypatch.setattr(settings, "watchtower_http_api_token", "secret-token", raising=False)
    monkeypatch.setattr(
        httpx, "AsyncClient", _fake_client_cls(post_resp=_FakeResp(200), capture=capture)
    )
    msg = await su.trigger_system_update()
    assert capture["post_url"] == "http://watchtower:8080/v1/update"
    assert capture["post_headers"]["Authorization"] == "Bearer secret-token"
    assert "triggered" in msg.lower()
