# ADR-0011 — The ingest contract: Pydantic schemas for Analysis / WikiPage / Message / ProviderCapabilities

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.2
- Decider: solution-architect
- Invariants: I6 (one contract for all backends), I5 (WikiPage carries Obsidian-valid frontmatter), I8 (schemas drive D3)
- Related: ADR-0007, AC-F17-1, AC-F3-1, AC-F3-2; full field tables in v0.2-architecture.md §"Locked schemas"
- Resolves: AQ-v0.2-3 (exact Pydantic schemas)

## Context

`Analysis`, `WikiPage`, `Message`, and `ProviderCapabilities` are referenced across AC-F17-1,
AC-F3-1, AC-F3-2 but never fully specified (AQ-v0.2-3). They are **the** contract between the
orchestrator and every provider; any divergence breaks all three backends. They must be frozen
before provider code begins.

## Decision

The schemas live in `backend/app/ingest/schemas.py` (a new module, not `models.py` — these are
transport DTOs, not SQLAlchemy ORM rows). `Analysis`, `WikiPage`, `Message`,
`SuggestedPage` are **Pydantic v2 `BaseModel`**s (they parse/validate provider output).
`ProviderCapabilities` and `Usage` are **frozen dataclasses** (internal descriptors, no external
parsing). `PageType` is a `str`-`Enum` with members
`entity, concept, source, synthesis, comparison`.

- `WikiPage.frontmatter` is a typed `WikiFrontmatter` BaseModel (not `dict[str, Any]`) so the
  required keys `type, title, sources, lang` are enforced by the schema itself, with `extra`
  allowed for future keys. This makes the validator (ADR-0007 §5) largely a re-use of Pydantic
  validation plus the non-empty-`sources` business rule.
- `WikiPage.content` is the Markdown body **without** the frontmatter block; the writer
  serializes `frontmatter` + `content` into the final `.md` file. This keeps the body and the
  metadata independently inspectable and avoids double-parsing.
- `Message` is the minimal `{role: Literal["user","assistant","system"], content: str}` — not
  the full Anthropic Message type — so it stays backend-neutral (I6). It is used only by the
  stubbed `chat()` in v0.2.

The exact, byte-level field tables (types, required/optional, constraints) are recorded in
`docs/sprints/v0.2-architecture.md` under "Locked schemas" and are the normative reference for
ai-agent-engineer. They are reproduced there rather than here to keep one source of truth for
the field list.

## Consequences

- (+) AQ-v0.2-3 resolved; provider implementations and the validator share one typed contract.
- (+) Typed `WikiFrontmatter` enforces I5 (Obsidian-valid frontmatter) at the schema boundary,
  so a malformed page cannot reach the writer.
- (+) Backend-neutral `Message` keeps `chat()` from leaking Anthropic types into the ABC (I6).
- (−) Two DTO styles (Pydantic for parsed I/O, dataclass for internal descriptors) is a minor
  inconsistency; justified by the parse-vs-descriptor distinction and avoids paying Pydantic
  validation cost on a pure capability read.
