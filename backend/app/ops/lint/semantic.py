"""
The LLM-backed opt-in semantic lint pass (I6/I7 — ADR-0037 §4.3).

Categories produced here: missing-xref / contradiction / stale-claim / missing-page /
suggestion. This is the ONLY module in the lint package that makes a provider ``chat()`` call
inside ``run_lint_scan``'s bounded loop; all plumbing rides ``app.ops._llm`` (extracted in
1.9.0 — BE-DUP-1) — NEVER duplicated here.

I6 CONTRACT: no isinstance / provider_type / class-name branching anywhere in this module.
I7 CONTRACT: one bounded ``asyncio.wait_for(LINT_TIMEOUT_SECONDS)``-wrapped provider call per
round; degrades to "" (deterministic findings stand) on timeout/error — never raises.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.config import settings
from app.ops._llm import bounded_chat_collect, clean_str, loads_json_lenient
from app.ops.lint._shared import (
    CANDIDATE_TITLES_MAX,
    VALID_CATEGORIES,
    VALID_SEVERITIES,
    FindingDTO,
)

logger = logging.getLogger(__name__)


# ── Semantic pass (ONE bounded provider call per round — I6/I7) ─────────────────


async def _semantic_pass(
    *,
    provider: Any,
    vault_id: str,
    page_digest: str,
    candidate_titles: list[str],
    already_found: list[str],
    token_budget: int,
    timeout_s: float,
) -> str:
    """
    ONE bounded provider.chat() turn for the semantic checks (ADR-0037 §4.3).

    Rides the chat() seam (backend-neutral — I6); cost recorded out of band on the bound
    accumulator. On timeout / error → returns "" (degrade; the deterministic findings stand).
    """
    instruction = _build_semantic_instruction(
        page_digest=page_digest,
        candidate_titles=candidate_titles,
        already_found=already_found,
        token_budget=token_budget,
    )
    try:
        return await asyncio.wait_for(
            bounded_chat_collect(provider, instruction), timeout=timeout_s
        )
    except TimeoutError:
        logger.warning(
            "_semantic_pass: provider call timed out after %.1fs (vault=%s) — "
            "deterministic findings only",
            timeout_s,
            vault_id,
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("_semantic_pass: provider call failed (vault=%s): %s", vault_id, exc)
        return ""


# ── Prompt + parse ──────────────────────────────────────────────────────────────


def _build_semantic_instruction(
    *,
    page_digest: str,
    candidate_titles: list[str],
    already_found: list[str],
    token_budget: int,
) -> str:
    """
    Build the single semantic-lint prompt (ADR-0037 §4.3).

    Asks for a JSON object {"findings": [...]} of health issues across the wiki. The model is
    told to return ONLY JSON and to NOT repeat any already-found description.
    """
    titles_block = "\n".join(f"- {t}" for t in candidate_titles[:CANDIDATE_TITLES_MAX]) or "(none)"
    already_block = "\n".join(f"- {d}" for d in already_found[:200]) or "(none)"
    # Language directive (llm_wiki languageRule parity): the semantic prompt was never
    # language-aware, so on an Italian vault descriptions came out in English. Localise the
    # human-facing `description` to the vault language (settings.overview_language); JSON keys and
    # the enum category/severity values stay English.
    lang = (getattr(settings, "overview_language", "") or "").strip()
    lang_directive = (
        (
            "# MANDATORY OUTPUT LANGUAGE\n"
            f"Write every finding's `description` and `target_title` in {lang} (ISO-639-1) — the "
            f"vault's language. Do NOT use English unless {lang!r} is 'en'. The JSON keys and the "
            "category/severity enum values stay in English.\n\n"
        )
        if lang
        else ""
    )
    return (
        lang_directive
        + "You are the LINT step of a self-organizing wiki (the third Karpathy operation: "
        "Ingest, Query, Lint). Health-check the wiki and report problems for a human to "
        "review. Do NOT fix anything — only report findings.\n\n"
        f"# Existing wiki page titles\n{titles_block}\n\n"
        f"# Page digest (title [type])\n{page_digest}\n\n"
        f"# Already-reported findings (do NOT repeat these)\n{already_block}\n\n"
        'Return ONLY a JSON object with a single key "findings" whose value is a list of '
        "objects. Each object has keys:\n"
        "  category: one of contradiction | stale-claim | missing-page | suggestion\n"
        "  severity: one of info | warning | error\n"
        "  description: a short string explaining the problem\n"
        "  target_title: REQUIRED for EVERY finding — the page (or subject) the finding is "
        "about. For contradiction/stale-claim/suggestion use the EXISTING page title it concerns "
        "(verbatim from the list above); for missing-page use the title that SHOULD exist. Never "
        "leave it null — if a finding is not about one specific page, use the most relevant page "
        "title from the list.\n\n"
        "Definitions: contradiction = conflicting claims across pages; stale-claim = superseded "
        "information; missing-page = a concept mentioned with NO page at all; "
        "suggestion = a question or source worth adding to the wiki. "
        "(Note: missing-xref is handled deterministically; do NOT emit it.) "
        "IMPORTANT: only report missing-page when the concept has NO existing page. If a page "
        "already exists in the 'Existing wiki page titles' list above but is linked with a "
        "different slug or casing (e.g. page 'AWS Cost Explorer' referenced as "
        "[[aws-cost-explorer]]), that is a broken LINK, NOT a missing page — do NOT report it as "
        "missing-page (broken links are handled deterministically). "
        f"Keep the output well under {token_budget} tokens. Return no prose, only the JSON "
        "object."
    )


def _norm_title_for_match(value: str) -> str:
    """
    Collapse a title/slug to alphanumeric-casefold for existence matching, so the proper title
    ("AWS Cost Explorer") and its wikilink slug ("aws-cost-explorer") normalise to the same key
    ("awscostexplorer"). Used to detect a semantic `missing-page` whose target page ALREADY exists.
    """
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _parse_findings(raw: str, existing_titles: list[str] | None = None) -> list[FindingDTO]:
    """
    Parse the semantic findings JSON into FindingDTO list. Tolerant of code fences / prose;
    silently drops malformed entries (degrade, never raise). Unknown categories are dropped;
    orphan-page is NEVER accepted from the model (it is deterministic-only — ADR-0037 §3.1).

    llm_wiki parity guard: a `missing-page` whose `target_title` matches an EXISTING page is a
    false positive — the page is not missing, the wikilink just uses a different slug/casing.
    llm_wiki treats that as a broken-link fix (re-point the link), never "Create a wiki page …"
    for a page that already exists. The real broken reference is already surfaced by the
    deterministic broken-wikilink/missing-xref pass (with a suggested target), so we DROP the
    redundant, wrongly-actioned semantic finding rather than tell the user to create a duplicate.
    """
    if not raw:
        return []
    obj = loads_json_lenient(raw)
    if obj is None:
        return []
    if isinstance(obj, dict):
        items_raw = obj.get("findings", obj.get("items", []))
    elif isinstance(obj, list):
        items_raw = obj
    else:
        return []
    if not isinstance(items_raw, list):
        return []

    # Normalised existence set for the missing-page false-positive guard (llm_wiki parity).
    existing_norm = {_norm_title_for_match(t) for t in (existing_titles or []) if t}

    # Semantic categories accepted from the model.
    # Excluded (deterministic-only — must never come from the model):
    #   orphan-page  — ADR-0037 §3.1 / ADR-0058 §L1
    #   no-outlinks  — ADR-0058 §L1 (Do-NOT #21)
    #   missing-xref — L2 parity fix: llm_wiki does not have this category; it is
    #                  handled deterministically via links.dangling in the enrich seam.
    #                  Silently drop it if a model emits it anyway.
    semantic_categories = VALID_CATEGORIES - {"orphan-page", "no-outlinks", "missing-xref"}

    out: list[FindingDTO] = []
    for entry in items_raw:
        if not isinstance(entry, dict):
            continue
        category = entry.get("category") or entry.get("type")
        if category not in semantic_categories:
            continue
        description = clean_str(entry.get("description"))
        if not description:
            continue
        severity = clean_str(entry.get("severity")) or "warning"
        if severity not in VALID_SEVERITIES:
            severity = "warning"
        target_title = clean_str(entry.get("target_title"))
        proposed_action: str | None = None
        if category == "missing-page" and target_title:
            # llm_wiki parity guard: drop "create" findings for pages that already exist.
            if existing_norm and _norm_title_for_match(target_title) in existing_norm:
                logger.debug(
                    "lint: dropped semantic missing-page for existing page %r "
                    "(link-format mismatch, not a missing page)",
                    target_title,
                )
                continue
            proposed_action = f"Create a wiki page titled {target_title!r}."
        out.append(
            FindingDTO(
                category=category,
                severity=severity,
                description=description,
                target_title=target_title,
                proposed_action=proposed_action,
            )
        )
    return out
