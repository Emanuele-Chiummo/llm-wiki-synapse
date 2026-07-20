"""
F9 HITL Review Queue — proposal model (ADR-0034, supersedes ADR-0025 F9 parts).

ARCHITECTURE OVERVIEW (ADR-0034 §2):
  Rows are PROPOSALS for follow-up work — NOT confirmations of auto-created pages.
  Five proposal types: missing-page | suggestion | contradiction | duplicate | confirm.
  Pages are created on-demand ONLY when the human clicks Create (lazy generation, §5).

PACKAGE LAYOUT (BE-ARCH-2, 1.9.2 — split from the single ~3960-line review.py):
  queue.py        — CRUD + status transitions: enqueue_review, list_queue,
                    bulk_update_reviews, clear_resolved_reviews, skip, dismiss, _set_status,
                    deep_research (a status transition delegating to F10).
  propose.py      — proposal + sweep LLM seams: _llm_propose_reviews, _llm_sweep_judge,
                    propose_reviews, sweep_reviews, propose_corpus_shape_review (SC-D3).
  create.py       — the single-page generation engine (PARALLEL to the main ingest pipeline,
                    see BE-DEBT-1) + the Create action: _run_generation, create_page_from_review,
                    stub-create (WS-C/ADR-0079).
  suggestions.py  — purpose.md / schema.md co-evolution suggestions (R9-3/R9-4) + the shared
                    apply-to-file helper.
  prompts.py      — pure prompt builders + lenient JSON parsers shared by propose.py and
                    suggestions.py (NOT the app/ops/_llm.py helpers extracted in 1.9.0 — those
                    stay there).

This module (__init__.py) re-exports the COMPLETE pre-split public surface so that no external
import (`from app.ops.review import X`) or test patch (`patch("app.ops.review.X", ...)` /
`monkeypatch.setattr(review_mod, "X", ...)`) needs to change. See propose.py's module docstring
for why several internal cross-seam calls are deferred (`from app.ops.review import X` at call
time) rather than statically imported — that pattern is what keeps monkeypatch-based tests
written against the old monolithic module passing unchanged.

KEY CONTRACTS:

  enqueue_review(...)        — pure DB write for one proposal row; no provider call.
  propose_reviews(...)       — orchestration entry point (called from run_ingest_pipeline):
                               rule-based missing-page/duplicate detection, then
                               _llm_propose_reviews for LLM proposals.
  sweep_reviews(vault_id)    — auto-resolution sweep: Pass-1 (rule-based) + Pass-2
                               (conservative LLM).
  create_page_from_review(item_id) — lazy on-demand Create handler [AI seam for generation].
  list_queue(...)            — paginated read for GET /review/queue.
  skip(item_id)              — status write → skipped.
  deep_research(item_id)     — delegates to F10; stores run_id.

AI SEAMS (implemented — ADR-0034 §11.2):
  _llm_propose_reviews(...)  — single bounded InferenceProvider call for LLM proposals.
  _llm_sweep_judge(...)      — single bounded conservative LLM pass for sweep Pass-2.
  _run_generation(...)       — bounded run_orchestrated_loop invocation for Create.

I7 CONTRACT (fire-and-forget wrappers in orchestrator — not here):
  propose_reviews() and sweep_reviews() NEVER raise into the ingest critical path.
  The orchestrator wraps them in try/except (Do-NOT #5, ADR-0034 §10).

I6 CONTRACT (all LLM calls route through resolve_operation_provider — no hardcoded backend):
  No isinstance / provider_type / class-name branching anywhere in this package.
"""

from __future__ import annotations

# Re-exported so `patch("app.ops.review.settings.X", ...)` and `review_mod.settings.X` keep
# working (the settings singleton is shared regardless of which submodule imports it).
from app.config import settings

# Re-exported ONLY so `monkeypatch.setattr("app.ops.review.get_session", ...)` (written against
# the pre-split module, which imported get_session at its own top level) does not raise
# AttributeError. The submodules themselves call `app.db.get_session()` via module-attribute
# access (see queue.py/propose.py/create.py/suggestions.py), which is patched directly at its
# origin (`app.db.get_session`) — also exercised by the same test fixtures. Do not remove.
from app.db import get_session

# Re-exported _llm.py helpers (extracted in 1.9.0) — several submodules resolve these via a
# DEFERRED `from app.ops.review import X` at call time specifically so that
# `patch("app.ops.review.resolve_operation_provider", ...)` /
# `patch("app.ops.review.bounded_chat_collect", ...)` (written against the pre-split module)
# keep taking effect. Do not remove these re-exports.
from app.ops._llm import (
    bounded_chat_collect,
    clean_str,
    clean_str_list,
    coerce_int,
    loads_json_lenient,
    resolve_operation_provider,
)

