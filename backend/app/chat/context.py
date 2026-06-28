"""
Light system-context builder for chat (ADR-0019 §2.3 — "Option (b)-lite").

Phase-3 chat does NOT do F5 4-phase retrieval, vector search, or [n] citations (M5, Do-NOT #8).
It injects ONE budget-capped system message built from cheap on-disk file reads:

  1. vault/purpose.md   (vault goal/scope — F2 idea, used as a short grounding header here)
  2. vault/wiki/overview.md  (auto-generated catalogue summary — K3/F3)

The result is budget-capped so we never blow the context window (F14): we reserve the bulk of
the configured `context_window` for the conversation + the model's answer and cap the injected
grounding header to a small slice (the "retrieval" 20% slice of the 60/20/5/15 budget, applied
here as a hard char cap derived from the token budget). Missing files are skipped silently — a
chat with no purpose/overview still works (honest minimum).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default chat context window when neither the request nor provider_config specifies one
# (ADR-0019 §2.2 / AC-F14-2). Not a model id / endpoint — a pure sizing default.
DEFAULT_CONTEXT_WINDOW = 32_768

# Fraction of the context window reserved for the injected grounding header (F14 "retrieval"
# slice, 20% of the 60/20/5/15 budget). The rest is left for the conversation + the answer.
_CONTEXT_HEADER_FRACTION = 0.20

# Coarse chars-per-token heuristic for budget capping (we cap by characters, not a tokenizer,
# to stay dependency-free; this is a SAFETY cap, not exact accounting). ~4 chars/token.
_CHARS_PER_TOKEN = 4

_SYSTEM_PREAMBLE = (
    "You are Synapse, an assistant grounded in a self-organising knowledge-base ('vault'). "
    "Use the vault context below to answer when relevant. If the context does not cover the "
    "question, answer from general knowledge and say so briefly. Be concise."
)


def _read_capped(path: Path, char_cap: int) -> str:
    """Read *path* if it exists, returning at most *char_cap* characters; '' on any error."""
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem edge
        logger.warning("chat context: could not read %s: %s", path, exc)
        return ""
    if len(text) > char_cap:
        text = text[:char_cap].rstrip() + "\n…[truncated]"
    return text.strip()


def build_chat_context(
    *,
    vault_root: Path,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
) -> str:
    """
    Build the budget-capped chat system context (ADR-0019 §2.3).

    Concatenates, in priority order, purpose.md then overview.md, each capped so the combined
    header stays within ~20% of *context_window* (F14). Returns a single system-message string
    (always includes the preamble, even when both files are absent).

    NO vector search, NO graph expansion, NO citations (M5, Do-NOT #8).
    """
    header_token_budget = int(max(context_window, 1) * _CONTEXT_HEADER_FRACTION)
    header_char_budget = header_token_budget * _CHARS_PER_TOKEN

    purpose = _read_capped(vault_root / "purpose.md", header_char_budget)
    # Leave room for overview after purpose consumed its share.
    remaining = max(header_char_budget - len(purpose), header_char_budget // 2)
    overview = _read_capped(vault_root / "wiki" / "overview.md", remaining)

    parts: list[str] = [_SYSTEM_PREAMBLE]
    if purpose:
        parts.append("## Vault purpose\n" + purpose)
    if overview:
        parts.append("## Vault overview\n" + overview)
    return "\n\n".join(parts)
