"""
Long-source chunked analysis + checkpointing (Feature 1, ADR-0063 §3).

Ported from nashsu/llm_wiki's ``analyzeLongSourceInChunks``: when a source is longer than the
analyze context budget, split it into bounded semantic chunks, run ``analyze()`` on each, and
MERGE the resulting Analysis objects instead of relying on the model's context window to swallow
the whole document (where the tail is silently truncated).

Synapse specifics:
  • The ONLY LLM entry point is ``provider.analyze()`` (I6) — this module never talks to a model
    directly; it just calls the same seam the single-source path uses, N times.
  • Bounded by ``ingest_long_source_max_chunks`` (I7): a huge document can never turn into one
    analyze() call per paragraph.
  • Degrade-safe: a per-chunk failure keeps every prior chunk's Analysis (the checkpoint), merges
    what succeeded, and continues; if EVERY chunk fails, it falls back to a single whole-source
    ``analyze()`` call — exactly the pre-parity behavior — so a provider outage surfaces through
    the loop's normal fallback path rather than here.
  • Best-effort on-disk checkpoint (``vault_root/.synapse/ingest-progress/<slug>-<hash>.json``)
    so a mid-way failure or retry RESUMES from the last completed chunk. All checkpoint I/O is
    swallowed — it never blocks or fails ingest.

The entry point ``analyze_source()`` is a drop-in for ``provider.analyze()`` used by the
orchestrated loop; under the threshold it simply delegates to the single-call path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.config import settings
from app.ingest.provider.base import InferenceProvider
from app.ingest.schemas import Analysis, PageType, SuggestedPage

logger = logging.getLogger(__name__)

_CHUNK_CHARS_FLOOR = 4_000
_OVERLAP_RATIO = 0.06
_OVERLAP_MIN = 400
_OVERLAP_MAX = 2_000
_CHECKPOINT_VERSION = 1


# ── Chunk splitting ──────────────────────────────────────────────────────────────


def split_into_chunks(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    """
    Split *text* into paragraph-boundary chunks of ~*target_chars*, each prefixed with the tail
    (*overlap_chars*) of the previous chunk so no cross-paragraph context is lost at a seam.

    Deterministic and dependency-free. A paragraph longer than the target on its own becomes its
    own chunk (never split mid-paragraph — keeps sentences intact for the analyzer).
    """
    target = max(_CHUNK_CHARS_FLOOR, int(target_chars))
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        stripped = text.strip()
        return [stripped] if stripped else []

    raw: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        add_len = len(para) + (2 if current else 0)
        if current and current_len + add_len > target:
            raw.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para) + (2 if len(current) > 1 else 0)
    if current:
        raw.append("\n\n".join(current))

    if len(raw) <= 1:
        return raw

    out: list[str] = []
    for idx, chunk in enumerate(raw):
        if idx > 0 and overlap_chars > 0:
            tail = raw[idx - 1][-overlap_chars:]
            out.append(f"{tail}\n\n{chunk}")
        else:
            out.append(chunk)
    return out


# ── Analysis merge ───────────────────────────────────────────────────────────────


def _dedup_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it.strip())
    return out


def merge_analyses(analyses: list[Analysis]) -> Analysis:
    """
    Merge per-chunk Analysis objects into one (Feature 1). Union topics / entities /
    suggested_pages (dedup, order-preserving), pick the modal language, and concatenate the
    non-empty chunk summaries. Assumes ``analyses`` is non-empty (caller guarantees it).
    """
    if len(analyses) == 1:
        return analyses[0]

    topics: list[str] = []
    entities: list[str] = []
    for a in analyses:
        topics.extend(a.topics)
        entities.extend(a.entities)
    topics = _dedup_preserve(topics) or ["source"]
    entities = _dedup_preserve(entities)

    # Modal language across chunks (ties → first-seen). Guards against a stray chunk whose
    # sample was code/quotes mis-detecting the whole document's language.
    lang_counts: dict[str, int] = {}
    lang_order: list[str] = []
    for a in analyses:
        code = (a.language or "").strip()
        if not code:
            continue
        if code not in lang_counts:
            lang_order.append(code)
        lang_counts[code] = lang_counts.get(code, 0) + 1
    language = (
        max(lang_order, key=lambda c: lang_counts[c]) if lang_order else (analyses[0].language)
    )

    # Union suggested_pages by (normalized title, type); keep the first rationale seen.
    seen_pages: set[tuple[str, PageType]] = set()
    suggested: list[SuggestedPage] = []
    for a in analyses:
        for sp in a.suggested_pages:
            key = (sp.title.strip().lower(), sp.type)
            if key in seen_pages:
                continue
            seen_pages.add(key)
            suggested.append(sp)
    if not suggested:
        suggested = [SuggestedPage(title=topics[0], type=PageType.CONCEPT)]

    summaries = [a.summary.strip() for a in analyses if a.summary and a.summary.strip()]
    summary = "\n\n".join(summaries) if summaries else None

    return Analysis(
        topics=topics,
        entities=entities,
        language=language,
        suggested_pages=suggested,
        summary=summary,
    )


# ── Best-effort on-disk checkpoint ───────────────────────────────────────────────


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _checkpoint_path(source_hash: str) -> Path:
    return settings.vault_root / ".synapse" / "ingest-progress" / f"source-{source_hash}.json"


def _load_checkpoint(path: Path, source_hash: str, chunk_total: int) -> list[Analysis]:
    """Load completed per-chunk analyses from a compatible checkpoint; [] on any mismatch/error."""
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != _CHECKPOINT_VERSION
            or data.get("source_hash") != source_hash
            or data.get("chunk_total") != chunk_total
        ):
            return []
        raw_list = data.get("analyses")
        if not isinstance(raw_list, list):
            return []
        return [Analysis.model_validate(item) for item in raw_list]
    except Exception as exc:  # noqa: BLE001 — checkpoint is advisory; never fail ingest
        logger.debug("long_source: checkpoint load skipped (%s): %s", path, exc)
        return []


def _save_checkpoint(
    path: Path, source_hash: str, chunk_total: int, analyses: list[Analysis]
) -> None:
    """Persist completed per-chunk analyses; swallow ALL errors (advisory only)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CHECKPOINT_VERSION,
            "source_hash": source_hash,
            "chunk_total": chunk_total,
            "completed_through": len(analyses),
            "analyses": [json.loads(a.model_dump_json()) for a in analyses],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — checkpoint is advisory; never fail ingest
        logger.debug("long_source: checkpoint save skipped (%s): %s", path, exc)