from .create import (
    GenerationOutcome,
    _clean_candidate_title,
    _create_stub_from_review,
    _detect_page_type,
    _extract_missing_page_candidates,
    _resolve_create_page_type,
    _resolve_delegated_created_page_id,
    _run_generation,
    create_page_from_review,
)
from .prompts import (
    _VALID_ITEM_TYPES,
    ProposalDTO,
    _build_propose_instruction,
    _build_purpose_drift_instruction,
    _build_schema_pattern_instruction,
    _build_sweep_instruction,
    _digest_frontmatter,
    _digest_written_pages,
    _parse_proposals,
    _parse_purpose_drift,
    _parse_schema_pattern,
    _parse_sweep_verdicts,
    _read_bounded_page_excerpt,
    _resolve_review_language,
    _review_lang_directive,
    _trim_source_excerpt,
)
from .propose import (
    _AI_PROPOSE_MAX_ITEMS,
    _CORPUS_SHAPE_TYPES,
    _REVIEW_PROPOSE_TOTAL_HARD_CAP,
    _RULE_PROPOSE_MAX_ITEMS,
    _SWEEP_PASS1_MAX_ITEMS,
    SweepResult,
    _llm_propose_reviews,
    _llm_sweep_judge,
    _merge_proposals_bounded,
    _normalize_title,
    _rule_missing_page_search_queries,
    _sweep_corpus_shape_proposals,
    propose_corpus_shape_review,
    propose_reviews,
    sweep_reviews,
)
from .queue import (
    _RESOLVED_STATUSES,
    _TERMINAL_STATUSES,
    _VALID_PROPOSAL_ORIGINS,
    BulkResult,
    DeepResearchResult,
    ReviewQueuePage,
    _all_search_queries,
    _bg_tasks,
    _content_key,
    _first_search_query,
    _fnv1a_16hex,
    _set_status,
    _status_filter_values,
    bulk_update_reviews,
    clear_resolved_reviews,
    deep_research,
    dismiss,
    enqueue_review,
    list_queue,
    skip,
)
from .suggestions import (
    _PURPOSE_ADDITION_MARKER,
    _PURPOSE_SUGGESTION_TYPE,
    _SCHEMA_ADDITION_MARKER,
    _SCHEMA_SUGGESTION_TYPE,
    _apply_suggestion_to_file,
    _extract_addition,
    _extract_purpose_addition,
    _extract_schema_addition,
    apply_purpose_suggestion,
    apply_schema_suggestion,
    generate_purpose_suggestion,
    generate_schema_suggestion,
)

__all__ = [
    "_AI_PROPOSE_MAX_ITEMS",
    "_CORPUS_SHAPE_TYPES",
    "_PURPOSE_ADDITION_MARKER",
    "_PURPOSE_SUGGESTION_TYPE",
    "_RESOLVED_STATUSES",
    "_REVIEW_PROPOSE_TOTAL_HARD_CAP",
    "_RULE_PROPOSE_MAX_ITEMS",
    "_SCHEMA_ADDITION_MARKER",
    "_SCHEMA_SUGGESTION_TYPE",
    "_SWEEP_PASS1_MAX_ITEMS",
    "_TERMINAL_STATUSES",
    "_VALID_ITEM_TYPES",
    "_VALID_PROPOSAL_ORIGINS",
    "BulkResult",
    "DeepResearchResult",
    "GenerationOutcome",
    "ProposalDTO",
    "ReviewQueuePage",
    "SweepResult",
    "_all_search_queries",
    "_apply_suggestion_to_file",
    "_bg_tasks",
    "_build_propose_instruction",
    "_build_purpose_drift_instruction",
    "_build_schema_pattern_instruction",
    "_build_sweep_instruction",
    "_clean_candidate_title",
    "_content_key",
    "_create_stub_from_review",
    "_detect_page_type",
    "_digest_frontmatter",
    "_digest_written_pages",
    "_extract_addition",
    "_extract_missing_page_candidates",
    "_extract_purpose_addition",
    "_extract_schema_addition",
    "_first_search_query",
    "_fnv1a_16hex",
    "_llm_propose_reviews",
    "_llm_sweep_judge",
    "_merge_proposals_bounded",
    "_normalize_title",
    "_parse_proposals",
    "_parse_purpose_drift",
    "_parse_schema_pattern",
    "_parse_sweep_verdicts",
    "_read_bounded_page_excerpt",
    "_resolve_create_page_type",
    "_resolve_delegated_created_page_id",
    "_resolve_review_language",
    "_review_lang_directive",
    "_rule_missing_page_search_queries",
    "_run_generation",
    "_sweep_corpus_shape_proposals",
    "_set_status",
    "_status_filter_values",
    "_trim_source_excerpt",
    "apply_purpose_suggestion",
    "apply_schema_suggestion",
    "bounded_chat_collect",
    "bulk_update_reviews",
    "clean_str",
    "clean_str_list",
    "clear_resolved_reviews",
    "coerce_int",
    "create_page_from_review",
    "deep_research",
    "dismiss",
    "enqueue_review",
    "generate_purpose_suggestion",
    "generate_schema_suggestion",
    "get_session",
    "list_queue",
    "loads_json_lenient",
    "propose_corpus_shape_review",
    "propose_reviews",
    "resolve_operation_provider",
    "settings",
    "skip",
    "sweep_reviews",
]
