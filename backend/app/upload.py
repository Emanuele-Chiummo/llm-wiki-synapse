"""
Upload sanitizer — Feature U (ADR-0020 §2.2/§2.3/§2.4) + F12 (ADR-0025 §4.2).

Pure functions for path-traversal protection and type/size gating.
Unit-testable in isolation; no I/O, no DB, no network.

safe_source_name(raw_filename)  → sanitized basename
resolve_under_sources(name)     → absolute Path under raw_sources_dir (containment-checked)

F12 extension contract (ADR-0025 §4.2, Do-NOT #13):
  _ALLOWED_EXTENSIONS        — watcher ingests these; UNCHANGED from v0.4 (watcher imports it).
  _EXTRACTABLE_EXTENSIONS    — extracted on upload; binary stays in raw/; companion .md is watched.
  _PLACEHOLDER_EXTENSIONS    — placeholder text on upload; no OCR/transcript in M5.
  _UPLOAD_ACCEPTED           — union of all three; 415 only for extensions outside this set.

CRITICAL: Do NOT add binary extensions to _ALLOWED_EXTENSIONS. The watcher imports that
frozenset and must remain format-agnostic (ADR-0025 §4.3, Do-NOT #13).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from app.config import settings

# ── Extension allow-list (watcher ingests THESE; UNCHANGED from v0.4) ─────────
# WARNING: The watcher (app.watcher) imports this frozenset directly.
# Do NOT add binary extensions here (ADR-0025 §4.3, Do-NOT #13).
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt", ".markdown"})

# ── F12: binary extensions extracted synchronously on upload (ADR-0025 §4.2) ──
# These produce a companion .extracted.md; the binary is preserved in raw/sources/.
# NOT added to _ALLOWED_EXTENSIONS so the watcher ignores the binary (I1).
_EXTRACTABLE_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})

# ── F12: placeholder-only extensions (§4.5 — no OCR/transcript in M5) ─────────
_PLACEHOLDER_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".wav", ".m4a"}
)

# ── Upload accepted = all three sets (415 only for truly unknown types) ────────
_UPLOAD_ACCEPTED: frozenset[str] = (
    _ALLOWED_EXTENSIONS | _EXTRACTABLE_EXTENSIONS | _PLACEHOLDER_EXTENSIONS
)

# ── Reject filenames with path separators (belt-and-braces) ───────────────────
_SEP_RE = re.compile(r"[/\\]")

# ── Maximum filename length (preserve extension) ──────────────────────────────
_MAX_FILENAME_LEN: int = 200


def safe_source_name(raw_filename: str) -> str:
    """
    Sanitize an untrusted filename from a multipart upload (ADR-0020 §2.2).

    Steps (as specified in ADR-0020):
    1. basename-only extraction: Path(raw_filename).name
       strips any directory component — "../../etc/passwd" → "passwd"
    2. reject if name is empty, ".", or ".."
    3. reject if name contains a path separator after step 1 (defensive)
    4. strip NUL/control chars; collapse whitespace
    5. enforce extension allow-list (.md/.txt/.markdown, case-insensitive) → 415
    6. clamp length to ≤ _MAX_FILENAME_LEN chars

    Returns the sanitized name.
    Raises HTTPException(422) for unsafe/empty names.
    Raises HTTPException(415) for disallowed extensions.
    """
    if not raw_filename:
        raise HTTPException(
            status_code=422, detail="Filename is empty or unsafe after sanitization."
        )

    # Step 1 — basename only
    name = Path(raw_filename).name

    # Step 2 — reject sentinel values
    if not name or name in {".", ".."}:
        raise HTTPException(
            status_code=422, detail="Filename is empty or unsafe after sanitization."
        )

    # Step 3 — belt-and-braces: no separator chars should survive step 1, but reject if present
    if _SEP_RE.search(name):
        raise HTTPException(
            status_code=422, detail="Filename is empty or unsafe after sanitization."
        )

    # Step 4 — strip NUL and control characters (chr < 0x20 except ordinary space),
    #           then collapse runs of whitespace
    name = "".join(ch for ch in name if ord(ch) >= 0x20 and ch != "\x7f")
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        raise HTTPException(
            status_code=422, detail="Filename is empty or unsafe after sanitization."
        )

    # Step 5 — extension allow-list check (authoritative — MIME hint is advisory)
    # F12 (ADR-0025 §4.2): _UPLOAD_ACCEPTED includes binary + placeholder extensions.
    # 415 only for extensions outside _UPLOAD_ACCEPTED.
    suffix = Path(name).suffix.lower()
    if suffix not in _UPLOAD_ACCEPTED:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {suffix!r}. "
                "Accepted text formats: .md, .txt, .markdown. "
                "Accepted binary formats (F12): .pdf, .docx, .pptx, .xlsx. "
                "Accepted placeholder formats: .png, .jpg, .jpeg, .gif, .webp, "
                ".mp3, .mp4, .wav, .m4a."
            ),
        )

    # Step 6 — clamp length (preserve extension)
    if len(name) > _MAX_FILENAME_LEN:
        stem = Path(name).stem
        ext = Path(name).suffix
        stem = stem[: _MAX_FILENAME_LEN - len(ext)]
        name = stem + ext

    return name


def resolve_under_sources(name: str) -> Path:
    """
    Resolve *name* to an absolute path under settings.raw_sources_dir with a
    containment check (ADR-0020 §2.2 — belt-and-braces second gate).

    Raises HTTPException(422) if the resolved path escapes raw_sources_dir.
    """
    raw_dir = settings.raw_sources_dir.resolve()
    dst = (raw_dir / name).resolve()

    # The resolved path MUST start with raw_sources_dir/ (trailing sep ensures prefix safety)
    if not str(dst).startswith(str(raw_dir) + "/"):
        # Also accept exact match (in case name has no extension or path ends exactly)
        if dst != raw_dir and not str(dst).startswith(str(raw_dir) + "/"):
            raise HTTPException(
                status_code=422, detail="Filename is empty or unsafe after sanitization."
            )

    return dst
