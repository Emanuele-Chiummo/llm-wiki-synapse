"""
Infra-free unit tests for the locked ingest schemas + the stubbed chat() contract (ADR-0011,
ADR-0007 §6). No live Ollama/Anthropic/SDK needed.

Coverage:
  - WikiFrontmatter accepts empty/omitted sources[] + omitted lang (ADR-0067 D2 — provenance
    moved to Postgres) and a `related` slug list.
  - WikiFrontmatter accepts a valid page.
  - WikiPage rejects empty content / empty title.
  - chat() raises NotImplementedError on ALL THREE providers (AC-F17-2).
  - Usage / ProviderCapabilities are frozen dataclasses.
"""

from __future__ import annotations

import dataclasses

import pytest
from app.ingest.provider.api import ApiProvider
from app.ingest.provider.cli import CliAgentProvider
from app.ingest.provider.config import ProviderSettings
from app.ingest.provider.ollama import OllamaProvider
from app.ingest.schemas import (
    Analysis,
    Message,
    PageType,
    ProviderCapabilities,
    Usage,
    WikiFrontmatter,
    WikiPage,
)
from pydantic import ValidationError


def _valid_frontmatter() -> WikiFrontmatter:
    return WikiFrontmatter(
        type=PageType.CONCEPT,
        title="Photosynthesis",
        sources=["raw/sources/bio.md"],
        lang="en",
    )


# ── WikiFrontmatter — I5 + ADR-0067 D2 (sources/lang optional; provenance in DB) ────


def test_frontmatter_accepts_empty_sources() -> None:
    """
    ADR-0067 D2: `sources` is OPTIONAL and no longer required non-empty — traceability moved to
    Postgres (pages.sources, injected by the orchestrator). Empty sources must NOT raise.
    """
    fm = WikiFrontmatter(type=PageType.CONCEPT, title="X", sources=[], lang="en")
    assert fm.sources == []


def test_frontmatter_default_sources_is_empty_list() -> None:
    """`sources` defaults to [] when omitted entirely (ADR-0067 D2)."""
    fm = WikiFrontmatter(type=PageType.CONCEPT, title="X", lang="en")
    assert fm.sources == []


def test_frontmatter_sources_cleaned_of_blanks_no_raise() -> None:
    """Blank-only sources are CLEANED to [] (kept-non-blank) rather than raising (ADR-0067 D2)."""
    fm = WikiFrontmatter(type=PageType.CONCEPT, title="X", sources=["", "   "], lang="en")
    assert fm.sources == []


def test_frontmatter_default_lang_when_missing() -> None:
    """ADR-0067 D2: `lang` is OPTIONAL and defaults to 'en' (still min_length 2 when present)."""
    fm = WikiFrontmatter(type=PageType.CONCEPT, title="X", sources=["raw/sources/bio.md"])
    assert fm.lang == "en"


def test_frontmatter_short_lang_still_rejected() -> None:
    """A present but too-short `lang` is still rejected (min_length 2)."""
    with pytest.raises(ValidationError):
        WikiFrontmatter(type=PageType.CONCEPT, title="X", lang="e")


def test_frontmatter_accepts_related_list() -> None:
    """ADR-0067 D2: `related` is a first-class list of page slugs (deduped, blanks dropped)."""
    fm = WikiFrontmatter(
        type=PageType.CONCEPT,
        title="X",
        related=["aws", "aws", "  s3  ", ""],
    )
    # Deduped (first wins), trimmed, blanks dropped, NOT lowercased.
    assert fm.related == ["aws", "s3"]


def test_frontmatter_related_defaults_empty() -> None:
    fm = _valid_frontmatter()
    assert fm.related == []


def test_frontmatter_accepts_valid() -> None:
    fm = _valid_frontmatter()
    assert fm.sources == ["raw/sources/bio.md"]
    assert fm.lang == "en"


def test_frontmatter_allows_extra_keys() -> None:
    fm = WikiFrontmatter(
        type=PageType.ENTITY,
        title="Marie Curie",
        sources=["raw/sources/curie.md"],
        lang="en",
        tags=["scientist"],  # extra key — allowed (extra="allow")
    )
    assert fm.model_dump().get("tags") == ["scientist"]


# ── WikiPage ─────────────────────────────────────────────────────────────────────


def test_wikipage_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        WikiPage(title="T", type=PageType.CONCEPT, content="", frontmatter=_valid_frontmatter())


def test_wikipage_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        WikiPage(title="", type=PageType.CONCEPT, content="body", frontmatter=_valid_frontmatter())


