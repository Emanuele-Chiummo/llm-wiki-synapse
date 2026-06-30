"""
Wikilink-enrichment post-pass (ADR-0036) — restores the F4 *direct link ×3* signal.

After the orchestrated ingest run has written all pages, this once-per-run pass asks the
provider for STRUCTURED SUBSTITUTIONS — `{mention, target_title}` pairs — and then
DETERMINISTICALLY applies them, single-mention, into page BODIES only. The model never
rewrites the document (the R1 discipline, ADR-0036 §3): the worst it can do is propose a bad
substitution, which step-5 validation drops.

Invariants (ADR-0036 §8):
  I6 — routes through ``resolve_provider_config("ingest", vault_id)`` + ``resolve_provider`` +
       ``InferenceProvider.chat()``. No hardcoded backend/model; no-provider → skip.
  I7 — ≤1 provider call, no loop/retry; ``WIKILINK_ENRICH_*`` caps + ``token_budget`` + ``wait_for``
       timeout; anti-spam gate skips trivial runs at zero cost; ``total_cost_usd`` logged with the
       $1 anomaly WARNING; fire-and-forget — failure never fails the ingest.
  I5 — wikilinks go into BODIES only; the frontmatter block is preserved byte-for-byte.
  I1 — candidate titles are a bounded indexed ``pages.title`` read; only edited pages are
       re-indexed (via ``reindex_wiki_page_body``); ``data_version`` is bumped ONCE for the pass.

Public entry point: ``enrich_wikilinks(written_pages, vault_id)`` — wired fire-and-forget into
``run_ingest_pipeline`` BEFORE ``propose_reviews`` (so proposals see the enriched link graph).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.db import get_session
from app.ingest.provider.base import UsageAccumulator
from app.models import Page
from app.wiki.links import _WIKILINK_RE

logger = logging.getLogger(__name__)

# $1 cost-anomaly threshold — same as the ingest path (ADR-0009 §3 / ADR-0036 §4).
_COST_ANOMALY_THRESHOLD_USD = 1.00


# ── DTOs ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WikilinkSubstitution:
    """One LLM-proposed substitution: a body substring that refers to an existing page."""

    page_id: uuid.UUID
    mention: str
    target_title: str


@dataclass
class EnrichResult:
    """Outcome of one enrichment pass (consumed by the caller for logging/tests)."""

    pages_enriched: int = 0
    links_added: int = 0
    total_cost_usd: float = 0.0
    skipped_reason: str | None = None  # set when the pass short-circuited (no cost)
    applied: list[WikilinkSubstitution] = field(default_factory=list)


# ── Public entry point ─────────────────────────────────────────────────────────


async def enrich_wikilinks(written_pages: list[Page], vault_id: str) -> EnrichResult:
    """
    Run the once-per-run wikilink-enrichment post-pass (ADR-0036).

    Fire-and-forget: this function NEVER raises into the ingest critical path — every failure
    path returns an :class:`EnrichResult` (the pages are already written and valid; enrichment
    is additive). The caller in ``run_ingest_pipeline`` still wraps it in try/except defensively.
    """
    try:
        return await _enrich_wikilinks_inner(written_pages, vault_id)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget (ADR-0036 §4 / Do-NOT #9)
        logger.warning("enrich_wikilinks: pass failed (non-fatal, pages already written): %s", exc)
        return EnrichResult(skipped_reason=f"error:{exc.__class__.__name__}")


async def _enrich_wikilinks_inner(written_pages: list[Page], vault_id: str) -> EnrichResult:
    # ── Master gate (zero-cost opt-out) ──────────────────────────────────────────
    if not bool(getattr(settings, "wikilink_enrich_enabled", True)):
        return EnrichResult(skipped_reason="disabled")

    # Only enrich live wiki pages that have a title (the link source must be addressable).
    enrichable = [p for p in written_pages if p.title and (p.file_path or "").startswith("wiki/")]
    if not enrichable:
        return EnrichResult(skipped_reason="no_enrichable_pages")

    # ── Load each page's on-disk body (frontmatter preserved separately, I5) ──────
    bodies: dict[uuid.UUID, tuple[str, str]] = {}  # page_id → (frontmatter_block, body)
    total_body_chars = 0
    for page in enrichable:
        split = _read_page_split(page)
        if split is None:
            continue
        bodies[page.id] = split
        total_body_chars += len(split[1])

    if not bodies:
        return EnrichResult(skipped_reason="no_readable_bodies")

    # ── Anti-spam / cost gate (ADR-0036 §2.1 step 2) ─────────────────────────────
    min_chars = int(getattr(settings, "wikilink_enrich_min_chars", 200))
    if total_body_chars < min_chars:
        return EnrichResult(skipped_reason="below_min_chars")

    # ── Candidate target set: existing vault titles (bounded indexed read, I1) ───
    max_candidates = int(getattr(settings, "wikilink_enrich_max_candidates", 500))
    candidate_titles = await _load_candidate_titles(vault_id, max_candidates)
    # Own-title set (a page may not link itself; also lets us treat just-written peers as targets).
    own_titles = {(p.title or "") for p in enrichable}
    candidate_titles |= own_titles
    if not candidate_titles:
        return EnrichResult(skipped_reason="no_candidates")

    # ── Resolve the ingest provider (I6 — "no provider" → skip, never default) ────
    resolved = await _resolve_provider(vault_id)
    if resolved is None:
        logger.debug(
            "enrich_wikilinks: no ingest provider resolved (vault=%s) — skip (I6)", vault_id
        )
        return EnrichResult(skipped_reason="no_provider")
    provider, config_row = resolved

    token_budget = _coerce_int(
        getattr(config_row, "token_budget", None),
        int(getattr(settings, "wikilink_enrich_token_budget", 4_000)),
    )
    timeout_s = float(getattr(settings, "wikilink_enrich_timeout_seconds", 30.0))
    max_subs = int(getattr(settings, "wikilink_enrich_max_subs", 100))

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    instruction = _build_instruction(
        enrichable=enrichable,
        bodies=bodies,
        candidate_titles=sorted(candidate_titles),
        token_budget=token_budget,
    )

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    raw: str | None = None
    try:
        raw = await asyncio.wait_for(_chat_collect(provider, instruction), timeout=timeout_s)
    except TimeoutError:
        logger.warning(
            "enrich_wikilinks: provider call timed out after %.1fs (vault=%s) — "
            "apply zero substitutions (degrade)",
            timeout_s,
            vault_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich_wikilinks: provider call failed (vault=%s): %s", vault_id, exc)
    finally:
        # I7: cost logged per call regardless of outcome (truthful ledger).
        total_cost_usd = round(accumulator.total_cost_usd, 4)
        logger.info(
            "enrich_wikilinks provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            total_cost_usd,
            accumulator.calls,
        )
        if total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
            logger.warning(
                "COST ANOMALY: enrich_wikilinks total_cost_usd=%.4f exceeds $%.2f (vault=%s) — "
                "investigate runaway/misconfiguration",
                total_cost_usd,
                _COST_ANOMALY_THRESHOLD_USD,
                vault_id,
            )

    total_cost_usd = round(accumulator.total_cost_usd, 4)
    if raw is None:
        return EnrichResult(total_cost_usd=total_cost_usd, skipped_reason="provider_error")

    # ── Parse → validate → apply (deterministic) ─────────────────────────────────
    title_to_page = {(p.title or ""): p for p in enrichable}
    subs = _parse_substitutions(raw, title_to_page, candidate_titles)[:max_subs]

    result = await _apply_and_reindex(
        enrichable=enrichable,
        bodies=bodies,
        subs=subs,
        vault_id=vault_id,
    )
    result.total_cost_usd = total_cost_usd
    return result


# ── Apply + incremental re-index ─────────────────────────────────────────────────


async def _apply_and_reindex(
    *,
    enrichable: list[Page],
    bodies: dict[uuid.UUID, tuple[str, str]],
    subs: list[WikilinkSubstitution],
    vault_id: str,
) -> EnrichResult:
    """
    Validate + apply substitutions single-mention to bodies, then re-index ONLY the edited pages
    (I1) reusing ``reindex_wiki_page_body``; bump ``data_version`` ONCE for the whole pass.
    """
    from app.ingest.orchestrator import bump_version, reindex_wiki_page_body

    # Group validated substitutions by page; enforce one link per (page, target) — single-mention.
    by_page: dict[uuid.UUID, list[WikilinkSubstitution]] = {}
    for sub in subs:
        by_page.setdefault(sub.page_id, []).append(sub)

    page_by_id = {p.id: p for p in enrichable}
    result = EnrichResult()
    edited_any = False

    for page_id, page_subs in by_page.items():
        page = page_by_id.get(page_id)
        split = bodies.get(page_id)
        if page is None or split is None:
            continue
        fm_block, body = split

        new_body = body
        applied_targets: set[str] = set()
        page_links_added = 0
        for sub in page_subs:
            if sub.target_title in applied_targets:
                continue  # single-mention: one link per (page, target)
            # Self-link guard (defence-in-depth — also dropped at parse).
            if sub.target_title == (page.title or ""):
                continue
            wrapped = _apply_first_mention(new_body, sub.mention, sub.target_title)
            if wrapped is None:
                continue  # mention not found OUTSIDE an existing [[...]] span → drop
            new_body = wrapped
            applied_targets.add(sub.target_title)
            page_links_added += 1
            result.applied.append(sub)

        if page_links_added == 0:
            continue

        new_file_text = _rejoin(fm_block, new_body)
        await reindex_wiki_page_body(
            page=page,
            new_file_text=new_file_text,
            body_for_embedding=new_body,
            bump=False,  # batched: one bump for the whole pass (I1)
        )
        result.pages_enriched += 1
        result.links_added += page_links_added
        edited_any = True
        logger.info(
            "enrich_wikilinks: +%d link(s) on page %s (%r)",
            page_links_added,
            page_id,
            page.title,
        )

    # Single data_version bump for the whole pass (I1 — one bump, debounced FA2 recompute, I2).
    if edited_any:
        await bump_version()

    return result


# ── Deterministic substitution apply (body-only, first-occurrence, no double-wrap) ─


def _existing_link_spans(body: str) -> list[tuple[int, int]]:
    """Char ranges of existing [[...]] spans (so we never re-wrap inside one)."""
    return [(m.start(), m.end()) for m in _WIKILINK_RE.finditer(body)]


def _apply_first_mention(body: str, mention: str, target_title: str) -> str | None:
    """
    Wrap the FIRST occurrence of *mention* in *body* that is NOT already inside an existing
    ``[[…]]`` span as a wikilink: ``[[target_title]]`` when the surface text equals the title,
    else ``[[target_title|mention]]``. Returns the new body, or None if no eligible occurrence
    exists (mention absent, or every occurrence is already inside a link).
    """
    if not mention:
        return None
    spans = _existing_link_spans(body)
    search_from = 0
    while True:
        idx = body.find(mention, search_from)
        if idx == -1:
            return None
        end = idx + len(mention)
        # Skip occurrences that fall inside an existing [[...]] span (no double-wrap, Do-NOT #6).
        if any(s <= idx < e or s < end <= e for s, e in spans):
            search_from = idx + 1
            continue
        if mention == target_title:
            replacement = f"[[{target_title}]]"
        else:
            replacement = f"[[{target_title}|{mention}]]"
        return body[:idx] + replacement + body[end:]


# ── Frontmatter-safe split / rejoin (preserve the YAML block byte-for-byte, I5) ──


def _read_page_split(page: Page) -> tuple[str, str] | None:
    """
    Read the page file and split it into (frontmatter_block, body) WITHOUT touching the
    frontmatter bytes. ``frontmatter_block`` includes the trailing ``---`` delimiter and the
    blank separator line so ``_rejoin`` reproduces the file exactly when the body is unchanged.
    Returns None if the file is missing/unreadable.
    """
    abs_path = (settings.vault_root / page.file_path).resolve()
    try:
        text = abs_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _split_frontmatter(text)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """
    Split *text* into (frontmatter_block, body). When the file opens with a ``---`` fence, the
    block is everything up to and including the closing ``---`` line plus the following newline;
    the body is the remainder. Files without frontmatter return ("", text).
    """
    if not text.startswith("---\n") and text != "---":
        return "", text
    # Find the closing delimiter line (a line that is exactly '---').
    lines = text.splitlines(keepends=True)
    # lines[0] is the opening '---\n'. Scan for the next bare '---' line.
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            block = "".join(lines[: i + 1])
            body = "".join(lines[i + 1 :])
            return block, body
    # Malformed (no closing fence): treat the whole thing as body to avoid corrupting it.
    return "", text


def _rejoin(frontmatter_block: str, body: str) -> str:
    """Reassemble the file from the preserved frontmatter block + the (possibly edited) body."""
    return frontmatter_block + body


# ── Candidate set + provider resolution ──────────────────────────────────────────


async def _load_candidate_titles(vault_id: str, max_candidates: int) -> set[str]:
    """
    Bounded indexed read of live ``pages.title`` for the vault (most-recent first — ADR-0036 §6
    risk 2). No bodies, no vault walk (I1).
    """
    from sqlalchemy import select

    async with get_session() as session:
        rows = await session.execute(
            select(Page.title)
            .where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
                Page.title.is_not(None),
            )
            .order_by(Page.updated_at.desc())
            .limit(max_candidates)
        )
        return {t for (t,) in rows.all() if t and t.strip()}


async def _resolve_provider(vault_id: str) -> tuple[Any, Any] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6). Returns (provider, config_row) or
    None when no provider_config resolves / DB unavailable. Never hardcodes a backend; never
    branches on isinstance/type/class-name. Mirrors ops/review.py::_resolve_review_provider.
    """
    from app.ingest.provider import resolve_provider
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich_wikilinks: provider resolution failed (vault=%s): %s", vault_id, exc)
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich_wikilinks: provider build failed (vault=%s): %s", vault_id, exc)
        return None
    return provider, config_row


