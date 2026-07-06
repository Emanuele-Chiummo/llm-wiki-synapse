"""
B2-C2: Chat web-search context block — single-shot SearXNG fetch, [W] citation namespace.

When `use_web_search=True` (and, for `local_first` mode, only when wiki retrieval returned
fewer than LOCAL_FIRST_MIN_HITS results), this module makes ONE bounded SearXNG call via
ops/searxng.py (I9), fetches + strips up to CHAT_WEB_MAX_RESULTS pages (reusing
deep_research's HTML→markdown helpers), and assembles a clearly-labelled context block with
its OWN [W1]..[Wn] citation namespace — completely separate from the wiki [n] namespace.

Bounding (I7):
  - SINGLE shot: no loop, no refinement pass.
  - CHAT_WEB_MAX_RESULTS (default 5) — env-configurable cap on results.
  - CHAT_WEB_FETCH_MAX_CHARS (default 8 000) — per-URL content cap.
  - Total cost logged at INFO; no runaway accumulation.

I9: ALL web search goes through ops/searxng.py → SEARXNG_URL. Zero fallback engines.
I6: This module makes NO inference calls — it is pure retrieval + assembly.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.ops.deep_research import (
    _html_to_markdown,  # noqa: PLC2701 — reuse deep_research HTML→markdown helper (I9)
    _is_texty_content_type,
    _sanitize_db_text,
)
from app.ops.searxng import SearchHit, _semaphore, searxng_search
from app.security_net import SSRFError, safe_fetch

logger = logging.getLogger(__name__)


class WebCitation:
    """One [Wn] web-search citation entry for the done event."""

    __slots__ = ("index", "title", "url")

    def __init__(self, index: int, title: str, url: str) -> None:
        self.index = index
        self.title = title
        self.url = url

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "title": self.title, "url": self.url}


class WebContext:
    """Assembled web-search context block + [W] citations (B2-C2)."""

    def __init__(self, text: str, citations: list[WebCitation]) -> None:
        self.text = text
        self.citations = citations

    @property
    def empty(self) -> bool:
        return not self.text


_EMPTY = WebContext(text="", citations=[])


def _fetch_char_cap() -> int:
    from app.config_overrides import effective_int

    return effective_int(
        "chat_web_fetch_max_chars",
        int(settings.chat_web_fetch_max_chars),
    )


def _max_results() -> int:
    from app.config_overrides import effective_int

    return effective_int(
        "chat_web_max_results",
        int(settings.chat_web_max_results),
    )


async def _fetch_one_stripped(hit: SearchHit, *, char_cap: int) -> str | None:
    """
    Fetch one URL and return stripped markdown text (B2-C2).

    Reuses the deep_research HTTP fetch + HTML→markdown stack (I9 — same helpers,
    same SSRF guard). Returns None on any failure (degrade, never raise into caller).
    Text is DB-safe (NUL bytes stripped) and capped to `char_cap` characters.
    """
    async with _semaphore:
        try:
            resp = await safe_fetch(
                hit.url,
                headers={"User-Agent": "Synapse/1.0 ChatWebSearch"},
            )
            if resp.status_code != 200:
                logger.debug(
                    "chat web: HTTP %d for %s — skipping",
                    resp.status_code,
                    hit.url,
                )
                return None
            content_type = (
                resp.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if not _is_texty_content_type(content_type):
                logger.debug(
                    "chat web: non-text content-type %r for %s — skipping",
                    content_type,
                    hit.url,
                )
                return None
            md = _html_to_markdown(resp.text)[:char_cap]
            return _sanitize_db_text(md) if md else None
        except SSRFError as exc:
            logger.info("chat web: SSRF guard blocked %s: %s", hit.url, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("chat web: fetch failed for %s: %s", hit.url, exc)
            return None


async def build_web_context(
    query: str,
    *,
    max_results: int | None = None,
    fetch_max_chars: int | None = None,
) -> WebContext:
    """
    Make ONE bounded SearXNG search for `query` and assemble a [W]-namespaced context block.

    Bounded (I7): single-shot search (no loop), capped results, capped per-URL chars.
    I9: searxng_search() is the sole web-search call — never another backend.
    I6: no inference call — pure retrieval + assembly.

    Returns a :class:`WebContext` whose `text` is a clearly-labelled section with
    ``[W1]…[Wn]`` markers and whose `citations` list is the [Wn] → title/url map.
    Returns :data:`_EMPTY` when SearXNG is unconfigured or returns no hits.
    """
    cap = fetch_max_chars if fetch_max_chars is not None else _fetch_char_cap()
    n_results = max_results if max_results is not None else _max_results()

    # Single bounded SearXNG call (I9, I7).
    hits: list[SearchHit] = await searxng_search(query, max_results=n_results)
    if not hits:
        logger.debug("chat web: no SearXNG hits for query %r", query)
        return _EMPTY

    # Fetch+strip each hit (reuse deep_research helpers, I9). Cap to n_results.
    import asyncio

    raw_texts: list[str | None] = await asyncio.gather(
        *[_fetch_one_stripped(h, char_cap=cap) for h in hits[:n_results]]
    )

    parts: list[str] = []
    citations: list[WebCitation] = []
    w_idx = 1

    for hit, text in zip(hits[:n_results], raw_texts, strict=False):
        if not text:
            # Snippet fallback: use the SearXNG snippet as minimal context.
            text = hit.snippet or ""
        if not text:
            continue
        block = f"[W{w_idx}] {hit.title}\n{text}\n"
        parts.append(block)
        citations.append(WebCitation(index=w_idx, title=hit.title, url=hit.url))
        w_idx += 1

    if not parts:
        logger.debug("chat web: all fetch attempts returned empty content for query %r", query)
        return _EMPTY

    assembled_text = "## Web search results\n\n" + "".join(parts)
    logger.info(
        "chat web: assembled %d web citations for query %r",
        len(citations),
        query,
    )
    return WebContext(text=assembled_text, citations=citations)
