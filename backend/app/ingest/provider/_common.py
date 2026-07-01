"""
Shared prompt construction + structured-output parsing for the orchestrated providers
(Ollama, API). Keeps the analyze/generate JSON contract identical across backends (I6) so the
orchestrated loop validates one shape regardless of which backend produced it.

No model id / key / endpoint here — those are confined to the concrete provider modules.
"""

from __future__ import annotations

import json
from typing import Any

from app.ingest.schemas import Analysis, WikiPage

# ── System prompts (provider-neutral) ───────────────────────────────────────────

ANALYZE_SYSTEM = (
    "You are the analysis step of a self-organizing wiki ingest pipeline. "
    "Read the source document and the vault context, then return ONLY a JSON object with "
    "keys: topics (list[str], >=1), entities (list[str]), language (ISO-639-1 string), "
    "suggested_pages (list of {title, type, rationale?} where type is one of "
    "entity|concept|source|synthesis|comparison, >=1 item), summary (short string). "
    "Detect the source language and report it in 'language'. Return no prose, only JSON."
)

GENERATE_SYSTEM = (
    "You are the generation step of a self-organizing wiki ingest pipeline. "
    "Given the analysis and retrieval context, return ONLY a JSON object with key 'pages': "
    "a list of wiki pages. Each page is "
    "{title: str, type: entity|concept|source|synthesis|comparison, content: markdown body, "
    "frontmatter: {type, title, sources: non-empty list[str] including the origin source "
    "path, lang: ISO-639-1, tags: 3-6 concise lowercase reusable tags}}. "
    "Every page MUST cite its sources (traceability) and assign 3-6 concise, lowercase, "
    "reusable frontmatter 'tags' for navigation. "
    "'content' is the markdown body WITHOUT the frontmatter block. Return no prose, only JSON."
)


# ── Prompt builders ──────────────────────────────────────────────────────────────


def build_analyze_prompt(source_text: str, vault_context: str) -> str:
    return (
        f"# Vault context\n{vault_context}\n\n"
        f"# Source document\n{source_text}\n\n"
        "Return the analysis JSON now."
    )


def build_generate_prompt(analysis: Analysis, retrieval_context: str) -> str:
    return (
        f"# Analysis\n{analysis.model_dump_json(indent=2)}\n\n"
        f"# Retrieval context\n{retrieval_context}\n\n"
        "Return the pages JSON now."
    )


# ── Structured-output parsing ────────────────────────────────────────────────────


def _loads_json_object(raw: str) -> dict[str, Any]:
    """
    Parse a JSON object from a model response that may wrap it in prose or ```json fences.
    Raises ValueError if no object can be extracted (treated as a generation defect → retry).
    """
    raw = raw.strip()
    # Strip common code fences.
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Best-effort: slice from the first { to the last }.
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"no JSON object in model response: {raw[:200]!r}") from None
        obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model response JSON was not an object")
    return obj


def parse_analysis(raw: str) -> Analysis:
    """Parse + validate an Analysis from a model JSON response."""
    return Analysis.model_validate(_loads_json_object(raw))


def parse_pages(raw: str) -> list[WikiPage]:
    """
    Parse + validate a list[WikiPage] from a model JSON response.

    Pydantic validation here enforces the I5/F3 frontmatter rules at parse time; a malformed
    batch raises and is surfaced as a generation defect for the orchestrated loop to retry.
    """
    obj = _loads_json_object(raw)
    pages_raw = obj.get("pages", obj if isinstance(obj.get("title"), str) else [])
    if isinstance(pages_raw, dict):
        pages_raw = [pages_raw]
    if not isinstance(pages_raw, list):
        raise ValueError("model response 'pages' was not a list")
    return [WikiPage.model_validate(p) for p in pages_raw]