def _coerce_int(raw: Any, fallback: int) -> int:
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value or fallback


async def _chat_collect(provider: Any, instruction: str) -> str:
    """
    ONE capability-agnostic ``provider.chat()`` turn, collecting the full text (I6). Same surface
    ops/review.py and ops/deep_research.py use — backend-neutral, no new ABC method.
    """
    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)
    return "".join(chunks).strip()


# ── Prompt + parse ───────────────────────────────────────────────────────────────


def _build_instruction(
    *,
    enrichable: list[Page],
    bodies: dict[uuid.UUID, tuple[str, str]],
    candidate_titles: list[str],
    token_budget: int,
) -> str:
    """
    Build the single substitution-only prompt (ADR-0036 §3). The model returns
    ``{"substitutions": [{page_id, mention, target_title}]}`` — it must NOT rewrite any page.
    """
    page_blocks: list[str] = []
    for page in enrichable:
        split = bodies.get(page.id)
        if split is None:
            continue
        body = split[1]
        page_blocks.append(
            f"### page_id: {page.id}\n### title: {page.title}\n"
            f"--- body ---\n{body}\n--- end body ---"
        )
    pages_section = "\n\n".join(page_blocks)
    titles_block = "\n".join(f"- {t}" for t in candidate_titles[:500]) or "(none)"

    return (
        "You are the wikilink-enrichment step of a self-organizing wiki. You are given freshly "
        "written page bodies and a list of EXISTING page titles in the vault. Your job is to find "
        "places in each body that MENTION an existing page, so they can be turned into "
        "[[wikilinks]].\n\n"
        "IMPORTANT: Do NOT rewrite or return any page content. Return ONLY a compact list of "
        "substitutions. Each substitution names an EXACT substring already present in that page's "
        "body and the existing page title it refers to.\n\n"
        f"# Existing vault page titles (the ONLY valid targets)\n{titles_block}\n\n"
        f"# Pages just written\n{pages_section}\n\n"
        'Return ONLY a JSON object with a single key "substitutions" whose value is a list of '
        "objects. Each object has keys:\n"
        "  page_id: the page_id of the body the mention occurs in (copy it exactly)\n"
        "  mention: the EXACT substring present in that body (verbatim, case-sensitive)\n"
        "  target_title: one of the existing vault page titles above (verbatim)\n\n"
        "Rules: the mention MUST occur verbatim in that page's body; target_title MUST be one of "
        "the listed titles; never link a page to itself; propose at most one substitution per "
        f"(page, target). Keep the output well under {token_budget} tokens. Return no prose, only "
        "the JSON object."
    )


