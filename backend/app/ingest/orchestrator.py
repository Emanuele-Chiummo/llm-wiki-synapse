"""
Ingest COMPATIBILITY FACADE + persistence primitives — the historical single seam through which
files enter Postgres and Qdrant (ADR-0003, I6). NOT "v0.1 mechanical only": the full F17
capability-aware ingest (analyze → generate → validate → retry, or delegated CLI loop) is live and
lives in ``pipeline.py``; this module now plays two roles.

1) COMPATIBILITY FACADE (in the process of being dissolved — target 2.0.0).
   1.7.0 PR2 decomposed the ingest into cohesive siblings; this module re-exports their seams
   (see the façade block at the end of the file) so every importer AND every monkeypatch of
   ``app.ingest.orchestrator.<name>`` keeps resolving through this module unchanged:
     • ``context.py``  — vault/ingest context assembly (``_load_ingest_context``, the existing-
                         pages catalogue, the R7-6 folderContext hint).
     • ``writer.py``   — the single shared write path (``write_wiki_page``) + slug / source-
                         identity / entity canonical-key / ``related`` helpers.
     • ``pipeline.py`` — ``ingest_file`` / ``delete_file``, ``run_ingest_pipeline`` (F17
                         capability routing), the orchestrated/delegated route helpers, the
                         source-summary guarantee, the language guard, the ingest_runs lifecycle.
   The siblings deliberately reach every primitive/seam via ``orch.<name>`` (NOT direct imports)
   so ``app.ingest.orchestrator`` remains the ONE monkeypatch surface. This mirror indirection is
   currently LOAD-BEARING FOR THE TEST SUITE — 18 test modules bind ``orch`` to this module and
   patch pipeline-internal functions (``_delegate_ingest`` /
   ``_open_ingest_run`` / ``_finalize_ingest_run`` / …), the shared write path, the persistence
   primitives, and the shared ``get_session`` / ``upsert_point`` seams ON THIS MODULE. Removing the
   mirror, or moving the DB-touching primitives to a new module, silently bypasses those patches
   (the get_session enumeration in those tests would miss the new module) and must therefore be
   sequenced AFTER a dedicated test-decoupling pass — see docs/adr and the 1.9.2 recon notes.

2) PERSISTENCE PRIMITIVES kept here (the low-level, incremental-index building blocks — I1):
     persist_metadata          — Postgres ``pages`` upsert (INSERT or re-ingest UPDATE)
     upsert_vector             — embed via EmbeddingClient + Qdrant upsert (skip when disabled)
     append_log                — K4 append-only ``log.md`` line
     bump_version              — ``vault_state.data_version`` +1 (once per content change)
     reindex_wiki_page_body    — atomic single-page rewrite + re-index (ADR-0035/0036)
   plus the ``overview.md`` regeneration seam (``_update_overview`` & friends — F3) and the
   F18 domain/type auto-tag write-back (``apply_domain_tags`` / ``apply_page_type``).

Public API (called by watcher.py and POST /ingest/trigger, via the façade re-exports):
  ingest_file(file_path)  -> IngestResult   (K6, ADR-0001, ADR-0002)
  delete_file(file_path)  -> None           (soft-delete, ADR-0005)

The mtime-then-hash gate (ADR-0001) lives in ``pipeline.py`` so the watcher and the REST
endpoint share the same change-detection logic (I1 — no full rescan).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter  # python-frontmatter

from app.config import settings

# get_session / resolve_provider are used by kept helpers here AND reached via
# ``orch.<name>`` from the extracted modules + monkeypatched on this module; the redundant
# ``as`` alias marks them as an explicit re-export (mypy --no-implicit-reexport) (1.7.0 PR2).
from app.db import get_session as get_session
from app.embeddings import EmbeddingError, get_embedding_client
from app.ingest.provider import resolve_provider as resolve_provider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator

# ingest_queue / delete_point stay here only as re-export + monkeypatch surface for the
# extracted pipeline module (1.7.0 PR2); the redundant `as` alias marks the intentional re-export.
from app.ingest.queue_manager import ingest_queue as ingest_queue
from app.ingest.schemas import INDEX_TYPE, LOG_TYPE, Analysis
from app.models import Page, VaultState
from app.qdrant_client import delete_point as delete_point
from app.qdrant_client import upsert_point
from app.wiki.summary import extract_first_paragraph_summary

logger = logging.getLogger(__name__)

# Cost-anomaly threshold (AQ-v0.2-8 / ADR-0009 §3) — inline WARNING site, not a hook.
COST_ANOMALY_THRESHOLD_USD = 1.00

# R8-2 / F12: image extensions routed through the vision caption seam (app.ingest.vision).
# Mirrors extract.PLACEHOLDER image set; AV extensions are handled by R8-3, not here.
_VISION_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# R8-3 / F12: AV extensions routed through the Whisper transcription seam
# (app.ingest.transcription). Kept separate from _VISION_IMAGE_EXTENSIONS (I6).
_AV_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".mp4"})


def subdir_path(subdir: str) -> Path:
    """vault/wiki/<subdir> relative segment for the writer."""
    return Path("wiki") / subdir


async def reindex_wiki_page_body(
    *,
    page: Page,
    new_file_text: str,
    body_for_embedding: str,
    bump: bool = True,
) -> None:
    """
    Atomically rewrite an already-existing wiki page file with *new_file_text* and re-index it
    INCREMENTALLY (I1) — the shared single-page re-index primitive (ADR-0035 / ADR-0036 §2.1 §7).

    This is the seam that wikilink enrichment (ADR-0036) and any in-place body edit reuse so the
    re-index logic lives in exactly one place. It:
      1. writes the new bytes atomically (temp file + os.replace — crash-safe, no partial file),
      2. refreshes ``pages.content_hash`` via ``persist_metadata`` (metadata unchanged: title/type/
         sources are preserved from the existing row — enrichment never touches frontmatter, I5),
      3. re-embeds the body into Qdrant (``upsert_vector``),
      4. re-derives the K5 ``links`` rows from the new body (``parse_wikilinks``/``persist_links``);
         this is where the new ``[[wikilinks]]`` become F4 *direct link ×3* edges,
      5. optionally bumps ``data_version`` ONCE (``bump=True``). When enriching a batch, the caller
         passes ``bump=False`` per page and bumps once for the whole pass (I1 — one version bump).

    Only THIS page is touched (no rescan, no vault walk — I1). ``index.md`` is NOT regenerated here
    (the link targets already exist; the catalogue is unchanged by adding an inline link). The
    caller is responsible for the single ``bump_version()`` when batching with ``bump=False``.
    """
    import os
    import tempfile

    abs_path = (settings.vault_root / page.file_path).resolve()
    new_bytes = new_file_text.encode("utf-8")

    def _atomic_write() -> None:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(abs_path.parent), suffix=".enrich_tmp")
        try:
            os.write(tmp_fd, new_bytes)
            os.close(tmp_fd)
            Path(tmp_name).replace(abs_path)
        except Exception:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            Path(tmp_name).unlink(missing_ok=True)
            raise

    await asyncio.get_event_loop().run_in_executor(None, _atomic_write)

    # Refresh content_hash; preserve existing metadata verbatim (frontmatter untouched, I5).
    # summary IS recomputed here (unlike apply_domain_tags/apply_page_type below): the body
    # itself changed (wikilink enrichment / in-place edit), so the K3 gloss must stay in sync
    # (1.9.4 W6).
    await persist_metadata(
        page_id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        tags=page.tags,
        summary=extract_first_paragraph_summary(body_for_embedding),
        content_hash=_sha256(new_bytes),
        source_mtime_ns=page.source_mtime_ns or 0,
    )
    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        vault_id=page.vault_id,
    )

    # K5: re-derive wikilinks from the new body (the new [[links]] land in `links` → F4 ×3 signal).
    from app.wiki.links import parse_wikilinks, persist_links

    parsed = parse_wikilinks(body_for_embedding)
    async with get_session() as wl_sess:
        await persist_links(wl_sess, page.id, parsed)

    if bump:
        await bump_version()


# ── F18 / R12-2: domain auto-tag post-write hook (ADR-0054 §3/§4) ──────────────


async def apply_domain_tags(page: Page, new_tags: list[str]) -> None:
    """
    Rewrite *page*'s frontmatter ``tags`` to *new_tags* and persist incrementally (I1), WITHOUT
    a second ``data_version`` bump (ADR-0054 §3.2 — one ingest ⇒ at most one bump).

    Reads the on-disk file, replaces ONLY the ``tags`` key in the YAML frontmatter (all other
    frontmatter — type/title/sources/lang — is preserved), rewrites the file atomically, refreshes
    ``pages.tags`` + ``content_hash`` via ``persist_metadata``, and re-embeds the body. The K5
    ``links`` are unaffected by a frontmatter-only tag change, so they are left as-is. Reuses the
    same single-page primitives ``write_wiki_page`` uses (I1 — only this page is touched, no
    re-scan). This is the shared write-back seam for BOTH the ingest hook and the backfill.
    """
    from app.ops.enrich_wikilinks import _rejoin, _split_frontmatter

    abs_path = (settings.vault_root / page.file_path).resolve()
    text = abs_path.read_text(encoding="utf-8")
    fm_block, body = _split_frontmatter(text)

    # Parse the whole file so python-frontmatter round-trips every key; set tags authoritatively.
    # NOTE: callers pass the ALREADY-MERGED list (merge_domain_tags(page.tags, classified)), which
    # preserves non-domain keyword/nav tags (e.g. the overview's F3 tag cloud) + the domain/* set.
    post = frontmatter.loads(text)
    cleaned = [t for t in new_tags if t]
    if cleaned:
        post["tags"] = cleaned
    else:
        post.metadata.pop("tags", None)
    new_file_text = frontmatter.dumps(post) + "\n"

    # Fallback: if the parse-round-trip somehow lost the frontmatter block, keep the original
    # split-and-rejoin body (defence-in-depth; never corrupt the page).
    if not new_file_text.strip():
        new_file_text = _rejoin(fm_block, body)

    new_bytes = new_file_text.encode("utf-8")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(new_file_text, encoding="utf-8")

    await persist_metadata(
        page_id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        tags=cleaned or None,
        content_hash=_sha256(new_bytes),
        source_mtime_ns=page.source_mtime_ns or 0,
    )
    # Re-embed the body (unchanged text, but keeps Qdrant payload consistent — cheap, I1).
    body_for_embedding = frontmatter.loads(new_file_text).content
    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        vault_id=page.vault_id,
    )
    # Reflect the new tags on the in-memory ORM object so callers see the merged set.
    page.tags = cleaned or None
    # NO bump_version() here — the ingest already bumped once (ADR-0054 §3.2, Do-NOT #3).


async def apply_page_type(page: Page, new_type: str) -> None:
    """
    Rewrite *page*'s frontmatter ``type`` to *new_type* and persist ``pages.page_type`` (I1),
    WITHOUT a ``data_version`` bump (the reclassify run bumps once for the whole batch).

    The TYPE twin of :func:`apply_domain_tags`: reads the on-disk file, replaces ONLY the ``type``
    key in the YAML frontmatter (title/sources/tags/lang preserved byte-exact via the
    python-frontmatter round-trip), rewrites the file, refreshes ``pages.page_type`` +
    ``content_hash`` via ``persist_metadata``, and re-embeds the body (unchanged text, but keeps
    the Qdrant payload's ``type`` consistent). Only this page is touched (I1 — no re-scan). The
    file is NOT moved between ``wiki/<type>/`` subdirectories: only the frontmatter + DB column
    change, keeping the write byte-minimal and the wikilinks stable.
    """
    from app.ops.enrich_wikilinks import _rejoin, _split_frontmatter  # noqa: PLC0415

    abs_path = (settings.vault_root / page.file_path).resolve()
    text = abs_path.read_text(encoding="utf-8")
    fm_block, body = _split_frontmatter(text)

    # Parse the whole file so python-frontmatter round-trips every key; set type authoritatively.
    post = frontmatter.loads(text)
    post["type"] = new_type
    new_file_text = frontmatter.dumps(post) + "\n"

    # Fallback: if the parse-round-trip somehow lost the frontmatter block, keep the original
    # split-and-rejoin body (defence-in-depth; never corrupt the page).
    if not new_file_text.strip():
        new_file_text = _rejoin(fm_block, body)

    new_bytes = new_file_text.encode("utf-8")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(new_file_text, encoding="utf-8")

    await persist_metadata(
        page_id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=new_type,
        sources=page.sources,
        tags=page.tags,
        content_hash=_sha256(new_bytes),
        source_mtime_ns=page.source_mtime_ns or 0,
    )
    # Re-embed the body so the Qdrant payload's `type` reflects the new value (cheap, I1).
    body_for_embedding = frontmatter.loads(new_file_text).content
    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=page.file_path,
        title=page.title,
        page_type=new_type,
        vault_id=page.vault_id,
    )
    # Reflect the new type on the in-memory ORM object so callers see the change.
    page.page_type = new_type
    # NO bump_version() here — the reclassify run bumps once at the end (batch, not per-page).


async def _auto_tag_written_pages(
    *,
    provider: InferenceProvider,
    written_pages: list[Page],
    origin_source: str,
) -> None:
    """
    ADR-0054 §3 auto-tag hook: classify each just-written page against the effective domain
    vocabulary and merge ``domain/*`` tags. Non-fatal per page (a failure leaves that page
    written+untagged; ingest continues). Dormant vocabulary ⇒ zero provider calls, one debug
    line max (Do-NOT #2). The provider's usage is already bound to this run's accumulator by the
    caller, so classification cost folds into the ingest run's ``total_cost_usd`` (I7, §3.3).
    """
    from app.config_overrides import effective_domain_vocabulary  # noqa: PLC0415
    from app.ingest.domain_tagger import (  # noqa: PLC0415
        classify_page_domains,
        merge_domain_tags,
    )

    vocabulary = effective_domain_vocabulary()
    if not vocabulary:
        # Dormant: no vocabulary ⇒ zero provider calls, zero log noise (I6, §3.2).
        logger.debug("_auto_tag_written_pages: vocabulary dormant — skip origin=%s", origin_source)
        return

    taggable = [p for p in written_pages if p.title and (p.file_path or "").startswith("wiki/")]
    for page in taggable:
        try:
            body = _read_body_for_classification(page)
            classified = await classify_page_domains(
                provider,
                page_title=page.title or "",
                page_content=body,
                vocabulary=vocabulary,
            )
            merged = merge_domain_tags(page.tags, classified)
            if merged != (page.tags or []):
                await apply_domain_tags(page, merged)
            logger.info(
                "auto_tag: page=%s domains=%s origin=%s",
                page.id,
                classified,
                origin_source,
            )
        except Exception as exc:  # noqa: BLE001 — non-fatal: page stays untagged (§3.4, Do-NOT #6)
            logger.warning(
                "auto_tag: classification failed for page=%s (non-fatal, page stays untagged): %s",
                page.id,
                exc,
            )


def _read_body_for_classification(page: Page) -> str:
    """Read the page body (frontmatter stripped) for the classifier; '' if unreadable."""
    from app.ops.enrich_wikilinks import _split_frontmatter

    abs_path = (settings.vault_root / page.file_path).resolve()
    try:
        text = abs_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _split_frontmatter(text)[1]


OVERVIEW_REL_PATH = "wiki/overview.md"
# D4 (ADR-0063 §9, nashsu/llm_wiki parity — wiki-graph.ts:182-209): index.md and log.md are graph
# nodes too (llm_wiki makes a node for every wiki/*.md except type:query). Synapse previously kept
# them disk-only, so they were missing from the graph. We upsert a Page row for each after the
# per-page writers maintain them, mirroring _index_overview_file.
INDEX_REL_PATH = "wiki/index.md"
LOG_REL_PATH = "wiki/log.md"


async def _update_overview(analysis: Analysis | None, origin_source: str) -> None:
    """
    REGENERATE the single auto-maintained overview.md note (F3, nashsu/llm_wiki parity).

    Mirrors llm_wiki: overview.md is a SINGLE note, fully OVERWRITTEN on each ingest with a
    concise narrative of the wiki's current themes/context — NOT an append-only marker log.

    Pipeline (bounded, degrade-safe):
      1. Build a compact context prompt from purpose.md (if present), a bounded set of existing
         page titles+types (indexed read — I1, no vault re-scan), and the just-ingested analysis.
      2. Make AT MOST ONE InferenceProvider call resolved via resolve_provider_config("ingest")
         (I6 — never a hardcoded backend), wrapped in wait_for(overview_timeout_seconds) and
         bounded by the resolved row's token_budget / overview_token_budget (I7). Cost logged.
      3. OVERWRITE vault/wiki/overview.md with valid Obsidian frontmatter (type: overview,
         title: <descriptive title extracted from the narrative H1, else overview_title>) +
         the narrative body (I5).
      4. Index overview.md as a Page(type="overview") via the shared persist primitives so it
         surfaces in GET /pages and populates the nav "Overview" section (count 1).

    Fire-and-forget / degrade-safe (I7): if the provider is unavailable or the call fails/times
    out, the previous overview.md is KEPT (log a warning) and ingest still succeeds. This function
    NEVER raises into the ingest critical path — callers already treat it as best-effort.

    The (analysis, origin_source) signature is preserved so existing call sites / tests are
    unchanged; origin_source is used only for logging context here.
    """
    try:
        # ── Resolve provider (I6 — never hardcode; "no provider" → keep previous) ───
        resolved = await _resolve_overview_provider()
        if resolved is None:
            logger.debug(
                "_update_overview: no ingest provider resolved — keeping previous overview.md "
                "(I6: no silent default). origin=%s",
                origin_source,
            )
            # Still ensure a Page row exists for an already-present overview.md so the nav
            # Overview section can populate even before the first provider-backed regen.
            await _index_existing_overview_if_present()
            return
        provider, config_row = resolved

        # ── Build bounded context (purpose.md + existing titles + analysis) — I1 ────
        existing = await _load_overview_page_digest()
        # Language (F3 parity): use the just-ingested analysis language when available
        # (orchestrated route); otherwise (delegated route, analysis=None) detect the vault's
        # dominant content language from existing pages. If neither yields a language,
        # _build_overview_instruction falls back to the "match purpose + existing pages" directive.
        # settings.overview_language (OVERVIEW_LANGUAGE) FORCES the language when set — e.g. an
        # Italian user reading English source material wants an Italian overview regardless of
        # the content's detected language. Falls back to analysis/detected language otherwise.
        from app.config_overrides import effective_str  # noqa: PLC0415
        from app.ingest.pipeline import _vault_output_language  # noqa: PLC0415

        _effective_overview_lang = effective_str("overview_language", settings.overview_language)
        # F3/ADR-0081: the per-vault output_language (set at onboarding, drives ingest generation)
        # must also drive the overview — otherwise a vault set to Italian but built from English
        # sources gets an English overview (the *detected* language of its pages). Priority:
        # explicit overview_language override → per-vault output_language → this run's analysis
        # language → content detection.
        _vault_output_lang = await _vault_output_language()
        if _effective_overview_lang:
            overview_lang: str | None = _effective_overview_lang
        elif _vault_output_lang:
            overview_lang = _vault_output_lang
        elif analysis is not None and getattr(analysis, "language", None):
            overview_lang = analysis.language
        else:
            overview_lang = await _detect_vault_language()
        instruction = _build_overview_instruction(
            analysis=analysis, existing_digest=existing, lang=overview_lang
        )

        _raw_budget: Any = getattr(config_row, "token_budget", None) or getattr(
            settings, "overview_token_budget", 3_000
        )
        token_budget = int(_raw_budget) if _raw_budget is not None else 3_000
        timeout_s = float(getattr(settings, "overview_timeout_seconds", 30.0))

        # ── Bind a run-scoped Usage ledger (I7 — cost logged out of band) ──────────
        accumulator = UsageAccumulator()
        provider.bind_accumulator(accumulator)

        # ── ONE bounded call, no loop, no retry (I7) ───────────────────────────────
        try:
            narrative = await asyncio.wait_for(
                _overview_chat_collect(provider, instruction, token_budget),
                timeout=timeout_s,
            )
        except TimeoutError:
            logger.warning(
                "_update_overview: provider call timed out after %.1fs — keeping previous "
                "overview.md (degrade, never fail ingest). origin=%s",
                timeout_s,
                origin_source,
            )
            await _index_existing_overview_if_present()
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_update_overview: provider call failed (%s) — keeping previous overview.md. "
                "origin=%s",
                exc,
                origin_source,
            )
            await _index_existing_overview_if_present()
            return
        finally:
            logger.info(
                "overview regen provider call: tokens=%d cost_usd=%.4f calls=%d origin=%s",
                accumulator.total_tokens,
                round(accumulator.total_cost_usd, 4),
                accumulator.calls,
                origin_source,
            )

        narrative = (narrative or "").strip()
        if not narrative:
            logger.warning(
                "_update_overview: provider returned empty narrative — keeping previous "
                "overview.md. origin=%s",
                origin_source,
            )
            await _index_existing_overview_if_present()
            return

        # ── OVERWRITE overview.md with valid frontmatter (I5) + index it ────────────
        await _write_and_index_overview(narrative, lang=overview_lang)
    except Exception as exc:  # noqa: BLE001
        # Belt-and-braces: never let overview maintenance fail an ingest (I7).
        logger.warning(
            "_update_overview: unexpected failure (%s) — keeping previous overview.md. origin=%s",
            exc,
            origin_source,
        )


async def _resolve_overview_provider() -> tuple[InferenceProvider, object] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6) for the overview regen call.

    Returns (provider, config_row) or None when no provider_config resolves / DB unavailable.
    NEVER hardcodes a backend; NEVER branches on isinstance/type/class-name (I6). Mirrors
    ops/review.py::_resolve_review_provider and _resolve_ingest_provider_config.
    """
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        config_row = await resolve_provider_config("ingest")
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("_resolve_overview_provider: provider resolution unavailable: %s", exc)
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_resolve_overview_provider: provider build failed: %s", exc)
        return None
    return provider, config_row


async def _load_overview_page_digest() -> str:
    """
    Compact digest of existing wiki page titles+types (bounded indexed read — I1, no re-scan).

    Excludes the reserved catalogue types (overview/index) so the overview never summarizes
    itself. Capped at overview_max_titles. Returns a newline list "- <title> [<type>]".
    """
    from sqlalchemy import select

    max_titles = int(getattr(settings, "overview_max_titles", 200))
    lines: list[str] = []
    try:
        async with get_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(Page.title, Page.page_type)
                        .where(
                            Page.vault_id == settings.vault_id,
                            Page.deleted_at.is_(None),
                            Page.title.isnot(None),
                            Page.page_type.notin_(["overview", "index"]),
                        )
                        .order_by(Page.updated_at.desc())
                        .limit(max_titles)
                    )
                ).all()
            )
        for title, ptype in rows:
            t = (title or "").strip()
            if not t:
                continue
            lines.append(f"- {t} [{(ptype or '?').strip() or '?'}]")
    except Exception as exc:  # noqa: BLE001
        logger.debug("_load_overview_page_digest: title read failed (non-fatal): %s", exc)
    return "\n".join(lines) if lines else "(no pages yet)"


_ISO_LANG_NAMES = {
    "it": "Italian",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}

# Bounded sample size for vault-language detection (I7 — cheap, no full walk).
_LANG_DETECT_SAMPLE = 25


async def _detect_vault_language() -> str | None:
    """
    Detect the vault's dominant content language from existing wiki pages' `lang` frontmatter
    (nashsu/llm_wiki parity — the overview must match the vault content language, not default
    to English). Used for the DELEGATED ingest route where no per-source Analysis (hence no
    detected language) is available.

    I1 — NO directory walk: the file set comes from a BOUNDED DB query over the pages table
    (most-recently-updated non-meta pages); only those specific files are read for their `lang`
    frontmatter. Returns the modal `lang`, or None when undetectable — the caller then falls back
    to the "match purpose + existing pages" directive. Bounded to _LANG_DETECT_SAMPLE (I7).
    """
    from sqlalchemy import select

    async with get_session() as session:
        rows = await session.execute(
            select(Page.file_path)
            .where(Page.deleted_at.is_(None))
            .where(Page.page_type.not_in(["index", "log", "overview"]))
            .order_by(Page.updated_at.desc())
            .limit(_LANG_DETECT_SAMPLE)
        )
        file_paths = [fp for (fp,) in rows.all() if fp]

    counts: dict[str, int] = {}
    for rel in file_paths:
        path = settings.vault_root / rel
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, S112 — tolerant: skip unreadable/malformed files
            continue
        lang = post.metadata.get("lang")
        if isinstance(lang, str) and len(lang) >= 2:
            counts[lang.lower()] = counts.get(lang.lower(), 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def _build_overview_instruction(
    *,
    analysis: Analysis | None,
    existing_digest: str,
    lang: str | None = None,
    now_label: str | None = None,
) -> str:
    """
    Build the single overview-regeneration prompt (F3). Inputs: purpose.md (F2, if present),
    the existing page titles+types digest, and the just-ingested analysis. Asks for a concise
    narrative body ONLY (no frontmatter, no title heading — the writer adds valid frontmatter).

    Language (nashsu/llm_wiki buildLanguageDirective parity): the overview MUST be written in
    the vault's language, not defaulted to English. When the detected `lang` is known (from the
    just-ingested analysis) it is stated explicitly; in all cases the model is told to match the
    language of the purpose.md + existing pages provided below (covers the delegated route where
    analysis — hence lang — is None).
    """
    # v1.3.14 (F3 parity): the overview TITLE is descriptive + LLM-generated (like llm_wiki),
    # ending with the current period. Inject the real period so the model doesn't hallucinate a
    # date; render it in the output language. `now_label` is injectable for deterministic tests.
    if now_label is None:
        now_label = datetime.now(UTC).strftime("%Y-%m")
    if lang:
        lang_name = _ISO_LANG_NAMES.get(lang.lower(), lang)
        lang_directive = (
            f"MANDATORY OUTPUT LANGUAGE: {lang_name} ({lang}). Write the ENTIRE overview in "
            f"{lang_name}. Do NOT translate to English.\n\n"
        )
    else:
        lang_directive = (
            "MANDATORY OUTPUT LANGUAGE: write the overview in the SAME LANGUAGE as the wiki "
            "purpose and existing pages shown below. Do NOT default to English.\n\n"
        )
    purpose_parts: list[str] = []
    for name in ("purpose.md",):
        path = settings.vault_root / name
        if path.exists():
            try:
                purpose_parts.append(path.read_text(encoding="utf-8").strip())
            except OSError:
                pass
    purpose_block = "\n\n".join(purpose_parts).strip() or "(no purpose.md)"

    analysis_block = "(none)"
    if analysis is not None:
        topics = ", ".join(analysis.topics[:12]) if analysis.topics else "(none)"
        entities = ", ".join(analysis.entities[:12]) if analysis.entities else "(none)"
        summary = (analysis.summary or "").strip() or "(none)"
        analysis_block = f"topics: {topics}\nentities: {entities}\nsummary: {summary}"

    return (
        lang_directive
        + "You maintain the single OVERVIEW note of a self-organizing wiki. Regenerate it now to "
        "capture the CURRENT big picture of the whole wiki: its main themes, how the pages relate, "
        "and the key context a reader needs before diving in.\n\n"
        "STYLE — write a flowing, DISCURSIVE narrative, like a well-written encyclopedia "
        "overview essay (NOT a bulleted index):\n"
        "  - Open the body with a short paragraph on what this wiki covers and why, and BEGIN "
        "that first paragraph with a BOLDED thesis anchor: write `**Central thesis**:` (or its "
        "translation in the output language — e.g. `**Tesi centrale**:` for Italian) immediately "
        "followed by ONE sentence stating the wiki's core thesis/angle, then continue the prose.\n"
        "  - Organize the rest into a few thematic paragraphs; you MAY put a short `## Heading` "
        "before each major theme, but the body of each theme MUST be PROSE, not a list.\n"
        "  - Weave the [[wikilinks]] INLINE into full sentences — explain how pages relate and "
        "connect, e.g. 'Software discovery starts with [[X]], which feeds normalization in [[Y]] "
        "and reconciliation in [[Z]].' Do NOT emit long bulleted lists of "
        "`- [[Page]] — description`.\n"
        "  - Link generously to the existing pages below using their EXACT titles, but always "
        "embedded in the narrative. Favor readable flowing prose over enumeration.\n"
        "BEGIN with ONE top-level `# ` heading on the FIRST line: a DESCRIPTIVE title for the "
        "whole wiki — its domain/subject plus a few words capturing its current thesis or angle, "
        "ending with the current period in parentheses. "
        f"The current period is {now_label}; render it as 'Month Year' IN THE OUTPUT LANGUAGE "
        "(for an Italian wiki, e.g. '# Procurement Analytics Wiki — Visione Progettuale "
        "Integrata (Luglio 2026)'). Put a blank line after the title, then the body.\n"
        "Do NOT output YAML frontmatter or any preamble like 'Here is' — output the single `# ` "
        "title line followed by the Markdown body.\n\n"
        "AFTER the body, on the VERY LAST line, output a tag cloud exactly in this form:\n"
        "`TAGS: keyword-one, keyword-two, keyword-three, ...`\n"
        "List 40-120 SHORT lowercase hyphenated topic keywords that capture the wiki's themes — "
        "the key domains, regulations, standards, technologies, processes and concepts a reader "
        "would filter by (e.g. `procurement, licensing-governance, sla-maturity, dora, nis2, "
        "gdpr, iso-27001, cost-accounting`). Keywords only (no page titles, no sentences); this "
        "single `TAGS:` line is the ONLY exception to the no-frontmatter rule.\n\n"
        f"# Wiki purpose\n{purpose_block}\n\n"
        f"# Existing pages (title [type])\n{existing_digest}\n\n"
        f"# Most recent ingest analysis\n{analysis_block}\n"
    )


async def _overview_chat_collect(
    provider: InferenceProvider, instruction: str, token_budget: int
) -> str:
    """
    Run ONE single-turn ``provider.complete()`` call and return the text (I6/I7).

    Uses ``complete()`` (single-turn, no tools) rather than ``chat()``: the agentic CLI provider's
    ``chat()`` seam runs a full agent loop that hangs and times out on a simple one-shot generation
    — the same reason the block ingest loop uses ``complete()`` (ADR-0076). All providers implement
    ``complete()``; it is backend-neutral (no isinstance/type branch, I6). Usage is recorded out of
    band onto the bound accumulator; the hard bounds are the single call + the caller's wait_for.
    ``token_budget`` is unused here (kept for signature/caller compatibility) — the output is capped
    by ``max_tokens`` and the overall run by the caller's timeout.
    """
    _ = token_budget  # bounds come from max_tokens + the caller's wait_for (I7)
    narrative = await provider.complete(
        instruction,
        "Write the overview note now. Output only the note body (optionally a single leading "
        "'# ' title line). No preamble, no chain-of-thought.",
        max_tokens=4096,  # a whole-wiki overview is bounded prose (~gold 171 lines)
    )
    return narrative.strip()


def _extract_overview_title(narrative: str) -> tuple[str, str]:
    """
    Pull a DESCRIPTIVE title out of the LLM overview narrative (v1.3.14, F3 parity with llm_wiki,
    whose overview title reflects the wiki's domain/thesis rather than a static "Overview" label).

    If the narrative starts with a single top-level `# ` heading, that heading text becomes the
    title and is stripped from the body (the reader already renders the Page title, so keeping the
    H1 would duplicate it). Otherwise — or if the heading is empty/absurdly long — fall back to
    settings.overview_title so the behaviour stays degrade-safe and backward-compatible (a
    body-only narrative, e.g. from an older prompt, still yields a valid page).

    Returns (title, body).
    """
    fallback = str(getattr(settings, "overview_title", "Overview")) or "Overview"
    lines = (narrative or "").lstrip().splitlines()
    if not lines:
        return fallback, narrative
    heading = re.match(r"#\s+(.+?)\s*#*\s*$", lines[0])
    if heading is None:
        return fallback, narrative
    candidate = re.sub(r"\s+", " ", heading.group(1)).strip().strip("*_ ").strip()
    if not candidate or len(candidate) > 200:
        return fallback, narrative
    body = "\n".join(lines[1:]).lstrip("\n")
    return candidate, body


# Max keyword tags kept on the overview (ADR-0067 D6/P2-5 — LLM Wiki's overview carries a
# ~129-keyword tag cloud; the prompt asks for 40-120, this caps the parse for sanity, I7).
_OVERVIEW_MAX_TAGS = 130


def _slugify_tag(raw: str) -> str:
    """Normalise one keyword to a short lowercase hyphenated tag (llm_wiki tag-cloud style)."""
    s = raw.strip().lower().strip("#").strip()
    s = re.sub(r"[^\w\s-]", "", s)  # drop punctuation except hyphen/underscore
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s


def _extract_overview_keyword_tags(body: str) -> tuple[list[str], str]:
    """
    Pull the trailing ``TAGS: kw1, kw2, ...`` line the overview prompt asks for (F3 tag cloud,
    current llm_wiki parity) out of the narrative body.

    Returns (tags, body_without_the_tags_line). The line is matched case-insensitively on the LAST
    non-empty line; keywords are slugified, de-duplicated (order-preserving), and capped. If no
    TAGS line is present (older prompt / degraded model) → ([], body) — always degrade-safe.
    """
    lines = (body or "").rstrip().splitlines()
    idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            if re.match(r"^\s*tags\s*:", lines[i], re.IGNORECASE):
                idx = i
            break  # only consider the LAST non-empty line
    if idx is None:
        return [], body
    raw = re.sub(r"^\s*tags\s*:", "", lines[idx], flags=re.IGNORECASE)
    seen: set[str] = set()
    tags: list[str] = []
    for tok in raw.split(","):
        slug = _slugify_tag(tok)
        if slug and slug not in seen:
            seen.add(slug)
            tags.append(slug)
        if len(tags) >= _OVERVIEW_MAX_TAGS:
            break
    new_body = "\n".join(lines[:idx]).rstrip() + "\n"
    return tags, new_body


async def _write_and_index_overview(narrative: str, *, lang: str | None = None) -> None:
    """
    OVERWRITE vault/wiki/overview.md with valid frontmatter (I5) + index it as a Page (I1).

    Frontmatter (ADR-0067 D6/P2-5 — LLM Wiki 1:1 overview meta page): emitted in the SAME
    key order the D2 generated-page serializer uses — `type, title, created, updated, tags,
    related` — plus a `sources` key. `related`/`sources` are EMPTY lists (the overview is a
    meta page: ADR-0067 D2 permits `sources: []` here, and it carries no outbound wikilink
    seed). `created` is PRESERVED across regenerations (read from the prior on-disk file);
    `updated` always advances to today. `lang`/non-empty `sources` are NOT emitted (D2). The
    file is rebuilt from scratch (full overwrite — F3 regeneration), then a Page row is upserted
    via persist_metadata (key by (vault_id, file_path), hash over the exact file bytes) and
    embedded via upsert_vector so GET /pages returns it and the nav Overview section shows 1.

    Open-Questions closing block (ADR-0067 D6/P1-1): a DETERMINISTIC (no LLM) `## Open Questions`
    / `## Domande Aperte` numbered list of live `type=query` page wikilinks is appended AFTER the
    LLM body (idempotent — the same query set yields the same block; omitted when zero queries).
    """
    title, body = _extract_overview_title(narrative)
    # F3 tag cloud (current llm_wiki parity): pull the trailing `TAGS:` line into frontmatter tags.
    keyword_tags, body = _extract_overview_keyword_tags(body)

    # ── Open-Questions closing block (deterministic — no LLM, I1/K3, ADR-0067 D6/P1-1) ──
    open_questions = await _build_open_questions_block(lang)
    if open_questions:
        body = body.rstrip() + "\n\n" + open_questions + "\n"

    overview_path = settings.wiki_dir / "overview.md"
    # created/updated (LLM Wiki parity): preserve `created` from the prior on-disk overview so it
    # is STABLE across regenerations; `updated` always advances to today. Mirrors write_wiki_page.
    _today = datetime.now(UTC).strftime("%Y-%m-%d")
    _created = _today
    if overview_path.exists():
        try:
            _prior_created = frontmatter.load(str(overview_path)).metadata.get("created")
            if _prior_created:
                _created = str(_prior_created)
        except Exception as _created_exc:  # noqa: BLE001 — best-effort; fall back to today
            logger.debug(
                "_write_and_index_overview: could not read prior 'created': %s", _created_exc
            )

    # D2 key order (type, title, created, updated, tags, related) + empty related/sources for the
    # meta page. sort_keys=False so the on-disk order matches the D2 generated-page serializer.
    ordered: dict[str, Any] = {
        "type": "overview",
        "title": title,
        "created": _created,
        "updated": _today,
    }
    if keyword_tags:
        ordered["tags"] = keyword_tags
    ordered["related"] = []
    ordered["sources"] = []
    post = frontmatter.Post(body, **ordered)
    serialized = frontmatter.dumps(post, sort_keys=False)
    file_text = serialized + "\n"

    overview_path.parent.mkdir(parents=True, exist_ok=True)
    overview_path.write_text(file_text, encoding="utf-8")

    await _index_overview_file(file_text, title, keyword_tags or None)
    logger.info(
        "_update_overview: regenerated + indexed overview.md (title=%r, %d tags, open_q=%s)",
        title,
        len(keyword_tags),
        bool(open_questions),
    )


# Max live `type=query` pages listed in the overview Open-Questions block (ADR-0067 D6/P1-1;
# bounded indexed read — I1/I7). Newest-first, deterministic (idempotent regen).
_OVERVIEW_OPEN_QUESTIONS_MAX = 30


async def _build_open_questions_block(lang: str | None) -> str:
    """
    Build the DETERMINISTIC `## Open Questions` closing block for the overview (ADR-0067 D6/P1-1,
    LLM Wiki `## Tensioni Irrisolte … — N Query Aperte` parity). NO LLM call.

    Content: a numbered list of `[[Title]]` wikilinks to the vault's live `type=query` pages,
    from a BOUNDED indexed DB query (I1 — no vault re-scan), newest-first, capped at
    ``_OVERVIEW_OPEN_QUESTIONS_MAX``. Ordering is (created_at DESC, title ASC) so regenerating
    with the same query set yields a BYTE-IDENTICAL block (idempotent — K3/I1).

    Localised by the overview output language: an Italian overview gets `## Domande Aperte`,
    every other language gets `## Open Questions`. Returns "" (block omitted) when zero live
    query pages exist, so a vault without open questions carries no empty section.
    """
    from sqlalchemy import select

    try:
        async with get_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(Page.title)
                        .where(
                            Page.vault_id == settings.vault_id,
                            Page.deleted_at.is_(None),
                            Page.page_type == "query",
                            Page.title.isnot(None),
                        )
                        .order_by(Page.created_at.desc(), Page.title.asc())
                        .limit(_OVERVIEW_OPEN_QUESTIONS_MAX)
                    )
                ).all()
            )
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail overview on the query
        logger.debug("_build_open_questions_block: query-page read failed (non-fatal): %s", exc)
        return ""

    titles = [(t or "").strip() for (t,) in rows if (t or "").strip()]
    if not titles:
        return ""

    heading = "## Domande Aperte" if (lang or "").lower().startswith("it") else "## Open Questions"
    lines = [heading, ""]
    for i, t in enumerate(titles, start=1):
        lines.append(f"{i}. [[{t}]]")
    return "\n".join(lines)


async def _index_overview_file(file_text: str, title: str, tags: list[str] | None = None) -> None:
    """
    Upsert the Page row for wiki/overview.md (type="overview") from the given file bytes (I1).

    Reuses the existing live row's id when present (upsert by (vault_id, file_path)); content_hash
    hashes the EXACT file bytes (matches GET /pages/{id}/content recompute). Embeds the body via
    upsert_vector. index.md / log.md get the SAME treatment via _index_index_and_log_files (D4).
    """
    from sqlalchemy import select

    async with get_session() as _id_sess:
        existing = (
            await _id_sess.execute(
                select(Page).where(
                    Page.vault_id == settings.vault_id,
                    Page.file_path == OVERVIEW_REL_PATH,
                    Page.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    page_id = existing.id if existing is not None else uuid.uuid4()

    file_bytes = file_text.encode("utf-8")
    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=OVERVIEW_REL_PATH,
        title=title,
        page_type="overview",
        sources=None,
        tags=tags,
        content_hash=_sha256(file_bytes),
        source_mtime_ns=0,
    )
    # Embed the narrative body (frontmatter excluded) for retrieval parity with wiki pages.
    body_for_embedding = _strip_leading_frontmatter(file_text)
    await upsert_vector(
        page_id=page_id,
        text=body_for_embedding,
        file_path=OVERVIEW_REL_PATH,
        title=title,
        page_type="overview",
        vault_id=settings.vault_id,
    )


async def _index_aggregate_file(rel_path: str, page_type: str) -> None:
    """
    Upsert a Page row for a disk-maintained aggregate file (D4) — index.md / log.md — mirroring
    _index_overview_file so it becomes a graph node (wiki-graph.ts:182-209 parity). Reads the file
    the per-page writers (update_index / append_log) already produced, upserts by
    (vault_id, file_path) reusing any live row's id, hashes the EXACT file bytes, and embeds the
    frontmatter-stripped body. Title is read from the frontmatter, falling back to the filename.

    Best-effort: a missing file (nothing written yet) is a no-op; never raises into ingest (the
    graph node is additive — a failure here must not fail the run, D4 / I7 degrade-safe).
    """
    from sqlalchemy import select

    abs_path = settings.vault_root / rel_path
    if not abs_path.exists():
        return
    file_text = abs_path.read_text(encoding="utf-8")
    try:
        meta = frontmatter.loads(file_text).metadata
        title = str(meta.get("title") or Path(rel_path).stem)
    except Exception:  # noqa: BLE001 — malformed frontmatter → fall back to the filename stem
        title = Path(rel_path).stem

    async with get_session() as _id_sess:
        existing = (
            await _id_sess.execute(
                select(Page).where(
                    Page.vault_id == settings.vault_id,
                    Page.file_path == rel_path,
                    Page.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    page_id = existing.id if existing is not None else uuid.uuid4()

    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel_path,
        title=title,
        page_type=page_type,
        sources=None,
        tags=None,
        content_hash=_sha256(file_text.encode("utf-8")),
        source_mtime_ns=0,
    )
    await upsert_vector(
        page_id=page_id,
        text=_strip_leading_frontmatter(file_text),
        file_path=rel_path,
        title=title,
        page_type=page_type,
        vault_id=settings.vault_id,
    )


async def _index_index_and_log_files() -> None:
    """
    Upsert Page rows for wiki/index.md (type=index) and wiki/log.md (type=log) so both render as
    graph nodes (D4, ADR-0063 §9 — wiki-graph.ts:182-209 makes a node for every wiki/*.md except
    type:query). Called after the per-page writers maintain those files (update_index / append_log
    run inside write_wiki_page). Fire-and-forget: each file is indexed independently and a failure
    on one logs a WARNING without blocking the other or the ingest run (I7 degrade-safe).
    """
    for rel_path, page_type in ((INDEX_REL_PATH, INDEX_TYPE), (LOG_REL_PATH, LOG_TYPE)):
        try:
            await _index_aggregate_file(rel_path, page_type)
        except Exception as exc:  # noqa: BLE001 — additive graph node; never fail ingest (D4/I7)
            logger.warning(
                "_index_index_and_log_files: failed to index %s (non-fatal): %s", rel_path, exc
            )


async def _index_existing_overview_if_present() -> None:
    """
    If overview.md already exists on disk but is not yet indexed as a Page, index it (degrade
    path). Ensures the nav Overview section can populate from a previously-regenerated file even
    when the current run's provider call is unavailable/failed. Best-effort — never raises.
    """
    overview_path = settings.wiki_dir / "overview.md"
    if not overview_path.exists():
        return
    try:
        file_text = overview_path.read_text(encoding="utf-8")
        meta = frontmatter.loads(file_text).metadata
        title = str(meta.get("title") or getattr(settings, "overview_title", "Overview"))
        await _index_overview_file(file_text, title)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_index_existing_overview_if_present: skipped (non-fatal): %s", exc)


async def _index_bootstrap_file(
    *,
    vault_root: Path,
    vault_id: str,
    rel_path: str,
    page_type: str,
) -> None:
    """
    Upsert a Page row for one bootstrap meta-file (overview.md / index.md / log.md)
    for an EXPLICIT vault, reading the file from *vault_root*/*rel_path* on disk.

    Mirrors _index_aggregate_file but accepts vault_root/vault_id explicitly so the
    caller (bootstrap at project-creation time) can index files for a newly-created
    vault without touching settings.vault_id (which still points at the active vault).

    I1-compliant: targeted write of one known file, no directory scan.
    Idempotent: upserts by (vault_id, file_path) — safe to re-run.
    Best-effort: a missing file is a no-op; caller wraps in try/except.
    """
    from sqlalchemy import select

    abs_path = vault_root / rel_path
    if not abs_path.exists():
        return
    file_text = abs_path.read_text(encoding="utf-8")
    try:
        meta = frontmatter.loads(file_text).metadata
        title = str(meta.get("title") or Path(rel_path).stem)
    except Exception:  # noqa: BLE001 — malformed frontmatter → fall back to filename stem
        title = Path(rel_path).stem

    async with get_session() as _id_sess:
        existing = (
            await _id_sess.execute(
                select(Page).where(
                    Page.vault_id == vault_id,
                    Page.file_path == rel_path,
                    Page.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    page_id = existing.id if existing is not None else uuid.uuid4()

    file_bytes = file_text.encode("utf-8")
    await persist_metadata(
        page_id=page_id,
        vault_id=vault_id,
        file_path=rel_path,
        title=title,
        page_type=page_type,
        sources=None,
        tags=None,
        content_hash=_sha256(file_bytes),
        source_mtime_ns=0,
    )
    await upsert_vector(
        page_id=page_id,
        text=_strip_leading_frontmatter(file_text),
        file_path=rel_path,
        title=title,
        page_type=page_type,
        vault_id=vault_id,
    )


async def index_bootstrap_meta_files(*, vault_root: Path, vault_id: str) -> None:
    """
    Index wiki/overview.md, wiki/index.md, and wiki/log.md for a newly-created vault (NC-3).

    I1-compliant: targeted index of 3 known files, no vault scan. Idempotent (upsert by
    (vault_id, file_path)). Best-effort: each file is indexed independently; a failure on
    one logs a WARNING without blocking the others or the project-creation response.

    Called from POST /projects immediately after bootstrap_vault_at() writes the scaffold
    files so GET /pages returns the meta rows without waiting for the first watcher event.

    vault_root: absolute path to the new vault (the project directory).
    vault_id:   the new project's id (e.g. "my-vault") — NOT settings.vault_id.
    """
    from app.ingest.schemas import INDEX_TYPE, LOG_TYPE  # noqa: PLC0415 — avoid top-level cycle

    for rel_path, page_type in (
        (OVERVIEW_REL_PATH, "overview"),
        (INDEX_REL_PATH, INDEX_TYPE),
        (LOG_REL_PATH, LOG_TYPE),
    ):
        try:
            await _index_bootstrap_file(
                vault_root=vault_root,
                vault_id=vault_id,
                rel_path=rel_path,
                page_type=page_type,
            )
        except Exception as exc:  # noqa: BLE001 — additive graph node; never fail bootstrap (I7)
            logger.warning(
                "index_bootstrap_meta_files: failed to index %s for vault %s (non-fatal): %s",
                rel_path,
                vault_id,
                exc,
            )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Filesystem-safe, unicode-tolerant slug for a page filename (I5-friendly)."""
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


# ── Factored helpers (reused by v0.2 orchestrated loop) ───────────────────────


async def persist_metadata(
    *,
    page_id: uuid.UUID,
    vault_id: str,
    file_path: str,
    title: str | None,
    page_type: str | None,
    sources: list[str] | None,
    content_hash: str,
    source_mtime_ns: int,
    tags: list[str] | None = None,
    generation_key: str | None = None,
    summary: str | None = None,
) -> None:
    """
    Upsert the `pages` row for *page_id* inside a single Postgres transaction.

    Handles both INSERT (new page) and UPDATE (re-ingest of existing page).
    Clears deleted_at on resurrection (ADR-0005 — same file_path recreated).

    `tags` (K6 navigation, nashsu/llm_wiki parity) is persisted exactly like `sources`
    (JSONB list; None when absent). Additive keyword — existing callers that omit it write
    NULL, preserving backward compatibility.

    `summary` (K3 gloss, 1.9.4 W6, PF-INDEX-GLOSS-1) mirrors `generation_key`'s
    preserve-if-omitted semantics on UPDATE: a caller that did not recompute the body (e.g.
    a metadata-only tag/type change) passes nothing and the existing summary is left
    untouched, rather than being wiped to NULL. Callers that DID rewrite the body pass the
    freshly extracted summary (``app.wiki.summary.extract_first_paragraph_summary``) so it
    stays in sync with the content. On INSERT, a None summary is written as NULL (backfilled
    later by ``backend/scripts/backfill_page_summary.py`` for pre-existing pages).
    """
    from sqlalchemy import select

    now = datetime.now(UTC)

    async with get_session() as session:
        row = await session.execute(select(Page).where(Page.id == page_id))
        page = row.scalar_one_or_none()

        if page is None:
            page = Page(
                id=page_id,
                vault_id=vault_id,
                file_path=file_path,
                title=title,
                page_type=page_type,
                sources=sources,
                tags=tags,
                generation_key=generation_key,
                summary=summary,
                content_hash=content_hash,
                source_mtime_ns=source_mtime_ns,
                qdrant_point_id=page_id,  # == pages.id (ADR-0002)
                deleted_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(page)
        else:
            page.title = title
            page.page_type = page_type
            page.sources = sources
            page.tags = tags
            # Preserve a corpus identity through metadata-only re-index operations. The shared
            # writer and raw frontmatter ingest pass the key when present; ordinary callers omit
            # it and must never erase an existing indexed identity accidentally.
            if generation_key is not None:
                page.generation_key = generation_key
            # Preserve the existing gloss through metadata-only updates (tags/type changes) that
            # did not recompute the body (K3, 1.9.4 W6) — only overwrite when a caller passes one.
            if summary is not None:
                page.summary = summary
            page.content_hash = content_hash
            page.source_mtime_ns = source_mtime_ns
            page.qdrant_point_id = page_id
            page.deleted_at = None  # resurrect if previously deleted
            page.updated_at = now


async def upsert_vector(
    *,
    page_id: uuid.UUID,
    text: str,
    file_path: str,
    title: str | None,
    page_type: str | None,
    vault_id: str,
) -> None:
    """
    Compute an embedding via EmbeddingClient (I9 — calls EMBEDDING_URL) and upsert to Qdrant.

    Point id == page_id (ADR-0002).
    Payload = {file_path, title, type, vault_id} (AC-QD-2, BE-PERF-3).

    When ``settings.embeddings_enabled`` is False (ADR-0030 §2.2) this returns early WITHOUT
    embedding or upserting: no EmbeddingClient call, no Qdrant point. Every other ingest step
    (Postgres metadata, K5 wikilinks, K4 log, dataVersion bump) still runs in the caller, so
    the page stays fully indexed in Postgres and ingest remains a single incremental pass (I1).
    Toggling the flag never triggers a bulk re-embed.
    """
    from app.config_overrides import effective_bool  # noqa: PLC0415

    if not effective_bool("embeddings_enabled", settings.embeddings_enabled):
        logger.info(
            "upsert_vector: embeddings disabled (effective EMBEDDINGS_ENABLED=false) — "
            "skipping embed + Qdrant upsert for page_id=%s (file_path=%s)",
            page_id,
            file_path,
        )
        return

    client = get_embedding_client()
    try:
        vector = await client.embed(text)
    except EmbeddingError as exc:
        # Degrade to a vector-less page (I1/I7): a token-dense body the embedding server rejects
        # (bge-m3 context 500, even after the client's bounded shrink-retry) must NOT abort the
        # whole document ingest. The page is already fully indexed by the caller (Postgres
        # metadata, K5 wikilinks, K4 log, dataVersion) — it just lacks a dense Qdrant vector, so
        # retrieval still finds it via the graph-expansion + lexical phases. Only EmbeddingError
        # is swallowed; a Qdrant/upsert failure below still surfaces.
        logger.warning(
            "upsert_vector: embedding failed for page_id=%s (file_path=%s) — persisting a "
            "vector-less page (no Qdrant point). reason=%s",
            page_id,
            file_path,
            exc,
        )
        return
    await upsert_point(
        page_id=page_id,
        vector=vector,
        file_path=file_path,
        title=title,
        page_type=page_type,
        vault_id=vault_id,
    )


async def append_log(
    rel_path: str,
    *,
    action: str = "indexed",
    page_type: str | None = None,
    title: str | None = None,
) -> None:
    """
    Append one entry to vault/wiki/log.md (K4, AC-K4-1).

    Format (nashsu/llm_wiki §1.8 parity, ADR-0078):

        ## [YYYY-MM-DD] ingest | Title
        ## [YYYY-MM-DD] deleted | wiki/path/to/file.md

    Each call appends EXACTLY ONE ``## [date] verb | subject`` section heading (AC-K4-1).
    ``action`` is a lowercase verb (``indexed`` → written as "ingest" per llm_wiki parity;
    ``deleted`` → written as "deleted"). ``title`` is the page display title for indexed
    entries; ``rel_path`` is the fallback for deletions or when no title is available.
    ``page_type`` is accepted for backward compatibility but not included in the output
    (the llm_wiki format does not carry per-entry type metadata).

    File is opened in 'a' (append) mode — never truncated (AC-K4-2).
    Never writes to vault/raw/ (AC-K1-5).
    """
    log_path = settings.log_md_path
    # Ensure log.md exists (vault bootstrap normally creates it, but be defensive)
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8")

    day = datetime.now(UTC).strftime("%Y-%m-%d")

    # Map Synapse action to llm_wiki log verb: "indexed" → "ingest" (§1.8 parity).
    # All other actions (e.g. "deleted") are written as-is.
    verb = "ingest" if action == "indexed" else action
    subject = title if title else rel_path
    # llm_wiki format: ## [YYYY-MM-DD] ingest | Title  (one self-contained heading per entry)
    entry = f"## [{day}] {verb} | {subject}\n"

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n" + entry)


async def bump_version() -> None:
    """
    Increment vault_state.data_version by 1 for this vault (AC-F16dv-2).

    Monotonic non-decreasing; only called on successful content-changing ingest.
    Startup, restart, deletion, GET requests, and skipped ingests do NOT call this.
    """
    from sqlalchemy import select, update

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Seed it now if somehow missing (startup should have done this)
            state = VaultState(vault_id=settings.vault_id, data_version=1)
            state.updated_at = datetime.now(UTC)
            session.add(state)
        else:
            await session.execute(
                update(VaultState)
                .where(VaultState.vault_id == settings.vault_id)
                .values(
                    data_version=VaultState.data_version + 1,
                    updated_at=datetime.now(UTC),
                )
            )


# ── Private helpers ────────────────────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_path(path: Path) -> str:
    """
    Return a consistent relative path string for use as Postgres file_path key.

    Prefer a path relative to vault_root; fall back to the absolute string if
    the path is outside the vault (unusual but handled gracefully).
    """
    try:
        return str(path.resolve().relative_to(settings.vault_root))
    except ValueError:
        return str(path.resolve())


async def _load_page(rel_path: str) -> Page | None:
    """Load a live Page row by relative file_path, or None if absent/deleted."""
    from sqlalchemy import select

    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.file_path == rel_path,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()
        # Expunge from session so we can use the object outside the context manager
        if page is not None:
            session.expunge(page)
        return page


async def _touch_mtime(page_id: uuid.UUID, mtime_ns: int) -> None:
    """Update only source_mtime_ns so the next event re-hits the fast path."""
    from sqlalchemy import update

    async with get_session() as session:
        await session.execute(
            update(Page).where(Page.id == page_id).values(source_mtime_ns=mtime_ns)
        )


# _enqueue_review_items is REMOVED (ADR-0034 §4 — replaced by propose_reviews in ops/review.py).
# The per-page question-spam hook (ADR-0025 §3.3) is superseded by the single bounded
# once-per-run propose_reviews stage. The call site above now imports propose_reviews directly.


def _parse_frontmatter(raw_bytes: bytes, rel_path: str) -> dict[str, object]:
    """
    Parse YAML frontmatter from raw file bytes (K6).

    Tolerant: missing fields → empty dict (caller treats missing keys as NULL).
    No exception raised for missing frontmatter block (AC-K6-2/3).
    Issues a warning for missing required fields.
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    try:
        doc = frontmatter.loads(text)
        meta: dict[str, object] = dict(doc.metadata)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ingest_file: frontmatter parse error in %s: %s — treating as empty metadata",
            rel_path,
            exc,
        )
        return {}

    # K6 (YAML frontmatter: type/title/sources) applies only to wiki/ pages.
    # Raw sources under raw/ are plain documents and need not carry frontmatter —
    # emitting a WARNING for each missing field there is spurious noise (NC-2).
    # Relative paths from vault_root: wiki pages start with "wiki/" or "wiki\";
    # raw sources start with "raw/". Only warn for wiki paths; log DEBUG for raw.
    _is_wiki_path = rel_path.startswith("wiki/") or rel_path.startswith("wiki\\")
    for required in ("type", "title", "sources"):
        if required not in meta:
            if _is_wiki_path:
                logger.warning(
                    "ingest_file: missing frontmatter field %r in %s (AC-K6-2/3)",
                    required,
                    rel_path,
                )
            else:
                logger.debug(
                    "ingest_file: frontmatter field %r absent in raw file %s "
                    "(K6 not required for raw sources — NC-2)",
                    required,
                    rel_path,
                )

    return meta


# ── 1.7.0 PR2 decomposition re-exports ───────────────────────────────────────
# ingest_file / write_wiki_page / the context builders now live in sibling modules
# (context.py, writer.py, pipeline.py). They are re-exported here verbatim so every
# existing importer of ``app.ingest.orchestrator.<name>`` AND every monkeypatch target
# keeps resolving through this module unchanged (1.7.0 PR2 — pure extraction). __all__
# names the extracted seams so the re-exports read as an intentional façade, not dead
# imports; the module's own kept helpers stay importable by attribute as before.
from app.ingest.context import (  # noqa: E402
    _CATALOGUE_EXCLUDED_TYPES,
    _CATALOGUE_MAX_CHARS,
    _CATALOGUE_MAX_TITLES,
    _FOLDER_CONTEXT_MAX_CHARS,
    _FOLDER_CONTEXT_MAX_SEGMENTS,
    _FOLDER_CONTEXT_ROOTS,
    _folder_context,
    _folder_context_block,
    _load_existing_pages_catalogue,
    _load_ingest_context,
    _load_vault_context,
)
from app.ingest.pipeline import (  # noqa: E402
    _LANGUAGE_GUARD_EXEMPT_TYPES,
    IngestError,
    IngestResult,
    IngestRunResult,
    _delegate_ingest,
    _derive_run_status,
    _drop_wrong_language_pages,
    _enrich_wikilinks_for_delegated,
    _ensure_source_summary,
    _ensure_source_summary_for_delegated,
    _finalize_ingest_run,
    _is_raw_sources_page,
    _open_ingest_run,
    _page_type_counts,
    _page_type_counts_for_ids,
    _propose_reviews_for_delegated,
    _purpose_suggestion_for_delegated,
    _resolve_fallback_provider_config,
    _resolve_ingest_provider_config,
    _schema_suggestion_for_delegated,
    _seed_accumulator,
    _write_ingest_run,
    delete_file,
    ingest_file,
    run_ingest_pipeline,
)
from app.ingest.writer import (  # noqa: E402
    _ACRONYM_FOLD,
    _CANON_PARENS_RE,
    _CANON_PUNCT_RE,
    _LEGAL_SUFFIX_TOKENS,
    _RAW_SOURCES_MARKER,
    _RAW_SOURCES_PREFIX,
    _find_canonical_entity_page,
    _is_owned_only_by_source,
    _resolve_canonical_entity_key,
    _resolve_related_slugs,
    _source_identity,
    _source_identity_stem,
    _strip_leading_frontmatter,
    write_wiki_page,
)

__all__ = [
    "IngestError",
    "IngestResult",
    "IngestRunResult",
    "_ACRONYM_FOLD",
    "_CANON_PARENS_RE",
    "_CANON_PUNCT_RE",
    "_CATALOGUE_EXCLUDED_TYPES",
    "_CATALOGUE_MAX_CHARS",
    "_CATALOGUE_MAX_TITLES",
    "_FOLDER_CONTEXT_MAX_CHARS",
    "_FOLDER_CONTEXT_MAX_SEGMENTS",
    "_FOLDER_CONTEXT_ROOTS",
    "_LANGUAGE_GUARD_EXEMPT_TYPES",
    "_LEGAL_SUFFIX_TOKENS",
    "_RAW_SOURCES_MARKER",
    "_RAW_SOURCES_PREFIX",
    "_delegate_ingest",
    "_derive_run_status",
    "_drop_wrong_language_pages",
    "_enrich_wikilinks_for_delegated",
    "_ensure_source_summary",
    "_ensure_source_summary_for_delegated",
    "_finalize_ingest_run",
    "_find_canonical_entity_page",
    "_folder_context",
    "_folder_context_block",
    "_is_owned_only_by_source",
    "_is_raw_sources_page",
    "_load_existing_pages_catalogue",
    "_load_ingest_context",
    "_load_vault_context",
    "_open_ingest_run",
    "_page_type_counts",
    "_page_type_counts_for_ids",
    "_propose_reviews_for_delegated",
    "_purpose_suggestion_for_delegated",
    "_resolve_canonical_entity_key",
    "_resolve_fallback_provider_config",
    "_resolve_ingest_provider_config",
    "_resolve_related_slugs",
    "_schema_suggestion_for_delegated",
    "_seed_accumulator",
    "_source_identity",
    "_source_identity_stem",
    "_strip_leading_frontmatter",
    "_write_ingest_run",
    "delete_file",
    "ingest_file",
    "run_ingest_pipeline",
    "write_wiki_page",
]

# W7 / 1.9.4 — compatibility-facade deprecation notice.
# This module remains the single monkeypatch surface for 18+ test modules (see
# module docstring §1) and is KEPT until 2.0.0 when the test-decoupling pass is
# complete. Until then, this DeprecationWarning signals to callers that they
# should migrate to the cohesive siblings:
#   • app.ingest.pipeline   — ingest_file / delete_file / run_ingest_pipeline
#   • app.ingest.writer     — write_wiki_page
#   • app.ingest.context    — _load_ingest_context / vault context helpers
# Production code within the backend already imports from the siblings directly
# (mcp/server.py, routers/, etc.). External integrators importing this module will
# see the warning unless they suppress DeprecationWarning (the default in CPython
# for non-__main__ code; pytest surfaces it as a test warning, not a failure).
warnings.warn(
    "app.ingest.orchestrator is a compatibility facade scheduled for removal in "
    "Synapse 2.0.0 (W7). Import directly from app.ingest.pipeline, "
    "app.ingest.writer, or app.ingest.context instead.",
    DeprecationWarning,
    stacklevel=2,
)
