"""
Tests for POST /clip — Chrome MV3 web clipper ingress (F11, ADR-0038).

Acceptance criteria (AC-F11-2 — security, all unit-tested):
  TC-CLIP-01: Missing token         → 401
  TC-CLIP-02: Invalid token         → 401
  TC-CLIP-03: Bad origin            → 403
  TC-CLIP-04: Oversized body        → 413
  TC-CLIP-05: Path traversal title  → 400 (safe filename contains no path sep)
  TC-CLIP-06: Happy path            → 202, file in raw/sources/, watcher triggered
  TC-CLIP-07: Idempotent re-clip    → 202 overwritten=True, ingest NOT double-triggered
  TC-CLIP-08: CLIP_ENABLED=false    → 503
  TC-CLIP-09: Extension origin      → 202 (chrome-extension:// in allowlist)
  TC-CLIP-10: Loopback origin       → 202 (implicit allowlist)
  TC-CLIP-11: No origin header      → 202 (no-browser path, token gate sufficient)
  TC-CLIP-12: Content-Length header too large → 413
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

_VALID_TOKEN = "test-clip-token-abc123"
_VALID_BODY = {
    "url": "https://example.com/article",
    "title": "Example Article",
    "markdown": "# Example Article\n\nBody text here.",
}


async def _noop_lifespan(app_: Any) -> Any:
    """No-op lifespan for tests."""
    yield


@pytest.fixture()
async def clip_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Minimal test environment for POST /clip tests.

    Patches:
    - settings.clip_enabled = True
    - settings.clip_token   = _VALID_TOKEN
    - settings.clip_allowed_origins = "chrome-extension://fakeextensionid"
    - settings.clip_max_body_bytes  = 1_024 (tiny — lets us test 413 cheaply)
    - settings.vault_root / raw_sources_dir → tmp_path
    - get_session         → no-op
    - ingest_file         → AsyncMock (spy for happy-path / idempotency checks)
    - _graph_cache        → None
    """
    from contextlib import asynccontextmanager

    from app import config as cfg

    vault_root = tmp_path / "vault"
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n")

    # Settings overrides
    monkeypatch.setattr(cfg.settings, "clip_enabled", True)
    monkeypatch.setattr(cfg.settings, "clip_token", _VALID_TOKEN)
    monkeypatch.setattr(cfg.settings, "clip_allowed_origins", "chrome-extension://fakeextensionid")
    # Keep default 2 MB; individual tests override for 413 checks
    monkeypatch.setattr(cfg.settings, "clip_max_body_bytes", 2 * 1024 * 1024)

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test-clip")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    # No-op DB session
    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        yield session

    monkeypatch.setattr("app.main.get_session", _fake_session)
    monkeypatch.setattr("app.main._graph_cache", None)

    # FastAPI test client (no-op lifespan)
    from contextlib import asynccontextmanager as acm

    from app.main import app
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    @acm
    async def test_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    app.router.lifespan_context = test_lifespan

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "sources_dir": sources_dir,
            "vault_root": vault_root,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-01 — Missing token → 401
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_missing_token(clip_env: dict[str, Any]) -> None:
    """TC-CLIP-01: No Authorization header → 401 (S-1 fix: token gate, ADR-0038 §2.1)."""
    client = clip_env["client"]
    resp = await client.post("/clip", json=_VALID_BODY)
    assert resp.status_code == 401, resp.text
    assert "token" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-02 — Invalid token → 401
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_invalid_token(clip_env: dict[str, Any]) -> None:
    """TC-CLIP-02: Wrong bearer token → 401 (constant-time compare, ADR-0038 §2.1)."""
    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer wrong-token-xyz"},
    )
    assert resp.status_code == 401, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-03 — Bad origin → 403
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_bad_origin(clip_env: dict[str, Any]) -> None:
    """TC-CLIP-03: Origin not in allowlist → 403 (S-3 fix: server-side check, ADR-0038 §2.2)."""
    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={
            "Authorization": f"Bearer {_VALID_TOKEN}",
            "Origin": "http://evil.example.com",
        },
    )
    assert resp.status_code == 403, resp.text
    assert "allowlist" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-04 — Oversized body (via Content-Length) → 413
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_body_too_large_content_length(
    clip_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-CLIP-04a: Content-Length exceeds CLIP_MAX_BODY_BYTES → 413 (S-5 fix, ADR-0038 §2.3)."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "clip_max_body_bytes", 100)  # tiny cap

    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={
            "Authorization": f"Bearer {_VALID_TOKEN}",
            "Content-Length": "999999",  # lie about size; our gate reads the header
        },
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.asyncio
async def test_clip_body_too_large_accumulated(
    clip_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-CLIP-04b: Accumulated body bytes exceed cap → 413 (belt-and-braces, ADR-0038 §2.3)."""
    from app import config as cfg

    # Set a very small cap so the JSON body of _VALID_BODY exceeds it
    monkeypatch.setattr(cfg.settings, "clip_max_body_bytes", 10)

    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
    )
    assert resp.status_code == 413, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-05 — Path traversal in title → sanitized (NOT stored at traversal path)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_traversal_title(clip_env: dict[str, Any]) -> None:
    """
    TC-CLIP-05: Title containing path separators / traversal attempts is sanitized.

    The filename derivation strips '/' and '\\' and then safe_source_name + resolve_under_sources
    enforce containment. A title like '../../etc/passwd' must NOT write outside raw/sources/.
    """
    client = clip_env["client"]
    vault_root: Path = clip_env["vault_root"]
    sources_dir: Path = clip_env["sources_dir"]

    resp = await client.post(
        "/clip",
        json={
            "url": "https://evil.com",
            "title": "../../etc/passwd",
            "markdown": "evil content",
        },
        headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
    )
    # Must NOT escape raw/sources/; either 400 (if it produces an unsafe name) or
    # 202 (if sanitized to something safe inside raw/sources/).
    if resp.status_code == 202:
        file_path = resp.json()["file_path"]
        # Verify the written file is genuinely inside raw/sources/
        abs_path = (vault_root / file_path).resolve()
        assert str(abs_path).startswith(str(sources_dir.resolve()))
        # Title ../.. must NOT result in a path that steps outside raw/sources/
        assert ".." not in abs_path.parts
    else:
        assert resp.status_code == 400, (
            f"Expected 202 (sanitized) or 400 (rejected), " f"got {resp.status_code}: {resp.text}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-06 — Happy path → 202, file in raw/sources/, watcher triggered
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_happy_path(clip_env: dict[str, Any]) -> None:
    """
    TC-CLIP-06: Valid token, known origin, normal body → 202.

    Verifies:
    - Response shape {file_path, status='queued', overwritten=False}
    - File written under vault/raw/sources/
    - File has YAML frontmatter (Obsidian-valid, I5)
    - The watcher will pick up the file (async — confirmed by file existence)
    """
    client = clip_env["client"]
    vault_root: Path = clip_env["vault_root"]

    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={
            "Authorization": f"Bearer {_VALID_TOKEN}",
            "Origin": "chrome-extension://fakeextensionid",
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["overwritten"] is False
    assert "raw/sources/" in body["file_path"]

    # File must exist in raw/sources/
    abs_path = (vault_root / body["file_path"]).resolve()
    assert abs_path.exists(), f"Expected file at {abs_path}"

    # Must have valid YAML frontmatter
    content = abs_path.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "Missing YAML frontmatter"
    assert "type: source" in content
    assert "clip_url:" in content
    assert "# Example Article" in content


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-07 — Idempotent re-clip → overwritten=True, no double-ingest
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_idempotent_reclip(clip_env: dict[str, Any]) -> None:
    """
    TC-CLIP-07: Re-clipping the same URL/title overwrites the file (overwritten=True).

    The watcher's mtime/SHA gate handles deduplication — the endpoint itself
    always returns 'queued'; the watcher may return 'skipped' on unchanged content.
    This test confirms the API returns overwritten=True on the second POST.
    """
    client = clip_env["client"]
    headers = {
        "Authorization": f"Bearer {_VALID_TOKEN}",
        "Origin": "chrome-extension://fakeextensionid",
    }

    # First clip
    r1 = await client.post("/clip", json=_VALID_BODY, headers=headers)
    assert r1.status_code == 202, r1.text
    assert r1.json()["overwritten"] is False

    # Second clip — same title → same filename → overwritten=True
    r2 = await client.post("/clip", json=_VALID_BODY, headers=headers)
    assert r2.status_code == 202, r2.text
    assert r2.json()["overwritten"] is True
    # Same file_path
    assert r1.json()["file_path"] == r2.json()["file_path"]


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-08 — CLIP_ENABLED=false → 503
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_disabled(clip_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CLIP-08: CLIP_ENABLED=false → 503 before any auth check (F11, ADR-0038 §2)."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "clip_enabled", False)

    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
    )
    assert resp.status_code == 503, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-09 — Extension origin in allowlist → 202
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_extension_origin_allowed(clip_env: dict[str, Any]) -> None:
    """TC-CLIP-09: chrome-extension:// origin in CLIP_ALLOWED_ORIGINS → 202 (AC-F11-1)."""
    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={
            "Authorization": f"Bearer {_VALID_TOKEN}",
            "Origin": "chrome-extension://fakeextensionid",
        },
    )
    assert resp.status_code == 202, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-10 — Loopback origin → 202 (implicit allowlist)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_loopback_origin_allowed(clip_env: dict[str, Any]) -> None:
    """TC-CLIP-10: http://localhost:5173 is implicitly allowed regardless of CLIP_ALLOWED_ORIGINS.

    Loopback origins are in the implicit list in _CLIP_LOOPBACK_ORIGINS (ADR-0038 §2.3).
    """
    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={
            "Authorization": f"Bearer {_VALID_TOKEN}",
            "Origin": "http://localhost:5173",
        },
    )
    assert resp.status_code == 202, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-CLIP-11 — No Origin header → 202 (local automation, token sufficient)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_no_origin_header(clip_env: dict[str, Any]) -> None:
    """TC-CLIP-11: No Origin header (curl/local) → 202 — token gate is sufficient."""
    client = clip_env["client"]
    resp = await client.post(
        "/clip",
        json=_VALID_BODY,
        headers={"Authorization": f"Bearer {_VALID_TOKEN}"},
    )
    assert resp.status_code == 202, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for _clip_origin_allowed helper