def test_wikipage_valid() -> None:
    page = WikiPage(
        title="Photosynthesis",
        type=PageType.CONCEPT,
        content="Body text.",
        frontmatter=_valid_frontmatter(),
    )
    assert page.type is PageType.CONCEPT


# ── Analysis ─────────────────────────────────────────────────────────────────────


def test_analysis_requires_topics_and_suggested_pages() -> None:
    with pytest.raises(ValidationError):
        Analysis(topics=[], entities=[], language="en", suggested_pages=[])


def test_analysis_valid() -> None:
    a = Analysis.model_validate(
        {
            "topics": ["biology"],
            "entities": ["chloroplast"],
            "language": "en",
            "suggested_pages": [{"title": "Photosynthesis", "type": "concept"}],
        }
    )
    assert a.suggested_pages[0].type is PageType.CONCEPT


# ── Frozen descriptors ───────────────────────────────────────────────────────────


def test_usage_is_frozen_dataclass() -> None:
    u = Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.0)
    assert dataclasses.is_dataclass(u)
    with pytest.raises(dataclasses.FrozenInstanceError):
        u.input_tokens = 99  # type: ignore[misc]


def test_capabilities_is_frozen_dataclass() -> None:
    c = ProviderCapabilities(
        mode="local",
        supports_tools=False,
        supports_agentic_loop=False,
        max_context=8192,
        name="X",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.supports_agentic_loop = True  # type: ignore[misc]


# ── chat() — real bodies in v0.4 (F6, ADR-0019 supersedes the ADR-0007 §6 stub) ──


def _all_three_providers(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    cfg = ProviderSettings(provider_type="local", model_id="dummy-model")
    ollama = OllamaProvider(cfg)
    api = ApiProvider(ProviderSettings(provider_type="api", model_id="dummy-model"))
    cli = CliAgentProvider(ProviderSettings(provider_type="cli", model_id="dummy-model"))
    return [ollama, api, cli]


@pytest.mark.asyncio
async def test_chat_returns_async_iterator_for_local_and_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ADR-0019 (M4 Phase 3) fills the ollama/api chat() bodies (non-breaking ABC change,
    ADR-0007 §6). chat() now returns an async iterator and performs NO network I/O until it is
    iterated — so simply CALLING it (without iterating) must not raise and must not connect.
    """
    import inspect

    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    ollama = OllamaProvider(ProviderSettings(provider_type="local", model_id="dummy-model"))
    api = ApiProvider(ProviderSettings(provider_type="api", model_id="dummy-model"))
    for provider in (ollama, api):
        result = provider.chat([Message(role="user", content="hi")], "")  # type: ignore[attr-defined]
        agen = await result if inspect.isawaitable(result) else result
        assert hasattr(agen, "__anext__"), "chat() must yield an async iterator (F6)"
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            await aclose()  # close without iterating → no network call


@pytest.mark.asyncio
async def test_chat_cli_no_longer_notimplemented_clean_config_error_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    S-F17-1 (ADR-0022 §2.7) removed the M4 NotImplementedError stub: CliAgentProvider.chat() is
    now a delegated streaming chat. With no auth configured (no ANTHROPIC_API_KEY, no
    CLAUDE_CODE_OAUTH_TOKEN, no CLAUDE_CODE_USE_SUBSCRIPTION) it raises a CLEAN pre-stream config
    error (ValueError) naming the auth options — never NotImplementedError, never a fake stream
    (Do-NOT #9). Full chat + auth behavior is covered in test_cli_chat.py / test_cli_auth.py.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    # A claude-* model so the CLI reaches the auth check: the `_assert_claude_model` guard now
    # rejects non-Claude model ids (codex/OpenAI CLI unsupported) BEFORE the auth check, so a
    # placeholder like "dummy-model" would fail on the wrong error. We want the auth-missing path.
    cli = CliAgentProvider(
        ProviderSettings(provider_type="cli", model_id="claude-haiku-4-5-20251001")
    )
    with pytest.raises(ValueError, match="CLAUDE_CODE_USE_SUBSCRIPTION"):
        await cli.chat([Message(role="user", content="hi")], "")  # type: ignore[attr-defined]


def test_capabilities_routing_signal_per_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    ollama, api, cli = _all_three_providers(monkeypatch)
    assert ollama.capabilities().supports_agentic_loop is False  # type: ignore[attr-defined]
    assert api.capabilities().supports_agentic_loop is False  # type: ignore[attr-defined]
    assert cli.capabilities().supports_agentic_loop is True  # type: ignore[attr-defined]
