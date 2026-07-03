"""
R8-2 / F12 — vision captions for images.

Tests:
  T-R8V-001  migration 0022 imports; revision/down_revision chain correct; upgrade/downgrade callable
  T-R8V-002  ImageCaption model in Base.metadata with the R8-2 columns + unique (vault_id, sha256)
  T-R8V-003  image_captions table creates on SQLite (portable SQL) and round-trips a row
  T-R8V-004  ProviderCapabilities gains supports_vision (default False)
  T-R8V-005  ApiProvider(Anthropic) supports_vision True; OpenAI-compatible driven by config flag
  T-R8V-006  OllamaProvider supports_vision True for llava/minicpm-v names, False otherwise;
             OLLAMA_VISION_MODELS extends the match
  T-R8V-007  CliAgentProvider supports_vision True
  T-R8V-008  caption_image default raises NotImplementedError on the ABC
  T-R8V-009  maybe_caption_image: cache HIT returns cached caption, NO provider call
  T-R8V-010  maybe_caption_image: cache MISS calls provider once, stores caption, cost on accumulator
  T-R8V-011  maybe_caption_image: VISION_MAX_IMAGES_PER_RUN cap → placeholder (None) beyond cap
  T-R8V-012  maybe_caption_image: provider without supports_vision → None (placeholder)
  T-R8V-013  maybe_caption_image: VISION_CAPTIONS_ENABLED False → None (no provider call)
  T-R8V-014  maybe_caption_image: provider caption failure → None (placeholder, ingest never breaks)

Infra-free: SQLite in-memory + a mock InferenceProvider. No live Postgres/Qdrant/Ollama.
"""

from __future__ import annotations

import importlib.util
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.provider.config import ProviderSettings
from app.ingest.schemas import Analysis, Message, ProviderCapabilities, Usage, WikiPage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 1x1 transparent PNG (magic-byte sniffing path in resolve_image_bytes_and_media_type).
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


# ── Mock provider ────────────────────────────────────────────────────────────────


class _MockVisionProvider(InferenceProvider):
    """Vision-capable mock: counts caption_image calls and records a fixed Usage."""

    def __init__(self, *, supports_vision: bool = True, fail: bool = False) -> None:
        self._supports_vision = supports_vision
        self._fail = fail
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="api",
            supports_tools=True,
            supports_agentic_loop=False,
            max_context=1000,
            name="MockVisionProvider",
            supports_vision=self._supports_vision,
        )

    async def caption_image(self, path_or_bytes: object, context: str) -> str:  # type: ignore[override]
        self.calls += 1
        if self._fail:
            raise RuntimeError("boom")
        self._record_usage(Usage(input_tokens=100, output_tokens=20, total_cost_usd=0.0007))
        return "A tiny transparent test image."

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:  # pragma: no cover
        raise NotImplementedError

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(self, messages: list[Message], retrieval_context: str):  # pragma: no cover
        raise NotImplementedError
        yield ""  # unreachable; makes this an async generator


# ── SQLite env for the cache ─────────────────────────────────────────────────────


@pytest.fixture()
async def sqlite_session_factory():
    """In-memory SQLite with the ImageCaption table created from the ORM model (portable SQL)."""
    from app.models import ImageCaption

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(ImageCaption.__table__.create)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _patch_vision_session(monkeypatch, factory) -> None:
    @asynccontextmanager
    async def _patched():  # type: ignore[no-untyped-def]
        async with factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.ingest.vision.get_session", _patched)


def _patch_resolve_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr("app.ingest.vision.resolve_provider", lambda _row: provider)


def _enable_vision(monkeypatch, *, enabled: bool = True, cap: int = 5) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "vision_captions_enabled", enabled, raising=False)
    monkeypatch.setattr(settings, "vision_max_images_per_run", cap, raising=False)
    monkeypatch.setattr(settings, "vault_id", "test-vault", raising=False)


# ── T-R8V-001/002/003: migration + model ─────────────────────────────────────────


