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
    The five user-content wiki page types (v0.2-architecture.md §2). A str-Enum (ADR-0011):
    `PageType.CONCEPT == "concept"` and serializes to its value for Obsidian-valid YAML (I5).

    `overview` and `index` are reserved for the K3/F3 auto-generated catalogue writers and
    are NOT valid `suggested_pages` / `WikiPage` types — the catalogue writers use the
    OVERVIEW_TYPE / INDEX_TYPE string constants below directly, bypassing this enum.
    """

    ENTITY = "entity"
    CONCEPT = "concept"
    SOURCE = "source"
    SYNTHESIS = "synthesis"
    COMPARISON = "comparison"


# Reserved catalogue types — auto-generated only, never provider output (K3/F3).
OVERVIEW_TYPE = "overview"
INDEX_TYPE = "index"

# Map a PageType to its wiki/ subdirectory (matches vault.py bootstrap dirs).
_TYPE_DIR: dict[PageType, str] = {
    PageType.ENTITY: "entities",
    PageType.CONCEPT: "concepts",
    PageType.SOURCE: "sources",
    PageType.SYNTHESIS: "synthesis",
    PageType.COMPARISON: "comparisons",
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
    Typed YAML frontmatter enforcing I5 (Obsidian-valid) + F3 (traceability) at the schema
    boundary. `extra="allow"` keeps future frontmatter keys (e.g. tags) round-trippable.

    The non-empty `sources` rule (incl. the originating source's relative path) is the F3
    traceability guarantee — a page with empty sources[] is invalid (ADR-0007 §5).
    """

    model_config = ConfigDict(extra="allow")

    type: PageType
    title: str = Field(..., min_length=1)
    sources: list[str] = Field(..., min_length=1)
    lang: str = Field(..., min_length=2, description="ISO-639-1; == Analysis.language")

    @field_validator("sources")
    @classmethod
    def _sources_non_empty_strings(cls, v: list[str]) -> list[str]:
        cleaned = [s for s in v if isinstance(s, str) and s.strip()]
        if not cleaned:
            raise ValueError("sources[] must be a non-empty list of non-empty strings (F3)")
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


# ── Message (stubbed chat() only — backend-neutral, I6) ─────────────────────────


class Message(BaseModel):
    """Minimal backend-neutral chat message (ADR-0011). Used only by stubbed chat()."""

    role: Literal["user", "assistant", "system"]
    content: str


# ── ProviderCapabilities / Usage (frozen descriptors) ───────────────────────────


@dataclass(frozen=True)
class ProviderCapabilities:
    """
    Immutable provider descriptor. `supports_agentic_loop` is the ONLY routing signal the
    orchestrator may read (I6); `name`/`mode` are audit metadata, never routing inputs.
    """

    mode: Literal["local", "api", "cli"]
    supports_tools: bool
    supports_agentic_loop: bool
    max_context: int
    name: str


@dataclass(frozen=True)
class Usage:
    """
    Uniform per-call token/cost accounting (ADR-0009). `total_cost_usd` is 0.0 for local and
    cli backends by convention. Recorded out-of-band on a run-scoped accumulator (ADR-0007 §1).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
