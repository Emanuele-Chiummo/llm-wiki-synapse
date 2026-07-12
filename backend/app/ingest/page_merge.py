"""
LLM body-merge on re-ingest (Feature 2, ADR-0063 §4).

Ported from nashsu/llm_wiki's ``mergePageContent``: when a generated page targets a file that
already exists, ask the model to MERGE the old + new bodies into one coherent body rather than
silently clobbering the prior contribution (the classic "second source enriches an existing
entity page" case). Silent overwrite is real data loss.

Synapse specifics vs the reference:
  • The writer (``write_wiki_page``) owns frontmatter composition (type/title/sources/created/
    updated/tags — the reference's "locked fields" and "array-field union" are already handled
    there: created is preserved, sources are unioned). So this module merges ONLY the markdown
    BODY — never the frontmatter block — keeping the merge surface small and safe.
  • The LLM call routes through the ``provider.chat()`` seam (I6) — no provider/model branching.
  • Bounded (I7): a SINGLE chat call wrapped by a timeout; cost folds into the run-scoped
    accumulator the provider is already bound to.
  • Degrade-safe: on disabled config, no meaningful prior body, provider failure, timeout, or a
    sanity-check rejection (empty / suspiciously short merged body) it returns the NEW body —
    exactly the pre-parity overwrite behavior — so re-ingest never regresses.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.ingest.provider.base import InferenceProvider
from app.ingest.schemas import Message

logger = logging.getLogger(__name__)

# A prior body shorter than this is treated as a stub / placeholder not worth an LLM merge.
_MIN_MEANINGFUL_CHARS = 40
# Reject a merged body shorter than this fraction of the longer input — the model almost
# certainly truncated / lazily summarized rather than genuinely deduplicating (reference: 0.7).
_BODY_SHRINK_THRESHOLD = 0.7

_MERGE_SYSTEM_PROMPT = (
    "You merge two Markdown versions of the SAME wiki page body into one coherent body. "
    "Combine all substantive information from BOTH versions: keep every distinct fact, section, "
    "list item, and citation; remove only literal duplication. Preserve the original writing "
    "language — do NOT translate. Preserve existing [[wikilinks]] and Markdown structure. "
    "Preserve subject boundaries (nashsu/llm_wiki parity — ingest.ts:2792-2793): if either "
    "version mentions other entities/models/products/methods for comparison, keep those "
    "comparisons attribution-exact and do NOT fold them into claims about the main page subject. "
    "When claims conflict or apply to different subjects, keep them separated rather than "
    "synthesizing a single generalized conclusion. "
    "Output ONLY the merged Markdown body — no frontmatter block, no code fences, no commentary."
)


def _build_merge_messages(existing_body: str, new_body: str, source_file: str) -> list[Message]:
    user = (
        "## Existing page body (on disk)\n\n"
        f"{existing_body}\n\n"
        "---\n\n"
        f"## Newly generated body (from {source_file or 'a new source'})\n\n"
        f"{new_body}\n\n"
        "---\n\n"
        "Output the single merged Markdown body now."
    )
    return [
        Message(role="system", content=_MERGE_SYSTEM_PROMPT),
        Message(role="user", content=user),
    ]


async def _stream_merge(provider: InferenceProvider, messages: list[Message]) -> str:
    """Consume the provider.chat() stream into a single string (I6). Usage recorded out of band."""
    stream = await provider.chat(messages, "")
    parts: list[str] = []
    async for token in stream:
        parts.append(token)
    return "".join(parts)


async def maybe_merge_page_body(
    provider: InferenceProvider | None,
    existing_body: str | None,
    new_body: str,
    *,
    title: str,
    origin_source: str,
) -> str:
    """
    Return the BODY to write for a re-ingested page (Feature 2). Merges *existing_body* +
    *new_body* via a single bounded ``provider.chat()`` call when the merge is enabled, a
    provider is available, and the prior body is meaningful; otherwise (and on ANY failure)
    returns *new_body* unchanged — the safe, pre-parity overwrite behavior.
    """
    if provider is None or not settings.ingest_reingest_merge_enabled:
        return new_body

    old = (existing_body or "").strip()
    new = (new_body or "").strip()
    if not old or old == new or len(old) < _MIN_MEANINGFUL_CHARS:
        return new_body

    timeout = float(settings.ingest_reingest_merge_timeout_seconds)
    try:
        merged = await asyncio.wait_for(
            _stream_merge(provider, _build_merge_messages(old, new, origin_source)),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning(
            "page_merge: merge timed out after %.0fs for %r — keeping new body (degrade)",
            timeout,
            title,
        )
        return new_body
    except Exception as exc:  # noqa: BLE001 — degrade-safe: any provider error keeps new body
        logger.warning(
            "page_merge: merge failed for %r (%s) — keeping new body (degrade)", title, exc
        )
        return new_body

    merged = merged.strip()
    # Strip a stray leading frontmatter fence / code fence a provider may have added despite the
    # instruction, so the merged output is a pure body (write_wiki_page composes frontmatter).
    if merged.startswith("```"):
        end = merged.rfind("```")
        if end > 3:
            merged = merged[merged.find("\n") + 1 : end].strip()

    min_len = _BODY_SHRINK_THRESHOLD * max(len(old), len(new))
    if not merged or len(merged) < min_len:
        logger.warning(
            "page_merge: merged body for %r rejected (len=%d < %.0f) — keeping new body (degrade)",
            title,
            len(merged),
            min_len,
        )
        return new_body

    logger.info(
        "page_merge: merged body for %r (old=%d + new=%d -> %d chars)",
        title,
        len(old),
        len(new),
        len(merged),
    )
    return merged
