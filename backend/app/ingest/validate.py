"""
Shared ingest validation utilities (2.0.0 — extracted from the deleted loop.py).

``validate_pages`` is the ONE validator for wiki pages (ADR-0010 §2) — reused by the
MCP ``write_page`` tool and the block loop's own post-write checks.
``IngestCancelled`` is the cooperative cancellation signal (ADR-0046 §3).
``augment_context`` is the retry-context builder for the orchestrated loop.
"""

from __future__ import annotations

import re

from app.ingest.schemas import PageType, WikiPage

_QUERY_RESEARCH_HEADING_RE = re.compile(
    r"(?im)^#{1,6}\s*(?:research|search|retrieval)\s+quer(?:y|ies)\s*$"
)
_QUERY_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
_QUERY_GENERIC_TOKENS = frozenset(
    {
        "a",
        "altro",
        "ancora",
        "about",
        "anything",
        "can",
        "cercare",
        "conoscere",
        "cosa",
        "darmi",
        "del",
        "della",
        "details",
        "dettagli",
        "di",
        "dimmi",
        "domanda",
        "dovrei",
        "else",
        "explain",
        "find",
        "fornire",
        "give",
        "i",
        "il",
        "information",
        "informazioni",
        "know",
        "la",
        "learn",
        "le",
        "lo",
        "me",
        "mi",
        "more",
        "out",
        "per",
        "più",
        "please",
        "provide",
        "puoi",
        "qualcosa",
        "question",
        "questo",
        "research",
        "ricerca",
        "sapere",
        "search",
        "should",
        "scoprire",
        "spiega",
        "something",
        "su",
        "tell",
        "the",
        "this",
        "topic",
        "un",
        "una",
        "vorrei",
        "what",
        "you",
    }
)


def _specific_query_terms(value: str) -> set[str]:
    """Return concrete lexical terms usable as a source-grounding proxy (EN/IT UI locales)."""
    normalized = re.sub(r"\W+", " ", value).casefold().strip()
    return {
        token
        for token in normalized.split()
        if len(token) >= 3
        and token not in _QUERY_GENERIC_TOKENS
        and (not token.isdigit() or len(token) >= 4)
    }


# ── Cooperative cancellation exception (ADR-0046 §3) ─────────────────────────


class IngestCancelled(Exception):
    """
    Raised at the top of each orchestrated-loop iteration when the run's
    cancel_event is set (ADR-0046 §3 / I6: never raised inside a provider call).

    Carries the origin_source for logging.  Caught by run_ingest_pipeline in
    pipeline.py, which performs cascade cleanup then finalises the run as
    status="cancelled".
    """

    def __init__(self, origin_source: str) -> None:
        self.origin_source = origin_source
        super().__init__(f"ingest cancelled: {origin_source}")


# ── Shared validator (ADR-0007 §5 / ADR-0010 §2 — ONE validator) ────────────────


def validate_pages(pages: list[WikiPage], origin_source: str) -> list[str]:
    """
    Return a list of human-readable validation errors for *pages* (empty list == valid).

    A batch is INVALID (triggers a retry) if ANY page fails (AQ-v0.2-7):
      • type in the PageType enum;
      • non-empty title;
      • non-empty sources[] that CONTAINS the origin source's relative path (F3 traceability);
      • non-empty lang;
      • non-empty content.
    Dangling wikilinks do NOT invalidate (K5 stores them with dangling=True).

    Because WikiPage is a Pydantic model, most rules are already enforced at parse time; this
    function re-checks them defensively AND adds the business rule "origin path ∈ sources[]"
    that Pydantic alone cannot express. It is the SAME validator the MCP write_page tool calls.
    """
    errors: list[str] = []
    if not pages:
        return ["batch is empty: at least one page is required"]

    for i, page in enumerate(pages):
        prefix = f"page[{i}] ({page.title!r})"
        if not isinstance(page.type, PageType):
            errors.append(f"{prefix}: type {page.type!r} is not a valid PageType")
        if not page.title.strip():
            errors.append(f"{prefix}: title is empty")
        if not page.content.strip():
            errors.append(f"{prefix}: content is empty")
        if page.type is PageType.QUERY:
            heading = _QUERY_RESEARCH_HEADING_RE.search(page.content)
            if heading is None:
                errors.append(f"{prefix}: query page must include a '## Research queries' section")
            else:
                section = page.content[heading.end() :]
                next_heading = re.search(r"(?m)^#{1,6}\s+", section)
                if next_heading is not None:
                    section = section[: next_heading.start()]
                title_norm = re.sub(r"\W+", " ", page.title).casefold().strip()
                # The prose before the query list is the page's source-backed question context.
                # A retrieval query must be lexically anchored to that context and then add a
                # constraint/evidence term. This rejects generic paraphrases without pretending
                # that a finite placeholder dictionary can prove semantic grounding.
                context_terms = _specific_query_terms(
                    f"{page.title}\n{page.content[: heading.start()]}"
                )
                contextual_queries = []
                for candidate in _QUERY_LIST_ITEM_RE.findall(section):
                    candidate_norm = re.sub(r"\W+", " ", candidate).casefold().strip()
                    tokens = candidate_norm.split()
                    candidate_terms = _specific_query_terms(candidate)
                    anchor_terms = candidate_terms & context_terms
                    novel_terms = candidate_terms - context_terms
                    if (
                        candidate_norm == title_norm
                        or len(tokens) < 3
                        or len(candidate_terms) < 3
                        or len(anchor_terms) < 2
                        or not novel_terms
                    ):
                        continue
                    contextual_queries.append(candidate)
                if len(contextual_queries) < 2:
                    errors.append(
                        f"{prefix}: query page requires at least two contextual retrieval "
                        "queries beyond the title"
                    )
        fm = page.frontmatter
        if not fm.sources:
            errors.append(f"{prefix}: frontmatter.sources[] is empty (F3 traceability)")
        elif origin_source and origin_source not in fm.sources:
            errors.append(
                f"{prefix}: frontmatter.sources[] must include the origin path "
                f"{origin_source!r} (F3 traceability)"
            )
        if not fm.lang.strip():
            errors.append(f"{prefix}: frontmatter.lang is empty")
    return errors


def augment_context(retrieval_context: str, errors: list[str]) -> str:
    """
    Append a validation-error block to the retrieval context for the next generate() retry
    (ADR-0007 §4 — augmentation targets generation, not re-analysis).
    """
    block = "\n".join(f"- {e}" for e in errors)
    return (
        f"{retrieval_context}\n\n"
        "# Validation errors from the previous attempt — FIX ALL of these:\n"
        f"{block}\n"
    )