def _clear_checkpoint(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("long_source: checkpoint clear skipped (%s): %s", path, exc)


# ── Entry point ──────────────────────────────────────────────────────────────────


async def analyze_source(
    provider: InferenceProvider,
    source_text: str,
    vault_context: str,
) -> Analysis:
    """
    Drop-in replacement for ``provider.analyze()`` that transparently chunks long sources
    (Feature 1). Under the configured threshold — or when chunking is disabled — it simply
    delegates to the single whole-source ``analyze()`` call, so the common case is unchanged.

    Bounded (I7): at most ``ingest_long_source_max_chunks`` analyze() calls. Degrade-safe: a
    per-chunk failure keeps prior results and merges them; total failure falls back to a single
    whole-source analyze() call. Routes ALL LLM work through ``provider.analyze()`` (I6).
    """
    threshold = int(settings.ingest_long_source_char_threshold)
    if threshold <= 0 or len(source_text) <= threshold:
        return await provider.analyze(source_text, vault_context)

    target_chars = int(settings.ingest_long_source_chunk_chars)
    overlap = min(_OVERLAP_MAX, max(_OVERLAP_MIN, int(target_chars * _OVERLAP_RATIO)))
    chunks = split_into_chunks(source_text, target_chars, overlap)
    if len(chunks) <= 1:
        # Below the paragraph structure needed to chunk meaningfully → single-call path.
        return await provider.analyze(source_text, vault_context)

    max_chunks = max(1, int(settings.ingest_long_source_max_chunks))
    if len(chunks) > max_chunks:
        logger.info(
            "long_source: %d chunks exceeds max_chunks=%d — analyzing first %d only (I7)",
            len(chunks),
            max_chunks,
            max_chunks,
        )
        chunks = chunks[:max_chunks]
    chunk_total = len(chunks)

    checkpoint_on = bool(settings.ingest_long_source_checkpoint_enabled)
    src_hash = _source_hash(source_text)
    ck_path = _checkpoint_path(src_hash)

    analyses: list[Analysis] = []
    if checkpoint_on:
        analyses = _load_checkpoint(ck_path, src_hash, chunk_total)
        if analyses:
            logger.info(
                "long_source: resuming from checkpoint (%d/%d chunks already analyzed)",
                len(analyses),
                chunk_total,
            )

    for idx in range(len(analyses), chunk_total):
        chunk_ctx = (
            f"{vault_context}\n\n# Long-source chunk {idx + 1}/{chunk_total}\n"
            "This is one section of a larger document analyzed in chunks; analyze THIS section."
        )
        try:
            chunk_analysis = await provider.analyze(chunks[idx], chunk_ctx)
        except Exception as exc:  # noqa: BLE001 — degrade: keep prior chunks (the checkpoint)
            logger.warning(
                "long_source: chunk %d/%d analyze failed (%s) — merging %d prior chunk(s)",
                idx + 1,
                chunk_total,
                exc,
                len(analyses),
            )
            break
        analyses.append(chunk_analysis)
        if checkpoint_on:
            _save_checkpoint(ck_path, src_hash, chunk_total, analyses)

    if not analyses:
        # Every chunk failed → fall back to the single whole-source call (pre-parity behavior).
        logger.warning(
            "long_source: no chunk analyzed successfully — falling back to single analyze()"
        )
        return await provider.analyze(source_text, vault_context)

    merged = merge_analyses(analyses)
    if checkpoint_on and len(analyses) == chunk_total:
        _clear_checkpoint(ck_path)
    logger.info(
        "long_source: merged %d/%d chunk analyses (topics=%d, suggested=%d, lang=%s)",
        len(analyses),
        chunk_total,
        len(merged.topics),
        len(merged.suggested_pages),
        merged.language,
    )
    return merged