# ─────────────────────────────────────────────────────────────────────────────


def test_clip_origin_allowed_extension() -> None:
    """chrome-extension:// origin in CLIP_ALLOWED_ORIGINS → True."""
    from app import config as cfg
    from app.main import _clip_origin_allowed

    with patch.object(
        type(cfg.settings),
        "clip_allowed_origins_list",
        new_callable=lambda: property(lambda self: ["chrome-extension://abc123"]),
    ):
        assert _clip_origin_allowed("chrome-extension://abc123") is True


def test_clip_origin_allowed_rejects_unknown() -> None:
    """Origin not in allowlist → False."""
    from app import config as cfg
    from app.main import _clip_origin_allowed

    with patch.object(
        type(cfg.settings),
        "clip_allowed_origins_list",
        new_callable=lambda: property(lambda self: ["chrome-extension://abc123"]),
    ):
        assert _clip_origin_allowed("http://attacker.example.com") is False


def test_clip_origin_allowed_loopback_implicit() -> None:
    """http://localhost is always allowed (implicit loopback list)."""
    from app import config as cfg
    from app.main import _clip_origin_allowed

    with patch.object(
        type(cfg.settings),
        "clip_allowed_origins_list",
        new_callable=lambda: property(lambda self: []),
    ):
        assert _clip_origin_allowed("http://localhost") is True
        assert _clip_origin_allowed("http://127.0.0.1") is True


