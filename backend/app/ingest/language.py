"""
Deterministic, dependency-free language / script-family detection for the wrong-language
page-drop guard (Feature 3, ADR-0063 §5).

Ported (conceptually) from nashsu/llm_wiki's ``detect-language.ts`` +
``contentMatchesTargetLanguage``, reduced to what the guard actually needs: decide whether a
generated page body is written in a SCRIPT FAMILY compatible with the resolved target output
language. The core insight from the reference is that *cross-family* mismatch (e.g. a Chinese
body when the vault target is English) is the real defect; intra-Latin mismatch (English body
mis-detected as Italian for a short idiomatic sample) is not worth dropping a page over.

No provider call, no network, no external package — a pure function over Unicode ranges so the
guard stays deterministic and cheap (I7-friendly). This module NEVER raises: a malformed or
empty body degrades to "matches" (keep the page) so the guard can only ever DROP on a
confident cross-script signal.
"""

from __future__ import annotations

# Script families we distinguish. "latin" is the catch-all for ASCII + Latin-script languages
# (en/it/fr/de/es/pt/nl/...): we deliberately do NOT try to tell them apart (the reference does
# the same, treating short Latin samples as one family to avoid false drops).
_LATIN = "latin"
_CJK = "cjk"
_CYRILLIC = "cyrillic"
_ARABIC = "arabic"
_DEVANAGARI = "devanagari"
_HEBREW = "hebrew"
_GREEK = "greek"
_THAI = "thai"

# ISO-639-1 code → expected script family. Anything not listed falls back to "latin"
# (the safe default: it never triggers a cross-script drop for a Latin-script target).
_ISO_TO_FAMILY: dict[str, str] = {
    # CJK
    "zh": _CJK,
    "ja": _CJK,
    "ko": _CJK,
    # Cyrillic
    "ru": _CYRILLIC,
    "uk": _CYRILLIC,
    "bg": _CYRILLIC,
    "sr": _CYRILLIC,
    "mk": _CYRILLIC,
    "be": _CYRILLIC,
    # Arabic script
    "ar": _ARABIC,
    "fa": _ARABIC,
    "ur": _ARABIC,
    # Others
    "hi": _DEVANAGARI,
    "mr": _DEVANAGARI,
    "ne": _DEVANAGARI,
    "he": _HEBREW,
    "el": _GREEK,
    "th": _THAI,
}


def _char_family(cp: int) -> str | None:
    """Map a single code point to a non-Latin script family, or None (ASCII / Latin / other)."""
    # CJK: CJK Unified Ideographs, Hiragana, Katakana, Hangul.
    if (
        0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF  # CJK Ext A
        or 0x3040 <= cp <= 0x30FF  # Hiragana + Katakana
        or 0xAC00 <= cp <= 0xD7AF  # Hangul syllables
        or 0xF900 <= cp <= 0xFAFF  # CJK compatibility ideographs
    ):
        return _CJK
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:  # Cyrillic (+ supplement)
        return _CYRILLIC
    if (
        0x0600 <= cp <= 0x06FF  # Arabic
        or 0x0750 <= cp <= 0x077F  # Arabic Supplement
        or 0x08A0 <= cp <= 0x08FF  # Arabic Extended-A
        or 0xFB50 <= cp <= 0xFDFF  # Arabic Presentation Forms-A
        or 0xFE70 <= cp <= 0xFEFF  # Arabic Presentation Forms-B
    ):
        return _ARABIC
    if 0x0900 <= cp <= 0x097F:  # Devanagari
        return _DEVANAGARI
    if 0x0590 <= cp <= 0x05FF:  # Hebrew
        return _HEBREW
    if 0x0370 <= cp <= 0x03FF:  # Greek
        return _GREEK
    if 0x0E00 <= cp <= 0x0E7F:  # Thai
        return _THAI
    return None


def _strip_noise(body: str) -> str:
    """Remove fenced code, display/inline math — they skew script counting toward Latin/ASCII."""
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        # Fenced code block ``` ... ```
        if body.startswith("```", i):
            end = body.find("```", i + 3)
            i = n if end == -1 else end + 3
            continue
        # Display math $$ ... $$
        if body.startswith("$$", i):
            end = body.find("$$", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def dominant_script_family(text: str) -> str:
    """
    Return the dominant NON-Latin script family in *text*, or "latin" when no non-Latin script
    meaningfully dominates. Deterministic; never raises.

    A non-Latin family "wins" only if it accounts for a clear share of the alphabetic content —
    a couple of stray proper-noun characters in an otherwise-Latin page do NOT flip the family
    (mirrors the reference's ``maxCount >= 2`` conservatism, generalized to a ratio so long pages
    with a handful of foreign names are not misclassified).
    """
    sample = _strip_noise(text)[:4000]
    counts: dict[str, int] = {}
    latin_alpha = 0
    for ch in sample:
        cp = ord(ch)
        fam = _char_family(cp)
        if fam is not None:
            counts[fam] = counts.get(fam, 0) + 1
        elif ch.isalpha() and cp < 0x0250:  # basic Latin + Latin-1/Extended-A alphabetics
            latin_alpha += 1

    if not counts:
        return _LATIN

    best_fam = max(counts, key=lambda k: counts[k])
    best_n = counts[best_fam]
    total_scripted = latin_alpha + sum(counts.values())

    # CJK is dense (1 char = 1 word) so a low absolute count is still decisive; other scripts
    # must both clear a small floor AND out-mass the Latin content to win.
    if best_fam == _CJK:
        return _CJK if best_n >= 2 else _LATIN
    if best_n >= 4 and best_n >= latin_alpha:
        return best_fam
    if total_scripted > 0 and best_n / total_scripted >= 0.30 and best_n >= 3:
        return best_fam
    return _LATIN


def target_family(target_lang: str | None) -> str:
    """Map an ISO-639-1 target language code to its expected script family ("latin" default)."""
    if not target_lang:
        return _LATIN
    code = target_lang.strip().lower()[:2]
    return _ISO_TO_FAMILY.get(code, _LATIN)


def body_matches_target_language(body: str, target_lang: str | None) -> bool:
    """
    True if *body*'s dominant script family is COMPATIBLE with *target_lang* (Feature 3).

    Only a confident cross-script mismatch returns False (→ the caller drops the page). An empty
    / too-short / unresolved body returns True (keep the page). Intra-Latin differences always
    return True — this guard is about script family, not fine-grained language identification.
    """
    if body is None:
        return True
    tgt = target_family(target_lang)
    # A very short body is not enough signal to confidently drop a page.
    stripped = _strip_noise(body).strip()
    if len(stripped) < 20:
        return True
    detected = dominant_script_family(body)
    return detected == tgt