def test_migration_0022_chain() -> None:
    """T-R8V-001: migration 0022 imports; chain + callables correct."""
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0022_image_captions.py"
    )
    assert path.exists(), f"migration not found: {path}"
    spec = importlib.util.spec_from_file_location("migration_0022", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod.revision == "0022"
    assert mod.down_revision == "0021"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_image_caption_model_columns() -> None:
    """T-R8V-002: ImageCaption table + columns + unique constraint present."""
    from app.models import Base

    table = next(t for t in Base.metadata.sorted_tables if t.name == "image_captions")
    cols = {c.name for c in table.columns}
    assert {"id", "vault_id", "sha256", "file_path", "caption", "provider_type", "created_at"} <= cols
    uniques = {tuple(sorted(c.name for c in con.columns)) for con in table.constraints
               if con.__class__.__name__ == "UniqueConstraint"}
    assert ("sha256", "vault_id") in uniques or ("vault_id", "sha256") in uniques


async def test_image_captions_table_roundtrip(sqlite_session_factory) -> None:
    """T-R8V-003: table creates on SQLite and round-trips a row (portable SQL)."""
    from app.models import ImageCaption
    from sqlalchemy import select

    async with sqlite_session_factory() as sess:
        sess.add(
            ImageCaption(
                id=uuid.uuid4(),
                vault_id="v",
                sha256="a" * 64,
                file_path="raw/sources/x.png",
                caption="hello",
                provider_type="api",
            )
        )
        await sess.commit()
    async with sqlite_session_factory() as sess:
        row = (await sess.execute(select(ImageCaption))).scalar_one()
        assert row.caption == "hello"
        assert row.sha256 == "a" * 64


# ── T-R8V-004..008: capability matrix + ABC default ──────────────────────────────


def test_capabilities_supports_vision_default() -> None:
    """T-R8V-004: ProviderCapabilities.supports_vision defaults to False."""
    caps = ProviderCapabilities(
        mode="local", supports_tools=False, supports_agentic_loop=False, max_context=1, name="x"
    )
    assert caps.supports_vision is False


def test_api_provider_supports_vision(monkeypatch) -> None:
    """T-R8V-005: Anthropic → True; OpenAI-compatible → config flag."""
    from app.ingest.provider.api import ApiProvider

    anthropic = ApiProvider(ProviderSettings(provider_type="api", model_id="claude-x"))
    assert anthropic.capabilities().supports_vision is True

    openai_off = ApiProvider(
        ProviderSettings(provider_type="api", model_id="gpt-x", base_url="http://localhost:1234/v1")
    )
    assert openai_off.capabilities().supports_vision is False

    openai_on = ApiProvider(
        ProviderSettings(
            provider_type="api",
            model_id="gemini-x",
            base_url="http://localhost:1234/v1",
            supports_vision=True,
        )
    )
    assert openai_on.capabilities().supports_vision is True


def test_ollama_provider_supports_vision(monkeypatch) -> None:
    """T-R8V-006: vision derived from model name; OLLAMA_VISION_MODELS extends it."""
    from app.ingest.provider.ollama import OllamaProvider

    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.delenv("OLLAMA_VISION_MODELS", raising=False)

    llava = OllamaProvider(ProviderSettings(provider_type="local", model_id="llava:13b"))
    assert llava.capabilities().supports_vision is True

    minicpm = OllamaProvider(ProviderSettings(provider_type="local", model_id="minicpm-v:8b"))
    assert minicpm.capabilities().supports_vision is True

    text_model = OllamaProvider(ProviderSettings(provider_type="local", model_id="qwen2.5:7b"))
    assert text_model.capabilities().supports_vision is False

    monkeypatch.setenv("OLLAMA_VISION_MODELS", "qwen2.5")
    text_now_vision = OllamaProvider(ProviderSettings(provider_type="local", model_id="qwen2.5:7b"))
    assert text_now_vision.capabilities().supports_vision is True


def test_cli_provider_supports_vision() -> None:
    """T-R8V-007: CliAgentProvider advertises vision."""
    from app.ingest.provider.cli import CliAgentProvider

    cli = CliAgentProvider(ProviderSettings(provider_type="cli", model_id="claude-x"))
    assert cli.capabilities().supports_vision is True


async def test_abc_caption_image_default_raises() -> None:
    """T-R8V-008: the ABC default caption_image raises NotImplementedError."""

    class _NoVision(_MockVisionProvider):
        pass

    # Use a provider that does NOT override caption_image: build a minimal one.
    class _Bare(InferenceProvider):
        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                mode="local", supports_tools=False, supports_agentic_loop=False,
                max_context=1, name="bare",
            )

        async def analyze(self, source_text: str, vault_context: str) -> Analysis:
            raise NotImplementedError

        async def generate(self, analysis: Analysis, retrieval_context: str) -> list[WikiPage]:
            raise NotImplementedError

        async def chat(self, messages: list[Message], retrieval_context: str):
            raise NotImplementedError
            yield ""

    with pytest.raises(NotImplementedError):
        await _Bare().caption_image(_PNG_BYTES, "")


# ── T-R8V-009..014: orchestrator seam (maybe_caption_image) ──────────────────────


async def test_cache_hit_skips_provider(monkeypatch, sqlite_session_factory) -> None:
    """T-R8V-009: a cached (vault, sha256) row is returned without any provider call."""
    from app.ingest.vision import maybe_caption_image, sha256_bytes
    from app.models import ImageCaption

    _enable_vision(monkeypatch)
    _patch_vision_session(monkeypatch, sqlite_session_factory)
    provider = _MockVisionProvider()
    _patch_resolve_provider(monkeypatch, provider)

    digest = sha256_bytes(_PNG_BYTES)
    async with sqlite_session_factory() as sess:
        sess.add(
            ImageCaption(
                id=uuid.uuid4(), vault_id="test-vault", sha256=digest,
                file_path="raw/sources/x.png", caption="CACHED CAPTION", provider_type="api",
            )
        )
        await sess.commit()

    result = await maybe_caption_image(
        provider_config_row=object(), raw_bytes=_PNG_BYTES, origin_source="raw/sources/x.png"
    )
    assert result == "CACHED CAPTION"
    assert provider.calls == 0


