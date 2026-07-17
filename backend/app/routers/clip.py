"""
Per-domain APIRouter: POST /clip (Chrome MV3 web clipper ingress, F11).

Also exposes: _clip_origin_allowed, _clip_safe_filename — re-imported by main.py
for backward-compatible test imports.
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import runtime_state
from app.config import settings
from app.upload import resolve_under_sources, safe_source_name

logger = logging.getLogger(__name__)

# Local mirror of main.py's _TokenSource (Literal used in clip authentication)
_TokenSource = Literal["db", "env", "none"]

router = APIRouter()


_CLIP_LOOPBACK_ORIGINS: frozenset[str] = frozenset(
    {
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        # Include port variants for the Vite dev server
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }
)
"""
Implicit loopback origins always allowed (not token-gated — they still need CLIP_TOKEN,
but they don't need to be listed in CLIP_ALLOWED_ORIGINS). This covers:
  - Vite dev server during development
  - Local automation scripts on the same machine
ADR-0038 §2.2: allowlist = CLIP_ALLOWED_ORIGINS ∪ _CLIP_LOOPBACK_ORIGINS.
"""


def _clip_origin_allowed(
    origin: str | None,
    extra_origins: list[str] | None = None,
) -> bool:
    """
    Return True iff the Origin header is on the clip allowlist (ADR-0038 §2.2, ADR-0040).

    Allowlist = resolved_allowed_origins (DB if set, else env) ∪ loopback origins (implicit).
    When Origin is absent the request is treated as NOT browser-origin-fenced
    (e.g. a local curl); we allow it because the token gate already enforces
    authentication — origin validation is a defence against drive-by CSRF, which
    requires an Origin header in the browser. No Origin → allow (bearer-only path).

    Parameters
    ----------
    origin : str | None
        The request's Origin header value.
    extra_origins : list[str] | None
        Additional configured origins to merge in (caller passes the resolved list
        from the cache or env — allows unit tests to inject values without patching).
        When None, the function calls settings.clip_allowed_origins_list (env-only;
        kept for backward-compat unit tests that patch the settings property).
    """
    if origin is None:
        return True  # no Origin header → not a browser CSRF; token gate is sufficient

    if extra_origins is not None:
        configured = set(extra_origins)
    else:
        configured = set(settings.clip_allowed_origins_list)
    full_allowlist = configured | _CLIP_LOOPBACK_ORIGINS
    return origin in full_allowlist


def _clip_safe_filename(title: str, url: str) -> str:
    """
    Derive a safe, sanitized filename for a clipped page.

    Steps:
    1. Normalise: use title if non-empty, else derive from URL hostname.
    2. Strip NUL/control chars, collapse whitespace.
    3. Replace chars unsafe on all filesystems with '-'.
    4. Clamp to 180 chars (leaving room for '.md' within the 200-char limit).
    5. Append '.md' extension.
    6. Ensure not empty/'.' after the above (fallback to 'clip-untitled.md').
    """
    import re as _re
    from urllib.parse import urlparse as _urlparse

    base = title.strip() if title.strip() else _urlparse(url).hostname or "untitled"
    # Strip NUL and control chars
    base = "".join(ch for ch in base if ord(ch) >= 0x20 and ch != "\x7f")
    # Replace chars unsafe on all FS with hyphen
    base = _re.sub(r'[/\\:*?"<>|]', "-", base)
    # Collapse runs of whitespace and hyphens
    base = _re.sub(r"[\s\-]+", "-", base).strip("-")
    # Clamp length
    base = base[:180] if len(base) > 180 else base
    if not base or base in {".", ".."}:
        base = "clip-untitled"
    return base + ".md"


class ClipRequest(BaseModel):
    """
    Request body for POST /clip (F11, ADR-0038).

    Sent by the Chrome MV3 extension after converting the article to Markdown
    via Readability + Turndown. The extension owns the conversion; the server
    only validates, sanitizes, and stores.
    """

    url: str = Field(..., min_length=1, description="Source URL of the clipped page")
    title: str = Field(default="", description="Article title (used for the filename)")
    markdown: str = Field(..., min_length=1, description="Article body as Markdown")
    source: str | None = Field(
        default=None,
        description=(
            "Optional source hint for the YAML frontmatter sources[] field. "
            "Defaults to the url when omitted."
        ),
    )
    vault_id: str | None = Field(
        default=None,
        description=(
            "Target vault identifier (W5 / PF-MCP-VAULT-1). "
            "Defaults to the active vault when omitted. "
            "Clips to a non-active vault are rejected (400) — activate the vault first."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com/article",
                "title": "Example Article",
                "markdown": "# Example Article\n\nArticle body...",
                "source": None,
                "vault_id": None,
            }
        }
    }


class ClipResponse(BaseModel):
    """202 response body for POST /clip (F11, ADR-0038)."""

    file_path: str = Field(
        ...,
        description='Saved path relative to vault_root, e.g. "raw/sources/Example-Article.md"',
    )
    status: str = Field(
        ...,
        description='"queued" — file saved to raw/sources/; watcher ingests asynchronously.',
    )
    overwritten: bool = Field(
        ...,
        description="True if a same-named file already existed and was replaced on disk",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/Example-Article.md",
                "status": "queued",
                "overwritten": False,
            }
        }
    }


@router.post(
    "/clip",
    response_model=ClipResponse,
    status_code=202,
    summary="Chrome MV3 web clipper ingress — secure clip receiver (F11, ADR-0038)",
    description=(
        "F11 Web Clipper ingress (ADR-0038). "
        "Accepts already-converted Markdown from the Chrome MV3 extension, "
        "writes it atomically to vault/raw/sources/, then the EXISTING watcher "
        "ingests it asynchronously (I1/I5/K1 — no new ingest path). "
        "\n\n"
        "Security (ADR-0038 §2 — explicitly addresses llm_wiki audit S-1..S-6): "
        "(a) CLIP_ENABLED must be true, else 503; "
        "(b) CLIP_TOKEN bearer required — constant-time compare, reject 401 on missing/invalid; "
        "(c) Origin/Host allowlist checked server-side BEFORE processing "
        "(chrome-extension://<id> + loopback + CLIP_ALLOWED_ORIGINS), reject 403 — "
        "CORS alone does not block simple POST drive-by writes; "
        "(d) body capped at CLIP_MAX_BODY_BYTES → 413; "
        "(e) filename derived from title, sanitized, safe-joined under vault/raw/sources/, "
        "containment-verified — caller never supplies a base path → 400 on traversal; "
        "(f) atomic write via temp+replace (I5). "
        "\n\n"
        "Idempotency (I1): watcher's mtime/SHA gate deduplicates re-clips of unchanged content. "
        "No second HTTP server. No 0.0.0.0 bind. "
        "NEVER stores or logs the token."
    ),
    responses={
        202: {"description": "File saved; watcher ingests asynchronously"},
        400: {"description": "Path traversal rejected or unsafe filename"},
        401: {"description": "Missing or invalid CLIP_TOKEN"},
        403: {"description": "Origin not in allowlist"},
        413: {"description": "Body exceeds CLIP_MAX_BODY_BYTES"},
        503: {"description": "CLIP_ENABLED is false — clipper ingress is disabled"},
    },
)
async def clip_ingest(
    request: Request,
    body: ClipRequest,
) -> ClipResponse:
    """
    POST /clip — web clipper ingress (F11, ADR-0038).

    Ordered security gates (fail-fast before any disk write):
    1. CLIP_ENABLED gate             → 503 if disabled
    2. CLIP_TOKEN bearer             → 401 if missing/invalid
    3. Origin allowlist              → 403 if disallowed
    4. Body size check               → 413 if exceeded
    5. Filename sanitization         → 400 if unsafe
    6. Path containment (safe-join)  → 400 if escapes raw/sources/
    7. Atomic write to raw/sources/
    8. Watcher picks up file (async, I1)
    """
    import tempfile

    # ── 1. CLIP_ENABLED gate (ADR-0040: DB wins over env when set) ─────────────
    # Resolution: DB clip_enabled_db (if not None) else CLIP_ENABLED env.
    if not runtime_state.clip_config_cache.resolved_enabled():
        raise HTTPException(
            status_code=503,
            detail="Web clipper ingress is disabled (CLIP_ENABLED=false).",
        )

    # ── 1b. Multi-vault guard (W5 / PF-MCP-VAULT-1) ──────────────────────────
    # POST /clip always writes to the ACTIVE vault's raw/sources/.  If the caller
    # sent a vault_id that differs from the active vault, return 400 immediately
    # (before spending auth time) so the extension can show a clear error rather
    # than silently clipping to the wrong vault.
    if body.vault_id and body.vault_id != settings.vault_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot clip to vault {body.vault_id!r}: only the active vault "
                f"({settings.vault_id!r}) accepts clips. "
                "Switch the active project first (POST /projects/{id}/activate)."
            ),
        )

    # ── 2. AuthN: bearer token — source-aware constant-time compare (ADR-0038 §2.1, ADR-0040) ──
    # Precedence (ADR-0040 §2.2):
    #   DB path  (token_source == "db"):  runtime_state.verify_token(presented, stored_pbkdf2_hash)
    #   Env path (token_source == "env"): hmac.compare_digest(presented, env_plaintext)
    # NEVER log the token, hash, or presented value. Fail-closed: no token = always 401.
    tok_source: _TokenSource = runtime_state.clip_config_cache.token_source()
    if tok_source == "none":
        raise HTTPException(
            status_code=401,
            detail="Clip ingress is not configured (no CLIP_TOKEN set).",
        )
    auth_header: str = request.headers.get("authorization", "")
    presented: str | None = None
    if auth_header.lower().startswith("bearer "):
        presented = auth_header[len("bearer ") :]

    bearer_ok: bool = False
    if presented is not None:
        if tok_source == "db":
            # PBKDF2 constant-time verification (mirrors MCP _BearerAuthMiddleware).
            db_hash = runtime_state.clip_config_cache.get_hash()
            bearer_ok = db_hash is not None and runtime_state.verify_token(presented, db_hash)
        else:
            # Env bootstrap: plaintext pre-shared secret — constant-time compare.
            env_token = settings.clip_token or ""
            bearer_ok = bool(env_token) and hmac.compare_digest(presented, env_token)

    if not bearer_ok:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid clip token.",
        )

    # ── 3. Origin allowlist (server-side — CORS alone doesn't block simple POSTs) ──
    # ADR-0040: resolved_allowed_origins_list() = DB if set, else env.
    origin: str | None = request.headers.get("origin")
    resolved_origins = runtime_state.clip_config_cache.resolved_allowed_origins_list()
    if not _clip_origin_allowed(origin, extra_origins=resolved_origins):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Origin {origin!r} is not in the clip allowlist "
                "(CLIP_ALLOWED_ORIGINS). Configure allowed origins in CLIP_ALLOWED_ORIGINS."
            ),
        )

    # ── 4. Body size check ───────────────────────────────────────────────────
    # JSON body is already parsed by FastAPI/Pydantic; check the serialized size.
    # The actual guard is the raw Content-Length header (before deserialization).
    content_length_str = request.headers.get("content-length")
    if content_length_str is not None:
        try:
            cl = int(content_length_str)
            if cl > settings.clip_max_body_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Body size {cl} bytes exceeds the {settings.clip_max_body_bytes} "
                        "byte limit (CLIP_MAX_BODY_BYTES)."
                    ),
                )
        except ValueError:
            pass  # unparseable content-length; continue (we check body bytes below)

    # Encode the already-parsed body to count bytes (belt-and-braces)
    import json as _json

    body_bytes = _json.dumps(body.model_dump()).encode("utf-8")
    if len(body_bytes) > settings.clip_max_body_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Body size {len(body_bytes)} bytes exceeds the {settings.clip_max_body_bytes} "
                "byte limit (CLIP_MAX_BODY_BYTES)."
            ),
        )

    # ── 5. Filename sanitization ─────────────────────────────────────────────
    # Derive from title (never from a caller-supplied path).
    raw_name = _clip_safe_filename(body.title, body.url)
    # safe_source_name enforces extension allowlist + basename-only + NUL strip.
    # We pre-generate a .md filename so we only need to confirm it passes.
    try:
        name = safe_source_name(raw_name)
    except HTTPException as exc:
        raise HTTPException(status_code=400, detail=f"Unsafe filename: {exc.detail}") from exc

    # ── 6. Path containment (safe-join) ─────────────────────────────────────
    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)
    try:
        dst = resolve_under_sources(name)
    except HTTPException as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Path traversal rejected: {exc.detail}",
        ) from exc

    # ── 7. Build the Markdown file content ──────────────────────────────────
    source_value = body.source or body.url
    # Escape YAML special chars in title and source
    safe_title = body.title.replace('"', '\\"') if body.title else "Untitled Clip"
    safe_url = body.url.replace('"', '\\"')
    safe_source = source_value.replace('"', '\\"')
    md_content = (
        f"---\n"
        f'title: "{safe_title}"\n'
        f"type: source\n"
        f"sources:\n"
        f'  - "{safe_source}"\n'
        f'clip_url: "{safe_url}"\n'
        f"---\n\n"
        f"{body.markdown}\n"
    )
    content_bytes = md_content.encode("utf-8")

    # ── 8. Atomic write (I5) ─────────────────────────────────────────────────
    overwritten: bool = dst.exists()
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(raw_sources), suffix=".clip_tmp")
    try:
        import os as _os

        _os.write(tmp_fd, content_bytes)
        _os.close(tmp_fd)
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        try:
            _os.close(tmp_fd)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to write clip file: {exc}") from exc

    # ── 9. Watcher picks up the file asynchronously (I1) ────────────────────
    # The watchdog observer sees the file creation/replace event in raw/sources/
    # and calls ingest_file() via the existing incremental pipeline.
    # mtime/SHA gate prevents double-ingest on re-clip of unchanged content (I1).
    rel_path = str(dst.relative_to(settings.vault_root))
    logger.info(
        "Clip saved: file_path=%r overwritten=%s (F11, ADR-0038)",
        rel_path,
        overwritten,
    )

    return ClipResponse(file_path=rel_path, status="queued", overwritten=overwritten)