def _parse_substitutions(
    raw: str,
    title_to_page: dict[str, Page],
    candidate_titles: set[str],
) -> list[WikilinkSubstitution]:
    """
    Parse + VALIDATE the substitution JSON (ADR-0036 §2.1 step 5, anti-hallucination). Drop any
    entry whose target_title is not a real candidate, whose mention is absent, whose page_id is
    not one of the written pages, or that is a self-link. Tolerant of fences/prose; never raises.
    """
    obj = _loads_json_lenient(raw)
    if obj is None:
        return []
    if isinstance(obj, dict):
        items = obj.get("substitutions", obj.get("links", []))
    elif isinstance(obj, list):
        items = obj
    else:
        return []
    if not isinstance(items, list):
        return []

    page_by_id = {str(p.id): p for p in title_to_page.values()}
    out: list[WikilinkSubstitution] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        page_id_raw = entry.get("page_id")
        mention = entry.get("mention")
        target = entry.get("target_title") or entry.get("target")
        if not isinstance(mention, str) or not isinstance(target, str):
            continue
        mention = mention.strip()
        target = target.strip()
        if not mention or not target:
            continue
        # target must be a real candidate (anti-hallucination).
        if target not in candidate_titles:
            continue
        page = page_by_id.get(str(page_id_raw))
        if page is None:
            continue
        # self-link drop.
        if target == (page.title or ""):
            continue
        out.append(WikilinkSubstitution(page_id=page.id, mention=mention, target_title=target))
    return out


def _loads_json_lenient(raw: str) -> Any | None:
    """Best-effort JSON parse tolerant of ```json fences / surrounding prose. None on failure."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return None
