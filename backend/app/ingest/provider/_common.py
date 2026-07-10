"""
Shared prompt construction + structured-output parsing for the orchestrated providers
(Ollama, API). Keeps the analyze/generate JSON contract identical across backends (I6) so the
orchestrated loop validates one shape regardless of which backend produced it.

No model id / key / endpoint here — those are confined to the concrete provider modules.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from app.ingest.schemas import Analysis, WikiPage

# ── Vision captioning (R8-2 / F12) ──────────────────────────────────────────────

CAPTION_INSTRUCTION = (
    "Describe this image for a knowledge-base entry. Be factual and concise: state what the "
    "image shows, any visible text, diagrams, or data, and its likely subject. Return plain "
    "prose only — no markdown headings, no preamble like 'This image shows'."
)

# Image extension → MIME media type for provider vision blocks (R8-2). Kept here so all three
# vision providers agree on the media type without re-deriving it per module.
_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Magic-byte sniffing for the bytes-only path (no filename to key off).
_MAGIC_MEDIA_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def resolve_image_bytes_and_media_type(path_or_bytes: str | Path | bytes) -> tuple[bytes, str]:
    """
    Normalize a caption_image() input into (raw_bytes, media_type) for a provider vision block.

    Accepts a filesystem path (str/Path) — media type derived from the suffix — or raw bytes,
    for which the media type is sniffed from magic bytes (WEBP via the RIFF/WEBP header),
    defaulting to image/png when unknown. Never raises for an unknown type; the default keeps
    the provider call well-formed and lets the model interpret the payload.
    """
    if isinstance(path_or_bytes, (str, Path)):
        p = Path(path_or_bytes)
        data = p.read_bytes()
        media_type = _IMAGE_MEDIA_TYPES.get(p.suffix.lower(), "image/png")
        return data, media_type
    data = bytes(path_or_bytes)
    for magic, media_type in _MAGIC_MEDIA_TYPES:
        if data.startswith(magic):
            return data, media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data, "image/webp"
    return data, "image/png"


def encode_image_base64(data: bytes) -> str:
    """Return the standard base64 (ASCII) encoding of image *data* for a provider vision block."""
    return base64.standard_b64encode(data).decode("ascii")


# ── System prompts (provider-neutral) ───────────────────────────────────────────

ANALYZE_SYSTEM = (
    "You are the analysis step of a self-organizing wiki ingest pipeline. "
    "Read the source document and the vault context, then return ONLY a JSON object with "
    "keys: topics (list[str], >=1), entities (list[str]), language (ISO-639-1 string), "
    "suggested_pages (list of {title, type, rationale?} where type is one of "
    "entity|concept|source|synthesis|comparison, >=1 item), summary (short string). "
    "Restrict suggested_pages to entity, concept, or source types, and only when the source "
    "actually supports them; never invent synthesis, comparison, goals, habits, journal "
    "entries, or other pages that aren't in the source (nashsu/llm_wiki parity). "
    "Detect the source language and report it in 'language'. Return no prose, only JSON."
)

# Provider-neutral generation scaffold (nashsu/llm_wiki parity — ingest.ts:2017-2024 "What to
# generate" + ingest.ts:2229 synthesis/comparison prohibition). Embedded in GENERATE_SYSTEM for the
# orchestrated backends (Ollama, API) AND appended to the CLI agent's system_prompt by the
# orchestrator, so the SAME restriction reaches all three backends (I6 — the policy is prompt text,
# never provider-branching code). This is the fix for the page-type distribution divergence:
# without it the model saw 5 flat co-equal types and over-produced synthesis/comparison pages.
GENERATION_SCAFFOLD = (
    "## What to generate\n"
    "Generate ONLY the following pages:\n"
    "1. EXACTLY ONE source-summary page (type=source) for the origin source. This page ALWAYS "
    "exists — never omit it — and its frontmatter sources[] MUST include the origin source path.\n"
    "2. Entity pages (type=entity, or a schema-defined typed page) for the key named things "
    "(people, systems, organizations, products) identified in the analysis — only when the "
    "source actually describes them.\n"
    "3. Concept pages (type=concept, or a schema-defined typed page) for the key ideas, methods, "
    "techniques, and abstractions in the source — only when the source actually supports them.\n"
    "The aggregate files (index.md, log.md, overview.md) are maintained separately by the "
    "pipeline — do NOT emit them here.\n"
    "Do NOT create synthesis or comparison pages during ingest — those are created only later "
    "via the review queue when a human requests them. Do NOT invent pages the source does not "
    "support.\n"
    "## Naming\n"
    "Name each entity at its canonical short name. If an entity already exists (see the "
    "existing-pages context), reuse its EXACT title. Never append parenthetical acronyms "
    "(e.g. (AWS)) or legal suffixes (e.g. Inc./Ltd.) to an entity title. Emit [[wikilinks]] and "
    "related: using page SLUGS (ADR-0067 D5)."
)

GENERATE_SYSTEM = (
    "You are the generation step of a self-organizing wiki ingest pipeline. "
    "Given the analysis and retrieval context, return ONLY a JSON object with key 'pages': "
    "a list of wiki pages. Each page is "
    "{title: str, type: entity|concept|source, content: markdown body, "
    "frontmatter: {type, title, sources: non-empty list[str] including the origin source "
    "path, lang: ISO-639-1, tags: 3-6 concise lowercase reusable tags}}. "
    f"\n\n{GENERATION_SCAFFOLD}\n\n"
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


def _trim_source_for_generation(source_text: str) -> str:
    """
    Budget-trim the ORIGINAL source document for the generation prompt (D1, ADR-0063 §9; I7).

    nashsu/llm_wiki threads the (budget-trimmed) full source into generation (ingest.ts:926-945)
    so pages are written from the source, not the lossy Analysis summary. We mirror that with a
    bounded character budget read from ``ingest_generation_source_char_budget`` (DB override →
    env default, config_overrides). A budget of 0 DISABLES the source section (Analysis-only, the
    pre-D1 behaviour). Over-budget text is head-trimmed with an explicit truncation marker so the
    model knows the tail was cut (never silently blows the context window — I7).
    """
    from app.config import settings
    from app.config_overrides import effective_int

    budget = effective_int(
        "ingest_generation_source_char_budget",
        settings.ingest_generation_source_char_budget,
    )
    if budget <= 0 or not source_text:
        return ""
    if len(source_text) <= budget:
        return source_text
    return source_text[:budget].rstrip() + "\n\n[... source truncated to fit generation budget ...]"


def build_generate_prompt(analysis: Analysis, retrieval_context: str, source_text: str = "") -> str:
    # R7-10(b) / F3 language-aware ingest: inject a MANDATORY output-language directive derived
    # from the detected source language (analysis.language). Applies to BOTH orchestrated backends
    # (Ollama + API) because both call this builder — parity with the CLI provider's behaviour.
    # The directive is provider-neutral text (I6): the model must write page content AND the
    # frontmatter `lang` in the source language, not default to English.
    lang = (analysis.language or "").strip()
    lang_directive = ""
    if lang:
        lang_directive = (
            f"# MANDATORY OUTPUT LANGUAGE\n"
            f"Write ALL page content and set every page's frontmatter `lang` to the source "
            f"language: {lang} (ISO-639-1). Do NOT translate to English unless "
            f"{lang!r} is 'en'.\n\n"
        )
    # D1 (ADR-0063 §9, nashsu/llm_wiki parity — ingest.ts:1014-1016): thread the ORIGINAL source
    # document (budget-trimmed) into generation alongside the Analysis, so pages are written from
    # the source text, not only the lossy Analysis summary. Provider-neutral (I6): both
    # orchestrated backends call this builder, so the same source section reaches Ollama + API.
    # Empty section when the caller passes no source or the budget is 0 (Analysis-only fallback).
    trimmed_source = _trim_source_for_generation(source_text)
    source_block = f"# Source document\n{trimmed_source}\n\n" if trimmed_source else ""
    return (
        f"{lang_directive}"
        f"# Analysis\n{analysis.model_dump_json(indent=2)}\n\n"
        f"# Retrieval context\n{retrieval_context}\n\n"
        f"{source_block}"
        # Restate the restricted scaffold at the point of generation (nashsu/llm_wiki parity —
        # ingest.ts:2017-2024/2229) so the model's most recent instruction is the "what to
        # generate" restriction: exactly one source page + entity/concept pages, no
        # synthesis/comparison (those are review-only).
        f"{GENERATION_SCAFFOLD}\n\n"
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