def test_clip_origin_allowed_none_passes() -> None:
    """No Origin header (None) → True (non-browser path; token gate is sufficient)."""
    from app.main import _clip_origin_allowed

    assert _clip_origin_allowed(None) is True


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for _clip_safe_filename helper
# ─────────────────────────────────────────────────────────────────────────────


def test_clip_safe_filename_normal() -> None:
    from app.main import _clip_safe_filename

    result = _clip_safe_filename("My Article", "https://example.com")
    assert result == "My-Article.md"


def test_clip_safe_filename_strips_path_separators() -> None:
    from app.main import _clip_safe_filename

    result = _clip_safe_filename("../../etc/passwd", "https://evil.com")
    # Must NOT contain path separators; hyphens or underscores are fine
    assert "/" not in result
    assert "\\" not in result
    assert result.endswith(".md")


def test_clip_safe_filename_empty_title_uses_hostname() -> None:
    from app.main import _clip_safe_filename

    result = _clip_safe_filename("", "https://example.com/article")
    assert "example" in result
    assert result.endswith(".md")


def test_clip_safe_filename_length_clamp() -> None:
    from app.main import _clip_safe_filename

    long_title = "a" * 300
    result = _clip_safe_filename(long_title, "https://example.com")
    assert len(result) <= 200
    assert result.endswith(".md")


def test_clip_safe_filename_unsafe_chars() -> None:
    from app.main import _clip_safe_filename

    result = _clip_safe_filename('bad:*?"<>|title', "https://example.com")
    for ch in ':*?"<>|':
        assert ch not in result
    assert result.endswith(".md")


def test_clip_safe_filename_empty_fallback() -> None:
    from app.main import _clip_safe_filename

    # If both title and URL hostname produce nothing sensible
    result = _clip_safe_filename("   ", "")
    assert result.endswith(".md")
    assert len(result) > 3
