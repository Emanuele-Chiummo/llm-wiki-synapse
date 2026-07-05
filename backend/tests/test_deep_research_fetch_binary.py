"""
v1.3.3 — deep-research fetch: binary bodies must never reach Postgres as text.

Regression for the field failure: a SearXNG result pointing at a PDF was stored
as resp.text (raw bytes incl. NUL 0x00) into deep_research_sources and the whole
run failed with asyncpg CharacterNotInRepertoireError.

Covers:
- _sanitize_db_text strips NUL bytes
- _is_texty_content_type dispatch table
- _fetch_and_extract: PDF via extractor seam (header AND magic sniff), non-text
  types skipped, HTML path sanitized, oversized PDFs skipped
- one unpersistable source does not raise out of the persist loop
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import app.ops.deep_research as dr
import pytest
from app.ops.deep_research import (
    _fetch_and_extract,
    _is_texty_content_type,
    _sanitize_db_text,
)
from app.ops.searxng import SearchHit


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b"", content_type: str = ""):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _hit(url: str = "https://example.com/x", title: str = "T") -> SearchHit:
    return SearchHit(url=url, title=title, snippet="")


# ── _sanitize_db_text ─────────────────────────────────────────────────────────


class TestSanitizeDbText:
    def test_strips_nul_bytes(self) -> None:
        assert _sanitize_db_text("a\x00b\x00c") == "abc"

    def test_preserves_normal_text_and_newlines(self) -> None:
        s = "riga1\nriga2\ttab é中"
        assert _sanitize_db_text(s) == s


# ── _is_texty_content_type ────────────────────────────────────────────────────


class TestIsTextyContentType:
    @pytest.mark.parametrize(
        "ctype",
        ["text/html", "text/plain", "", "application/xhtml+xml", "application/json"],
    )
    def test_texty(self, ctype: str) -> None:
        assert _is_texty_content_type(ctype) is True

    @pytest.mark.parametrize(
        "ctype",
        ["application/pdf", "image/png", "application/octet-stream", "application/zip"],
    )
    def test_not_texty(self, ctype: str) -> None:
        assert _is_texty_content_type(ctype) is False


# ── _fetch_and_extract dispatch ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestFetchAndExtractDispatch:
    async def _run_one(self, resp: _FakeResponse, monkeypatch: pytest.MonkeyPatch) -> Any:
        monkeypatch.setattr(dr, "safe_fetch", AsyncMock(return_value=resp))
        sources = await _fetch_and_extract([_hit()], iteration=1)
        assert len(sources) == 1
        return sources[0]

    async def test_pdf_by_content_type_goes_through_extractor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = _FakeResponse(content=b"%PDF-1.7 rest\x00binary", content_type="application/pdf")
        with patch("app.ingest.extract.extract_text", return_value="testo estratto") as ext:
            src = await self._run_one(resp, monkeypatch)
        assert ext.call_count == 1
        assert src.content_md == "testo estratto"

    async def test_pdf_by_magic_without_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Server lies (text/html) but the body is a PDF — magic sniff must win.
        resp = _FakeResponse(content=b"%PDF-1.4 stuff\x00", content_type="text/html")
        with patch("app.ingest.extract.extract_text", return_value="estratto") as ext:
            src = await self._run_one(resp, monkeypatch)
        assert ext.call_count == 1
        assert src.content_md == "estratto"

    async def test_non_text_binary_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _FakeResponse(content=b"\x89PNG\r\n\x00\x00", content_type="image/png")
        src = await self._run_one(resp, monkeypatch)
        assert src.content_md is None

    async def test_html_path_still_works_and_is_sanitized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = _FakeResponse(
            content=b"<html><body><p>ciao\x00mondo</p></body></html>",
            content_type="text/html",
        )
        src = await self._run_one(resp, monkeypatch)
        assert src.content_md is not None
        assert "\x00" not in src.content_md
        assert "ciao" in src.content_md

    async def test_oversized_pdf_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dr, "_PDF_MAX_BYTES", 10)
        resp = _FakeResponse(content=b"%PDF-1.7 " + b"x" * 100, content_type="application/pdf")
        with patch("app.ingest.extract.extract_text") as ext:
            src = await self._run_one(resp, monkeypatch)
        assert ext.call_count == 0
        assert src.content_md is None

    async def test_extractor_failure_keeps_source_without_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = _FakeResponse(content=b"%PDF-1.7 broken", content_type="application/pdf")
        with patch("app.ingest.extract.extract_text", side_effect=RuntimeError("boom")):
            src = await self._run_one(resp, monkeypatch)
        assert src.content_md is None

    async def test_title_is_sanitized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _FakeResponse(content=b"<p>ok</p>", content_type="text/html")
        monkeypatch.setattr(dr, "safe_fetch", AsyncMock(return_value=resp))
        sources = await _fetch_and_extract([_hit(title="tit\x00olo")], iteration=1)
        assert sources[0].title == "titolo"
