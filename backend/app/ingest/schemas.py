"""
Locked ingest contract DTOs (ADR-0011, v0.2-architecture.md §2). Pydantic v2 for parsed
provider output; frozen dataclasses for internal descriptors that are never parsed from
external input.

This module is THE contract between the orchestrator and every InferenceProvider backend
(I6). Any divergence here breaks all three backends — change only via ADR.

Styles (deliberate, per ADR-0011):
  - Analysis / SuggestedPage / WikiPage / WikiFrontmatter / Message  → Pydantic v2 BaseModel
    (they parse + validate provider output; WikiFrontmatter enforces I5 at the boundary).
  - ProviderCapabilities / Usage                                     → frozen dataclass
    (internal descriptors; no external parsing; no Pydantic validation cost).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── PageType ────────────────────────────────────────────────────────────────────


class PageType(StrEnum):
    """
    The six user-content wiki page types (v0.2-architecture.md §2). A str-Enum (ADR-0011):
    `PageType.CONCEPT == "concept"` and serializes to its value for Obsidian-valid YAML (I5).

    `overview` and `index` are reserved for the K3/F3 auto-generated catalogue writers and
    are NOT valid `suggested_pages` / `WikiPage` types — the catalogue writers use the
    OVERVIEW_TYPE / INDEX_TYPE string constants below directly, bypassing this enum.

    QUERY is the saved-chat-answer type (G-P0-1, nashsu/llm_wiki F6 parity).
    Written to wiki/queries/<slug>.md by POST /chat/save-to-wiki (never generated
    by an InferenceProvider — it is a human-curated K8 artefact).
    """

    ENTITY = "entity"
    CONCEPT = "concept"
    SOURCE = "source"
    SYNTHESIS = "synthesis"
    COMPARISON = "comparison"
    QUERY = "query"


# Reserved catalogue types — auto-generated only, never provider output (K3/F3/K4).
# These are DELIBERATELY string constants, NOT PageType enum members: the catalogue/log writers
# use them directly so they can never appear as valid `suggested_pages` / `WikiPage` types, while
# still being VALID `type:` values on their Page rows for the graph (the graph engine excludes only
# raw/* + type:query, so index/log/overview render as nodes — D4, ADR-0063 §9).
OVERVIEW_TYPE = "overview"
INDEX_TYPE = "index"
LOG_TYPE = "log"

# Map a PageType to its wiki/ subdirectory (matches vault.py bootstrap dirs).
_TYPE_DIR: dict[PageType, str] = {
    PageType.ENTITY: "entities",
    PageType.CONCEPT: "concepts",
    PageType.SOURCE: "sources",
    PageType.SYNTHESIS: "synthesis",
    PageType.COMPARISON: "comparisons",
    PageType.QUERY: "queries",
}


def type_subdir(page_type: PageType) -> str:
    """Return the wiki/ subdirectory name for *page_type* (used by the writer)."""
    return _TYPE_DIR[page_type]


# ── Analysis (two-step CoT, step 1 output — F3) ─────────────────────────────────


class SuggestedPage(BaseModel):
    """One page the analysis step proposes generating (v0.2-architecture.md §2)."""

    title: str = Field(..., min_length=1)
    type: PageType
    rationale: str | None = None


class Analysis(BaseModel):
    """
    Output of `InferenceProvider.analyze()` — the analysis half of the two-step CoT (F3).

    Produced ONCE per ingest run (AQ-v0.2-1); only `generate()` is retried.
    """

    topics: list[str] = Field(..., min_length=1)
    entities: list[str] = Field(default_factory=list)
    language: str = Field(..., min_length=2, description="ISO-639-1, e.g. 'en', 'it'")
    suggested_pages: list[SuggestedPage] = Field(..., min_length=1)
    summary: str | None = None


# ── WikiFrontmatter / WikiPage (step 2 output — F3, I5) ─────────────────────────


class WikiFrontmatter(BaseModel):
    """
    Typed YAML frontmatter enforcing I5 (Obsidian-valid) at the schema boundary.
    `extra="allow"` keeps future / source-only frontmatter keys (e.g. authors, year, url,
    venue) round-trippable.

    ADR-0067 D2 (LLM Wiki 1:1 frontmatter parity) amends F3: the on-disk generated-page
    frontmatter now MIRRORS LLM Wiki's byte-shape — `type, title, created, updated, tags,
    related` (+ `authors, year, url, venue` on source pages) — and NO LONGER emits `sources`
    or `lang` keys. Traceability (F3) is preserved in Postgres: the ingest pipeline still
    populates `pages.sources`/`links` (the graph source-overlap ×4 signal F4 and cascade-delete
    F13 read the DB, not the file). Consequently `sources` and `lang` become OPTIONAL on this
    model (default `[]` / `"en"`): the object + the DB write still carry them, but the serializer
    drops them from the .md. `related` is a first-class list of resolvable page slugs — a second
    graph-edge seed (F4) alongside `[[wikilinks]]`.
    """

    model_config = ConfigDict(extra="allow")

    type: PageType
    title: str = Field(..., min_length=1)
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "F3 provenance carried on the object + written to Postgres (pages.sources). "
            "ADR-0067 D2: no longer emitted in the .md and no longer required non-empty "
            "(cleaned of blanks at the boundary; the orchestrator injects the ingest origin)."
        ),
    )
    lang: str = Field(
        default="en",
        min_length=2,
        description="ISO-639-1; == Analysis.language. ADR-0067 D2: optional, not emitted in .md.",
    )
    related: list[str] = Field(
        default_factory=list,
        description=(
            "ADR-0067 D2: list of page SLUGS this page relates to (resolved outbound wikilinks + "
            "top graph neighbours; the orchestrator emits only resolvable slugs, capped ~8). A "
            "second F4 graph-edge seed. Normalized at the boundary: stringified, trimmed, "
            "de-duplicated (order preserved), blanks dropped — NOT lowercased (slugs are already "
            "lowercase). Serializes as a YAML list (I5)."
        ),
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional K6 navigation tags (nashsu/llm_wiki parity). Additive; absent → []. "
            "Normalized at the schema boundary: trimmed, lowercased, de-duplicated (order "
            "preserved), each ≤ 40 chars, capped at 12 tags. Serializes as a YAML list (I5)."
        ),
    )

    @field_validator("sources")
    @classmethod
    def _sources_clean_strings(cls, v: list[str]) -> list[str]:
        """
        Clean the sources list (ADR-0067 D2): keep only non-blank strings. NO LONGER raises on
        an empty list — traceability lives in Postgres (pages.sources), which the orchestrator
        always populates from the ingest origin regardless of what the model emitted.
        """
        return [s for s in v if isinstance(s, str) and s.strip()]

    @field_validator("related", mode="before")
    @classmethod
    def _normalize_related(cls, v: object) -> list[str]:
        """
        Coerce → clean the related-slug list (ADR-0067 D2). Absent/None → []. Accepts a scalar
        string (one slug) or a list. Each item is stringified + trimmed; blanks dropped; the list
        de-duplicated (first occurrence wins, order preserved). NOT lowercased — slugs are already
        lowercase. Never raises (navigation/graph metadata, not the F3 guarantee). The write-time
        cap (≤ 8, resolvable slugs only) is applied by the orchestrator, not here.
        """
        if v is None:
            return []
        items = v if isinstance(v, (list, tuple)) else [v]
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item is None:
                continue
            slug = str(item).strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            cleaned.append(slug)
        return cleaned

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, v: object) -> list[str]:
        """
        Coerce → clean → cap the tags list (K6 navigation, additive/backward-compatible).

        Absent/None → []. Accepts a scalar string (treated as one tag) or a list. Each item is
        stringified, trimmed, lowercased; blanks dropped; each truncated to ≤ 40 chars; the list
        de-duplicated (first occurrence wins, order preserved) and capped at 12 tags. Never
        raises — a malformed value degrades to a best-effort clean list so tags never fail a
        page (they are navigation metadata, not the F3 traceability guarantee).
        """
        max_tags = 12
        max_len = 40
        if v is None:
            return []
        items = v if isinstance(v, (list, tuple)) else [v]
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item is None:
                continue
            tag = str(item).strip().lower()[:max_len].strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
            if len(cleaned) >= max_tags:
                break
        return cleaned


class WikiPage(BaseModel):
    """
    A single generated wiki page (output of `InferenceProvider.generate()`).

    `content` is the Markdown body WITHOUT the frontmatter block; the writer serializes
    `frontmatter` + `content` into the final `.md` file (ADR-0011).
    """

    title: str = Field(..., min_length=1)
    type: PageType
    content: str = Field(..., min_length=1)
    frontmatter: WikiFrontmatter


# ── Message / MessageImage (chat() — backend-neutral, I6) ───────────────────────


class MessageImage(BaseModel):
    """
    One image attachment on a chat Message (B2-C1, F17/I6 vision surface).

    Backend-neutral: `mime` is the IANA media type (e.g. "image/png") and `data_base64` is the
    already-base64-encoded image payload (NOT a data URI — no "data:...;base64," prefix). Each
    provider that advertises `capabilities().supports_vision` maps these two fields into its own
    multimodal format in chat() (Anthropic image blocks, OpenAI `image_url` data URIs, Ollama
    `images[]`). Size is capped UPSTREAM (CHAT_MAX_IMAGE_BYTES) before it reaches this DTO; the
    provider layer never re-validates size and NEVER logs `data_base64` (it can be large).
    """

    mime: str = Field(..., min_length=1, description="IANA media type, e.g. 'image/png'")
    data_base64: str = Field(
        ..., min_length=1, description="base64 image payload WITHOUT a data-URI prefix"
    )


class Message(BaseModel):
    """
    Backend-neutral chat message (ADR-0011, B2-C1).

    `images` is additive and defaults to empty, so every existing text-only Message is
    unaffected (backward-compat). Images are carried into a provider's chat() payload ONLY when
    that provider advertises `capabilities().supports_vision`; a non-vision backend drops them
    silently (defense-in-depth — the frontend gates on the same capability, ADR B2-C1).
    """

    role: Literal["user", "assistant", "system"]
    content: str
    images: list[MessageImage] = Field(default_factory=list)


# ── ProviderCapabilities / Usage (frozen descriptors) ───────────────────────────


@dataclass(frozen=True)
class ProviderCapabilities:
    """
    Immutable provider descriptor. `supports_agentic_loop` is the ONLY routing signal the
    orchestrator may read (I6); `name`/`mode` are audit metadata, never routing inputs.

    `supports_vision` (R8-2 / F12) advertises whether the backend can caption an image via
    `InferenceProvider.caption_image()`. Defaults to False so the field is additive — providers
    that cannot see images (or whose local model was not pulled with vision weights) keep the
    existing text-only behaviour and the orchestrator falls back to the extract.py placeholder.
    It is NOT a routing input (I6): image captioning is a capability the ingest orchestrator
    checks explicitly for the image-file path, never a branch on class/type.
    """

    mode: Literal["local", "api", "cli"]
    supports_tools: bool
    supports_agentic_loop: bool
    max_context: int
    name: str
    supports_vision: bool = False


@dataclass(frozen=True)
class Usage:
    """
    Uniform per-call token/cost accounting (ADR-0009). `total_cost_usd` is 0.0 for local and
    cli backends by convention. Recorded out-of-band on a run-scoped accumulator (ADR-0007 §1).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