async def test_cache_miss_calls_provider_and_stores(monkeypatch, sqlite_session_factory) -> None:
    """T-R8V-010: MISS → one provider call → caption stored → cost on accumulator (I7)."""
    from app.ingest.vision import maybe_caption_image
    from app.models import ImageCaption
    from sqlalchemy import select

    _enable_vision(monkeypatch)
    _patch_vision_session(monkeypatch, sqlite_session_factory)
    provider = _MockVisionProvider()
    _patch_resolve_provider(monkeypatch, provider)

    acc = UsageAccumulator()
    result = await maybe_caption_image(
        provider_config_row=object(),
        raw_bytes=_PNG_BYTES,
        origin_source="raw/sources/x.png",
        accumulator=acc,
    )
    assert result == "A tiny transparent test image."
    assert provider.calls == 1
    assert acc.calls == 1
    assert acc.total_cost_usd == pytest.approx(0.0007)

    async with sqlite_session_factory() as sess:
        rows = (await sess.execute(select(ImageCaption))).scalars().all()
        assert len(rows) == 1
        assert rows[0].caption == "A tiny transparent test image."
        assert rows[0].provider_type == "api"


async def test_run_cap_respected(monkeypatch, sqlite_session_factory) -> None:
    """T-R8V-011: beyond VISION_MAX_IMAGES_PER_RUN the same budget yields None (placeholder)."""
    from app.ingest.vision import VisionRunBudget, maybe_caption_image

    _enable_vision(monkeypatch, cap=2)
    _patch_vision_session(monkeypatch, sqlite_session_factory)
    provider = _MockVisionProvider()
    _patch_resolve_provider(monkeypatch, provider)

    budget = VisionRunBudget(max_images=2)
    results = []
    for i in range(4):
        # Distinct bytes → distinct sha256 → always a cache MISS.
        results.append(
            await maybe_caption_image(
                provider_config_row=object(),
                raw_bytes=_PNG_BYTES + bytes([i]),
                origin_source=f"raw/sources/x{i}.png",
                budget=budget,
            )
        )
    # First two captioned, last two fall back to placeholder (None); provider called exactly twice.
    assert results[0] is not None and results[1] is not None
    assert results[2] is None and results[3] is None
    assert provider.calls == 2


async def test_unsupported_provider_placeholder(monkeypatch, sqlite_session_factory) -> None:
    """T-R8V-012: provider without supports_vision → None (placeholder path)."""
    from app.ingest.vision import maybe_caption_image

    _enable_vision(monkeypatch)
    _patch_vision_session(monkeypatch, sqlite_session_factory)
    provider = _MockVisionProvider(supports_vision=False)
    _patch_resolve_provider(monkeypatch, provider)

    result = await maybe_caption_image(
        provider_config_row=object(), raw_bytes=_PNG_BYTES, origin_source="raw/sources/x.png"
    )
    assert result is None
    assert provider.calls == 0


async def test_disabled_flag_no_call(monkeypatch, sqlite_session_factory) -> None:
    """T-R8V-013: VISION_CAPTIONS_ENABLED False → None, no provider resolution/call."""
    from app.ingest.vision import maybe_caption_image

    _enable_vision(monkeypatch, enabled=False)
    _patch_vision_session(monkeypatch, sqlite_session_factory)
    provider = _MockVisionProvider()
    _patch_resolve_provider(monkeypatch, provider)

    result = await maybe_caption_image(
        provider_config_row=object(), raw_bytes=_PNG_BYTES, origin_source="raw/sources/x.png"
    )
    assert result is None
    assert provider.calls == 0


async def test_provider_failure_falls_back(monkeypatch, sqlite_session_factory) -> None:
    """T-R8V-014: a caption_image failure → None (placeholder); ingest never breaks."""
    from app.ingest.vision import maybe_caption_image
    from app.models import ImageCaption
    from sqlalchemy import select

    _enable_vision(monkeypatch)
    _patch_vision_session(monkeypatch, sqlite_session_factory)
    provider = _MockVisionProvider(fail=True)
    _patch_resolve_provider(monkeypatch, provider)

    result = await maybe_caption_image(
        provider_config_row=object(), raw_bytes=_PNG_BYTES, origin_source="raw/sources/x.png"
    )
    assert result is None
    assert provider.calls == 1
    async with sqlite_session_factory() as sess:
        rows = (await sess.execute(select(ImageCaption))).scalars().all()
        assert rows == []  # nothing cached on failure
