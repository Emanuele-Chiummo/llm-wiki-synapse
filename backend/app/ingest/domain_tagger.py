"""
Domain auto-tagging seam (ADR-0054, F18 / R12-2 — I6/I7).

Classifies a just-written wiki page into a SUBSET of an owner-controlled controlled
vocabulary (the ``domain_vocabulary`` app_config key, §2.1). The classifier may ONLY
choose from the vocabulary; anything it invents is dropped (owner-lock #1, §3.3).

Contract (ADR-0054 §3.3):
  * ONE bounded provider call per page (I7), routed through the resolved ingest provider
    (I6 — never a hardcoded backend/model). Cost flows through the bound ``UsageAccumulator``.
  * Structured output via ``provider.chat()`` → lenient JSON parse (the same backend-neutral
    surface enrich_wikilinks / review / deep_research use — no new ABC method, no isinstance).
  * Output validated STRICTLY against the vocabulary: case-insensitive match to the canonical
    casing; hallucinated / out-of-vocabulary names dropped. 0 domains is a valid result.
  * Deterministic prompt; page content truncated to a sane cap (``_CONTENT_CHAR_CAP``).

This module owns ONLY the classification primitive. The orchestrator hook (merge + write-back)
and the backfill both call :func:`classify_page_domains`.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ingest.provider.base import InferenceProvider
from app.ops._llm import bounded_chat_collect, loads_json_lenient

logger = logging.getLogger(__name__)

# Body slice cap for the classification prompt (ADR-0054 §3.3 — a sane cap, ~4k chars).
_CONTENT_CHAR_CAP = 4_000

# Domain tag prefix (ADR-0054 §2.2 — Obsidian nested tag; membership = "domain/"+D in tags).
DOMAIN_TAG_PREFIX = "domain/"


def is_domain_tag(tag: str) -> bool:
    """True if *tag* is a domain tag (``domain/<Name>`` convention, ADR-0054 §2.2)."""
    return tag.startswith(DOMAIN_TAG_PREFIX)


def has_domain_tag(tags: list[str] | None) -> bool:
    """True if *tags* contains at least one ``domain/*`` entry (backfill idempotency, §4.3)."""
    return any(is_domain_tag(t) for t in (tags or []))


def merge_domain_tags(existing: list[str] | None, classified: list[str]) -> list[str]:
    """
    Merge the classified domains into *existing* tags per ADR-0054 §3.3 (idempotent):

      new tags = (existing tags with every ``domain/*`` entry removed)  [user tags, order kept]
                 ∪ (sorted ``domain/<Name>`` for each classified domain)

    User (non-``domain/``) tags are preserved VERBATIM and first, in their original order;
    the new ``domain/*`` set follows, sorted for a stable, hash-stable result. Re-running with
    the same *classified* on the same page yields the same list (idempotent — required for
    backfill §4.3 and content-hash stability).
    """
    user_tags = [t for t in (existing or []) if not is_domain_tag(t)]
    domain_tags = sorted({DOMAIN_TAG_PREFIX + name for name in classified})
    return user_tags + domain_tags


async def classify_page_domains(
    provider: InferenceProvider,
    page_title: str,
    page_content: str,
    vocabulary: list[str],
) -> list[str]:
    """
    Classify a page into 0..N domains drawn STRICTLY from *vocabulary* (ADR-0054 §3.3).

    ONE bounded ``provider.chat()`` call (I6/I7). Returns the matched vocabulary names in
    their CANONICAL casing (as they appear in *vocabulary*), de-duplicated, in vocabulary
    order. Any name the provider returns that is not in the vocabulary is dropped
    (anti-hallucination — the LLM can never invent a domain, owner-lock #1).

    * Empty vocabulary → returns ``[]`` WITHOUT making a provider call (caller should have
      short-circuited already; this is a defensive zero-cost guard, I6).
    * Malformed / empty provider output → ``[]`` (a page fitting no domain is not a failure).

    Cost is recorded out-of-band through whatever ``UsageAccumulator`` the caller bound to
    *provider* (``provider.bind_accumulator`` / ``record_usage`` — same ledger as the ingest run).
    """
    canonical = _canonical_vocabulary(vocabulary)
    if not canonical:
        # Defensive: no vocabulary ⇒ zero provider calls (I6). Caller normally short-circuits.
        return []

    instruction = _build_instruction(
        page_title=page_title,
        page_content=page_content,
        vocabulary=list(canonical.values()),
    )

    raw = await bounded_chat_collect(provider, instruction)
    matched = _parse_domains(raw, canonical)
    logger.debug(
        "classify_page_domains: title=%r matched=%s (vocab_size=%d)",
        page_title,
        matched,
        len(canonical),
    )
    return matched


# ── Vocabulary + validation ───────────────────────────────────────────────────


def _canonical_vocabulary(vocabulary: list[str]) -> dict[str, str]:
    """
    Build a case-insensitive lookup ``lowercased → canonical`` from *vocabulary*, preserving
    the FIRST spelling on a case-insensitive collision and vocabulary order. Empty/blank
    entries are dropped (the config layer already normalises, but stay fail-closed here).
    """
    canonical: dict[str, str] = {}
    for name in vocabulary:
        if not isinstance(name, str):
            continue
        stripped = name.strip()
        if not stripped:
            continue
        key = stripped.casefold()
        if key not in canonical:
            canonical[key] = stripped
    return canonical


def _parse_domains(raw: str, canonical: dict[str, str]) -> list[str]:
    """
    Parse the classification JSON and validate STRICTLY against *canonical* (ADR-0054 §3.3).

    Accepts ``{"domains": [...]}`` (preferred) or a bare JSON array. Each returned name is
    matched case-insensitively to the canonical vocabulary casing; unknown names are dropped.
    Output is de-duplicated and emitted in vocabulary order. Never raises.
    """
    obj = loads_json_lenient(raw)
    items: Any
    if isinstance(obj, dict):
        items = obj.get("domains", obj.get("domain", []))
    elif isinstance(obj, list):
        items = obj
    else:
        return []
    if not isinstance(items, list):
        return []

    # Collect the canonical-cased matches (anti-hallucination: only vocabulary members survive).
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, str):
            continue
        key = entry.strip().casefold()
        canonical_name = canonical.get(key)
        if canonical_name is not None:
            seen.add(canonical_name)

    # Emit in vocabulary order (stable, deterministic) — dedup is implicit via `seen`.
    return [name for name in canonical.values() if name in seen]


# ── Prompt + provider surface ─────────────────────────────────────────────────


def _build_instruction(*, page_title: str, page_content: str, vocabulary: list[str]) -> str:
    """
    Deterministic classification prompt (ADR-0054 §3.3). The model must pick 0..N domains
    ONLY from the provided list and return ``{"domains": [...]}`` — it may not invent a domain.
    """
    body = (page_content or "").strip()[:_CONTENT_CHAR_CAP]
    vocab_block = "\n".join(f"- {name}" for name in vocabulary) or "(none)"
    return (
        "You are the domain-classification step of a self-organizing wiki. You are given a "
        "page (title + body excerpt) and a CONTROLLED VOCABULARY of domain names. Decide which "
        "domains (zero, one, or several) this page belongs to.\n\n"
        "IMPORTANT RULES:\n"
        "  - Choose ONLY from the vocabulary below. Never invent a domain that is not listed.\n"
        "  - Copy each chosen name VERBATIM (exact spelling) from the vocabulary.\n"
        "  - If the page fits none of the domains, return an empty list. That is a valid answer.\n"
        "  - Do not force a match; only include a domain the page is genuinely about.\n\n"
        f"# Controlled vocabulary (the ONLY valid domain names)\n{vocab_block}\n\n"
        f"# Page title\n{page_title}\n\n"
        f"# Page body excerpt\n{body}\n\n"
        'Return ONLY a JSON object with a single key "domains" whose value is a list of the '
        "chosen domain names (each one exactly as spelled in the vocabulary). Return no prose, "
        "only the JSON object."
    )
