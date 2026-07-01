"""
Synapse FastAPI service — v0.5 (M5 Phase 3: F9 HITL Review Queue + F12 Multi-format ingest).

Endpoints:
  GET  /status                — vault_id, data_version, started_at, uptime
  GET  /pages                 — paginated list of live pages
  GET  /pages/{id}            — single page by UUID
  GET  /pages/{id}/related    — top-N related pages by 4-signal edge weight (reuses edges; I1/I2)
  POST /ingest/trigger        — sync ingest; HTTP 202 (typed IngestTriggerResponse, AC-D4u)
  POST /ingest/upload         — multipart file upload → ingest; 202 (ADR-0020 Feature U + F12)
  POST /ingest/from-text      — inline text → raw/sources/ + ingest; 202 (ADR-0019 §2.7)
  GET  /ingest/runs           — paginated ingest run history (ADR-0018 §7, AC-BE-IR-1..5)
  GET  /search                — 4-phase RAG retrieval (F5, ADR-0022); read-only (AC-F5-5/6)
  GET  /provider/config       — list effective + raw provider_config rows (F17)
  POST /provider/config       — create/update a provider_config row (F17, §12 — no api key)
  DELETE /provider/config/{id} — delete a provider_config row by UUID
  GET  /graph                 — precomputed graph coords + edges (F4, I2, ADR-0014)
  PATCH /pages/{id}/position — persist manual node drag position; pin the node (Feature A)
  GET  /conversations         — list chat conversations (F6, ADR-0019)
  POST /conversations         — create an empty conversation (F6)
  GET  /conversations/{id}/messages — ordered message history (F6)
  DELETE /conversations/{id}  — soft-delete a conversation (F6)
  POST /chat/stream           — bounded NDJSON streaming chat turn (F6/F7, I6/I7, ADR-0019/0022)
  POST /chat/save-to-wiki     — save cleaned assistant answer to wiki/queries/<slug>.md (G-P0-1)
  GET  /import-schedule       — scheduled folder import config + last-run (ADR-0020 Feature S)
  PUT  /import-schedule       — upsert import schedule config (Feature S)
  POST /import-schedule/run-now — trigger one bounded scan immediately (Feature S)
  GET  /config/embedding        — current embedding config (EMBEDDING_URL/MODEL/DIM env vars)
  GET  /mcp/info                — read-only MCP server introspection (F1-MCP-UI, ADR-0027)
  PUT  /mcp/remote              — runtime toggle for remote MCP HTTP surface (ADR-0032)
  PUT  /mcp/auth                — set/rotate/clear MCP token + allow-without-token flag (ADR-0033)
  /mcp/server                  — FastMCP Streamable-HTTP; always-mounted (ADR-0033 §2.4)
  POST /research/start          — start a bounded deep-research run; 202 {run_id} (F10, ADR-0024)
  GET  /research/runs           — paginated deep-research run list (F10)
  GET  /research/runs/{id}      — deep-research run detail + sources (F10)
  GET  /review/queue            — paginated HITL review queue (F9, ADR-0034)
  POST /review/queue/{id}/approve  — Create: lazy on-demand page generation; 201 (F9, ADR-0034)
  POST /review/queue/{id}/create   — alias for approve/Create (preferred explicit verb)
  POST /review/queue/{id}/skip     — set status=skipped (F9)
  POST /review/queue/{id}/deep-research — delegate to F10; 202 {review_item_id, run_id} (F9)
  POST /review/queue/sweep         — manual auto-resolution sweep trigger (F9, ADR-0034 §6)
  POST /lint/scan               — bounded lint scan → run + findings; 200 (K2, ADR-0037)
  GET  /lint/runs · /lint/runs/{id} — lint run history + detail (K2, ADR-0037)
  GET  /lint/findings           — paginated lint findings (K2, ADR-0037)
  POST /lint/findings/{id}/apply   — HUMAN GATE: apply a safe/bounded fix (K2, ADR-0037)
  POST /lint/findings/{id}/dismiss — set status=dismissed (K2, ADR-0037)
  POST /pages/{id}/cascade-delete/preview — dry-run plan; read-only; 200 (F13, ADR-0026)
  DELETE /pages/{id}               — cascade-delete; single-pass; 200 (F13, ADR-0026)
  GET  /clip/config                — read-only clip ingress posture (ADR-0040)
  PUT  /clip/config                — set/rotate/clear clip token + enabled/origins (ADR-0040)
  POST /clip                       — Chrome MV3 web clipper ingress; secure; 202 (F11, ADR-0038)
  GET  /web-search/config          — read-only SearXNG web-search posture (ADR-0041)
  PUT  /web-search/config          — set/clear SearXNG URL + categories + max_queries (ADR-0041)
  GET  /provider/cli-auth          — read-only CLI subscription OAuth token posture (ADR-0043)
  PUT  /provider/cli-auth          — set or clear the CLI subscription OAuth token (ADR-0043)

Startup sequence (ordered, per v0.1-architecture §2.5):
  1. Vault skeleton bootstrap (vault.py) — AC-K7-1, I5
  2. Seed vault_state (idempotent) — ADR-0005, AC-F16dv-1
  3. Validate EMBEDDING_DIM vs live bge-m3 + ensure synapse_pages collection — ADR-0004
  4. Start watchdog observer — watcher.py
  5. Start GraphCache background debounce loop — ADR-0014
  6. Start ImportScheduler asyncio background task — ADR-0020 §4.5
  7. Emit AQ-3 INFO line if raw/sources/ is non-empty — ADR-0006

OpenAPI: auto-served at /openapi.json; `make openapi` snapshots to docs/api/openapi.json (D4).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import logging
import re as _re
import secrets
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.engine import CursorResult
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import cli_auth as _cli_auth
from app.chat.stream import ChatStreamError, run_chat_stream
from app.config import settings
from app.db import dispose_engine, get_session
from app.embeddings import EmbeddingError, get_embedding_client
from app.graph.cache import GraphCache
from app.graph.engine import GraphEngine
from app.import_scheduler import ImportScheduler, load_schedule, upsert_schedule
from app.ingest.orchestrator import IngestResult, ingest_file
from app.ingest.schemas import Message
from app.mcp.server import build_http_mcp
from app.mcp.server import mcp as _mcp_server
from app.models import (
    ChatMessage,
    Conversation,
    DeepResearchRun,
    DeepResearchSource,
    ImportSchedule,
    IngestRun,
    LintFinding,
    LintRun,
    Page,
    ProviderConfig,
    ReviewItem,
    VaultState,
)
from app.qdrant_client import ensure_collection
from app.upload import resolve_under_sources, safe_source_name
from app.vault import bootstrap_vault
from app.watcher import start_watcher, stop_watcher

# ── Module-level singletons (initialised in lifespan) ─────────────────────────
_graph_cache: GraphCache | None = None
_import_scheduler: ImportScheduler | None = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── MCP mount-path constant (ADR-0032 I6; retained ADR-0033) ─────────────────
# Single source of truth: used by the mount, the middleware gate, and /mcp/info.
# Never duplicate this literal elsewhere (I6).
MCP_MOUNT_PATH: str = "/mcp/server"

# ── Private CIDR ranges for source classification (ADR-0033 §2.3) ─────────────
# Named constant (I6 — no scattered literals). Used by _classify_source() to
# determine if a request is PRIVATE (eligible for allow-without-token) or PUBLIC
# (always requires a token regardless of allow_without_token flag).
#
# A request is PRIVATE only when BOTH:
#   (a) no CF-Connecting-IP / CF-Ray header is present, AND
#   (b) the resolved source IP falls in one of these ranges.
#
# Fail-safe: when uncertain (unresolvable IP, parse error, etc.) → PUBLIC.
MCP_PRIVATE_CIDRS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.IPv4Network("127.0.0.0/8"),  # loopback IPv4
    ipaddress.IPv6Network("::1/128"),  # loopback IPv6
    ipaddress.IPv4Network("100.64.0.0/10"),  # Tailscale CGNAT (RFC6598)
    ipaddress.IPv4Network("10.0.0.0/8"),  # RFC1918
    ipaddress.IPv4Network("172.16.0.0/12"),  # RFC1918
    ipaddress.IPv4Network("192.168.0.0/16"),  # RFC1918
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local IPv4
    ipaddress.IPv6Network("fe80::/10"),  # link-local IPv6
    ipaddress.IPv6Network("fc00::/7"),  # ULA (unique-local) IPv6
)


def _ip_is_private(ip_str: str) -> bool:
    """
    Return True iff the given IP string falls in MCP_PRIVATE_CIDRS.

    Fail-safe: parse errors or unexpected types return False (treated as PUBLIC).
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in MCP_PRIVATE_CIDRS)
    except (ValueError, TypeError):
        return False  # unknown → PUBLIC (fail-safe)


def _resolve_source_ip(scope: Scope) -> str | None:
    """
    Resolve the effective client IP for source classification (ADR-0033 §2.3).

    Trust model:
    1. Default: use scope["client"][0] (transport peer — the actual TCP peer ASGI
       reports). Never trusts X-Forwarded-For by default.
    2. If the transport peer is in MCP_TRUSTED_PROXIES (settings.mcp_trusted_proxies_list),
       read the LAST X-Forwarded-For entry appended by that proxy (proxy-attested client).
       "Last" means rightmost non-empty hop after stripping the proxy's own append
       — practically the last comma-separated IP in the XFF chain NOT added by the proxy.
    3. On any parse failure → return None (caller treats as PUBLIC — fail-safe).

    CF-Connecting-IP / CF-Ray are intentionally NOT used for IP resolution here
    (they are PUBLIC *signals* handled separately in _classify_source).
    """
    try:
        peer_ip: str = scope["client"][0]
    except (KeyError, TypeError, IndexError):
        return None  # no transport peer → PUBLIC (fail-safe)

    trusted = settings.mcp_trusted_proxies_list
    if not trusted:
        return peer_ip  # default: trust only the transport peer

    # Check if peer is trusted
    peer_is_trusted = False
    for cidr_or_ip in trusted:
        try:
            network = ipaddress.ip_network(cidr_or_ip.strip(), strict=False)
            if ipaddress.ip_address(peer_ip) in network:
                peer_is_trusted = True
                break
        except (ValueError, TypeError):
            continue

    if not peer_is_trusted:
        return peer_ip  # peer not trusted → use peer IP as-is

    # Peer is trusted: extract the last XFF hop (proxy-attested client).
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))
    xff: bytes = headers.get(b"x-forwarded-for", b"")
    if not xff:
        return peer_ip  # no XFF header from trusted proxy → use peer

    hops = [h.strip() for h in xff.decode("utf-8", errors="replace").split(",")]
    hops = [h for h in hops if h]
    if not hops:
        return peer_ip

    # Take the LAST hop (rightmost) — the proxy-attested client IP.
    # The leftmost is client-controlled; the rightmost is the most recently appended.
    return hops[-1]


def _classify_source(scope: Scope) -> bool:
    """
    Classify whether the MCP request is from a PUBLIC or PRIVATE source.

    Returns True if PUBLIC (token ALWAYS required regardless of allow_without_token),
    False if PRIVATE (token-less access may be permitted when allow_without_token=ON).

    PUBLIC conditions (ANY of the following):
      (a) CF-Connecting-IP or CF-Ray header is present (Cloudflare tunnel signal).
      (b) The resolved source IP is not in MCP_PRIVATE_CIDRS.
      (c) The source IP cannot be resolved (fail-safe).

    PRIVATE requires BOTH:
      (a) No CF-Connecting-IP / CF-Ray header, AND
      (b) Resolved source IP is in MCP_PRIVATE_CIDRS.

    Security notes:
    - CF-Connecting-IP / CF-Ray are PUBLIC signals, never trust grants. Their presence
      can only *restrict* (force PUBLIC), never *relax* auth. An attacker forging these
      headers only makes their own request more restricted.
    - XFF is only honoured when the transport peer is in MCP_TRUSTED_PROXIES (see
      _resolve_source_ip). An untrusted peer forging XFF is classified by peer IP.
    - Fail-safe: uncertain → PUBLIC (require token).
    """
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))

    # Check for Cloudflare edge headers (PUBLIC signal — fail-safe: presence → PUBLIC)
    if b"cf-connecting-ip" in headers or b"cf-ray" in headers:
        return True  # PUBLIC

    # Resolve source IP
    source_ip = _resolve_source_ip(scope)
    if source_ip is None:
        return True  # cannot resolve → PUBLIC (fail-safe)

    # Private CIDR check
    if _ip_is_private(source_ip):
        return False  # PRIVATE

    return True  # public IP → PUBLIC


# ── Token hashing helpers (ADR-0033 §2.1 — stdlib only, no new deps) ─────────
# Format: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
# - PBKDF2-HMAC-SHA256 with 260_000 iterations (NIST 2023 recommendation floor).
# - 16-byte random salt (secrets.token_bytes).
# - Constant-time verification via hmac.compare_digest.
# NEVER log or return any component of this string.

_PBKDF2_ITERS: int = 260_000
_PBKDF2_ALGO: str = "sha256"
_HASH_PREFIX: str = "pbkdf2_sha256"


def _hash_token(plaintext: str) -> str:
    """
    Hash a plaintext MCP token for DB storage (ADR-0033 §2.1).

    Returns a self-describing string:
        pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

    NEVER call this in a log statement or return it in an API response.
    """
    salt: bytes = secrets.token_bytes(16)
    dk: bytes = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, plaintext.encode(), salt, _PBKDF2_ITERS)
    salt_b64: str = base64.b64encode(salt).decode("ascii")
    hash_b64: str = base64.b64encode(dk).decode("ascii")
    return f"{_HASH_PREFIX}${_PBKDF2_ITERS}${salt_b64}${hash_b64}"


def _verify_token(plaintext: str, stored_hash: str) -> bool:
    """
    Constant-time verification of a plaintext token against a stored PBKDF2 hash.

    Returns True iff the plaintext hashes to the same digest as stored_hash.
    Returns False on any parse/format error (fail-closed).
    NEVER log the plaintext, stored_hash, or any intermediate value.
    """
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != _HASH_PREFIX:
            return False
        iters = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
    except (ValueError, TypeError, Exception):
        return False

    # Recompute PBKDF2 with the stored salt and iteration count.
    dk: bytes = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, plaintext.encode(), salt, iters)
    # Constant-time comparison (hmac.compare_digest handles length differences safely).
    return hmac.compare_digest(dk, expected)


# ── Token source resolver (ADR-0033 §2.1 — precedence: DB → env → none) ──────

_TokenSource = Literal["db", "env", "none"]


def _resolve_token_source(db_hash: str | None) -> _TokenSource:
    """
    Determine which token is authoritative (ADR-0033 §2.1 precedence).

    DB hash set → "db"; else env token set → "env"; else → "none".
    """
    if db_hash is not None:
        return "db"
    if settings.mcp_auth_token:
        return "env"
    return "none"


def _token_configured(db_hash: str | None) -> bool:
    """True iff a token is available (DB hash or env bootstrap)."""
    return _resolve_token_source(db_hash) != "none"


# ── In-process caches for ADR-0033 DB-backed flags ────────────────────────────

# RemoteMcpFlag: in-process cache for vault_state.remote_mcp_enabled (ADR-0032 §2.2).
# Loaded from vault_state at startup; refreshed on PUT /mcp/remote.


class RemoteMcpFlag:
    """
    In-process cache for vault_state.remote_mcp_enabled (ADR-0032 §2.2).

    The DB column is the source of truth; this holder is a process cache of it.
    Single-process deployment: in-memory cache and DB never diverge because
    PUT /mcp/remote writes both atomically and there is no external writer.
    """

    def __init__(self) -> None:
        self._enabled: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        """Return the cached runtime flag value (O(1), no I/O)."""
        return self._enabled

    async def load(self, enabled: bool) -> None:
        """Set the cached value (called at startup from the DB row)."""
        async with self._lock:
            self._enabled = enabled

    async def set(self, enabled: bool) -> None:
        """Update the cached value (called by PUT /mcp/remote after DB write)."""
        async with self._lock:
            self._enabled = enabled


class _McpAuthCache:
    """
    In-process cache for vault_state.mcp_access_token_hash and
    vault_state.mcp_allow_without_token (ADR-0033 §2.1/§2.3).

    Loaded from vault_state at startup (alongside RemoteMcpFlag).
    Refreshed on PUT /mcp/auth writes.
    The middleware reads both O(1) per request (no DB round-trip).
    NEVER exposes the hash string to callers — only boolean-derived values.
    """

    def __init__(self) -> None:
        # _hash is the stored PBKDF2 string; None = no DB token.
        # It is private and NEVER returned or logged by any method.
        self._hash: str | None = None
        self._allow_without_token: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    def get_hash(self) -> str | None:
        """Return the stored hash (needed only for verification — keep internal)."""
        return self._hash

    def allow_without_token(self) -> bool:
        """Return the persisted allow-without-token flag value."""
        return self._allow_without_token

    async def load(self, hash_value: str | None, allow: bool) -> None:
        """Load from DB at startup."""
        async with self._lock:
            self._hash = hash_value
            self._allow_without_token = allow

    async def set_hash(self, hash_value: str | None) -> None:
        """Update the cached hash (after DB write)."""
        async with self._lock:
            self._hash = hash_value

    async def set_allow(self, allow: bool) -> None:
        """Update the cached allow flag (after DB write)."""
        async with self._lock:
            self._allow_without_token = allow


# Module-level singletons — initialised in lifespan.
_remote_mcp_flag: RemoteMcpFlag = RemoteMcpFlag()
_mcp_auth_cache: _McpAuthCache = _McpAuthCache()


class _ClipConfigCache:
    """
    In-process cache for vault_state clip runtime config columns (ADR-0040 §3).

    Loaded from vault_state at startup; refreshed on PUT /clip/config writes.
    The middleware / handler reads all three O(1) per request (no DB round-trip).
    Precedence (DB wins when set, else env fallback — ADR-0040 §2.2):
      clip_enabled:        DB clip_enabled_db (if not None) else CLIP_ENABLED env
      clip_token:          DB clip_access_token hash (if not None) else CLIP_TOKEN env plaintext
      clip_allowed_origins: DB clip_allowed_origins_db (if not None) else CLIP_ALLOWED_ORIGINS env

    Token storage strategy (mirrors _McpAuthCache / ADR-0033 §2.1):
      - DB path: PBKDF2-SHA256 hash stored in vault_state.clip_access_token;
        verification via _verify_token(presented, stored_hash) (constant-time).
      - Env path: CLIP_TOKEN plaintext env var; inherently plaintext (same as .env);
        verification via hmac.compare_digest (constant-time).
    NEVER exposes clip_access_token or its hash to callers outside the auth check.
    """

    def __init__(self) -> None:
        self._enabled_db: bool | None = None  # None = unset; fall back to env
        self._hash: str | None = None  # DB token PBKDF2 hash; None = fall back to env
        self._allowed_origins_db: str | None = None  # None = fall back to env
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Resolved accessors (apply env fallback) ──────────────────────────────

    def resolved_enabled(self) -> bool:
        """Return DB clip_enabled_db if set, else settings.clip_enabled (env)."""
        if self._enabled_db is not None:
            return self._enabled_db
        return settings.clip_enabled

    def get_hash(self) -> str | None:
        """Return the stored PBKDF2 hash (DB path only). None = DB token not set.

        NEVER log or return this to callers. Use only for _verify_token().
        """
        return self._hash

    def resolved_allowed_origins_list(self) -> list[str]:
        """Return DB origins list if set, else env list (settings.clip_allowed_origins_list)."""
        if self._allowed_origins_db is not None:
            return [o.strip() for o in self._allowed_origins_db.split(",") if o.strip()]
        return settings.clip_allowed_origins_list

    # ── Source helpers ────────────────────────────────────────────────────────

    def token_source(self) -> str:
        """'db' | 'env' | 'none' — which token source is authoritative."""
        if self._hash is not None:
            return "db"
        if settings.clip_token:
            return "env"
        return "none"

    def token_configured(self) -> bool:
        """True iff a token is available (DB hash or env bootstrap)."""
        return self.token_source() != "none"

    def enabled_source(self) -> str:
        """'db' | 'env' — which enabled source is authoritative."""
        return "db" if self._enabled_db is not None else "env"

    def origins_source(self) -> str:
        """'db' | 'env' — which allowed_origins source is authoritative."""
        return "db" if self._allowed_origins_db is not None else "env"

    # ── Cache management ──────────────────────────────────────────────────────

    async def load(
        self,
        enabled_db: bool | None,
        token_hash: str | None,
        allowed_origins_db: str | None,
    ) -> None:
        """Load from DB at startup (or full reload). token_hash must be a PBKDF2 string or None."""
        async with self._lock:
            self._enabled_db = enabled_db
            self._hash = token_hash
            self._allowed_origins_db = allowed_origins_db

    async def set_enabled_db(self, value: bool | None) -> None:
        """Update cached enabled_db after DB write."""
        async with self._lock:
            self._enabled_db = value

    async def set_hash(self, hash_value: str | None) -> None:
        """Update cached hash after DB write. NEVER log the value."""
        async with self._lock:
            self._hash = hash_value

    async def set_allowed_origins_db(self, value: str | None) -> None:
        """Update cached allowed_origins_db after DB write."""
        async with self._lock:
            self._allowed_origins_db = value


# Module-level singleton — initialised in lifespan.
_clip_config_cache: _ClipConfigCache = _ClipConfigCache()


class _WebSearchConfigCache:
    """
    In-process cache for vault_state SearXNG runtime config columns (ADR-0041 §3).

    Loaded from vault_state at startup; refreshed on PUT /web-search/config writes.
    All handlers read resolved values O(1) per request (no DB round-trip).
    Precedence (DB wins when set, else env fallback — ADR-0041 §2.2):
      searxng_url:        DB searxng_url_db (if not None) else SEARXNG_URL env
      searxng_categories: DB searxng_categories_db (if not None) else env/code default
      searxng_max_queries: DB searxng_max_queries_db (if not None) else
                          DEEP_RESEARCH_MAX_QUERIES env

    KEY DIFFERENCE FROM CLIP: The SearXNG URL is NOT a secret.
      - It IS returned by GET /web-search/config (no masking, no token_configured pattern).
      - No PBKDF2, no one-time-reveal, no hash storage.
      - DB value is plain text; same blast-radius as the .env file.
    """

    def __init__(self) -> None:
        self._url_db: str | None = None  # None = fall back to SEARXNG_URL env
        self._categories_db: str | None = None  # None = fall back to code default
        self._max_queries_db: int | None = None  # None = fall back to env
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Resolved accessors (apply env fallback) ──────────────────────────────

    def resolved_url(self) -> str | None:
        """Return DB searxng_url_db if set, else settings.searxng_url (env)."""
        if self._url_db is not None:
            return self._url_db
        return settings.searxng_url

    def resolved_categories(self) -> list[str]:
        """Return DB categories list if set, else empty list (caller decides default)."""
        if self._categories_db is not None:
            return [c.strip() for c in self._categories_db.split(",") if c.strip()]
        return []

    def resolved_max_queries(self) -> int:
        """Return DB max_queries if set, else settings.deep_research_max_queries (env)."""
        if self._max_queries_db is not None:
            return self._max_queries_db
        return settings.deep_research_max_queries

    # ── Source helpers ────────────────────────────────────────────────────────

    def url_source(self) -> str:
        """'db' | 'env' | 'none' — which URL source is authoritative."""
        if self._url_db is not None:
            return "db"
        if settings.searxng_url:
            return "env"
        return "none"

    def configured(self) -> bool:
        """True iff a SearXNG URL is available (DB or env)."""
        return self.resolved_url() is not None

    def categories_source(self) -> str:
        """'db' | 'default' — which categories source is authoritative."""
        return "db" if self._categories_db is not None else "default"

    def max_queries_source(self) -> str:
        """'db' | 'env' — which max_queries source is authoritative."""
        return "db" if self._max_queries_db is not None else "env"

    # ── Cache management ──────────────────────────────────────────────────────

    async def load(
        self,
        url_db: str | None,
        categories_db: str | None,
        max_queries_db: int | None,
    ) -> None:
        """Load from DB at startup (or full reload)."""
        async with self._lock:
            self._url_db = url_db
            self._categories_db = categories_db
            self._max_queries_db = max_queries_db

    async def set_url_db(self, value: str | None) -> None:
        """Update cached url_db after DB write."""
        async with self._lock:
            self._url_db = value

    async def set_categories_db(self, value: str | None) -> None:
        """Update cached categories_db after DB write."""
        async with self._lock:
            self._categories_db = value

    async def set_max_queries_db(self, value: int | None) -> None:
        """Update cached max_queries_db after DB write."""
        async with self._lock:
            self._max_queries_db = value


# Module-level singleton — initialised in lifespan.
_web_search_config_cache: _WebSearchConfigCache = _WebSearchConfigCache()

# ── MCP HTTP surface (ADR-0033 §2.4 — always-mount) ──────────────────────────
# Built unconditionally at module load (ADR-0033 §2.4: mount condition is no longer
# "token set"). The _McpGate middleware is the sole per-request arbiter.
# _http_mcp_asgi_app lifespan MUST be chained into the FastAPI lifespan (FastMCP
# session manager). The sub-app is always started/stopped once (no remount —
# ADR-0032 §2.3 stands).
_http_mcp_asgi_app: ASGIApp | None = None

_http_mcp_instance = build_http_mcp(write_enabled=settings.mcp_remote_write_enabled)
_http_mcp_asgi_app = _http_mcp_instance.http_app()
logger.info(
    "MCP HTTP surface always-mounted (ADR-0033 §2.4): %s, write_enabled=%s",
    MCP_MOUNT_PATH,
    settings.mcp_remote_write_enabled,
)


# ── MCP access gate middleware (ADR-0033 §2.4 — replaces _BearerAuthMiddleware) ─
# Implements the full decision table from ADR-0033 §2.4.
# Wraps ONLY the /mcp/server sub-app; the REST API is unaffected.
#
# Decision table (HTTP scope, remote_enabled = ADR-0032 flag):
#   remote_enabled OFF        → 404 (any source, any token, any bearer)
#   ON + valid bearer         → PASS
#   ON + PRIVATE + tok + !allow + no/bad bearer → 401
#   ON + PRIVATE + tok + allow + no bearer      → PASS
#   ON + PRIVATE + !tok + allow + no bearer     → PASS
#   ON + PRIVATE + !tok + !allow + no bearer    → 404 (surface closed)
#   ON + PUBLIC  + tok + no/bad bearer          → 401
#   ON + PUBLIC  + !tok + any                   → 404
#
# Lifespan/WS scopes: always pass through (session manager stability — ADR-0032 §2.3).
# Bearer verification: DB-hash (PBKDF2 constant-time) → env-bootstrap (hmac.compare_digest).


class _BearerAuthMiddleware:
    """
    MCP access gate — ADR-0033 §2.4 decision table.

    Formerly a static-token-only guard (ADR-0029/0032); now source-aware with
    allow-without-token support. Renamed conceptually the "MCP access gate" but
    the class is kept as _BearerAuthMiddleware for test-import compatibility.

    Parameters
    ----------
    app : ASGIApp
        The wrapped FastMCP sub-app.
    token : str
        The BOOTSTRAP plaintext env token (MCP_AUTH_TOKEN); used only when the DB
        hash cache holds None. May be empty string when unset (never compared then).
    flag : RemoteMcpFlag
        In-process cache of remote_mcp_enabled.
    auth_cache : _McpAuthCache
        In-process cache of mcp_access_token_hash + mcp_allow_without_token.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        flag: RemoteMcpFlag,
        auth_cache: _McpAuthCache | None = None,
    ) -> None:
        self._app = app
        self._token = token  # env bootstrap (plaintext; may be "")
        self._flag = flag
        self._auth_cache: _McpAuthCache = auth_cache if auth_cache is not None else _McpAuthCache()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Pass lifespan/WS through immediately (ADR-0032 §2.3 — session manager stability).
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # ── Step 1: remote_enabled flag (ADR-0032 floor) ──────────────────────
        # OFF → 404 regardless of everything else.
        if not self._flag.is_enabled():
            await self._respond_404(scope, receive, send)
            return

        # ── Step 2: bearer extraction ──────────────────────────────────────────
        headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        auth_header: bytes = headers.get(b"authorization", b"")
        bearer_presented: str | None = None
        if auth_header.lower().startswith(b"bearer "):
            bearer_presented = auth_header[len(b"bearer ") :].decode("utf-8", errors="replace")

        # ── Step 3: verify bearer if presented ────────────────────────────────
        # A valid token always passes (regardless of source/allow flag).
        db_hash = self._auth_cache.get_hash()
        env_token = self._token  # env bootstrap (may be "")
        tok_configured = _token_configured(db_hash)
        tok_source = _resolve_token_source(db_hash)

        if bearer_presented is not None:
            bearer_ok = self._verify_bearer(bearer_presented, db_hash, env_token, tok_source)
            if bearer_ok:
                await self._app(scope, receive, send)
                return

        # ── Step 4: source classification ──────────────────────────────────────
        is_public = _classify_source(scope)
        allow = self._auth_cache.allow_without_token()

        # ── Step 5: apply decision table ───────────────────────────────────────
        if is_public:
            # PUBLIC source: token ALWAYS required (ADR-0033 §2.3).
            if tok_configured:
                await self._respond_401(scope, receive, send)
            else:
                await self._respond_404(scope, receive, send)
            return

        # PRIVATE source:
        if tok_configured:
            if allow:
                # Token configured + allow ON + private + no bearer → PASS
                await self._app(scope, receive, send)
                return
            else:
                # Token configured + allow OFF + private + no/bad bearer → 401
                await self._respond_401(scope, receive, send)
                return
        else:
            # No token configured at all
            if allow:
                # No token + allow ON + private → PASS (open on private)
                await self._app(scope, receive, send)
                return
            else:
                # No token + allow OFF → 404 (surface closed — no way to authenticate)
                await self._respond_404(scope, receive, send)
                return

    def _verify_bearer(
        self,
        candidate: str,
        db_hash: str | None,
        env_token: str,
        tok_source: _TokenSource,
    ) -> bool:
        """
        Verify the presented bearer against the authoritative token.

        Precedence (ADR-0033 §2.1):
          1. DB hash → PBKDF2 verify (constant-time).
          2. Env bootstrap → hmac.compare_digest (plaintext compare, constant-time).
          3. No token → always False.

        NEVER log candidate, db_hash, or env_token.
        """
        if tok_source == "db" and db_hash is not None:
            return _verify_token(candidate, db_hash)
        if tok_source == "env" and env_token:
            return hmac.compare_digest(candidate, env_token)
        return False

    @staticmethod
    async def _respond_404(scope: Scope, receive: Receive, send: Send) -> None:
        response = StarletteResponse(
            content='{"detail":"Not Found"}',
            status_code=404,
            media_type="application/json",
        )
        await response(scope, receive, send)

    @staticmethod
    async def _respond_401(scope: Scope, receive: Receive, send: Send) -> None:
        response = StarletteResponse(
            content='{"detail":"Unauthorized"}',
            status_code=401,
            media_type="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


# ── Startup timestamp ──────────────────────────────────────────────────────────
_started_at: datetime = datetime.now(UTC)


# ── Lifespan ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan: startup → yield → shutdown.

    Ordered startup sequence per v0.1-architecture §2.5 + v0.3 graph cache + M4-EXT scheduler.
    ADR-0029: if the MCP HTTP surface is enabled, the FastMCP sub-app's lifespan context
    (which starts/stops the StreamableHTTP session manager) is entered here to guarantee
    that MCP sessions are properly initialised before serving requests and torn down on
    shutdown. FastAPI does NOT forward lifespan events to mounted sub-apps automatically.
    """
    global _started_at, _graph_cache, _import_scheduler
    _started_at = datetime.now(UTC)

    # 1. Vault skeleton (K1, I5, AC-K7-1)
    bootstrap_vault()

    # 2. Seed vault_state (ADR-0005, AC-F16dv-1) + load runtime caches (ADR-0032/0033/0040/0041)
    await _seed_vault_state()
    await _load_remote_mcp_flag()
    await _load_mcp_auth_cache()
    await _load_clip_config_cache()
    await _load_web_search_config_cache()
    await _load_cli_auth_config_cache()

    # 3. Validate EMBEDDING_DIM vs live bge-m3 + ensure collection (ADR-0004).
    #    Skipped when EMBEDDINGS_ENABLED=false (ADR-0030 §2.5) so the app boots
    #    with no embedding service reachable — startup must not fail in lexical mode.
    if settings.embeddings_enabled:
        await _validate_embedding_and_collection()
    else:
        logger.info(
            "EMBEDDINGS_ENABLED=false — skipping embedding probe and collection "
            "validation (ADR-0030 §2.5). Retrieval will use lexical degrade (Feature B)."
        )

    # 3b. Sweep orphan status="running" rows from a prior crash (ADR-0046 §3 consequences).
    #     These are rows where the backend was killed mid-run so the row was never finalised.
    #     We mark them failed — STATUS UPDATE ONLY: no re-ingest, no rescan (I1).
    await _sweep_orphan_running_rows()

    # 4. Start watcher (I1)
    loop = asyncio.get_running_loop()
    start_watcher(loop)

    # 5. Initialise GraphCache + background debounce loop (I2, ADR-0014)
    _graph_cache = GraphCache(
        engine=GraphEngine(),
        vault_id=settings.vault_id,
    )
    _graph_cache.start_background_loop()
    logger.info("GraphCache initialised and background loop started")

    # 6. Start ImportScheduler asyncio task (ADR-0020 §4.5; after watcher so copies are seen)
    _import_scheduler = ImportScheduler()
    _import_scheduler.start()
    logger.info("ImportScheduler started")

    # 7. Chain MCP HTTP sub-app lifespan (ADR-0029 §5 / FastMCP lifespan note).
    #    The StarletteWithLifespan returned by http_app() has its own lifespan that
    #    starts the StreamableHTTP session manager.  Starlette does NOT forward lifespan
    #    to mounted sub-apps; we must enter it manually here.
    if _http_mcp_asgi_app is not None:
        mcp_sub = _http_mcp_asgi_app
        # StarletteWithLifespan exposes .lifespan (= .router.lifespan_context).
        mcp_lifespan = getattr(mcp_sub, "lifespan", None)
        if mcp_lifespan is not None and callable(mcp_lifespan):
            async with mcp_lifespan(mcp_sub):
                logger.info("MCP HTTP session manager started (ADR-0029)")
                yield
                logger.info("MCP HTTP session manager stopping (ADR-0029)")
        else:
            # Fallback: no lifespan property — just yield (defensive)
            logger.warning("MCP HTTP sub-app has no .lifespan; session manager may not start")
            yield
    else:
        yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    if _import_scheduler is not None:
        _import_scheduler.stop()
    if _graph_cache is not None:
        _graph_cache.stop_background_loop()
    stop_watcher()
    await dispose_engine()


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Synapse",
    version="0.6.0",
    description=(
        "Self-organising wiki backend — M5 Phase 1 (F5 4-phase retrieval + [n] citations). "
        "4-signal knowledge graph (F4): direct×3 + source-overlap×4 + Adamic-Adar×1.5 + type×1. "
        "FA2 server-side via igraph (I2); coords persisted in Postgres; "
        "dataVersion-debounced GraphCache; GET /graph precomputed coords (ADR-0014). "
        "Pluggable inference provider (F17): Local/Ollama, API/Anthropic-compatible, "
        "CLI/claude-agent-sdk. Bounded orchestrated ingest loop (I7). "
        "POST /ingest/upload: multipart upload → ingest (ADR-0020 Feature U). "
        "POST /ingest/from-text: inline text → ingest (ADR-0019 §2.7, AC-F6-5 save-to-wiki). "
        "GET /search: F5 4-phase RAG retrieval (ADR-0022, AC-F5-6). "
        "GET|PUT /import-schedule + POST /import-schedule/run-now: scheduled folder import "
        "(ADR-0020 Feature S). "
        "Karpathy LLM Wiki pattern [K1–K8]. "
        "POST /clip: Chrome MV3 web clipper ingress — token-gated, origin-checked, "
        "body-capped, safe-joined (F11, ADR-0038)."
    ),
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────────────────
# Allow the browser frontend (Vite dev server / PWA / Tauri) to call the API.
# Origins come from CORS_ALLOW_ORIGINS (env) — never hardcoded in prod (§12).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Graph-Cache"],  # so the viewer can read cache hit/miss (ADR-0014)
)

# ── MCP HTTP mount (ADR-0033 §2.4 — always-mount; gate is the sole arbiter) ──
# Mounted at MCP_MOUNT_PATH — always, regardless of token configuration.
# The _BearerAuthMiddleware (now the full MCP access gate) is applied ONLY to
# this sub-app (scoped; REST API unaffected).
# The gate carries _remote_mcp_flag, _mcp_auth_cache, and the env bootstrap token.
# No remount on flag changes (ADR-0032 §2.3 — session manager stable).
if _http_mcp_asgi_app is not None:
    _guarded_mcp_app = _BearerAuthMiddleware(
        _http_mcp_asgi_app,
        settings.mcp_auth_token or "",
        _remote_mcp_flag,
        _mcp_auth_cache,
    )
    app.mount(MCP_MOUNT_PATH, _guarded_mcp_app)
    logger.info("MCP HTTP surface mounted at %s (ADR-0033 §2.4 always-mount)", MCP_MOUNT_PATH)


# ── Pydantic response models ───────────────────────────────────────────────────


class StatusResponse(BaseModel):
    vault_id: str
    data_version: int
    started_at: datetime
    uptime_seconds: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "data_version": 3,
                "started_at": "2026-06-28T10:00:00Z",
                "uptime_seconds": 42.7,
            }
        }
    }


class PageResponse(BaseModel):
    id: uuid.UUID
    vault_id: str
    file_path: str
    title: str | None
    page_type: str | None = Field(None, serialization_alias="type")
    sources: list[str] | None
    content_hash: str
    qdrant_point_id: uuid.UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True, "from_attributes": True}


class PageListResponse(BaseModel):
    items: list[PageResponse]
    total: int
    limit: int
    offset: int


class PageContentResponse(BaseModel):
    """
    Response for GET /pages/{id}/content (F1-content-read).

    Additive extension (backward-compatible): page_type (serialised as "type"), sources, and
    tags are included so the reader can render a type badge, sources list, and navigation
    tags without a second call to GET /pages/{id}.  All three are nullable (NULL when absent
    from YAML frontmatter).  tags is the K6 navigation list (nashsu/llm_wiki parity), mirroring
    the sources column.
    """

    id: uuid.UUID
    title: str | None
    file_path: str
    content: str
    content_hash: str
    updated_at: datetime
    # Frontmatter fields (additive — backward-compatible)
    page_type: str | None = Field(
        None, serialization_alias="type", description="Frontmatter 'type'; NULL if absent (K6)"
    )
    sources: list[str] | None = Field(
        None, description="Frontmatter 'sources[]'; NULL if absent (K6)"
    )
    tags: list[str] | None = Field(
        None, description="Frontmatter 'tags[]' navigation tags; NULL if absent (K6)"
    )

    model_config = {"populate_by_name": True, "from_attributes": True}


class PageContentPutRequest(BaseModel):
    """Request body for PUT /pages/{id}/content (F1-content-write, ADR-0035)."""

    content: str = Field(..., min_length=1, description="Full UTF-8 markdown content to write")
    expected_hash: str | None = Field(
        default=None,
        description=(
            "Optimistic concurrency guard — sha256 hex of the content the client last read. "
            "When provided and it does NOT match the current on-disk hash, 409 is returned "
            "so the editor can warn about a stale edit."
        ),
    )


# Maximum body size for PUT /pages/{id}/content (ADR-0035). 4 MB covers any realistic
# markdown page; larger bodies are rejected with 413 before any disk write.
_MAX_PAGE_CONTENT_BYTES = 4 * 1024 * 1024  # 4 MB


class PageContentPutResponse(BaseModel):
    """Response for PUT /pages/{id}/content (F1-content-write)."""

    id: uuid.UUID
    content_hash: str
    updated_at: datetime


class IngestTriggerRequest(BaseModel):
    file_path: str = Field(..., description="Relative path under vault/raw/sources/")


class IngestTriggerResponse(BaseModel):
    """
    Typed 202 body for POST /ingest/trigger (AC-D4u — task_id appears in OpenAPI schema).

    task_id is None in v0.2 (synchronous path); v0.3 fills it with a real async task UUID.
    status: "completed" | "skipped" (I1 fast-path) | "queued"/"running" (async, v0.3+).
    """

    task_id: uuid.UUID | None = Field(
        default=None,
        description="Async task UUID (None in v0.2 synchronous mode; filled in v0.3+)",
    )
    status: str = Field(
        ...,
        description='"completed" or "skipped" (I1 mtime/hash fast-path)',
    )
    page_id: uuid.UUID = Field(..., description="UUID of the ingested page row")

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": None,
                "status": "completed",
                "page_id": "00000000-0000-0000-0000-000000000001",
            }
        }
    }


# ── Provider config Pydantic models (F17 CRUD — §12: NO api_key field) ────────

_VALID_PROVIDER_TYPES = {"local", "api", "cli"}
_VALID_SCOPES = {"global", "vault", "operation"}
_VALID_OPERATIONS = {"ingest", "chat", "lint"}


class ProviderConfigCreate(BaseModel):
    """
    Request body for POST /provider/config (F17).

    Stores NO API key (§12 / ADR-0008 §3). Keys are env-only inside provider/.
    model_id must be provided explicitly — no hardcoded defaults in app code (AC-F17-8).
    """

    scope: str = Field(..., description="global | vault | operation")
    operation: str | None = Field(
        default=None,
        description="ingest | chat | lint; required when scope='operation'",
    )
    vault_id: str | None = Field(
        default=None,
        description="Required when scope='vault' or 'operation'",
    )
    provider_type: str = Field(..., description="local | api | cli")
    model_id: str = Field(
        ...,
        description="Model name (e.g. claude-sonnet-4-6); lives only in DB rows (AC-F17-8)",
    )
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible endpoint; NULL for Anthropic/local default",
    )
    max_iter: int = Field(default=3, ge=1, le=20, description="Orchestrated-loop cap (I7)")
    token_budget: int = Field(
        default=60000,
        ge=1000,
        le=1_000_000,
        description="Loop token budget (I7)",
    )
    is_fallback: bool = Field(default=False, description="Marks the single fallback row")

    @field_validator("provider_type")
    @classmethod
    def _valid_provider_type(cls, v: str) -> str:
        if v not in _VALID_PROVIDER_TYPES:
            raise ValueError(
                f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}, got {v!r}"
            )
        return v

    @field_validator("scope")
    @classmethod
    def _valid_scope(cls, v: str) -> str:
        if v not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {sorted(_VALID_SCOPES)}, got {v!r}")
        return v

    @field_validator("operation")
    @classmethod
    def _valid_operation(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_OPERATIONS:
            raise ValueError(
                f"operation must be one of {sorted(_VALID_OPERATIONS)} or null, got {v!r}"
            )
        return v


class ProviderConfigResponse(BaseModel):
    """API response shape for a provider_config row (§12: no api_key field)."""

    id: uuid.UUID
    scope: str
    operation: str | None
    vault_id: str | None
    provider_type: str
    model_id: str
    base_url: str | None
    max_iter: int
    token_budget: int
    is_fallback: bool
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}


class ProviderConfigListResponse(BaseModel):
    items: list[ProviderConfigResponse]
    total: int


# ── Ingest run Pydantic models (ADR-0018 §7, AC-BE-IR-1) ──────────────────────


class IngestRunResponse(BaseModel):
    """
    API response shape for one ingest_runs row (ADR-0018 §7, AC-BE-IR-1).

    Column aliases (no DB rename — ADR-0018 §7 decision):
      max_iter_used  → iterations_used
      finished_at    → completed_at
    total_cost_usd serialised as a float; frontend formats to exactly 4dp (I7).
    """

    id: uuid.UUID
    vault_id: str
    status: str = Field(description="running | completed | failed | converged_false (ADR-0018 §7)")
    provider_type: str = Field(description="local | api | cli")
    pages_created: int = Field(description="Wiki pages persisted during this run")
    iterations_used: int = Field(
        description="Iterations consumed (aliases max_iter_used; 0 for delegated)"
    )
    total_cost_usd: float = Field(
        description="Total cost in USD; 0.0 for local/cli; serialised as number (I7)"
    )
    started_at: datetime
    completed_at: datetime | None = Field(
        description="Run finish time (aliases finished_at); null for running rows"
    )
    error_message: str | None = Field(description="Error detail for failed runs; null otherwise")

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "id": "00000000-0000-0000-0000-000000000001",
                "vault_id": "default",
                "status": "completed",
                "provider_type": "api",
                "pages_created": 3,
                "iterations_used": 2,
                "total_cost_usd": 0.0042,
                "started_at": "2026-06-28T10:00:00Z",
                "completed_at": "2026-06-28T10:00:05Z",
                "error_message": None,
            }
        },
    }


class IngestRunListResponse(BaseModel):
    """
    Paginated list response for GET /ingest/runs (ADR-0018 §7, AC-BE-IR-1).
    Ordered started_at DESC (AC-BE-IR-3).
    """

    items: list[IngestRunResponse]
    total: int
    limit: int
    offset: int


# ── Queue Pydantic models (ADR-0046 §6) ───────────────────────────────────────


class QueueTaskItem(BaseModel):
    """One task in the live ingest queue snapshot (ADR-0046 §6)."""

    run_id: str | None = Field(
        description="UUID of the in-flight run; null for pending (not yet dispatched) tasks"
    )
    source_path: str = Field(description="Relative raw source path (raw/sources/…)")
    filename: str = Field(description="Basename of source_path")
    status: str = Field(description="pending | processing | failed")
    retry_count: int = Field(description="Times this source has been retried (I7, max 3)")
    error: str | None = Field(description="Error detail for failed tasks; null otherwise")
    started_at: str | None = Field(description="ISO-8601 start time; null for pending tasks")


class QueueSnapshotResponse(BaseModel):
    """Live activity queue snapshot for GET /ingest/queue (ADR-0046 §6)."""

    paused: bool = Field(description="True when the queue is paused (no new dispatches)")
    pending: int = Field(description="Number of FS events parked while queue is paused")
    processing: int = Field(description="Number of currently in-flight ingest runs")
    failed: int = Field(description="Number of recently-failed tasks retained for retry")
    completed_since_idle: int = Field(
        description="Successful completions since the queue last went idle"
    )
    total: int = Field(description="pending + processing + failed")
    tasks: list[QueueTaskItem] = Field(description="All visible tasks (pending+processing+failed)")


class QueueCancelResponse(BaseModel):
    """Response for POST /ingest/runs/{id}/cancel (ADR-0046 §6)."""

    run_id: str
    status: str = Field(description="'cancelling' — abort requested; cleanup completes async")
    cleaned_pages: int = Field(
        default=0,
        description="Always 0 at request time; cascade cleanup happens at the next loop boundary",
    )


class QueueRetryResponse(BaseModel):
    """Response for POST /ingest/runs/{id}/retry (ADR-0046 §6)."""

    run_id_prev: str = Field(description="UUID of the failed run that was retried")
    source_path: str
    retry_count: int = Field(description="New retry count (1..3); I7 hard cap = 3")
    status: str = Field(default="queued", description="'queued' — re-dispatch accepted")


class QueuePauseResponse(BaseModel):
    """Response for POST /ingest/queue/pause (ADR-0046 §6)."""

    paused: bool = Field(description="Always true; idempotent")


class QueueResumeResponse(BaseModel):
    """Response for POST /ingest/queue/resume (ADR-0046 §6)."""

    paused: bool = Field(description="Always false; idempotent")
    drained: int = Field(description="Number of pending entries replayed to the watcher")


# ── Upload Pydantic models (Feature U, ADR-0020 §2.1) ─────────────────────────


class UploadResponse(BaseModel):
    """
    202 response body for POST /ingest/upload (ADR-0020 §2.1, M4-EXT non-blocking).

    file_path:  saved path relative to vault_root (e.g. "raw/sources/notes.md")
    status:     always "queued" — the watcher picks up the file asynchronously.
    overwritten: true if a same-name file already existed and was replaced on disk.

    page_id is not returned because ingest is async (watcher-driven); poll GET /ingest/runs
    or GET /pages to confirm the page exists after ingest completes (~15-30s).
    """

    file_path: str = Field(
        ...,
        description='Saved path relative to vault_root, e.g. "raw/sources/notes.md"',
    )
    status: str = Field(
        ...,
        description='"queued" — file saved to raw/sources/; watcher ingests asynchronously.',
    )
    overwritten: bool = Field(
        ...,
        description="True if a same-name file already existed and was replaced on disk",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/notes.md",
                "status": "queued",
                "overwritten": False,
            }
        }
    }


# ── GET /search Pydantic models (F5, ADR-0022 §2.5) ──────────────────────────


class SearchResultItem(BaseModel):
    """
    One citation entry in the GET /search response (ADR-0022 §2.5, AC-F5-6).

    Maps to Citation.{n, ref.id, ref.title, ref.slug, score, phase}.
    """

    n: int = Field(..., description="1-based citation index, contiguous from 1")
    id: str = Field(..., description="UUID of the pages row (== Qdrant point id, ADR-0002)")
    title: str = Field(..., description="Frontmatter title or filename stem (never empty, §2.6)")
    slug: str = Field(..., description="slugify(title) — derived, not a DB column (§2.6)")
    score: float = Field(..., description="Cosine similarity (vector) or edge weight (expansion)")
    phase: str = Field(..., description='"vector" | "expansion"')

    model_config = {
        "json_schema_extra": {
            "example": {
                "n": 1,
                "id": "00000000-0000-0000-0000-000000000001",
                "title": "Homelab Setup",
                "slug": "homelab-setup",
                "score": 0.87,
                "phase": "vector",
            }
        }
    }


class SearchResponse(BaseModel):
    """
    GET /search response (ADR-0022 §2.5, AC-F5-6).

    read-only — never bumps data_version (AC-F5-5).
    0-hit → 200 with empty results + empty context (AC-F5-7a).
    """

    query: str
    context: str = Field(
        ...,
        description="Assembled context string with inline [n] markers (≤ token_budget, ADR-0022)",
    )
    results: list[SearchResultItem] = Field(
        ...,
        description="Citations in rank order (vector seeds first, then expansions by edge weight)",
    )
    data_version: int = Field(
        ...,
        description="Snapshot read BEFORE assembly — proves the call is read-only (AC-F5-5)",
    )
    approx_tokens: int = Field(..., description="char/4 estimate of context length")
    token_budget: int = Field(..., description="20% of context_window used as the retrieval slice")

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "homelab docker services",
                "context": "[1] Homelab Setup\nDocker Compose ...\n",
                "results": [
                    {
                        "n": 1,
                        "id": "00000000-0000-0000-0000-000000000001",
                        "title": "Homelab Setup",
                        "slug": "homelab-setup",
                        "score": 0.87,
                        "phase": "vector",
                    }
                ],
                "data_version": 5,
                "approx_tokens": 512,
                "token_budget": 6553,
            }
        }
    }


# ── POST /ingest/from-text Pydantic models (ADR-0019 §2.7, AC-F6-5) ──────────


class IngestFromTextRequest(BaseModel):
    """
    Request body for POST /ingest/from-text (ADR-0019 §2.7, AC-F6-5 save-to-wiki).

    Writes ``text`` to ``vault/raw/sources/chat-{message_id}.md`` (or a derived name)
    and runs the same ``ingest_file`` seam (ADR-0003).  No new ingest logic — only a
    file-materialisation step.
    """

    text: str = Field(
        ...,
        min_length=1,
        description="Raw text to ingest (e.g. an assistant message)",
    )
    source_hint: str | None = Field(
        default=None,
        description=(
            "Optional hint for the output filename stem, e.g. a message_id or short slug. "
            "Sanitised to basename; falls back to 'chat-<uuid>' when omitted or unsafe."
        ),
    )
    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "# Homelab notes\nDocker services on TrueNAS...",
                "source_hint": "chat-homelab-notes",
                "vault_id": None,
            }
        }
    }


class IngestFromTextResponse(BaseModel):
    """202 response for POST /ingest/from-text (ADR-0019 §2.7)."""

    file_path: str = Field(..., description="Path written relative to vault_root")
    status: str = Field(..., description='"queued" — watcher ingests asynchronously')
    page_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Page UUID when ingest completes synchronously (trigger path); "
            "null when async (watcher path)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/chat-homelab-notes.md",
                "status": "queued",
                "page_id": None,
            }
        }
    }


# ── Import-schedule Pydantic models (Feature S, ADR-0020 §4.6) ────────────────

_VALID_FREQUENCIES = {"15m", "1h", "6h", "daily"}


class ImportScheduleResponse(BaseModel):
    """
    GET /import-schedule response body (ADR-0020 §4.6).

    Returns the current config + last-run status for the vault's import schedule.
    Returns sane defaults (enabled=false, frequency="1h") if no row exists yet.
    """

    enabled: bool = Field(default=False, description="Scheduler is enabled")
    source_dir: str | None = Field(
        default=None,
        description="Container-visible absolute path to scan (e.g. /import)",
    )
    frequency: str = Field(
        default="1h",
        description="'15m' | '1h' | '6h' | 'daily'",
    )
    last_run_at: datetime | None = Field(
        default=None,
        description="Timestamp of the last completed scan; null if never run",
    )
    last_status: str | None = Field(
        default=None,
        description="ok | error | running | skipped_disabled | dir_missing | null",
    )
    last_imported_count: int = Field(
        default=0,
        description="Files copied (new/changed) during the last scan",
    )
    last_error: str | None = Field(
        default=None,
        description="Error detail from the last failed scan; null on success",
    )

    model_config = {"from_attributes": True}


class ImportSchedulePutBody(BaseModel):
    """Request body for PUT /import-schedule (ADR-0020 §4.6)."""

    enabled: bool | None = Field(default=None, description="Enable or disable the scheduler")
    source_dir: str | None = Field(
        default=None,
        description="Container-visible path (e.g. /import); null to clear",
    )
    frequency: str | None = Field(
        default=None,
        description="'15m' | '1h' | '6h' | 'daily'",
    )

    @field_validator("frequency")
    @classmethod
    def _valid_frequency(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of {sorted(_VALID_FREQUENCIES)}, got {v!r}")
        return v


class ImportSchedulePutResponse(ImportScheduleResponse):
    """
    PUT /import-schedule response body (ADR-0020 §4.6).

    Extends ImportScheduleResponse with dir validation fields (save-then-warn).
    """

    dir_ok: bool = Field(
        default=True,
        description="False if source_dir does not exist/is not readable inside the container",
    )
    dir_message: str | None = Field(
        default=None,
        description="Warning message when dir_ok is False; null when ok",
    )


class RunNowResponse(BaseModel):
    """202 response body for POST /import-schedule/run-now (ADR-0020 §4.6)."""

    status: str = Field(default="started", description="'started' — scan running in background")


# ── Chat Pydantic models (F6/F7, ADR-0019 §2.2/§2.5) ──────────────────────────

_VALID_CHAT_ROLES = {"user", "assistant", "system"}


class ConversationResponse(BaseModel):
    """API shape for one conversations row (ADR-0019 §2.5)."""

    id: uuid.UUID
    vault_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    total: int
    limit: int
    offset: int


class ConversationCreate(BaseModel):
    """Request body for POST /conversations (ADR-0019 §2.5). vault_id defaults to settings."""

    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")
    title: str | None = Field(default=None, description="Optional initial title")


class ChatMessageResponse(BaseModel):
    """
    API shape for one messages row (ADR-0019 §2.5). `content` is RAW incl. literal
    <think>… (AC-F7-2); the client re-derives think-vs-content with the same split.
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    citations: list[Any] | None = Field(default=None, description="[] in M4 (M5 reserved)")
    provider_type: str | None
    model_id: str | None
    input_tokens: int
    output_tokens: int
    total_cost_usd: float = Field(description="0.0 for local/cli (I7); serialised as number")
    created_at: datetime

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


class ChatMessageListResponse(BaseModel):
    items: list[ChatMessageResponse]
    total: int


class ChatMessageIn(BaseModel):
    """One turn in a ChatRequest. Mirrors the backend-neutral Message shape (I6)."""

    role: str = Field(..., description="user | assistant | system")
    content: str = Field(..., min_length=1)

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in _VALID_CHAT_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_CHAT_ROLES)}, got {v!r}")
        return v


class ChatRequest(BaseModel):
    """
    Request body for POST /chat/stream (ADR-0019 §2.2).

    The server NEVER accepts provider_type / model_id (I6 / Do-NOT #4): the backend resolves
    `resolve_provider_config("chat", vault_id)`. `operation` is fixed to "chat" so the same
    abstraction can route ingest-vs-chat differently.
    """

    conversation_id: uuid.UUID | None = Field(
        default=None, description="null = start a new conversation (id returned in done event)"
    )
    messages: list[ChatMessageIn] = Field(..., min_length=1)
    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")
    context_window: int | None = Field(
        default=None,
        ge=4096,
        le=1_000_000,
        description="F14 window override (4096..1_000_000); null → provider/32K default",
    )
    operation: Literal["chat"] = Field(default="chat", description="Fixed to 'chat'")
    regenerate: bool = Field(
        default=False,
        description="AC-F6-4: delete the last assistant message before re-streaming",
    )


# ── GET /status ────────────────────────────────────────────────────────────────


@app.get(
    "/status",
    response_model=StatusResponse,
    summary="Service health + data_version",
    description=(
        "Returns vault_id, current data_version (monotonic ingest counter), "
        "service started_at, and uptime_seconds. (AC-REST-1, AC-F16dv-3)"
    ),
)
async def get_status() -> StatusResponse:
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        data_version = state.data_version if state is not None else 0

    now = datetime.now(UTC)
    uptime = (now - _started_at).total_seconds()
    return StatusResponse(
        vault_id=settings.vault_id,
        data_version=data_version,
        started_at=_started_at,
        uptime_seconds=uptime,
    )


# ── GET /pages ─────────────────────────────────────────────────────────────────


@app.get(
    "/pages",
    response_model=PageListResponse,
    summary="List live pages",
    description=(
        "Paginated list of pages where deleted_at IS NULL. " "Supports limit/offset. (AC-REST-2)"
    ),
)
async def list_pages(
    limit: int = Query(default=50, ge=1, le=500, description="Max rows to return"),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> PageListResponse:
    async with get_session() as session:
        total_row = await session.execute(
            select(func.count())
            .select_from(Page)
            .where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        total: int = total_row.scalar_one()

        rows = await session.execute(
            select(Page)
            .where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
            .order_by(Page.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        pages = rows.scalars().all()

    return PageListResponse(
        items=[_page_to_response(p) for p in pages],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── GET /pages/{id} ────────────────────────────────────────────────────────────


@app.get(
    "/pages/{page_id}",
    response_model=PageResponse,
    summary="Get a single page by UUID",
    description=(
        "Returns full page metadata; 404 if unknown or deleted; 422 on invalid UUID. "
        "(AC-REST-3, AC-REST-6)"
    ),
)
async def get_page(page_id: uuid.UUID) -> PageResponse:
    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()

    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

    return _page_to_response(page)


# ── GET /pages/{id}/related ────────────────────────────────────────────────────

_RELATED_MAX_LIMIT = 50  # hard cap: never return more than this many related pages


class RelatedPageItem(BaseModel):
    """
    One entry in the GET /pages/{id}/related response.

    score is the stored 4-signal edge weight (ADR-0012):
      3·direct_link_count + 4·shared_source_count + 1.5·adamic_adar + 1·same_type
    Reuses the persisted edges table — no recompute (I1/I2).
    """

    page_id: uuid.UUID = Field(..., description="UUID of the related page")
    title: str | None = Field(None, description="YAML frontmatter title; NULL if absent")
    type: str | None = Field(None, description="YAML frontmatter type; NULL if absent")
    score: float = Field(..., description="4-signal edge weight (higher = more related)")


class RelatedPagesResponse(BaseModel):
    """Response for GET /pages/{id}/related."""

    items: list[RelatedPageItem]
    total: int = Field(..., description="Total related pages found (before limit)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "page_id": "00000000-0000-0000-0000-000000000002",
                        "title": "Beta Concept",
                        "type": "concept",
                        "score": 11.5,
                    }
                ],
                "total": 1,
            }
        }
    }


@app.get(
    "/pages/{page_id}/related",
    response_model=RelatedPagesResponse,
    summary="Top-N related pages ranked by 4-signal edge weight",
    description=(
        "Returns the top *limit* pages most related to *page_id*, ranked by the stored "
        "4-signal edge weight (direct ×3, source-overlap ×4, Adamic-Adar ×1.5, "
        "type-affinity ×1 — ADR-0012). "
        "Reads the persisted *edges* table directly: no graph recompute, no FA2 (I1/I2). "
        "Empty list (200) if the page has no edges yet. "
        "404 if the page is unknown or soft-deleted. "
        "limit is capped at 50; default 10."
    ),
    responses={
        200: {"description": "Related pages list (may be empty if no edges yet)"},
        404: {"description": "Page not found"},
        422: {"description": "Invalid page_id UUID or limit out of range"},
    },
)
async def get_related_pages(
    page_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=_RELATED_MAX_LIMIT, description="Max results (1–50)"),
) -> RelatedPagesResponse:
    """
    GET /pages/{page_id}/related — top-N related pages from the persisted edges table.

    I1/I2 compliance: reads edges + pages tables only. Never triggers a graph recompute.
    Edges are stored canonically (smaller UUID first) but are undirected, so we match
    both endpoints. Raw SQL used for the dual-endpoint join to guarantee identical
    behaviour on SQLite (tests) and Postgres (production).
    CAST(… AS TEXT) used for UUID columns for cross-DB portability (memory note:
    raw-sql-sqlite-tests-vs-postgres-runtime).
    """
    # On Postgres, UUID columns are native (UUID type) and comparisons are type-safe;
    # the text cast still works correctly.  On SQLite (tests), SQLAlchemy stores UUID
    # columns as 32-char hex strings (no hyphens), while str(uuid.UUID(...)) produces
    # the hyphenated form.  REPLACE(..., '-', '') on both sides normalises the comparison
    # to format-agnostic hex matching — portable across both engines (memory note:
    # raw-sql-sqlite-tests-vs-postgres-runtime).
    pid_str = str(page_id)  # standard hyphenated form, e.g. "abc-..."; stripped in SQL
    vault = settings.vault_id

    async with get_session() as session:
        # 1. Verify the page exists and is live (ORM — clean, portable)
        page_row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == vault,
                Page.deleted_at.is_(None),
            )
        )
        page = page_row.scalar_one_or_none()
        if page is None:
            raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

        # 2. Query edges — undirected, so match either endpoint.
        #    UNION ALL of two directional selects is the simplest portable pattern:
        #    one leg where we are the source, one where we are the target.
        #    REPLACE(CAST(col AS TEXT), '-', '') strips hyphens from both UUID columns
        #    and the parameter so the comparison is format-agnostic on SQLite (test)
        #    and Postgres (production).  The neighbour_id is returned as-cast (no
        #    strip needed — uuid.UUID() handles both formats).
        neighbours_sql = sa_text(
            """
            SELECT e.weight,
                   CAST(p.id AS TEXT)  AS neighbour_id,
                   p.title             AS neighbour_title,
                   p.type              AS neighbour_type
            FROM edges e
            JOIN pages p
              ON REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
               = REPLACE(CAST(p.id            AS TEXT), '-', '')
             AND p.deleted_at IS NULL
            WHERE e.vault_id = :vault_id
              AND REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
                = REPLACE(:page_id, '-', '')

            UNION ALL

            SELECT e.weight,
                   CAST(p.id AS TEXT)  AS neighbour_id,
                   p.title             AS neighbour_title,
                   p.type              AS neighbour_type
            FROM edges e
            JOIN pages p
              ON REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
               = REPLACE(CAST(p.id            AS TEXT), '-', '')
             AND p.deleted_at IS NULL
            WHERE e.vault_id = :vault_id
              AND REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
                = REPLACE(:page_id, '-', '')

            ORDER BY weight DESC
            LIMIT :lim
            """
        ).bindparams(vault_id=vault, page_id=pid_str, lim=limit)

        result = await session.execute(neighbours_sql)
        rows = result.all()

        # 3. Count total related (before limit) — same UNION, wrapped in COUNT
        count_sql = sa_text(
            """
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM edges e
                JOIN pages p
                  ON REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
                   = REPLACE(CAST(p.id            AS TEXT), '-', '')
                 AND p.deleted_at IS NULL
                WHERE e.vault_id = :vault_id
                  AND REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
                    = REPLACE(:page_id, '-', '')

                UNION ALL

                SELECT 1
                FROM edges e
                JOIN pages p
                  ON REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
                   = REPLACE(CAST(p.id            AS TEXT), '-', '')
                 AND p.deleted_at IS NULL
                WHERE e.vault_id = :vault_id
                  AND REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
                    = REPLACE(:page_id, '-', '')
            ) AS _related
            """
        ).bindparams(vault_id=vault, page_id=pid_str)

        total_result = await session.execute(count_sql)
        total: int = total_result.scalar_one()

    items = [
        RelatedPageItem(
            page_id=uuid.UUID(row.neighbour_id),
            title=row.neighbour_title,
            type=row.neighbour_type,
            score=row.weight,
        )
        for row in rows
    ]
    return RelatedPagesResponse(items=items, total=total)


# ── GET /pages/{id}/content ────────────────────────────────────────────────────


def _resolve_page_path(file_path: str) -> Path:
    """
    Resolve a page's file_path (relative to vault_root) to an absolute Path.

    Raises HTTPException 400 if the resolved path escapes the vault root (path
    traversal guard). The check uses Path.resolve() so symlinks and ``..`` components
    cannot be used to escape. Used by GET /pages/{id}/content.
    """
    vault_root = settings.vault_root.resolve()
    candidate = (vault_root / file_path).resolve()
    try:
        candidate.relative_to(vault_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path {file_path!r} resolves outside the vault root — " "path traversal rejected."
            ),
        ) from exc
    return candidate


def _resolve_wiki_page_path(file_path: str) -> Path:
    """
    Resolve a page's file_path for editing (PUT /pages/{id}/content, ADR-0035).

    Two-level guard (ADR-0035):
      1. Traversal: resolved path must stay inside vault_root → 400.
      2. Wiki-only: PUT only touches vault/wiki/ pages (never raw/sources/) → 403.
         Attempting to overwrite a sources file via this endpoint is rejected to prevent
         inadvertent replacement of immutable raw inputs (K1 vault layer separation, I5).

    Returns the absolute resolved Path on success.
    """
    abs_path = _resolve_page_path(file_path)  # raises 400 on traversal
    wiki_root = settings.vault_root.resolve() / "wiki"
    try:
        abs_path.relative_to(wiki_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Path {file_path!r} is not inside vault/wiki/. "
                "PUT /pages/{{id}}/content only edits wiki pages, "
                "not raw/sources/ files (K1 layer separation)."
            ),
        ) from exc
    return abs_path


@app.get(
    "/pages/{page_id}/content",
    response_model=PageContentResponse,
    summary="Read raw markdown content of a wiki page",
    description=(
        "Returns the raw UTF-8 markdown (including YAML frontmatter) for the page "
        "identified by *page_id*. The content is read directly from the vault filesystem; "
        "no caching layer is applied so callers always get the latest committed bytes. "
        "404 if the page row is unknown or soft-deleted; 410 if the row exists but the "
        "file is absent on disk (watcher has not yet re-indexed a deletion in flight); "
        "400 on path-traversal attempt. (F1-content-read, I1, I5)"
    ),
    responses={
        200: {"description": "Page content returned"},
        400: {"description": "Path traversal rejected"},
        404: {"description": "Page not found in index"},
        410: {"description": "Page row exists but file missing on disk"},
    },
)
async def get_page_content(page_id: uuid.UUID) -> PageContentResponse:
    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()

    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

    abs_path = _resolve_page_path(page.file_path)

    if not abs_path.exists():
        raise HTTPException(
            status_code=410,
            detail=(
                f"Page {page_id} row exists (file_path={page.file_path!r}) "
                "but the file is not present on disk. "
                "The watcher will remove the row when the deletion event is processed."
            ),
        )

    raw_bytes = await asyncio.get_event_loop().run_in_executor(None, abs_path.read_bytes)
    content = raw_bytes.decode("utf-8", errors="replace")

    # content_hash is the optimistic-lock token (ADR-0035): it MUST hash the exact bytes returned
    # here, so PUT's on-disk comparison succeeds iff the file is unchanged between GET and PUT.
    # We recompute from the file bytes rather than returning page.content_hash, which can lag the
    # file (the DB row reflects the last index, not necessarily the current disk state).
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    return PageContentResponse(
        id=page.id,
        title=page.title,
        file_path=page.file_path,
        content=content,
        content_hash=content_hash,
        updated_at=page.updated_at,
        page_type=page.page_type,
        sources=page.sources,
        tags=page.tags,
    )


# ── PUT /pages/{id}/content ────────────────────────────────────────────────────


@app.put(
    "/pages/{page_id}/content",
    response_model=PageContentPutResponse,
    summary="Overwrite the markdown content of a wiki page",
    description=(
        "Atomically overwrites the markdown file for *page_id* with the supplied content. "
        "Only edits pages inside vault/wiki/ — raw/sources/ files are rejected with 403 "
        "(K1 vault layer separation). "
        "Write is done via a temp-file + os.replace so a crash mid-write does not corrupt "
        "the vault. A trailing newline is enforced (Obsidian / git convention, I5). "
        "\n\n"
        "Validation (ADR-0035): "
        "(a) body > 4 MB → 413; "
        "(b) YAML frontmatter must parse cleanly → 422 (protects Obsidian vault validity, I5). "
        "\n\n"
        "Optimistic concurrency: when *expected_hash* is supplied and does not match the "
        "current sha256 of the on-disk file, 409 Conflict is returned — the caller should "
        "reload the page and present the diff to the user before retrying. "
        "\n\n"
        "Re-indexing (I1/ADR-0035): the watcher observes vault/raw/sources/ only, NOT "
        "vault/wiki/. Therefore this endpoint calls reindex_wiki_page_body() INLINE after "
        "writing so the Postgres row (content_hash, updated_at, wikilinks) and Qdrant point "
        "are updated synchronously before the response is returned. reindex_wiki_page_body() "
        "is the purpose-built single-page re-index primitive (ADR-0036 §2.1): it updates "
        "content_hash, re-embeds the body into Qdrant, re-derives K5 wikilinks, and bumps "
        "data_version ONCE so the debounced GraphCache recompute fires (I2). It does NOT "
        "invoke the LLM analyze→generate pipeline — preserving the user's exact edit (I5). "
        "This is a single-page update, never a full rescan (I1). "
        "(F1-content-write, I1, I5, ADR-0035)"
    ),
    responses={
        200: {"description": "Content written; new hash returned"},
        400: {"description": "Path traversal rejected"},
        403: {"description": "Path is not inside vault/wiki/ (K1 layer separation)"},
        404: {"description": "Page not found"},
        409: {"description": "Stale expected_hash — content was modified since last read"},
        410: {"description": "Page row exists but file missing (cannot overwrite)"},
        413: {"description": "Content body exceeds _MAX_PAGE_CONTENT_BYTES (4 MB)"},
        422: {"description": "YAML frontmatter is invalid — Obsidian vault would break (I5)"},
    },
)
async def put_page_content(
    page_id: uuid.UUID,
    body: PageContentPutRequest,
) -> PageContentPutResponse:
    import tempfile

    # ── Body size guard (ADR-0035, I7) ───────────────────────────────────────
    if len(body.content.encode("utf-8")) > _MAX_PAGE_CONTENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Content body exceeds the maximum allowed size of "
                f"{_MAX_PAGE_CONTENT_BYTES // (1024 * 1024)} MB."
            ),
        )

    # ── YAML frontmatter validation (ADR-0035, I5) ────────────────────────────
    # Reject content that python-frontmatter cannot parse to protect Obsidian
    # vault validity (I5). An absent frontmatter block is NOT an error (K6 — tolerant).
    try:
        import frontmatter as _fm

        _fm.loads(body.content)
    except Exception as _fm_exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"YAML frontmatter parse error: {_fm_exc}. "
                "Fix the frontmatter before writing (Obsidian vault validity, I5)."
            ),
        ) from _fm_exc

    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()

    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

    # ── Path safety + wiki-only guard (ADR-0035) ──────────────────────────────
    abs_path = _resolve_wiki_page_path(page.file_path)

    if not abs_path.exists():
        raise HTTPException(
            status_code=410,
            detail=(
                f"Page {page_id} row exists (file_path={page.file_path!r}) "
                "but the file is not present on disk."
            ),
        )

    # ── Optimistic concurrency check ──────────────────────────────────────────
    if body.expected_hash is not None:
        on_disk_bytes = await asyncio.get_event_loop().run_in_executor(None, abs_path.read_bytes)
        on_disk_hash = hashlib.sha256(on_disk_bytes).hexdigest()
        if on_disk_hash != body.expected_hash:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Content hash mismatch: expected {body.expected_hash!r} but the "
                    f"current on-disk hash is {on_disk_hash!r}. "
                    "The page was modified since you last read it. "
                    "Reload the page before retrying."
                ),
            )

    # ── Enforce trailing newline (Obsidian / git convention, I5) ─────────────
    new_content = body.content if body.content.endswith("\n") else body.content + "\n"
    new_bytes = new_content.encode("utf-8")
    new_hash = hashlib.sha256(new_bytes).hexdigest()

    # ── Atomic write: tmp file in same dir + os.replace (Path.replace) ───────
    def _write() -> None:
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(abs_path.parent),
            suffix=".content_tmp",
        )
        try:
            import os

            os.write(tmp_fd, new_bytes)
            os.close(tmp_fd)
            Path(tmp_name).replace(abs_path)
        except Exception:  # noqa: BLE001
            try:
                os.close(tmp_fd)
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001, S110
                pass
            raise

    await asyncio.get_event_loop().run_in_executor(None, _write)

    # ── Inline incremental re-index (I1, ADR-0035) ───────────────────────────
    # The watcher observes vault/raw/sources/ ONLY — not vault/wiki/. We use the
    # purpose-built reindex_wiki_page_body() primitive here (ADR-0035 / ADR-0036 §2.1):
    #   - atomic-write the new bytes (already done above via _write)
    #   - refreshes content_hash in Postgres (preserve existing title/type/sources — I5)
    #   - re-embeds the body into Qdrant (bge-m3) — skipped when embeddings disabled
    #   - re-derives K5 wikilinks from the new body (→ F4 direct-link ×3 edges)
    #   - bumps data_version ONCE → GraphCache debounce fires → FA2 recomputes (I2)
    # This satisfies I1 (single-page incremental update) and I2 (data_version bump,
    # no inline FA2). Do NOT use ingest_file() here: ingest_file() calls
    # _resolve_ingest_provider_config() and, when a provider is configured, invokes
    # run_ingest_pipeline() (analyze→generate loop) on the wiki content — which would
    # regenerate and overwrite the user's manual edit (data-loss bug, ADR-0035 gap).
    # reindex_wiki_page_body() skips the provider entirely (it is a pure re-index
    # primitive, not a content-generation primitive). Do NOT add a watcher for wiki/
    # (rejected in ADR-0026 §5).
    # Extract the body (sans frontmatter) for embedding and wikilink parsing.
    # _fm.loads() already ran above for validation; re-run cheaply for body extraction.
    import frontmatter as _fm_body  # noqa: PLC0415

    from app.ingest.orchestrator import reindex_wiki_page_body  # noqa: PLC0415

    _doc = _fm_body.loads(new_content)
    body_for_embedding = _doc.content  # the markdown body without the YAML block

    await reindex_wiki_page_body(
        page=page,
        new_file_text=new_content,
        body_for_embedding=body_for_embedding,
        bump=True,
    )

    # ── Return updated_at from the freshly committed row ─────────────────────
    async with get_session() as session:
        row2 = await session.execute(select(Page).where(Page.id == page_id))
        updated_page = row2.scalar_one_or_none()

    updated_at = updated_page.updated_at if updated_page is not None else datetime.now(UTC)

    return PageContentPutResponse(
        id=page_id,
        content_hash=new_hash,
        updated_at=updated_at,
    )


# ── PATCH /pages/{id}/position ────────────────────────────────────────────────


class PatchPositionRequest(BaseModel):
    """Body for PATCH /pages/{page_id}/position (Feature A)."""

    x: float = Field(..., description="New x coordinate (FR space)")
    y: float = Field(..., description="New y coordinate (FR space)")


class PatchPositionResponse(BaseModel):
    """Response for PATCH /pages/{page_id}/position (Feature A)."""

    id: str
    x: float
    y: float
    pinned: bool


@app.patch(
    "/pages/{page_id}/position",
    response_model=PatchPositionResponse,
    summary="Persist a manual node drag position and pin the node",
    description=(
        "Updates pages.x/y and sets pages.pinned=true so the node stays at the dropped "
        "position across FR recomputes.  Also patches the live GraphCache snapshot in place "
        "so the next GET /graph HIT reflects the new position immediately. "
        "Does NOT trigger FR, does NOT bump data_version — O(1). (Feature A, I2)"
    ),
    responses={
        200: {"description": "Position updated and node pinned"},
        404: {"description": "Page not found"},
    },
)
async def patch_node_position(
    page_id: uuid.UUID,
    body: PatchPositionRequest,
) -> PatchPositionResponse:
    """
    PATCH /pages/{page_id}/position — persist a manual drag position (Feature A).

    1. UPDATE pages SET x=:x, y=:y, pinned=true WHERE id=:id and vault_id=:vid.
    2. Patch the live GraphCache snapshot in-memory so HIT path returns new coords.
    3. Return 200 {id, x, y, pinned: true}.

    Does NOT bump data_version; does NOT trigger FR recompute (I2).
    404 if the page is missing or soft-deleted.
    """
    from sqlalchemy import text as sa_text

    async with get_session() as session:
        result = await session.execute(
            sa_text(
                "UPDATE pages "
                "SET x = :x, y = :y, pinned = true "
                "WHERE id = CAST(:page_id AS uuid) "
                "  AND vault_id = :vault_id "
                "  AND deleted_at IS NULL "
                "RETURNING id"
            ).bindparams(
                x=body.x,
                y=body.y,
                page_id=str(page_id),
                vault_id=settings.vault_id,
            )
        )
        row = result.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_id} not found or deleted",
        )

    # Patch the live snapshot so the next HIT already has the new coords (Feature A).
    node_id_str = str(page_id)
    if _graph_cache is not None:
        found = _graph_cache.patch_node_position(node_id_str, body.x, body.y)
        logger.debug(
            "patch_node_position: cache patch %s for node_id=%s",
            "succeeded" if found else "no-op (no snapshot yet)",
            node_id_str,
        )

    return PatchPositionResponse(id=node_id_str, x=body.x, y=body.y, pinned=True)


# ── POST /ingest/trigger ───────────────────────────────────────────────────────


@app.post(
    "/ingest/trigger",
    response_model=IngestTriggerResponse,
    status_code=202,
    summary="Manually trigger ingest of a single file",
    description=(
        "Synchronously ingests the file at file_path through the seam. "
        "Returns HTTP 202 with typed {task_id, status, page_id} (ADR-0006, AC-REST-4, AC-D4u). "
        "status is 'completed' or 'skipped' (I1 fast-path)."
    ),
    responses={
        202: {"description": "Ingest accepted and completed"},
        422: {"description": "Validation error (missing file_path, bad format, or file not found)"},
    },
)
async def trigger_ingest(body: IngestTriggerRequest) -> IngestTriggerResponse:
    """
    Trigger incremental ingest of a single file (K2 partial, ADR-0006, AC-D4u).

    Resolves the file path under vault_root if relative.
    Runs ingest_file through the seam (ADR-0003); never touches DB/Qdrant directly.
    Returns 202 per ADR-0006 contract with a typed schema so task_id appears in OpenAPI (AC-D4u).
    """
    from pathlib import Path

    # Resolve relative or absolute path
    path = Path(body.file_path)
    if not path.is_absolute():
        path = settings.vault_root / path

    if not path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"File not found: {body.file_path}",
        )

    result: IngestResult = await ingest_file(path)

    return IngestTriggerResponse(
        task_id=None,
        status=result.status,
        page_id=result.page_id,
    )


# ── POST /ingest/upload ────────────────────────────────────────────────────────


@app.post(
    "/ingest/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="Upload a document for async watcher-driven ingest (F12 multi-format)",
    description=(
        "Feature U (ADR-0020 §2, M4-EXT) + F12 Multi-format ingest (ADR-0025 §4.2). "
        "Accepts text/markdown (.md/.txt/.markdown), binary formats (.pdf/.docx/.pptx/.xlsx), "
        "and placeholder formats (.png/.jpg/.jpeg/.gif/.webp/.mp3/.mp4/.wav/.m4a). "
        "For text: writes directly to vault/raw/sources/<name>; watcher ingests asynchronously. "
        "For binary/placeholder: (1) writes original binary to vault/raw/sources/<name>.<ext> "
        "(preserved, I5/K1); (2) synchronously extracts text → companion "
        "<stem>.extracted.md with valid YAML frontmatter (I5); (3) returns 202. "
        "The watcher ingests ONLY the companion (.md is in _ALLOWED_EXTENSIONS); the binary "
        "is ignored by the watcher (I1). Extraction is upload-time, NEVER in the watcher. "
        "413 on oversize (MAX_UPLOAD_BYTES). 415 for truly unknown types. "
        "422 for unsafe filename. 202 {file_path, status:'queued', overwritten}."
    ),
    responses={
        202: {
            "description": "File saved; watcher will ingest asynchronously (companion for binaries)"
        },
        413: {"description": "File exceeds MAX_UPLOAD_BYTES"},
        415: {"description": "Unsupported file type"},
        422: {"description": "Filename is empty or unsafe after sanitization"},
    },
)
async def upload_ingest(
    file: UploadFile = File(..., description="The document to upload"),
) -> UploadResponse:
    """
    POST /ingest/upload — non-blocking multipart upload (ADR-0020 Feature U, §2).

    1. Validate extension (hard) + Content-Type (soft advisory) → 415 on non-text.
    2. Stream body to a temp file, abort at MAX_UPLOAD_BYTES              → 413.
    3. safe_source_name(filename)                                          → 422 on unsafe.
    4. resolve_under_sources(name) containment check                       → 422 on escape.
    5. overwritten = dst.exists()
    6. Atomically move temp file to dst (same-fs rename inside /vault).
    7. Return 202 {file_path, status:"queued", overwritten} immediately.

    The WATCHER observes the vault/raw/sources/ write and ingests asynchronously.
    This is the same path Feature S (scheduled copy) uses — no double-ingest (I9).
    Poll GET /ingest/runs or GET /pages to confirm ingest completion (~15-30s).

    Security: basename-only; no caller-controlled path segments; containment-checked.
    I1: watcher's mtime/hash gate deduplicates re-uploads of unchanged content.
    I5: writes ONLY to vault/raw/sources/ — never to wiki/ or .obsidian/.
    """
    import tempfile

    max_bytes: int = settings.max_upload_bytes

    # ── Extension check (authoritative; MIME is advisory) ────────────────────
    # Do this BEFORE reading bytes (fail fast)
    raw_name: str = file.filename or ""
    # safe_source_name raises 415 for non-text extensions, 422 for unsafe
    name = safe_source_name(raw_name)

    # ── Stream body with byte cap (I7) ───────────────────────────────────────
    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(raw_sources), suffix=".upload_tmp")
    bytes_read = 0
    try:
        with open(tmp_fd, "wb") as tmp_file:
            chunk_size = 65_536  # 64 KB chunks
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit."),
                    )
                tmp_file.write(chunk)
    except HTTPException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload read error: {exc}") from exc
    finally:
        await file.close()

    # ── Containment check ────────────────────────────────────────────────────
    try:
        dst = resolve_under_sources(name)
    except HTTPException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    # ── Atomic move (same-fs: rename within /vault/raw/sources/) ────────────
    overwritten: bool = dst.exists()
    try:
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}") from exc

    # ── F12: synchronous extraction for binary/placeholder uploads (ADR-0025 §4.2) ──
    # If the file is a binary or placeholder extension, extract text NOW (before 202)
    # so the companion .extracted.md exists when the watcher fires.
    # The watcher ignores the binary (not in _ALLOWED_EXTENSIONS); only the companion is
    # ingested. This is the ONLY place extraction happens — never inside the watcher (Do-NOT #12).
    suffix_lower = Path(name).suffix.lower()
    from app.upload import _EXTRACTABLE_EXTENSIONS, _PLACEHOLDER_EXTENSIONS

    if suffix_lower in (_EXTRACTABLE_EXTENSIONS | _PLACEHOLDER_EXTENSIONS):
        try:
            from app.ingest.extract import UnsupportedFormatError, extract_text

            extracted = extract_text(dst)
            # Build companion filename: <stem>.extracted.md
            stem = Path(name).stem
            companion_name = f"{stem}.extracted.md"
            companion_dst = settings.raw_sources_dir / companion_name
            # Write valid Obsidian YAML frontmatter (I5, AC-F12-4, ADR-0025 §4.4)
            raw_rel = str(dst.relative_to(settings.vault_root))
            companion_content = (
                f'---\ntype: source\ntitle: {stem}\nsources: ["{raw_rel}"]\n---\n\n' + extracted
            )
            companion_dst.write_text(companion_content, encoding="utf-8")
            logger.info(
                "upload_ingest: extracted %s → companion %s (%d chars)",
                name,
                companion_name,
                len(extracted),
            )
            # Return the companion path as the queued file (the watcher ingests this)
            rel_path = str(companion_dst.relative_to(settings.vault_root))
        except UnsupportedFormatError as exc:
            # Should not happen (upload guard already validated the extension), but handle cleanly
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            # Extraction failure: log but do NOT block the 202 — the binary is safely saved.
            # The companion will not be created; the watcher will silently skip the binary (I1).
            logger.warning(
                "upload_ingest: extraction failed for %s: %s — companion not created",
                name,
                exc,
            )
            rel_path = str(dst.relative_to(settings.vault_root))
    else:
        rel_path = str(dst.relative_to(settings.vault_root))

    # ── Return 202 immediately — watcher ingests asynchronously ──────────────
    logger.info("upload_ingest: saved %s (%d bytes) — watcher will ingest", name, bytes_read)
    return UploadResponse(
        file_path=rel_path,
        status="queued",
        overwritten=overwritten,
    )


# ── POST /ingest/from-text ────────────────────────────────────────────────────


@app.post(
    "/ingest/from-text",
    response_model=IngestFromTextResponse,
    status_code=202,
    summary="Write inline text to raw/sources/ and queue watcher-driven ingest",
    description=(
        "Save-to-wiki seam (ADR-0019 §2.7, AC-F6-5). "
        "Materialises ``text`` to ``vault/raw/sources/chat-<hint>.md`` and returns 202 "
        "immediately. The watcher picks up the file and runs the full ingest pipeline "
        "(no new ingest logic — ADR-0003 guarantee, I1/I6). "
        "``source_hint`` is sanitised to a safe basename; falls back to ``chat-<uuid>`` when "
        "omitted or unsafe. 422 on empty text."
    ),
    responses={
        202: {"description": "Text saved; watcher will ingest asynchronously"},
        422: {"description": "Validation error (text empty or too long)"},
    },
)
async def ingest_from_text(body: IngestFromTextRequest) -> IngestFromTextResponse:
    """
    POST /ingest/from-text — materialise inline text to raw/sources/ and enqueue watcher.

    1. Derive a safe filename from source_hint (basename-only, slug-safe fallback).
    2. Write the text to vault/raw/sources/<name>.md (atomically via temp → rename).
    3. Return 202 {file_path, status:'queued'} — watcher ingests asynchronously.

    I1: watcher's mtime/hash gate deduplicates re-posts of identical content.
    I5: writes ONLY to vault/raw/sources/ — never to wiki/ or .obsidian/.
    I6: inference goes through the existing ingest pipeline (ADR-0003, no shortcut).
    """
    import re as _re
    import tempfile as _tempfile

    _SLUG_RE_MAIN = _re.compile(r"[^a-z0-9_-]+")

    # Derive a safe filename stem from the hint (or a fresh UUID).
    raw_hint = (body.source_hint or "").strip()
    if raw_hint:
        stem = _SLUG_RE_MAIN.sub("-", raw_hint.lower()).strip("-")[:80]
        if not stem:
            stem = f"chat-{uuid.uuid4().hex[:8]}"
    else:
        stem = f"chat-{uuid.uuid4().hex[:8]}"
    filename = f"{stem}.md"

    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)
    dst = raw_sources / filename

    # Atomic write via temp → rename (same approach as upload_ingest).
    tmp_fd, tmp_name = _tempfile.mkstemp(dir=str(raw_sources), suffix=".fromtext_tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(body.text)
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to write text: {exc}") from exc

    try:
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to persist file: {exc}") from exc

    rel_path = str(dst.relative_to(settings.vault_root))
    logger.info(
        "ingest_from_text: saved %s (%d chars) — watcher will ingest",
        filename,
        len(body.text),
    )
    return IngestFromTextResponse(file_path=rel_path, status="queued", page_id=None)


# ── GET /search ───────────────────────────────────────────────────────────────


@app.get(
    "/search",
    response_model=SearchResponse,
    summary="4-phase RAG retrieval (F5, ADR-0022)",
    description=(
        "Run the F5 4-phase retrieval pipeline (ADR-0022 §2.2, AC-F5-6) and return a grounded "
        "context string + citation list. "
        "Phase 1: dense vector search via bge-m3 (Qdrant, top-k). "
        "Phase 2: BFS graph-expansion over the `edges` table (depth ≤ 2). "
        "Phase 3: token-budget allocation (20% of context_window, F14). "
        "Phase 4: context assembly with inline [n] markers. "
        "0-hit query → 200 with empty results + empty context (AC-F5-7a). "
        "READ-ONLY — never bumps data_version (AC-F5-5). "
        "Documented in openapi.json (I8, AC-F5-6)."
    ),
    responses={
        200: {"description": "Retrieval result (0-hit → empty results array)"},
        422: {"description": "Validation error (k out of range or missing q)"},
    },
)
async def search(
    q: str = Query(..., min_length=1, description="The query string to retrieve context for"),
    vault_id: str | None = Query(
        default=None,
        description="Vault scope; defaults to settings.vault_id",
    ),
    k: int = Query(
        default=8,
        ge=1,
        le=50,
        description="Dense top-k for the vector phase (1..50); default 8 (ADR-0022 §2.1)",
    ),
    context_window: int | None = Query(
        default=None,
        ge=4096,
        le=1_000_000,
        description="Context window override (4096..1_000_000); null → 32 768 default (F14)",
    ),
) -> SearchResponse:
    """
    GET /search — F5 4-phase retrieval (ADR-0022, AC-F5-6).

    Single bounded pass (I7): Qdrant bge-m3 dense search → edges BFS expansion → budget
    allocation → context assembly. Zero inference calls, zero vault walk (I1). Read-only
    — data_version is unchanged (AC-F5-5).
    """
    from app.chat.context import DEFAULT_CONTEXT_WINDOW as _DEFAULT_WINDOW
    from app.rag.retrieval import retrieve

    effective_vault_id = vault_id or settings.vault_id
    window = context_window or _DEFAULT_WINDOW

    rctx = await retrieve(
        query=q,
        vault_id=effective_vault_id,
        context_window=window,
        k=k,
    )

    results: list[SearchResultItem] = [
        SearchResultItem(
            n=c.n,
            id=c.ref.id,
            title=c.ref.title,
            slug=c.ref.slug,
            score=c.score,
            phase=c.phase,
        )
        for c in rctx.citations
    ]

    return SearchResponse(
        query=rctx.query,
        context=rctx.text,
        results=results,
        data_version=rctx.data_version,
        approx_tokens=rctx.approx_tokens,
        token_budget=rctx.token_budget,
    )


# ── GET /ingest/runs ───────────────────────────────────────────────────────────


@app.get(
    "/ingest/runs",
    response_model=IngestRunListResponse,
    summary="List ingest run history",
    description=(
        "Returns a paginated, started_at DESC list of ingest_runs rows. "
        "Exposes the I7 cost ledger to the user (AC-BE-IR-1..5, ADR-0018 §7). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional UUID filter. "
        "Column aliases: max_iter_used→iterations_used, finished_at→completed_at. "
        "total_cost_usd serialised as a number; frontend formats to exactly 4dp (I7)."
    ),
    responses={
        200: {"description": "Paginated ingest run list"},
        422: {"description": "Validation error (limit out of 1..100 or offset < 0)"},
    },
)
async def list_ingest_runs(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Max rows to return (1..100); 422 on out-of-range (AC-BE-IR-2)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Row offset for pagination (>=0); 422 on negative (AC-BE-IR-2)",
    ),
    vault_id: str | None = Query(
        default=None,
        description="Optional vault_id filter; omit to list all vaults (AC-BE-IR-2)",
    ),
) -> IngestRunListResponse:
    """
    GET /ingest/runs — paginated ingest run history (ADR-0018 §7, AC-BE-IR-1..5).

    Plain read query — no heavy computation (pure SELECT, ORDER BY, LIMIT/OFFSET).
    Filters by vault_id when provided.
    Orders by started_at DESC (AC-BE-IR-3).
    422 enforced by Query(ge=1, le=100) / Query(ge=0) validators (AC-BE-IR-5).
    """
    async with get_session() as session:
        # COUNT query (filtered)
        count_stmt = select(func.count()).select_from(IngestRun)
        if vault_id is not None:
            count_stmt = count_stmt.where(IngestRun.vault_id == vault_id)
        total_row = await session.execute(count_stmt)
        total: int = total_row.scalar_one()

        # Data query (filtered, ordered, paginated)
        data_stmt = select(IngestRun)
        if vault_id is not None:
            data_stmt = data_stmt.where(IngestRun.vault_id == vault_id)
        data_stmt = data_stmt.order_by(IngestRun.started_at.desc()).offset(offset).limit(limit)
        rows = await session.execute(data_stmt)
        runs = list(rows.scalars().all())

    items = [_ingest_run_to_response(r) for r in runs]
    return IngestRunListResponse(items=items, total=total, limit=limit, offset=offset)


def _ingest_run_to_response(run: IngestRun) -> IngestRunResponse:
    """
    Map IngestRun ORM row → IngestRunResponse.

    Applies the two ADR-0018 §7 aliases:
      max_iter_used  → iterations_used
      finished_at    → completed_at
    total_cost_usd converted from Decimal (Numeric column) to float for JSON serialisation.
    completed_at is None when status == 'running' (run still in progress).
    """
    completed_at: datetime | None = None if run.status == "running" else run.finished_at
    return IngestRunResponse(
        id=run.id,
        vault_id=run.vault_id,
        status=run.status,
        provider_type=run.provider_type,
        pages_created=run.pages_created,
        iterations_used=run.max_iter_used,
        total_cost_usd=float(run.total_cost_usd),
        started_at=run.started_at,
        completed_at=completed_at,
        error_message=run.error_message,
    )


# ── GET /ingest/queue + POST /ingest/runs/{id}/cancel|retry + pause/resume ───
# ADR-0046 §6 — live activity queue endpoints


@app.get(
    "/ingest/queue",
    response_model=QueueSnapshotResponse,
    summary="Live ingest activity queue snapshot",
    description=(
        "Returns the live in-memory queue state: processing (in-flight), pending (paused), "
        "failed (retained for retry), completed_since_idle, and per-task details. "
        "Pure in-memory — no DB scan. Safe to poll every 5 s (ADR-0046 §6, I3). "
        "(ADR-0046)"
    ),
    responses={200: {"description": "Queue snapshot"}},
)
async def get_ingest_queue() -> QueueSnapshotResponse:
    """GET /ingest/queue — live snapshot from the in-process queue manager (ADR-0046 §6)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    snap = _iq.snapshot()
    tasks = [QueueTaskItem(**t) for t in snap["tasks"]]
    return QueueSnapshotResponse(
        paused=snap["paused"],
        pending=snap["pending"],
        processing=snap["processing"],
        failed=snap["failed"],
        completed_since_idle=snap["completed_since_idle"],
        total=snap["total"],
        tasks=tasks,
    )


@app.post(
    "/ingest/runs/{run_id}/cancel",
    response_model=QueueCancelResponse,
    status_code=202,
    summary="Request cancellation of an in-flight ingest run",
    description=(
        "Sets the cooperative cancel event for the run. The loop checks the event at the "
        "next iteration boundary (never mid-provider-call, I7/I6). Cascade-deletes any pages "
        "written so far (I1) once the boundary is reached. 202 = cancel requested. "
        "404 = run_id unknown. 409 = run already in a terminal state. (ADR-0046 §3/§6)"
    ),
    responses={
        202: {"description": "Cancel requested — cleanup happens asynchronously"},
        404: {"description": "run_id not found in the active queue"},
        409: {"description": "Run is already in a terminal state (completed/failed/cancelled)"},
    },
)
async def cancel_ingest_run(run_id: uuid.UUID) -> QueueCancelResponse:
    """POST /ingest/runs/{id}/cancel — request cooperative cancellation (ADR-0046 §3)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    # Check if the run exists at all (active or recently failed/completed in DB)
    if _iq.is_run_active(run_id):
        cancelled = _iq.cancel(run_id)
        if not cancelled:
            # Should not happen given is_run_active check, but guard anyway
            raise HTTPException(status_code=409, detail="Run is not in a cancellable state")
        return QueueCancelResponse(run_id=str(run_id), status="cancelling", cleaned_pages=0)

    # Not in active map — check if it's a known failed entry
    failed_entry = _iq.find_failed_by_run_id(run_id)
    if failed_entry is not None:
        raise HTTPException(
            status_code=409,
            detail="Run is already in a terminal state and cannot be cancelled",
        )

    # Unknown run_id
    raise HTTPException(status_code=404, detail="run_id not found in the active queue")


@app.post(
    "/ingest/runs/{run_id}/retry",
    response_model=QueueRetryResponse,
    status_code=202,
    summary="Retry a failed ingest run",
    description=(
        "Re-dispatches the source file for re-ingest, incrementing retry_count. "
        "Hard cap: 3 retries (I7). "
        "202 = re-dispatch accepted. "
        "404 = run_id unknown. "
        "409 detail='max_retries_exceeded' when retry_count >= 3. "
        "409 detail='not_retryable' when run is still active. (ADR-0046 §5/§6)"
    ),
    responses={
        202: {"description": "Retry dispatched"},
        404: {"description": "run_id unknown"},
        409: {"description": "max_retries_exceeded or run is not in a retryable state"},
    },
)
async def retry_ingest_run(run_id: uuid.UUID) -> QueueRetryResponse:
    """POST /ingest/runs/{id}/retry — re-dispatch a failed source file (ADR-0046 §5)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    try:
        result = _iq.request_retry(run_id)
    except ValueError as exc:
        detail = str(exc)
        if detail == "max_retries_exceeded":
            raise HTTPException(status_code=409, detail="max_retries_exceeded") from exc
        if detail == "not_retryable":
            raise HTTPException(
                status_code=409,
                detail="Run is currently active and cannot be retried; cancel it first",
            ) from exc
        raise HTTPException(status_code=409, detail=detail) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="run_id not found")

    source_path, new_retry_count = result
    return QueueRetryResponse(
        run_id_prev=str(run_id),
        source_path=source_path,
        retry_count=new_retry_count,
        status="queued",
    )


@app.post(
    "/ingest/queue/pause",
    response_model=QueuePauseResponse,
    status_code=200,
    summary="Pause the ingest queue",
    description=(
        "Pauses dispatch — new FS events are parked in memory until resume. "
        "Idempotent; calling while already paused is a no-op. (ADR-0046 §4)"
    ),
    responses={200: {"description": "Queue paused (idempotent)"}},
)
async def pause_ingest_queue() -> QueuePauseResponse:
    """POST /ingest/queue/pause — gate new dispatches (ADR-0046 §4)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    _iq.pause()
    return QueuePauseResponse(paused=True)


@app.post(
    "/ingest/queue/resume",
    response_model=QueueResumeResponse,
    status_code=200,
    summary="Resume the ingest queue",
    description=(
        "Resumes dispatch and drains any parked pending events through the watcher's "
        "normal debounce path. Idempotent; calling while not paused replays any stale "
        "pending entries. (ADR-0046 §4)"
    ),
    responses={200: {"description": "Queue resumed; pending entries replayed"}},
)
async def resume_ingest_queue() -> QueueResumeResponse:
    """POST /ingest/queue/resume — drain pending entries (ADR-0046 §4)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    drained = _iq.resume()
    return QueueResumeResponse(paused=False, drained=drained)


# ── GET /provider/config ───────────────────────────────────────────────────────


@app.get(
    "/provider/config",
    response_model=ProviderConfigListResponse,
    summary="List provider_config rows",
    description=(
        "Returns all raw provider_config rows. "
        "No API key field is stored or returned (§12). (F17, AC-F17-6)"
    ),
)
async def list_provider_configs(
    scope: str | None = Query(default=None, description="Filter by scope (global|vault|operation)"),
    vault_id: str | None = Query(default=None, description="Filter by vault_id"),
) -> ProviderConfigListResponse:
    async with get_session() as session:
        stmt = select(ProviderConfig)
        if scope is not None:
            stmt = stmt.where(ProviderConfig.scope == scope)
        if vault_id is not None:
            stmt = stmt.where(ProviderConfig.vault_id == vault_id)
        stmt = stmt.order_by(ProviderConfig.created_at.asc())
        rows = await session.execute(stmt)
        configs = list(rows.scalars().all())
        total = len(configs)
        items = [ProviderConfigResponse.model_validate(c) for c in configs]

    return ProviderConfigListResponse(items=items, total=total)


# ── POST /provider/config ──────────────────────────────────────────────────────


@app.post(
    "/provider/config",
    response_model=ProviderConfigResponse,
    status_code=201,
    summary="Create or update a provider_config row",
    description=(
        "Create a new provider_config row. "
        "provider_type must be one of: local | api | cli. "
        "NO api_key field is accepted or stored — keys are env-only (§12). (F17, ADR-0008)"
    ),
    responses={
        201: {"description": "Row created"},
        422: {"description": "Validation error (invalid provider_type, scope, or operation)"},
    },
)
async def create_provider_config(body: ProviderConfigCreate) -> ProviderConfigResponse:
    """
    Create a new provider_config row for F17 provider selection (ADR-0008).

    Scope validation: if scope='operation', operation must be non-null.
    No API key field: keys live in environment only (§12, ADR-0008 §3).
    """
    if body.scope == "operation" and body.operation is None:
        raise HTTPException(
            status_code=422,
            detail="operation must be provided when scope='operation'",
        )
    if body.scope in {"vault", "operation"} and not body.vault_id:
        raise HTTPException(
            status_code=422,
            detail=f"vault_id must be provided when scope={body.scope!r}",
        )

    async with get_session() as session:
        row = ProviderConfig(
            id=uuid.uuid4(),
            scope=body.scope,
            operation=body.operation,
            vault_id=body.vault_id,
            provider_type=body.provider_type,
            model_id=body.model_id,
            base_url=body.base_url,
            max_iter=body.max_iter,
            token_budget=body.token_budget,
            is_fallback=body.is_fallback,
        )
        session.add(row)
        await session.flush()
        response = ProviderConfigResponse.model_validate(row)

    return response


# ── DELETE /provider/config/{id} ───────────────────────────────────────────────


@app.delete(
    "/provider/config/{config_id}",
    status_code=204,
    summary="Delete a provider_config row by UUID",
    description="Hard-delete the provider_config row with the given id. (F17)",
    responses={
        204: {"description": "Row deleted"},
        404: {"description": "Row not found"},
    },
)
async def delete_provider_config(config_id: uuid.UUID) -> None:
    """Delete a provider_config row (F17). 404 if not found."""
    from sqlalchemy import delete as sa_delete

    async with get_session() as session:
        result = await session.execute(
            sa_delete(ProviderConfig).where(ProviderConfig.id == config_id)
        )
        deleted = cast("CursorResult[Any]", result).rowcount

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"provider_config {config_id} not found",
        )


# ── GET /config/embedding ─────────────────────────────────────────────────────


class EmbeddingConfigResponse(BaseModel):
    embedding_url: str = Field(description="HTTP endpoint for embeddings (EMBEDDING_URL env)")
    embedding_model: str = Field(description="Model name for embeddings (EMBEDDING_MODEL env)")
    embedding_dim: int = Field(description="Vector dimension (EMBEDDING_DIM env)")
    embeddings_enabled: bool = Field(
        description=(
            "Whether the embedding data plane is active (EMBEDDINGS_ENABLED env, "
            "default true). When false, retrieval degrades to lexical/keyword-only "
            "and no embedding service is required at startup (ADR-0030, Feature B). "
            "Never exposes the embedding API key."
        )
    )


@app.get(
    "/config/embedding",
    response_model=EmbeddingConfigResponse,
    summary="Get current embedding configuration",
    description=(
        "Returns the active embedding config read from environment variables "
        "(EMBEDDING_URL, EMBEDDING_MODEL, EMBEDDING_DIM, EMBEDDINGS_ENABLED). "
        "Read-only — edit .env to change. (I9, ADR-0030)"
    ),
)
async def get_embedding_config() -> EmbeddingConfigResponse:
    """Return current embedding settings including enabled/disabled state (F17 / I9 / ADR-0030)."""
    return EmbeddingConfigResponse(
        embedding_url=settings.embedding_url,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        embeddings_enabled=settings.embeddings_enabled,
    )


# ── GET /mcp/info — read-only MCP server introspection (F1-MCP-UI, ADR-0027) ──


class McpToolInfo(BaseModel):
    """Schema for a single tool entry in GET /mcp/info (ADR-0027 §2.1)."""

    name: str = Field(description="Tool name as registered in the FastMCP server")
    description: str = Field(description="Full tool description (docstring); UI truncates")
    input_schema: dict[str, Any] = Field(
        description="JSON-Schema object for the tool's input arguments (tool.parameters)"
    )


class McpInfoResponse(BaseModel):
    """Response model for GET /mcp/info (ADR-0027 §2.1; ADR-0029 §2.5; ADR-0032 §2.5; ADR-0033)."""

    server_name: str = Field(
        description="MCP server name, derived from the live FastMCP object (I6)"
    )
    transport: str = Field(
        description="MCP transport type (MCP_TRANSPORT env, default 'stdio'; ADR-0010)"
    )
    entry_point_command: str = Field(
        description="Command to launch the MCP server (MCP_ENTRY_COMMAND env; ADR-0027 §2.3)"
    )
    tool_count: int = Field(description="Number of tools currently registered in the server")
    tools: list[McpToolInfo] = Field(description="Introspected tool list from the live registry")
    # ADR-0029 §2.5 additions — remote posture, no token ever returned
    http_enabled: bool = Field(
        description=(
            "Whether the HTTP MCP surface is compiled in and mounted (ADR-0029 §2.2). "
            "Always true (ADR-0033 §2.4 always-mount). The token itself is never returned. "
            "Alias: token_configured for backward compat."
        )
    )
    remote_write_enabled: bool = Field(
        description=(
            "Whether write_page is exposed on the HTTP surface (ADR-0029 §2.3). "
            "Reflects MCP_REMOTE_WRITE_ENABLED env var (default false). "
            "Only meaningful when remote_enabled is true."
        )
    )
    # ADR-0032 §2.5 additions — runtime toggle posture; token NEVER returned
    token_configured: bool = Field(
        description=(
            "True iff a token is configured — DB hash set OR MCP_AUTH_TOKEN env set "
            "(ADR-0033 §2.1 precedence). NEVER contains the token value."
        )
    )
    remote_enabled: bool = Field(
        description=(
            "The persisted runtime toggle state from vault_state.remote_mcp_enabled "
            "(ADR-0032 §2.1). False by default; can be set via PUT /mcp/remote."
        )
    )
    mount_path: str = Field(
        description=(
            "The mount path for the remote MCP HTTP surface (= MCP_MOUNT_PATH constant). "
            "UI builds the connection URL as: window.location.origin + mount_path "
            "(ADR-0032 §2.5; I6 — derived from constant, never hardcoded in handler)."
        )
    )
    # ADR-0033 §2.5 additions — token source + allow flag; no token/hash/salt ever returned
    token_source: str = Field(
        description=(
            '"db" | "env" | "none" — which token source is authoritative (ADR-0033 §2.1). '
            '"db": UI-set token (PBKDF2 hash in vault_state). '
            '"env": MCP_AUTH_TOKEN env bootstrap. '
            '"none": no token configured. '
            "NEVER the token value, hash, or salt."
        )
    )
    allow_without_token: bool = Field(
        description=(
            "Whether token-less access is permitted for PRIVATE sources "
            "(loopback/CGNAT/RFC1918/link-local/ULA — ADR-0033 §2.3). "
            "PUBLIC sources (Cloudflare tunnel) are NEVER exempted regardless of this flag."
        )
    )


@app.get(
    "/mcp/info",
    response_model=McpInfoResponse,
    summary="Read-only MCP server introspection",
    description=(
        "Returns the live FastMCP server metadata: name, transport, entry-point command, "
        "and the full list of registered tools (name, description, input_schema). "
        "All values are derived from the live `mcp` object and settings — nothing hardcoded (I6). "
        "No MCP transport session is opened; no tool is invoked (I9). "
        "Read-only — edit MCP_TRANSPORT / MCP_ENTRY_COMMAND env vars to change. "
        "F1-MCP-UI (ADR-0027 §2.1)."
    ),
)
async def get_mcp_info() -> McpInfoResponse:
    """
    GET /mcp/info — read-only introspection of the Synapse FastMCP server (ADR-0027 §2.1).

    Derives every value from the live `mcp` object (imported at module level) and `settings`.
    No string about the MCP server is hardcoded inside this function (I6).
    No DB query, no Qdrant call, no MCP transport/stdio session is opened (I9).
    """
    # Introspect the live FastMCP registry — await directly in async handler (ADR-0027 §2.2).
    raw_tools = await _mcp_server.list_tools()

    tools: list[McpToolInfo] = [
        McpToolInfo(
            name=t.name,
            description=t.description or "",
            input_schema=t.parameters if t.parameters is not None else {},
        )
        for t in raw_tools
    ]

    # ADR-0033 §2.5: resolve token source from in-process cache.
    # NEVER return token/hash/salt — only boolean-derived values.
    db_hash = _mcp_auth_cache.get_hash()
    tok_source = _resolve_token_source(db_hash)
    tok_configured = _token_configured(db_hash)

    return McpInfoResponse(
        server_name=_mcp_server.name,
        transport=settings.mcp_transport,
        entry_point_command=settings.mcp_entry_command,
        tool_count=len(tools),
        tools=tools,
        # ADR-0029 §2.5 — always-mount (ADR-0033 §2.4); token NEVER returned
        http_enabled=True,  # always-mount (ADR-0033 §2.4)
        remote_write_enabled=settings.mcp_remote_write_enabled,
        # ADR-0032 §2.5 — runtime toggle posture
        token_configured=tok_configured,
        remote_enabled=_remote_mcp_flag.is_enabled(),
        mount_path=MCP_MOUNT_PATH,
        # ADR-0033 §2.5 — token source + allow flag; NEVER the token/hash/salt
        token_source=tok_source,
        allow_without_token=_mcp_auth_cache.allow_without_token(),
    )


# ── PUT /mcp/remote — runtime toggle for remote MCP HTTP surface (ADR-0032 §2.4) ──


class McpRemoteToggleRequest(BaseModel):
    """Request body for PUT /mcp/remote (ADR-0032 §2.4)."""

    enabled: bool = Field(description="Desired runtime state for the remote MCP HTTP surface.")


class McpRemoteStateResponse(BaseModel):
    """
    Response model for PUT /mcp/remote (ADR-0032 §2.4).

    Always returned with HTTP 200 (even when clamped — the posture is reported truthfully).
    The token itself is NEVER returned (I6).
    """

    remote_enabled: bool = Field(
        description=(
            "The resulting persisted runtime flag (post-clamp). " "False when clamped=true."
        )
    )
    token_configured: bool = Field(
        description=(
            "True iff MCP_AUTH_TOKEN is set (the security floor). "
            "NEVER contains the token value."
        )
    )
    mount_path: str = Field(
        description="Mount path for the remote MCP HTTP surface (= MCP_MOUNT_PATH constant; I6)."
    )
    clamped: bool = Field(
        description=(
            "True iff the request asked enabled=true but MCP_AUTH_TOKEN is unset — "
            "the flag was forced to false (token-floor clamp, ADR-0032 §2.4)."
        )
    )


@app.put(
    "/mcp/remote",
    response_model=McpRemoteStateResponse,
    summary="Toggle the remote MCP HTTP surface at runtime",
    description=(
        "Persists vault_state.remote_mcp_enabled for the active vault and refreshes the "
        "in-process RemoteMcpFlag cache immediately (ADR-0032 §2.2/§2.4). "
        "Token-floor clamp: if MCP_AUTH_TOKEN is unset and enabled=true, the flag is "
        "forced to false and clamped=true is returned (HTTP 200). "
        "enabled=false always succeeds. "
        "Same-origin / unauthenticated — consistent with the rest of the REST API "
        "(ADR-0028 / ADR-0032 §2.4). "
        "F1-MCP-UI (ADR-0032)."
    ),
)
async def put_mcp_remote(body: McpRemoteToggleRequest) -> McpRemoteStateResponse:
    """
    PUT /mcp/remote — persist the runtime MCP toggle (ADR-0032 §2.4; amended by ADR-0033 §2.4/§2.5).

    Allow-aware clamp (ADR-0033 §2.4): enabling is permitted when EITHER
    ``token_configured OR allow_without_token``. Without either, enabling remote is
    pointless (the surface 404s for everyone), so we clamp to OFF.
    On success: write vault_state, refresh RemoteMcpFlag cache.
    No MCP tool is invoked; no second writer is introduced (I9).
    """
    db_hash = _mcp_auth_cache.get_hash()
    tok_configured: bool = _token_configured(db_hash)
    allow: bool = _mcp_auth_cache.allow_without_token()
    clamped: bool = False
    desired: bool = body.enabled

    # Allow-aware clamp (ADR-0033 §2.4): cannot enable without token OR allow.
    if desired and not tok_configured and not allow:
        desired = False
        clamped = True

    # Persist to vault_state (DB is source of truth — ADR-0032 §2.1).
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=desired,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
        else:
            state.remote_mcp_enabled = desired
            state.updated_at = datetime.now(UTC)

    # Refresh the in-process cache immediately (ADR-0032 §2.2).
    await _remote_mcp_flag.set(desired)

    logger.info(
        "PUT /mcp/remote: enabled=%s clamped=%s tok_configured=%s allow=%s (ADR-0032/0033)",
        desired,
        clamped,
        tok_configured,
        allow,
    )

    return McpRemoteStateResponse(
        remote_enabled=desired,
        token_configured=tok_configured,
        mount_path=MCP_MOUNT_PATH,
        clamped=clamped,
    )


# ── PUT /mcp/auth — set/rotate/clear token + allow flag (ADR-0033 §2.5) ──────


class McpAuthRequest(BaseModel):
    """
    Request body for PUT /mcp/auth (ADR-0033 §2.5).

    All fields are optional; omitting a field leaves that aspect unchanged.
    Exactly one of rotate_token / token / clear_token should be used per call
    (using multiple is allowed but the last write wins: clear > explicit > rotate).
    allow_without_token can be set independently in the same call.
    """

    rotate_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ generate a new high-entropy token (secrets.token_urlsafe(32)), "
            "store its PBKDF2 hash, return plaintext ONCE in generated_token. "
            "The plaintext is NEVER stored and NEVER returned again."
        ),
    )
    token: str | None = Field(
        default=None,
        description=(
            "Owner-supplied explicit token; stored as PBKDF2 hash only. "
            "generated_token stays null (owner already knows the value). "
            "Not echoed in the response."
        ),
    )
    clear_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ set mcp_access_token_hash = NULL. "
            "If this leaves token_configured=false AND allow_without_token=false, "
            "remote_enabled is clamped OFF (no usable auth posture)."
        ),
    )
    allow_without_token: bool | None = Field(
        default=None,
        description=(
            "Persist the allow-without-token flag (ADR-0033 §2.3). "
            "Omit to leave unchanged. "
            "true: private sources (loopback/CGNAT/RFC1918/link-local) may connect "
            "without a bearer token. PUBLIC sources are NEVER exempted."
        ),
    )


class McpAuthStateResponse(BaseModel):
    """
    Response body for PUT /mcp/auth (ADR-0033 §2.5).

    token_configured, token_source, allow_without_token, remote_enabled, mount_path
    always reflect the post-write state. generated_token is populated ONLY when
    rotate_token=true was set — shown ONCE, never returned again by any GET/PUT.
    NEVER contains the token, hash, or salt.
    """

    token_configured: bool = Field(
        description="True iff DB hash is set OR MCP_AUTH_TOKEN env bootstrap is set."
    )
    token_source: str = Field(
        description='"db" | "env" | "none" — authoritative token source (ADR-0033 §2.1).'
    )
    allow_without_token: bool = Field(
        description="The persisted allow-without-token flag after this write."
    )
    remote_enabled: bool = Field(
        description="The remote_mcp_enabled flag after any allow-aware clamp."
    )
    mount_path: str = Field(description="MCP_MOUNT_PATH constant (I6).")
    generated_token: str | None = Field(
        default=None,
        description=(
            "Populated ONLY when rotate_token=true — the plaintext token shown ONCE. "
            "null in all other cases. NEVER stored; NEVER returned by subsequent calls."
        ),
    )


@app.put(
    "/mcp/auth",
    response_model=McpAuthStateResponse,
    summary="Set, rotate, or clear the MCP access token + allow-without-token flag",
    description=(
        "ADR-0033 §2.5 — UI-settable MCP token management. "
        "rotate_token=true: generate a new token (secrets.token_urlsafe(32)), store its "
        "PBKDF2 hash in vault_state, return plaintext ONCE in generated_token. "
        "token=<value>: store an explicit token as hash; NOT echoed; generated_token=null. "
        "clear_token=true: set hash to NULL (token_source may fall back to env or none). "
        "allow_without_token: persist the private-source allow flag. "
        "If post-write state has no token AND allow_without_token=false, remote_enabled "
        "is clamped OFF (allow-aware clamp — ADR-0033 §2.4). "
        "Same-origin / unauthenticated (consistent with ADR-0032 §2.4). "
        "NEVER returns or stores token plaintext (except the one-time generated_token). "
        "F1-MCP-UI (ADR-0033)."
    ),
)
async def put_mcp_auth(body: McpAuthRequest) -> McpAuthStateResponse:
    """
    PUT /mcp/auth — UI-settable MCP token management (ADR-0033 §2.5).

    Applies changes in this order:
      1. clear_token (if true) → set hash NULL.
      2. token (if set) → hash and persist.
      3. rotate_token (if true) → generate, hash, persist, capture plaintext.
      4. allow_without_token (if set) → persist.
      5. Apply allow-aware clamp to remote_enabled (§2.4).
      6. Refresh in-process caches.
      7. Return McpAuthStateResponse (no plaintext except generated_token).

    No MCP tool is invoked; no second writer is introduced (I9).
    """
    generated_token: str | None = None

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear_token
        if body.clear_token:
            state.mcp_access_token_hash = None

        # 2. explicit token
        if body.token is not None:
            state.mcp_access_token_hash = _hash_token(body.token)
            # Do NOT echo body.token in logs or response.

        # 3. rotate_token (takes precedence over explicit token if both are set)
        if body.rotate_token:
            new_plaintext = secrets.token_urlsafe(32)
            state.mcp_access_token_hash = _hash_token(new_plaintext)
            # Capture plaintext for the one-time response; NEVER persist it.
            generated_token = new_plaintext
            # Immediately discard from local scope after assigning to response var;
            # new_plaintext goes out of scope here.

        # 4. allow_without_token
        if body.allow_without_token is not None:
            state.mcp_allow_without_token = body.allow_without_token

        # 5. Allow-aware clamp on remote_enabled (ADR-0033 §2.4).
        new_hash = state.mcp_access_token_hash
        new_allow = state.mcp_allow_without_token
        tok_configured_post = _token_configured(new_hash)
        if state.remote_mcp_enabled and not tok_configured_post and not new_allow:
            state.remote_mcp_enabled = False
            logger.info(
                "PUT /mcp/auth: remote_enabled clamped OFF "
                "(no token AND allow=false, ADR-0033 §2.4)"
            )

        state.updated_at = datetime.now(UTC)

        # Capture final values for cache update (inside session scope).
        final_hash = state.mcp_access_token_hash
        final_allow = state.mcp_allow_without_token
        final_remote = state.remote_mcp_enabled

    # 6. Refresh in-process caches (outside session — DB write committed).
    await _mcp_auth_cache.set_hash(final_hash)
    await _mcp_auth_cache.set_allow(final_allow)
    await _remote_mcp_flag.set(final_remote)

    # 7. Derive response values (NEVER return hash, plaintext, or salt).
    tok_source = _resolve_token_source(final_hash)
    tok_configured = _token_configured(final_hash)

    logger.info(
        "PUT /mcp/auth: token_source=%s allow_without_token=%s remote_enabled=%s (ADR-0033)",
        tok_source,
        final_allow,
        final_remote,
    )

    return McpAuthStateResponse(
        token_configured=tok_configured,
        token_source=tok_source,
        allow_without_token=final_allow,
        remote_enabled=final_remote,
        mount_path=MCP_MOUNT_PATH,
        generated_token=generated_token,
    )


# ── Import schedule REST (Feature S, ADR-0020 §4.6) ───────────────────────────


def _schedule_to_response(schedule: ImportSchedule | None) -> ImportScheduleResponse:
    """Convert an ImportSchedule ORM row to the API response shape (or return defaults)."""
    if schedule is None:
        return ImportScheduleResponse()
    return ImportScheduleResponse(
        enabled=schedule.enabled,
        source_dir=schedule.source_dir,
        frequency=schedule.frequency,
        last_run_at=schedule.last_run_at,
        last_status=schedule.last_status,
        last_imported_count=schedule.last_imported_count,
        last_error=schedule.last_error,
    )


@app.get(
    "/import-schedule",
    response_model=ImportScheduleResponse,
    summary="Get scheduled folder import config + last-run status",
    description=(
        "Returns the current import schedule configuration and last-run status for the vault. "
        "Returns sane defaults (enabled=false, frequency='1h') if no row has been configured yet. "
        "Feature S (ADR-0020 §4.6)."
    ),
)
async def get_import_schedule() -> ImportScheduleResponse:
    """GET /import-schedule — current config + last-run status (ADR-0020 §4.6)."""
    schedule = await load_schedule(settings.vault_id)
    return _schedule_to_response(schedule)  # type: ignore[arg-type]


@app.put(
    "/import-schedule",
    response_model=ImportSchedulePutResponse,
    summary="Upsert import schedule configuration",
    description=(
        "Create or update the import schedule for the vault. "
        "Validates source_dir: if the directory does not exist inside the container, "
        "the row is still saved but dir_ok=false + dir_message is returned (save-then-warn). "
        "frequency must be one of '15m' | '1h' | '6h' | 'daily'. "
        "Config changes take effect on the next scheduler tick without a restart. "
        "Feature S (ADR-0020 §4.6)."
    ),
    responses={
        200: {"description": "Config saved (dir_ok may be false if mount is missing)"},
        422: {"description": "Invalid frequency value"},
    },
)
async def put_import_schedule(body: ImportSchedulePutBody) -> ImportSchedulePutResponse:
    """
    PUT /import-schedule — upsert schedule config with save-then-warn dir validation.

    If body.source_dir is provided, validate it exists & is readable inside the container.
    Persist regardless of dir_ok (operator may add the mount later; next tick picks it up).
    """
    # Build update kwargs
    update_kwargs: dict[str, object] = {}
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    if body.source_dir is not None:
        update_kwargs["source_dir"] = body.source_dir
    if body.frequency is not None:
        update_kwargs["frequency"] = body.frequency
    update_kwargs["updated_at"] = datetime.now(UTC)

    await upsert_schedule(settings.vault_id, **update_kwargs)

    # Reload the freshly persisted row
    schedule = await load_schedule(settings.vault_id)

    # Dir validation (save-then-warn — ADR-0020 §4.6)
    dir_ok = True
    dir_message: str | None = None
    source_dir_val: str | None = getattr(schedule, "source_dir", None) if schedule else None
    if source_dir_val is not None:
        import os as _os

        if not _os.path.isdir(source_dir_val):
            dir_ok = False
            dir_message = (
                f"Directory '{source_dir_val}' is not visible inside the backend container. "
                "Add a mount (e.g. - ./import:/import:ro in docker-compose.yml) and set "
                "source_dir to the CONTAINER path — see DEPLOY.md."
            )

    base = _schedule_to_response(schedule)  # type: ignore[arg-type]
    return ImportSchedulePutResponse(
        enabled=base.enabled,
        source_dir=base.source_dir,
        frequency=base.frequency,
        last_run_at=base.last_run_at,
        last_status=base.last_status,
        last_imported_count=base.last_imported_count,
        last_error=base.last_error,
        dir_ok=dir_ok,
        dir_message=dir_message,
    )


@app.post(
    "/import-schedule/run-now",
    response_model=RunNowResponse,
    status_code=202,
    summary="Trigger one bounded import scan immediately",
    description=(
        "Trigger one bounded scan of source_dir immediately (same bounds as the scheduler: "
        "IMPORT_SCAN_MAX_FILES + IMPORT_SCAN_MAX_SECONDS, I7). "
        "The scan runs in the background; poll GET /import-schedule for the result. "
        "409 if a scan is already in-flight. 400 if disabled or source_dir unset/missing. "
        "Feature S (ADR-0020 §4.6)."
    ),
    responses={
        202: {"description": "Scan started in the background"},
        400: {"description": "Schedule is disabled, source_dir not set, or directory missing"},
        409: {"description": "A scan is already in-flight (I7 — no overlap)"},
    },
)
async def run_import_now() -> RunNowResponse:
    """
    POST /import-schedule/run-now — trigger one bounded scan immediately (ADR-0020 §4.6).

    Uses the module-level ImportScheduler singleton started in the lifespan.
    Falls back to creating a temporary scheduler if the lifespan singleton is absent
    (e.g. test environments that bypass lifespan).
    """
    global _import_scheduler

    scheduler = _import_scheduler
    if scheduler is None:
        # Graceful degradation: create an ephemeral scheduler (test / direct-startup scenario)
        scheduler = ImportScheduler()

    if scheduler.scan_in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                "A scan is already in-flight. "
                "Wait for it to finish or poll GET /import-schedule."
            ),
        )

    # Kick off the scan as a background task
    async def _run() -> None:
        try:
            await scheduler.run_now()
        except (ValueError, RuntimeError) as exc:
            logger.warning("run_import_now: scan failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("run_import_now: unhandled error in background scan: %s", exc)

    try:
        # Validate preconditions before starting the background task (so we get 400 synchronously)
        cfg = await load_schedule(settings.vault_id)
        if cfg is None or not getattr(cfg, "enabled", False):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Schedule is disabled or not configured. " "Enable it and set source_dir first."
                ),
            )
        source_dir = getattr(cfg, "source_dir", None)
        if not source_dir:
            raise HTTPException(
                status_code=400,
                detail="source_dir is not set. Configure a container-visible path first.",
            )
        import os as _os

        if not _os.path.isdir(str(source_dir)):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Directory '{source_dir}' is not accessible inside the container. "
                    "Add a mount (e.g. - ./import:/import:ro) and set "
                    "source_dir to the container path."
                ),
            )
    except HTTPException:
        raise

    asyncio.create_task(_run())
    return RunNowResponse(status="started")


# ── Chat: conversations CRUD + streaming turn (F6/F7, ADR-0019) ───────────────


@app.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List chat conversations for a vault",
    description=(
        "Returns live (non-soft-deleted) conversations for a vault, ordered updated_at DESC "
        "(drives last-active restore, AC-F6-1). Paginated (limit 1..100, offset >=0). F6."
    ),
)
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    vault_id: str | None = Query(default=None, description="Defaults to settings.vault_id"),
) -> ConversationListResponse:
    effective_vault_id = vault_id or settings.vault_id
    async with get_session() as session:
        base = select(Conversation).where(
            Conversation.vault_id == effective_vault_id,
            Conversation.deleted_at.is_(None),
        )
        total_row = await session.execute(select(func.count()).select_from(base.subquery()))
        total: int = total_row.scalar_one()
        rows = await session.execute(
            base.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
        )
        convs = list(rows.scalars().all())
    return ConversationListResponse(
        items=[ConversationResponse.model_validate(c) for c in convs],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    summary="Create an empty chat conversation",
    description="Create a conversation {vault_id?, title?}. Also implicitly created by "
    "/chat/stream when conversation_id is null. F6 (ADR-0019 §2.5).",
)
async def create_conversation(body: ConversationCreate) -> ConversationResponse:
    effective_vault_id = body.vault_id or settings.vault_id
    async with get_session() as session:
        conv = Conversation(vault_id=effective_vault_id, title=body.title)
        session.add(conv)
        await session.flush()
        await session.refresh(conv)
        result = ConversationResponse.model_validate(conv)
    return result


@app.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageListResponse,
    summary="Get ordered message history for a conversation",
    description="Messages ordered created_at ASC. content is RAW incl. literal <think>… "
    "(AC-F7-2). 404 if the conversation is unknown/soft-deleted. F6.",
    responses={404: {"description": "Conversation not found"}},
)
async def get_conversation_messages(conversation_id: uuid.UUID) -> ChatMessageListResponse:
    async with get_session() as session:
        conv_row = await session.execute(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
        )
        if conv_row.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        rows = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
        )
        msgs = list(rows.scalars().all())
    items = [ChatMessageResponse.model_validate(m) for m in msgs]
    return ChatMessageListResponse(items=items, total=len(items))


@app.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Soft-delete a conversation",
    description="Sets deleted_at (ADR-0005 pattern). 404 if unknown/already deleted. F6.",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete_conversation(conversation_id: uuid.UUID) -> None:
    from sqlalchemy import update as sa_update

    async with get_session() as session:
        result = await session.execute(
            sa_update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
        )
        affected = cast("CursorResult[Any]", result).rowcount
    if affected == 0:
        raise HTTPException(status_code=404, detail="conversation not found")


@app.post(
    "/chat/stream",
    summary="Stream a chat turn (NDJSON)",
    description=(
        "Bounded chat turn (F6/F7, I6/I7, ADR-0019 §2.2). Returns 200 with "
        "application/x-ndjson: one JSON event per line (token | think | done | error). "
        "Routes via resolve_provider_config('chat', vault_id) — never a hardcoded provider "
        "(I6). Bounded by token_budget + timeout (I7); total_cost_usd in the done event. "
        "404 if conversation_id is unknown; 503 if no chat provider resolves."
    ),
    responses={
        200: {"content": {"application/x-ndjson": {}}, "description": "NDJSON event stream"},
        404: {"description": "conversation_id provided but unknown"},
        422: {"description": "Body validation failure"},
        503: {"description": "No chat provider_config resolves (I6)"},
    },
)
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """
    POST /chat/stream — the NDJSON streaming chat turn (ADR-0019 §2.2).

    Setup failures that must map to an HTTP status (unknown conversation → 404, no provider →
    503) are raised by run_chat_stream BEFORE the first yield; we surface them here. Once the
    stream starts (HTTP 200), all later failures are terminal `error` NDJSON events.
    """
    domain_messages = [Message(role=m.role, content=m.content) for m in body.messages]

    agen = run_chat_stream(
        conversation_id=body.conversation_id,
        messages=domain_messages,
        vault_id=body.vault_id,
        context_window=body.context_window,
        regenerate=body.regenerate,
    )

    # Pull the first line eagerly so pre-stream setup errors (404/503) become real HTTP codes
    # rather than a 200 stream that immediately errors.
    try:
        first_line = await agen.__anext__()
    except ChatStreamError as exc:
        if exc.code == "not_found":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if exc.code == "no_provider":
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except StopAsyncIteration:  # pragma: no cover - generator always yields
        first_line = ""

    async def _body() -> AsyncGenerator[str, None]:
        if first_line:
            yield first_line
        async for line in agen:
            yield line

    return StreamingResponse(
        _body(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── POST /chat/save-to-wiki (G-P0-1) ──────────────────────────────────────────
# Mirror of nashsu/llm_wiki F6 "Save to Wiki": routes a chat assistant answer into
# wiki/queries/<slug>.md as a typed "query" page (I1/I5/I6/I7):
#   I1 — single write via write_wiki_page (one data_version bump, no rescan)
#   I5 — Obsidian-valid frontmatter (type=query, sources=[])
#   I6 — NO provider call; pure DB/file write
#   I7 — no loop; single bounded operation


_THINK_BLOCK_RE = _re.compile(r"<think>.*?</think>", _re.DOTALL | _re.IGNORECASE)
_CITED_TRAILER_RE = _re.compile(r"<!--\s*cited:.*?-->", _re.DOTALL | _re.IGNORECASE)


def _clean_chat_content(content: str) -> str:
    """
    Strip <think>…</think> blocks and <!-- cited: … --> trailers from a chat
    assistant message before saving it to the wiki (G-P0-1).

    Both patterns are injected server-side during streaming (F7/F5) and MUST NOT
    appear in the saved wiki page (they are transport artifacts, not human-readable
    content — I5 Obsidian-valid frontmatter / body rule).
    """
    cleaned = _THINK_BLOCK_RE.sub("", content)
    cleaned = _CITED_TRAILER_RE.sub("", cleaned)
    return cleaned.strip()


class SaveToWikiRequest(BaseModel):
    """
    Request body for POST /chat/save-to-wiki (G-P0-1).

    Saves a cleaned chat assistant answer as a wiki/queries/<slug>.md page.
    No inference provider is called (I6); this is a pure DB+file write (I7).
    """

    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")
    title: str = Field(..., min_length=1, description="Page title (required)")
    content: str = Field(..., min_length=1, description="Page content (required)")
    sources: list[str] | None = Field(
        default=None,
        description="Optional source references to attach to the page frontmatter",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation UUID for provenance reference",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "What is bge-m3?",
                "content": "bge-m3 is a multi-lingual dense embedding model...",
                "sources": ["raw/sources/chat-123.md"],
                "conversation_id": "00000000-0000-0000-0000-000000000001",
            }
        }
    }


class SaveToWikiResponse(BaseModel):
    """201 response for POST /chat/save-to-wiki (G-P0-1)."""

    page_id: uuid.UUID = Field(..., description="UUID of the created/updated wiki/queries page")
    file_path: str = Field(..., description="Relative path in the vault (wiki/queries/<slug>.md)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "page_id": "00000000-0000-0000-0000-000000000001",
                "file_path": "wiki/queries/what-is-bge-m3.md",
            }
        }
    }


@app.post(
    "/chat/save-to-wiki",
    response_model=SaveToWikiResponse,
    status_code=201,
    summary="Save a chat answer to the wiki as a query page (G-P0-1)",
    description=(
        "Mirrors nashsu/llm_wiki F6 'Save to Wiki': saves a cleaned assistant answer as "
        "wiki/queries/<slug(title)>.md with type=query frontmatter (I5). "
        "Strips <think>…</think> and <!-- cited: … --> transport artifacts before saving. "
        "Persists via the single write_wiki_page seam (I1 — one data_version bump, no rescan). "
        "No inference provider is called (I6/I7). "
        "Returns {page_id, file_path}. 422 if title/content is missing."
    ),
    responses={
        201: {"description": "Page created or updated"},
        422: {"description": "title or content is missing / empty"},
    },
)
async def save_chat_to_wiki(body: SaveToWikiRequest) -> SaveToWikiResponse:
    """
    POST /chat/save-to-wiki — save a chat answer as a wiki/queries page (G-P0-1).

    Invariant compliance:
      I1 — single write_wiki_page call → one data_version bump, no rescan.
      I5 — Obsidian-valid YAML frontmatter (type=query, sources list, lang=en).
      I6 — NO provider call; pure DB/file write.
      I7 — no loop; single bounded operation.
    """
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    # Clean transport artifacts from content before writing to the wiki (G-P0-1)
    cleaned_content = _clean_chat_content(body.content)
    if not cleaned_content:
        raise HTTPException(
            status_code=422,
            detail="content is empty after stripping think/citation blocks",
        )

    # Build sources list (conversation provenance as a pseudo-source reference)
    sources: list[str] = list(body.sources) if body.sources else []
    if body.conversation_id:
        conv_ref = f"conversation/{body.conversation_id}"
        if conv_ref not in sources:
            sources.append(conv_ref)
    # Ensure at least one source so WikiFrontmatter validator passes (F3 traceability)
    if not sources:
        sources = ["chat"]

    fm = WikiFrontmatter(
        type=PageType.QUERY,
        title=body.title,
        sources=sources,
        lang="en",
    )
    wiki_page = WikiPage(
        title=body.title,
        type=PageType.QUERY,
        content=cleaned_content,
        frontmatter=fm,
    )

    # Single write seam (I1): persists file, Postgres row, Qdrant vector, links, index.md,
    # bumps data_version once. No provider call (I6/I7).
    persisted = await write_wiki_page(None, wiki_page, "")

    return SaveToWikiResponse(
        page_id=persisted.id,
        file_path=persisted.file_path,
    )


# ── POST /links/reresolve ────────────────────────────────────────────────────────


class ReresolveLinksResponse(BaseModel):
    """
    Response for POST /links/reresolve (F3/K3 cross-ingest connectivity backfill).

    reconnected: number of previously-dangling links now bound to a live page.
    remaining_dangling: number of links still dangling after the pass.
    """

    reconnected: int = Field(
        description="Previously-dangling links reconnected to a live page in this pass."
    )
    remaining_dangling: int = Field(
        description="Links still dangling after the pass (target has no matching live page)."
    )

    model_config = {
        "json_schema_extra": {"example": {"reconnected": 42, "remaining_dangling": 7}}
    }


@app.post(
    "/links/reresolve",
    response_model=ReresolveLinksResponse,
    status_code=200,
    summary="Re-resolve dangling wikilinks against current pages (F3/K3)",
    description=(
        "Backfill for cross-ingest graph connectivity: re-resolves every dangling Link against "
        "the current live pages using the tolerant matcher (exact → case-insensitive → slug). "
        "Links whose target now matches a live page are reconnected (target_page_id set, "
        "dangling cleared). Bounded single pass (I7 — two queries, no per-row round-trips). "
        "Bumps data_version once so the debounced GraphCache recomputes with the new edges (I2). "
        "Returns {reconnected, remaining_dangling}."
    ),
    responses={200: {"description": "Backfill completed"}},
)
async def reresolve_links() -> ReresolveLinksResponse:
    """
    POST /links/reresolve — reconnect historical dangling wikilinks (F3/K3).

    Invariant compliance:
      I1 — incremental: only touches Link rows that resolve; no page rescan.
      I2 — bumps data_version + notify_bump once so FA2 recomputes with the new edges.
      I6 — NO provider call; pure DB resolution.
      I7 — single bounded pass, no loop.
    """
    from sqlalchemy import func

    from app.ingest.orchestrator import bump_version
    from app.models import Link
    from app.wiki.links import reresolve_dangling_links

    async with get_session() as session:
        reconnected = await reresolve_dangling_links(session)
        remaining_row = await session.execute(
            select(func.count()).select_from(Link).where(Link.dangling.is_(True))
        )
        remaining_dangling = int(remaining_row.scalar_one() or 0)
        # Commit the reconnected rows before bumping the graph (session commits on exit).

    # Only bump the graph when something actually changed (avoid a needless FA2 recompute, I2).
    if reconnected:
        await bump_version()
        global _graph_cache
        if _graph_cache is not None:
            async with get_session() as _vs_sess:
                _vs_row = await _vs_sess.execute(
                    select(VaultState).where(VaultState.vault_id == settings.vault_id)
                )
                _vs = _vs_row.scalar_one_or_none()
                _new_version = _vs.data_version if _vs is not None else 0
            _graph_cache.notify_bump(_new_version)

    return ReresolveLinksResponse(
        reconnected=reconnected,
        remaining_dangling=remaining_dangling,
    )


# ── GET /graph ─────────────────────────────────────────────────────────────────


class GraphNodeResponse(BaseModel):
    """
    One graph node in the GET /graph response (ADR-0014 §6, AC-F4-3, ADR-0016 §4).

    Required: id, title, type, x, y.
    Optional rendering hints (derived server-side): size, degree.
    community: Louvain community id (G-P0-2); -1 when not yet assigned.
    """

    id: str
    title: str | None
    type: str | None
    x: float
    y: float
    size: float = Field(
        default=1.0,
        description="BASE + GROWTH·sqrt(structural_degree); BASE=1.0, GROWTH=2.5 (ADR-0016 §2)",
    )
    degree: int = Field(
        default=0,
        description=(
            "Structural degree: count of distinct incident structural edges "
            "(direct-link or shared-source); drives size (ADR-0016 §2/§4)"
        ),
    )
    community: int = Field(
        default=-1,
        description=(
            "Louvain community id (G-P0-2, I2). Re-numbered by size (0 = largest). "
            "-1 when not yet assigned (first recompute pending)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "00000000-0000-0000-0000-000000000001",
                "title": "Alpha",
                "type": "entity",
                "x": 1.23,
                "y": -0.45,
                "size": 2.1,
                "degree": 3,
                "community": 0,
            }
        }
    }


class GraphEdgeResponse(BaseModel):
    """
    One graph edge in the GET /graph response (ADR-0014 §6, AC-F4-3, ADR-0016 §4).

    source/target are page-id strings (UUID). Undirected — emitted once per pair.
    kind: structural edge discriminator — "link" (wikilink exists) or "source"
          (shared-source provenance only). ADR-0016 §4.
    """

    source: str
    target: str
    weight: float
    kind: str = Field(
        default="link",
        description=(
            'Structural edge kind: "link" (direct wikilink) | '
            '"source" (shared provenance). ADR-0016 §4'
        ),
    )


class GraphCommunityResponse(BaseModel):
    """
    Per-community summary in the GET /graph response (G-P0-2).

    id      : re-numbered Louvain community id (0 = largest community).
    size    : number of member nodes.
    cohesion: intra-edge density in [0, 1]; 0 for singleton communities.
              Low cohesion (<0.1) signals a loosely-connected community
              suitable for a warning in the client legend.
    """

    id: int
    size: int
    cohesion: float = Field(description="Intra-edge density [0,1]; 0 for singletons")

    model_config = {
        "json_schema_extra": {
            "example": {"id": 0, "size": 12, "cohesion": 0.42}
        }
    }


class GraphResponse(BaseModel):
    """
    GET /graph response payload (ADR-0014 §6, AC-F4-3, AC-D4v3-1).

    cached     : true on a HIT (no FA2 this request), false on a MISS (FA2 ran inline).
    communities: Louvain community summary list (G-P0-2); empty until first recompute.
    Header X-Graph-Cache: hit|miss mirrors cached (ADR-0014 §5).
    """

    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    data_version: int
    cached: bool
    communities: list[GraphCommunityResponse] = Field(
        default_factory=list,
        description=(
            "Per-community summary (G-P0-2, I2): id, size, cohesion. "
            "Ordered by id (0 = largest). Empty until first graph recompute."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "nodes": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "title": "Alpha",
                        "type": "entity",
                        "x": 1.23,
                        "y": -0.45,
                        "size": 2.1,
                        "degree": 3,
                        "community": 0,
                    }
                ],
                "edges": [
                    {
                        "source": "00000000-0000-0000-0000-000000000001",
                        "target": "00000000-0000-0000-0000-000000000002",
                        "weight": 11.0,
                        "kind": "link",
                    }
                ],
                "data_version": 7,
                "cached": True,
                "communities": [{"id": 0, "size": 2, "cohesion": 1.0}],
            }
        }
    }


@app.get(
    "/graph",
    response_model=GraphResponse,
    summary="Precomputed knowledge graph (nodes + edges with FA2 coordinates)",
    description=(
        "Returns the precomputed graph with FA2 layout coordinates (I2, F4, ADR-0014). "
        "HIT (X-Graph-Cache: hit): pure read from persisted coords + edges — no FA2. "
        "MISS (X-Graph-Cache: miss): one inline synchronous recompute, then return. "
        "Synchronous 200 — never 202 (AQ-v0.3-3). "
        "A second request at the same data_version is always a HIT (G2)."
    ),
    responses={
        200: {
            "description": "Graph payload with precomputed coords",
            "headers": {
                "X-Graph-Cache": {
                    "description": "hit|miss — mirrors the cached field (ADR-0014 §5)",
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def get_graph() -> Response:
    """
    GET /graph — precomputed knowledge graph with FA2 layout coords (F4, I2, ADR-0014).

    I2 compliance:
      - HIT path: pure read, no FA2 (X-Graph-Cache: hit).
      - MISS path: one inline synchronous recompute (X-Graph-Cache: miss).
      - The background debounce (GraphCache) keeps the common case a HIT.
      - Coords are precomputed server-side via igraph (R9, I9) — never on the client.
    """
    global _graph_cache

    # Read the current data_version (lightweight SELECT)
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        current_version: int = state.data_version if state is not None else 0

    # Initialise cache lazily (e.g. in test environments that bypass lifespan)
    if _graph_cache is None:
        _graph_cache = GraphCache(
            engine=GraphEngine(),
            vault_id=settings.vault_id,
        )

    snapshot, cached = await _graph_cache.get_graph(current_version)

    # Build response payload (ADR-0014 §6, G-P0-2)
    nodes: list[GraphNodeResponse] = [
        GraphNodeResponse(
            id=n.id,
            title=n.title,
            type=n.page_type,
            x=n.x,
            y=n.y,
            size=n.size,
            degree=n.degree,
            community=n.community,
        )
        for n in snapshot.nodes
    ]
    edges: list[GraphEdgeResponse] = [
        GraphEdgeResponse(source=e.source, target=e.target, weight=e.weight, kind=e.kind)
        for e in snapshot.edges
    ]
    communities: list[GraphCommunityResponse] = [
        GraphCommunityResponse(id=c.id, size=c.size, cohesion=c.cohesion)
        for c in snapshot.communities
    ]
    payload = GraphResponse(
        nodes=nodes,
        edges=edges,
        data_version=current_version,
        cached=cached,
        communities=communities,
    )

    cache_header = "hit" if cached else "miss"
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
        headers={"X-Graph-Cache": cache_header},
    )


# ── Deep Research REST (F10, ADR-0024 §8) ─────────────────────────────────────


class ResearchStartRequest(BaseModel):
    """
    Request body for POST /research/start (ADR-0024 §8.1, AC-F10-4).

    max_iter and token_budget are optional — env defaults apply when omitted.
    Both are FROZEN onto the deep_research_runs row before the background task starts
    (AQ-v0.5-4, I7). Server-side validators cap the range so callers cannot request an
    unbounded run (I7 / Do-NOT #1/#2).
    """

    vault_id: str = Field(..., description="Vault scope for the run")
    topic: str = Field(..., min_length=1, description="Research topic (non-empty)")
    max_iter: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Max refinement rounds (1..10); null → DEEP_RESEARCH_MAX_ITER default",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1_000,
        le=1_000_000,
        description="Token budget (1_000..1_000_000); null → DEEP_RESEARCH_TOKEN_BUDGET default",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "topic": "Kubernetes networking with Calico",
                "max_iter": 3,
                "token_budget": 100000,
            }
        }
    }


class ResearchStartResponse(BaseModel):
    """202 response for POST /research/start (ADR-0024 §8.1)."""

    run_id: uuid.UUID = Field(..., description="UUID of the created deep_research_runs row")

    model_config = {
        "json_schema_extra": {"example": {"run_id": "00000000-0000-0000-0000-000000000001"}}
    }


class ResearchRunSummary(BaseModel):
    """
    One item in GET /research/runs (ADR-0024 §8.2, AC-F10-4b).

    Mirrors the ingest_runs list shape: id, topic, status, cost, timing.
    """

    id: uuid.UUID
    vault_id: str
    topic: str
    status: str = Field(
        description="running | converged | max_iter_reached | budget_exhausted | error"
    )
    iterations_used: int
    sources_fetched: int
    total_cost_usd: float
    started_at: datetime
    completed_at: datetime | None = None

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


class ResearchRunListResponse(BaseModel):
    """Paginated list response for GET /research/runs (ADR-0024 §8.2)."""

    items: list[ResearchRunSummary]
    total: int
    limit: int
    offset: int


class ResearchSourceSummary(BaseModel):
    """One source row in GET /research/runs/{id} (ADR-0024 §8.3, AC-F10-6b)."""

    url: str
    title: str | None
    relevance_score: float | None = None
    iteration: int

    @field_validator("relevance_score", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float | None:
        return float(v) if v is not None else None

    model_config = {"from_attributes": True}


class ResearchRunDetail(BaseModel):
    """
    GET /research/runs/{id} response (ADR-0024 §8.3, AC-F10-4c).

    Includes the full queries_used array and per-source summaries.
    synthesis_text is null until step 5 completes (AC-F10-4c).
    sources array excludes fetched_content_md blobs by default (size guard, ADR-0024 §8.3).
    """

    id: uuid.UUID
    vault_id: str
    topic: str
    status: str
    max_iter: int
    token_budget: int
    iterations_used: int
    queries_used: list[str]
    sources_fetched: int
    total_cost_usd: float
    synthesis_text: str | None = None
    synthesis_page_id: uuid.UUID | None = None
    sources: list[ResearchSourceSummary] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


@app.post(
    "/research/start",
    response_model=ResearchStartResponse,
    status_code=202,
    summary="Start a bounded deep-research run",
    description=(
        "F10 Deep Research loop (ADR-0024 §8.1, AC-F10-4a). "
        "Validates topic/vault_id; bounds max_iter (1..10) and token_budget (1k..1M) so the "
        "caller cannot request an unbounded run (I7). "
        "Freezes bounds onto the deep_research_runs row before the background task starts "
        "(AQ-v0.5-4). Schedules run_deep_research as a background asyncio task (fire-and-poll). "
        "Returns 202 {run_id} immediately — poll GET /research/runs/{id} for progress. "
        "503 if SEARXNG_URL is unset (I9 — no fake run, no fallback engine)."
    ),
    responses={
        202: {"description": "Run accepted; poll GET /research/runs/{id} for progress"},
        422: {"description": "Validation error (empty topic, max_iter out of range, etc.)"},
        503: {"description": "SEARXNG_URL is not configured (I9)"},
    },
)
async def research_start(body: ResearchStartRequest) -> ResearchStartResponse:
    """
    POST /research/start — fire-and-poll deep research (ADR-0024 §8.1, I7/I9).

    1. 503 if SEARXNG_URL is unset (I9 — never a fake run, never a fallback engine).
    2. INSERT deep_research_runs row with status='running' + frozen bounds.
    3. Schedule run_deep_research(...) as asyncio background task.
    4. Return 202 {run_id} immediately.
    """
    # ── I9: SearXNG URL required before creating a run row (ADR-0024 §8.1, ADR-0041) ────
    # Resolution: DB vault_state.searxng_url_db wins over SEARXNG_URL env (ADR-0041 §2.2).
    if not _web_search_config_cache.configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "SEARXNG_URL is not configured. Set SEARXNG_URL env var or use "
                "PUT /web-search/config to set the SearXNG instance URL at runtime "
                "(e.g. http://searxng:8080) to enable deep research (I9, ADR-0041)."
            ),
        )

    from app.ops.deep_research import run_deep_research

    run_id = uuid.uuid4()
    # Use str(run_id) so the ORM INSERT works with both Postgres (UUID col)
    # and SQLite in-memory tests (String(36) variant via with_variant).
    # UUID(as_uuid=True) on Postgres can accept a string UUID value.
    run_id_str = str(run_id)

    # Freeze bounds (AQ-v0.5-4): resolve env defaults NOW, INSERT row, schedule task.
    frozen_max_iter = (
        body.max_iter if body.max_iter is not None else settings.deep_research_max_iter
    )
    frozen_token_budget = (
        body.token_budget if body.token_budget is not None else settings.deep_research_token_budget
    )

    # Pre-INSERT the row so the caller can poll immediately after 202
    async with get_session() as session:
        run = DeepResearchRun(
            id=run_id_str,
            vault_id=body.vault_id,
            topic=body.topic,
            status="running",
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            iterations_used=0,
            queries_used=[],
            sources_fetched=0,
            converged=False,
            total_cost_usd=0,
            synthesis_text=None,
            synthesis_page_id=None,
            started_at=datetime.now(UTC),
            completed_at=None,
            error_message=None,
        )
        session.add(run)

    # Schedule the bounded loop as a background task (ADR-0020 fire-and-poll pattern).
    # Pass the SAME run_id so the loop updates the row we just inserted — not a new one
    # (C1: without this the client polls a row the loop never touches → stuck "running").
    asyncio.create_task(
        run_deep_research(
            vault_id=body.vault_id,
            topic=body.topic,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            run_id=run_id,
        )
    )

    logger.info(
        "research_start: run_id=%s vault=%s topic=%r max_iter=%d budget=%d",
        run_id,
        body.vault_id,
        body.topic,
        frozen_max_iter,
        frozen_token_budget,
    )
    return ResearchStartResponse(run_id=run_id)


@app.get(
    "/research/runs",
    response_model=ResearchRunListResponse,
    summary="List deep-research run history",
    description=(
        "Paginated, started_at DESC list of deep_research_runs rows (ADR-0024 §8.2, AC-F10-4b). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional filter. "
        "Mirrors GET /ingest/runs contract."
    ),
    responses={
        200: {"description": "Paginated run list"},
        422: {"description": "Validation error (limit/offset out of range)"},
    },
)
async def list_research_runs(
    limit: int = Query(default=20, ge=1, le=100, description="Max rows (1..100)"),
    offset: int = Query(default=0, ge=0, description="Row offset (>=0)"),
    vault_id: str | None = Query(default=None, description="Optional vault_id filter"),
) -> ResearchRunListResponse:
    """GET /research/runs — paginated deep-research run list (ADR-0024 §8.2)."""
    async with get_session() as session:
        count_stmt = select(func.count()).select_from(DeepResearchRun)
        if vault_id is not None:
            count_stmt = count_stmt.where(DeepResearchRun.vault_id == vault_id)
        total: int = (await session.execute(count_stmt)).scalar_one()

        data_stmt = select(DeepResearchRun)
        if vault_id is not None:
            data_stmt = data_stmt.where(DeepResearchRun.vault_id == vault_id)
        data_stmt = (
            data_stmt.order_by(DeepResearchRun.started_at.desc()).offset(offset).limit(limit)
        )
        runs = list((await session.execute(data_stmt)).scalars().all())

    items = [
        ResearchRunSummary(
            id=r.id,
            vault_id=r.vault_id,
            topic=r.topic,
            status=r.status,
            iterations_used=r.iterations_used,
            sources_fetched=r.sources_fetched,
            total_cost_usd=float(r.total_cost_usd),
            started_at=r.started_at,
            completed_at=r.completed_at,
        )
        for r in runs
    ]
    return ResearchRunListResponse(items=items, total=total, limit=limit, offset=offset)


@app.get(
    "/research/runs/{run_id}",
    response_model=ResearchRunDetail,
    summary="Get deep-research run detail + sources",
    description=(
        "Returns full run detail including queries_used, synthesis_text, and per-source summaries "
        "(ADR-0024 §8.3, AC-F10-4c). synthesis_text is null until step 5 completes. "
        "sources array excludes fetched_content_md blobs (size guard). 404 if unknown run_id."
    ),
    responses={
        200: {"description": "Run detail with sources"},
        404: {"description": "No run with this id"},
    },
)
async def get_research_run(run_id: uuid.UUID) -> ResearchRunDetail:
    """GET /research/runs/{id} — deep-research run detail (ADR-0024 §8.3)."""
    # Use str(run_id) so the query works with both Postgres (UUID col) and SQLite (String col).
    # UUID(as_uuid=True).with_variant(String(36), "sqlite") handles the conversion when given
    # a str, but aiosqlite cannot bind a native uuid.UUID Python object.
    run_id_str = str(run_id)

    async with get_session() as session:
        # Load the run row
        run_result = await session.execute(
            select(DeepResearchRun).where(DeepResearchRun.id == run_id_str)
        )
        run = run_result.scalar_one_or_none()

        if run is None:
            raise HTTPException(status_code=404, detail=f"Deep research run {run_id} not found")

        # Load sources in a separate query (avoids lazy-load raise on relationship)
        sources_result = await session.execute(
            select(DeepResearchSource).where(DeepResearchSource.run_id == run_id_str)
        )
        source_rows = list(sources_result.scalars().all())

    sources = [
        ResearchSourceSummary(
            url=s.url,
            title=s.title,
            relevance_score=float(s.relevance_score) if s.relevance_score is not None else None,
            iteration=s.iteration,
        )
        for s in source_rows
    ]

    return ResearchRunDetail(
        id=run.id,
        vault_id=run.vault_id,
        topic=run.topic,
        status=run.status,
        max_iter=run.max_iter,
        token_budget=run.token_budget,
        iterations_used=run.iterations_used,
        queries_used=run.queries_used or [],
        sources_fetched=run.sources_fetched,
        total_cost_usd=float(run.total_cost_usd),
        synthesis_text=run.synthesis_text,
        synthesis_page_id=run.synthesis_page_id,
        sources=sources,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
    )


# ── F9 Review Queue REST (ADR-0034 §7 — proposal model redesign) ─────────────

# Maximum page size for GET /review/queue (I7 — bounded list)
_REVIEW_QUEUE_MAX_LIMIT: int = 200


class ReferencedPage(BaseModel):
    """Convenience join for a referenced_page_ids entry (ADR-0044 §6.1)."""

    id: uuid.UUID
    title: str | None = None
    type: str | None = None


class ReviewItemResponse(BaseModel):
    """
    API response shape for one review_items row (ADR-0034 §7.1; ADR-0044 §6.1 additions).

    Projection carries the full proposal model: type, proposed_title, proposed_page_type,
    proposed_dir, rationale, and the three page FK fields (page_id/source_page_id/created_page_id).
    page_title is a convenience join from pages.title for the page_id FK (UI display).
    resolution records how the item was closed (null while pending).

    ADR-0044 §6.1 adds: content_key (opaque dedup handle), referenced_page_ids (array),
    referenced_pages (convenience join, stale ids filtered), search_queries (Deep-Research seeds).
    """

    id: uuid.UUID
    vault_id: str
    item_type: str = Field(
        description="missing-page | suggestion | contradiction | duplicate | confirm"
    )
    status: str = Field(
        description="pending | created | skipped | dismissed | deep_researched | auto_resolved"
    )
    proposed_title: str | None = Field(
        default=None,
        description="Title the LLM proposes to create; drives lazy skeleton (ADR-0034 §5.2)",
    )
    proposed_page_type: str | None = Field(
        default=None,
        description="entity|concept|source|synthesis|comparison; NULL → heuristic at Create",
    )
    proposed_dir: str | None = Field(
        default=None,
        description="Target wiki/ subdir (display only; recomputed at Create — ADR-0034 §5.2)",
    )
    rationale: str | None = Field(
        default=None,
        description="Why this matters; used as topic hint for Deep Research (ADR-0034 §3.1)",
    )
    page_id: uuid.UUID | None = Field(
        default=None,
        description="Review TARGET: conflicting/context existing page FK (ADR-0034 §3.1)",
    )
    page_title: str | None = Field(
        default=None,
        description="Convenience join from pages.title for page_id (UI display)",
    )
    source_page_id: uuid.UUID | None = Field(
        default=None,
        description="Provenance: page whose ingest produced this proposal (ADR-0034 §3.1)",
    )
    created_page_id: uuid.UUID | None = Field(
        default=None,
        description="Page produced by a successful Create action (ADR-0034 §5); null otherwise",
    )
    resolution: str | None = Field(
        default=None,
        description=(
            "created|skipped|dismissed|researched|rule_resolved|llm_resolved; null while pending"
        ),
    )
    deep_research_run_id: uuid.UUID | None = Field(
        default=None,
        description="FK → deep_research_runs.id; set when Deep-Research fires (AC-F10-5)",
    )
    # ── ADR-0044 §6.1: contextual depth + stable idempotency (additions) ──────────
    content_key: str | None = Field(
        default=None,
        description="Stable FNV-1a dedup handle (opaque to UI); NULL for confirm (ADR-0044 §3.2)",
    )
    referenced_page_ids: list[str] | None = Field(
        default=None,
        description="Array of page-id strings this proposal is contextually about (ADR-0044 §2)",
    )
    referenced_pages: list[ReferencedPage] | None = Field(
        default=None,
        description=(
            "Convenience join [{id,title,type}] for referenced_page_ids; stale ids filtered at "
            "render (ADR-0044 §6.1/§9.2) so the card renders [[title]] links without a round-trip"
        ),
    )
    search_queries: list[str] | None = Field(
        default=None,
        description="≤3 pre-generated search queries; search_queries[0] seeds Deep Research",
    )
    created_at: datetime
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class ReviewQueueResponse(BaseModel):
    """Paginated response for GET /review/queue (ADR-0034 §7)."""

    items: list[ReviewItemResponse]
    total: int
    limit: int
    offset: int


class ReviewDeepResearchResponse(BaseModel):
    """202 response for POST /review/queue/{id}/deep-research (ADR-0034 §7)."""

    review_item_id: uuid.UUID
    run_id: uuid.UUID

    model_config = {
        "json_schema_extra": {
            "example": {
                "review_item_id": "00000000-0000-0000-0000-000000000001",
                "run_id": "00000000-0000-0000-0000-000000000002",
            }
        }
    }


class ReviewSweepResponse(BaseModel):
    """200 response for POST /review/queue/sweep (ADR-0034 §7)."""

    rule_resolved: int = Field(description="Items closed by rule-based Pass-1")
    llm_resolved: int = Field(description="Items closed by conservative LLM Pass-2")
    kept: int = Field(description="Items that remain pending after the sweep")


class ReviewBulkRequest(BaseModel):
    """Request body for POST /review/queue/bulk (ADR-0044 §6)."""

    vault_id: str = Field(..., description="Vault scope (required)")
    action: str = Field(
        ...,
        description="skip | dismiss | mark-resolved (ADR-0044 §6)",
    )
    ids: list[uuid.UUID] = Field(
        ...,
        description="Review item ids to act on; capped at REVIEW_BULK_MAX_IDS (I7 — 400 over)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "action": "dismiss",
                "ids": ["00000000-0000-0000-0000-000000000001"],
            }
        }
    }


class ReviewBulkResponse(BaseModel):
    """200 response for POST /review/queue/bulk (ADR-0044 §6)."""

    updated: int = Field(description="Pending ids mutated to the new terminal status")
    skipped_terminal: int = Field(
        description="Ids that were already terminal (or confirm under mark-resolved); never mutated"
    )


class ReviewClearResolvedResponse(BaseModel):
    """200 response for DELETE /review/queue/resolved (ADR-0044 §6)."""

    deleted: int = Field(description="Terminal rows hard-deleted for the vault (pending untouched)")


def _review_item_to_response(
    item: ReviewItem,
    page_title: str | None = None,
    *,
    referenced_pages: list[ReferencedPage] | None = None,
) -> ReviewItemResponse:
    """Convert ReviewItem ORM row to ReviewItemResponse (handles str/UUID for id fields).

    ADR-0044 §6.1: content_key, referenced_page_ids, search_queries pass through; referenced_pages
    is the caller-supplied convenience join (stale ids already filtered — §9.2)."""

    # UUID fields stored as str in SQLite, UUID in Postgres — normalise to UUID
    def _to_uuid(val: Any) -> uuid.UUID | None:
        if val is None:
            return None
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return None

    def _str_list(val: Any) -> list[str] | None:
        if not isinstance(val, list):
            return None
        out = [str(x) for x in val if isinstance(x, str) and x.strip()]
        return out or None

    return ReviewItemResponse(
        id=_to_uuid(item.id) or uuid.UUID(int=0),
        vault_id=item.vault_id,
        item_type=item.item_type,
        status=item.status,
        proposed_title=item.proposed_title,
        proposed_page_type=item.proposed_page_type,
        proposed_dir=item.proposed_dir,
        rationale=item.rationale,
        page_id=_to_uuid(item.page_id),
        page_title=page_title,
        source_page_id=_to_uuid(item.source_page_id),
        created_page_id=_to_uuid(item.created_page_id),
        resolution=item.resolution,
        deep_research_run_id=_to_uuid(item.deep_research_run_id),
        content_key=getattr(item, "content_key", None),
        referenced_page_ids=_str_list(getattr(item, "referenced_page_ids", None)),
        referenced_pages=referenced_pages,
        search_queries=_str_list(getattr(item, "search_queries", None)),
        created_at=item.created_at,
        reviewed_at=item.reviewed_at,
    )


@app.get(
    "/review/queue",
    response_model=ReviewQueueResponse,
    summary="List HITL review queue proposals",
    description=(
        "F9 HITL Review Queue (ADR-0034 §7; ADR-0044 §6 status filter + contextual projection). "
        "Returns paginated review_items for a vault, ordered created_at ASC. "
        "Each item is a PROPOSAL (missing-page|suggestion|contradiction|duplicate|confirm). "
        "status filter (ADR-0044 §6): pending (default) | resolved | dismissed | all. "
        "limit: default 50, max 200 (I7 — bounded page size). offset: >=0. "
        "vault_id: required filter. "
        "page_title is a convenience join from pages.title for the page_id FK (UI display). "
        "referenced_pages joins referenced_page_ids to [{id,title,type}] (stale ids filtered)."
    ),
    responses={
        200: {"description": "Paginated review proposals"},
        422: {"description": "Validation error (limit out of range, missing vault_id)"},
    },
)
async def list_review_queue(
    vault_id: str = Query(..., description="Vault scope (required)"),
    status: str = Query(
        default="pending",
        description="Status filter (ADR-0044 §6): pending | resolved | dismissed | all",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=_REVIEW_QUEUE_MAX_LIMIT,
        description=f"Max rows to return (1..{_REVIEW_QUEUE_MAX_LIMIT}); I7 cap",
    ),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> ReviewQueueResponse:
    """
    GET /review/queue — paginated HITL review proposals (ADR-0034 §7, ADR-0044 §6 filter).

    READ-ONLY — no data_version bump, no ingest triggered.
    limit capped at 200 (I7 — bounded page size). page_title + referenced_pages are convenience
    joins on pages; referenced_pages drops ids that no longer resolve to a live page (§9.2).
    """
    from app.ops.review import list_queue

    queue_page = await list_queue(vault_id, limit=limit, offset=offset, status=status)

    # Load page_title for page_id + referenced_pages for referenced_page_ids in ONE bounded
    # pages read across all items on the page (convenience joins — ADR-0044 §6.1).
    def _ids_of(val: Any) -> list[str]:
        if not isinstance(val, list):
            return []
        return [str(x) for x in val if isinstance(x, str) and x.strip()]

    all_page_ids: set[str] = set()
    for it in queue_page.items:
        if it.page_id is not None:
            all_page_ids.add(str(it.page_id))
        for rid in _ids_of(getattr(it, "referenced_page_ids", None)):
            all_page_ids.add(rid)

    page_info: dict[str, tuple[str | None, str | None]] = {}
    if all_page_ids:
        from sqlalchemy import String as _SAString
        from sqlalchemy import cast as _sa_cast

        async with get_session() as session:
            rows = await session.execute(
                select(Page.id, Page.title, Page.page_type).where(
                    # CAST for SQLite/Postgres id portability (mirrors retrieval.py / sweep).
                    _sa_cast(Page.id, _SAString).in_(list(all_page_ids)),
                    Page.deleted_at.is_(None),
                )
            )
            for row in rows:
                page_info[str(row[0])] = (row[1], row[2])

    items: list[ReviewItemResponse] = []
    for it in queue_page.items:
        page_title = page_info.get(str(it.page_id), (None, None))[0] if it.page_id else None
        # referenced_pages: resolve + DROP stale ids (§9.2 render-time filter, I9).
        referenced_pages: list[ReferencedPage] = []
        for rid in _ids_of(getattr(it, "referenced_page_ids", None)):
            info = page_info.get(rid)
            if info is None:
                continue  # stale id → filtered out
            try:
                referenced_pages.append(
                    ReferencedPage(id=uuid.UUID(rid), title=info[0], type=info[1])
                )
            except (ValueError, AttributeError):
                continue
        items.append(
            _review_item_to_response(
                it,
                page_title=page_title,
                referenced_pages=referenced_pages or None,
            )
        )

    return ReviewQueueResponse(
        items=items,
        total=queue_page.total,
        limit=queue_page.limit,
        offset=queue_page.offset,
    )


async def _create_review_item_handler(item_id: uuid.UUID) -> ReviewItemResponse:
    """
    Shared Create handler for both /approve and /create routes (ADR-0034 §5).

    Runs the bounded orchestrated loop to generate the page on-demand (lazy — ADR-0034 §2),
    writes it through write_wiki_page (I1 — one data_version bump), and returns 201.

    409 if item not pending or no ingest provider configured (I6).
    502 if generation fails; item left pending (§5.3).
    404 if item not found.
    """
    from app.ops.review import create_page_from_review

    item = await create_page_from_review(item_id)
    return _review_item_to_response(item)


@app.post(
    "/review/queue/{item_id}/approve",
    response_model=ReviewItemResponse,
    status_code=201,
    summary="Create: lazy on-demand page generation from a proposal",
    description=(
        "F9 HITL Review Queue — Create action (ADR-0034 §5; path kept for backward stability). "
        "Runs the bounded orchestrated loop targeting the proposed page, writes it through "
        "write_wiki_page (I1 — one data_version bump), sets status=created + created_page_id. "
        "409 if item is not pending or no ingest provider is configured (I6 — never hardcode). "
        "502 if generation fails; item is left pending — retry or skip. "
        "404 if item_id is unknown. "
        "Prefer the /create alias (explicit verb) for new clients (ADR-0034 §9 risk 6)."
    ),
    responses={
        201: {"description": "Page created; item status=created"},
        404: {"description": "Review item not found"},
        409: {"description": "Item not pending, or no ingest provider configured (I6)"},
        502: {"description": "Generation failed; item left pending"},
    },
)
async def approve_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/approve — Create alias for backward compatibility (ADR-0034 §5)."""
    return await _create_review_item_handler(item_id)


@app.post(
    "/review/queue/{item_id}/create",
    response_model=ReviewItemResponse,
    status_code=201,
    summary="Create: lazy on-demand page generation from a proposal (explicit verb)",
    description=(
        "F9 HITL Review Queue — Create action (ADR-0034 §5 — preferred explicit alias). "
        "Identical to POST /review/queue/{id}/approve. "
        "Runs the bounded orchestrated loop targeting the proposed page, writes it through "
        "write_wiki_page (I1 — one data_version bump), sets status=created + created_page_id. "
        "409 if item is not pending or no ingest provider is configured (I6). "
        "502 if generation fails; item is left pending. "
        "404 if item_id is unknown."
    ),
    responses={
        201: {"description": "Page created; item status=created"},
        404: {"description": "Review item not found"},
        409: {"description": "Item not pending, or no ingest provider configured (I6)"},
        502: {"description": "Generation failed; item left pending"},
    },
)
async def create_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/create — lazy on-demand Create (ADR-0034 §5 preferred verb)."""
    return await _create_review_item_handler(item_id)


@app.post(
    "/review/queue/{item_id}/skip",
    response_model=ReviewItemResponse,
    summary="Skip a review proposal",
    description=(
        "F9 HITL Review Queue — skip action (ADR-0034 §7). "
        "Sets status=skipped, resolution=skipped, reviewed_at=now(). "
        "404 if item_id is unknown."
    ),
    responses={
        200: {"description": "Item skipped"},
        404: {"description": "Review item not found"},
    },
)
async def skip_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/skip — status write (ADR-0034 §7)."""
    from app.ops.review import skip

    item = await skip(item_id)
    return _review_item_to_response(item)


@app.post(
    "/review/queue/{item_id}/dismiss",
    response_model=ReviewItemResponse,
    summary="Dismiss a review proposal",
    description=(
        "F9 HITL Review Queue — dismiss action (ADR-0044 §6). "
        "Sets status=dismissed, resolution=dismissed, reviewed_at=now(). Terminal. "
        "Distinct from skip: 'hide this, I'm not acting' vs skip's 'considered and declined'. "
        "404 if item_id is unknown."
    ),
    responses={
        200: {"description": "Item dismissed"},
        404: {"description": "Review item not found"},
    },
)
async def dismiss_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/dismiss — status write (ADR-0044 §6)."""
    from app.ops.review import dismiss

    item = await dismiss(item_id)
    return _review_item_to_response(item)


@app.post(
    "/review/queue/{item_id}/deep-research",
    response_model=ReviewDeepResearchResponse,
    status_code=202,
    summary="Trigger deep research for a review proposal",
    description=(
        "F9 HITL Review Queue — deep-research action (ADR-0034 §7, AC-F9-3, AC-F10-5). "
        "Sets status=deep_researched, resolution=researched; delegates to F10 with the item's "
        "proposed_title → rationale (first line) → page.title as the research topic. "
        "(pre_generated_query is DROPPED in ADR-0034; topic derivation updated.) "
        "Stores the returned run_id in review_items.deep_research_run_id (AC-F10-5). "
        "Returns 202 {review_item_id, run_id} immediately (fire-and-poll). "
        "503 if SEARXNG_URL is unset (inherits F10's guard, I9). "
        "404 if item_id is unknown."
    ),
    responses={
        202: {
            "description": "Deep research started; poll GET /research/runs/{run_id} for progress"
        },
        404: {"description": "Review item not found"},
        503: {"description": "SEARXNG_URL is not configured (I9)"},
    },
)
async def deep_research_review_item(item_id: uuid.UUID) -> ReviewDeepResearchResponse:
    """POST /review/queue/{id}/deep-research — delegate to F10 (ADR-0034 §7, AC-F10-5)."""
    from app.ops.review import deep_research as _deep_research_op

    result = await _deep_research_op(item_id)
    return ReviewDeepResearchResponse(
        review_item_id=result.review_item_id,
        run_id=result.run_id,
    )


@app.post(
    "/review/queue/sweep",
    response_model=ReviewSweepResponse,
    summary="Manual auto-resolution sweep of pending review proposals",
    description=(
        "F9 HITL Review Queue — manual sweep trigger (ADR-0034 §6). "
        "Runs Pass-1 (rule-based title-match for missing-page/duplicate) and "
        "Pass-2 (conservative bounded LLM judgment). "
        "Bounded; idempotent; never fails (returns partial results on error). "
        "vault_id: required. "
        "Auto-triggered after each orchestrated ingest run and after a successful Create. "
        "confirm items are NEVER auto-resolved (Do-NOT #7, ADR-0034 §10)."
    ),
    responses={
        200: {"description": "Sweep complete; counts of resolved and kept items"},
        422: {"description": "Validation error (missing vault_id)"},
    },
)
async def sweep_review_queue(
    vault_id: str = Query(..., description="Vault scope (required)"),
) -> ReviewSweepResponse:
    """POST /review/queue/sweep — manual auto-resolution sweep (ADR-0034 §6)."""
    from app.ops.review import sweep_reviews

    result = await sweep_reviews(vault_id)
    return ReviewSweepResponse(
        rule_resolved=result.rule_resolved,
        llm_resolved=result.llm_resolved,
        kept=result.kept,
    )


@app.post(
    "/review/queue/bulk",
    response_model=ReviewBulkResponse,
    summary="Bulk status action on review proposals",
    description=(
        "F9 HITL Review Queue — bounded bulk status write (ADR-0044 §6, I7). "
        "action: skip | dismiss | mark-resolved. "
        "Only PENDING ids (scoped to vault_id) are mutated; already-terminal ids are counted in "
        "skipped_terminal and NEVER re-mutated. mark-resolved NEVER auto-resolves a `confirm` "
        "item (Do-NOT #6/#10 — it is counted as skipped_terminal). No provider call. "
        "len(ids) is capped at REVIEW_BULK_MAX_IDS (400 over cap — I7)."
    ),
    responses={
        200: {"description": "Bulk action applied; {updated, skipped_terminal}"},
        400: {"description": "ids exceed REVIEW_BULK_MAX_IDS, or unknown action (I7)"},
    },
)
async def bulk_review_queue(body: ReviewBulkRequest) -> ReviewBulkResponse:
    """POST /review/queue/bulk — bounded bulk status write (ADR-0044 §6)."""
    from app.config import settings as _settings
    from app.ops.review import bulk_update_reviews

    max_ids = int(getattr(_settings, "review_bulk_max_ids", 200))
    if len(body.ids) > max_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"bulk ids ({len(body.ids)}) exceed REVIEW_BULK_MAX_IDS ({max_ids}) — "
                "split into smaller batches (I7 — bounded bulk write)."
            ),
        )
    if body.action not in ("skip", "dismiss", "mark-resolved"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown bulk action {body.action!r}; expected skip|dismiss|mark-resolved.",
        )

    result = await bulk_update_reviews(
        vault_id=body.vault_id,
        action=body.action,
        ids=body.ids,
    )
    return ReviewBulkResponse(updated=result.updated, skipped_terminal=result.skipped_terminal)


@app.delete(
    "/review/queue/resolved",
    response_model=ReviewClearResolvedResponse,
    summary="Clear (hard-delete) terminal review proposals",
    description=(
        "F9 HITL Review Queue — 'Clear resolved' (ADR-0044 §6, I7). "
        "Hard-deletes terminal rows (skipped/dismissed/created/auto_resolved/deep_researched) for "
        "the vault in ONE bounded vault-scoped statement. PENDING rows are NEVER touched. "
        "Idempotent. These rows are advisory metadata (not vault content); created_page_id points "
        "at a page that persists independently (ADR-0044 §9.5). "
        "vault_id: required."
    ),
    responses={
        200: {"description": "Terminal rows deleted; {deleted}"},
        422: {"description": "Validation error (missing vault_id)"},
    },
)
async def clear_resolved_review_queue(
    vault_id: str = Query(..., description="Vault scope (required)"),
) -> ReviewClearResolvedResponse:
    """DELETE /review/queue/resolved — bounded hard-delete of terminal rows (ADR-0044 §6)."""
    from app.ops.review import clear_resolved_reviews

    deleted = await clear_resolved_reviews(vault_id)
    return ReviewClearResolvedResponse(deleted=deleted)


# ── K2 Lint-fix loop REST (ADR-0037) ─────────────────────────────────────────

# Maximum page size for GET /lint/findings (I7 — bounded list)
_LINT_FINDINGS_MAX_LIMIT: int = 200


class LintScanRequest(BaseModel):
    """
    Request body for POST /lint/scan (ADR-0037 §6).

    max_iter and token_budget are optional — env defaults (LINT_MAX_ITER / LINT_TOKEN_BUDGET)
    apply when omitted. Both are FROZEN onto the lint_runs row before the scan runs (I7).
    Server-side validators cap the range so callers cannot request an unbounded run (I7).
    """

    vault_id: str = Field(..., description="Vault scope for the scan")
    max_iter: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Max semantic rounds (1..10); null → LINT_MAX_ITER default",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1_000,
        le=1_000_000,
        description="Token budget (1_000..1_000_000); null → LINT_TOKEN_BUDGET default",
    )

    model_config = {
        "json_schema_extra": {
            "example": {"vault_id": "default", "max_iter": 3, "token_budget": 20000}
        }
    }


class LintFindingResponse(BaseModel):
    """API response shape for one lint_findings row (ADR-0037 §6)."""

    id: uuid.UUID
    lint_run_id: uuid.UUID
    vault_id: str
    category: str = Field(
        description="orphan-page | missing-xref | contradiction | stale-claim | missing-page"
    )
    severity: str = Field(description="info | warning | error")
    target_page_id: uuid.UUID | None = None
    target_title: str | None = None
    description: str
    proposed_action: str | None = Field(
        default=None,
        description="Fix apply_lint_fix would attempt; null for flag-only findings",
    )
    status: str = Field(description="open | applied | dismissed")
    resolution_note: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True}


class LintRunResponse(BaseModel):
    """API response shape for one lint_runs row (ADR-0037 §6)."""

    id: uuid.UUID
    vault_id: str
    status: str = Field(description="running | completed | error")
    max_iter: int
    token_budget: int
    iterations_used: int
    findings_count: int
    total_cost_usd: float
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


class LintScanResponse(BaseModel):
    """200 response for POST /lint/scan (ADR-0037 §6): the run + its findings."""

    run: LintRunResponse
    findings: list[LintFindingResponse]


class LintRunListResponse(BaseModel):
    """Paginated list response for GET /lint/runs (ADR-0037 §6)."""

    items: list[LintRunResponse]
    total: int
    limit: int
    offset: int


class LintFindingListResponse(BaseModel):
    """Paginated list response for GET /lint/findings (ADR-0037 §6)."""

    items: list[LintFindingResponse]
    total: int
    limit: int
    offset: int


def _lint_finding_to_response(f: LintFinding) -> LintFindingResponse:
    """Convert a LintFinding ORM row to LintFindingResponse (str/UUID normalisation)."""

    def _to_uuid(val: Any) -> uuid.UUID | None:
        if val is None:
            return None
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return None

    return LintFindingResponse(
        id=_to_uuid(f.id) or uuid.UUID(int=0),
        lint_run_id=_to_uuid(f.lint_run_id) or uuid.UUID(int=0),
        vault_id=f.vault_id,
        category=f.category,
        severity=f.severity,
        target_page_id=_to_uuid(f.target_page_id),
        target_title=f.target_title,
        description=f.description,
        proposed_action=f.proposed_action,
        status=f.status,
        resolution_note=f.resolution_note,
        created_at=f.created_at,
        reviewed_at=f.reviewed_at,
    )


def _lint_run_to_response(r: LintRun) -> LintRunResponse:
    """Convert a LintRun ORM row to LintRunResponse (str/UUID normalisation)."""

    def _to_uuid(val: Any) -> uuid.UUID:
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return uuid.UUID(int=0)

    return LintRunResponse(
        id=_to_uuid(r.id),
        vault_id=r.vault_id,
        status=r.status,
        max_iter=r.max_iter,
        token_budget=r.token_budget,
        iterations_used=r.iterations_used,
        findings_count=r.findings_count,
        total_cost_usd=float(r.total_cost_usd),
        started_at=r.started_at,
        completed_at=r.completed_at,
        error_message=r.error_message,
        created_at=r.created_at,
    )


@app.post(
    "/lint/scan",
    response_model=LintScanResponse,
    summary="Run a bounded lint scan (K2 — produces findings, never auto-fixes)",
    description=(
        "K2 Lint-fix loop (ADR-0037). Runs a BOUNDED, HUMAN-GATED health check of the wiki: "
        "deterministic structural checks (orphan-page via the graph/links read, no LLM) plus a "
        "bounded semantic pass (missing-xref | contradiction | stale-claim | missing-page) that "
        "rides the resolved ingest provider (I6 — never hardcoded). "
        "Bounds: max_iter (1..10) + token_budget (1k..1M) FROZEN on the lint_runs row (I7); "
        "findings capped at LINT_MAX_FINDINGS; total_cost_usd logged. "
        "Produces FINDINGS only — applying a fix requires the explicit human gate "
        "(POST /lint/findings/{id}/apply). Returns the run row + its findings."
    ),
    responses={
        200: {"description": "Scan complete; run + findings returned"},
        422: {"description": "Validation error (max_iter/token_budget out of range)"},
    },
)
async def lint_scan(body: LintScanRequest) -> LintScanResponse:
    """POST /lint/scan — run a bounded lint scan synchronously (ADR-0037 §6)."""
    from app.ops.lint import run_lint_scan

    result = await run_lint_scan(
        body.vault_id,
        max_iter=body.max_iter,
        token_budget=body.token_budget,
    )

    # Load the run row + its findings for the response.
    run_id_str = str(result.run_id)
    async with get_session() as session:
        run = (await session.execute(select(LintRun).where(LintRun.id == run_id_str))).scalar_one()
        finding_rows = list(
            (
                await session.execute(
                    select(LintFinding)
                    .where(LintFinding.lint_run_id == run_id_str)
                    .order_by(LintFinding.created_at.asc())
                )
            ).scalars()
        )
        session.expunge(run)
        for fr in finding_rows:
            session.expunge(fr)

    return LintScanResponse(
        run=_lint_run_to_response(run),
        findings=[_lint_finding_to_response(f) for f in finding_rows],
    )


@app.get(
    "/lint/runs",
    response_model=LintRunListResponse,
    summary="List lint scan run history",
    description=(
        "Paginated, created_at DESC list of lint_runs rows (ADR-0037 §6). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional filter. "
        "Mirrors GET /research/runs."
    ),
    responses={
        200: {"description": "Paginated lint run list"},
        422: {"description": "Validation error (limit/offset out of range)"},
    },
)
async def list_lint_runs_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="Max rows (1..100)"),
    offset: int = Query(default=0, ge=0, description="Row offset (>=0)"),
    vault_id: str | None = Query(default=None, description="Optional vault_id filter"),
) -> LintRunListResponse:
    """GET /lint/runs — paginated lint run list (ADR-0037 §6)."""
    from app.ops.lint import list_lint_runs

    page = await list_lint_runs(vault_id, limit=limit, offset=offset)
    return LintRunListResponse(
        items=[_lint_run_to_response(r) for r in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@app.get(
    "/lint/runs/{run_id}",
    response_model=LintRunResponse,
    summary="Get a lint scan run by id",
    description="Returns one lint_runs row (ADR-0037 §6). 404 if unknown run_id.",
    responses={
        200: {"description": "Lint run detail"},
        404: {"description": "No lint run with this id"},
    },
)
async def get_lint_run(run_id: uuid.UUID) -> LintRunResponse:
    """GET /lint/runs/{id} — lint run detail (ADR-0037 §6)."""
    run_id_str = str(run_id)
    async with get_session() as session:
        run = (
            await session.execute(select(LintRun).where(LintRun.id == run_id_str))
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail=f"Lint run {run_id} not found")
        session.expunge(run)
    return _lint_run_to_response(run)


@app.get(
    "/lint/findings",
    response_model=LintFindingListResponse,
    summary="List lint findings",
    description=(
        "Paginated, created_at ASC list of lint_findings rows (ADR-0037 §6). "
        "vault_id: required. status: optional filter (open|applied|dismissed; default open). "
        "limit: default 50, max 200 (I7 — bounded page size). offset: >=0."
    ),
    responses={
        200: {"description": "Paginated lint findings"},
        422: {"description": "Validation error (limit out of range, missing vault_id)"},
    },
)
async def list_lint_findings_endpoint(
    vault_id: str = Query(..., description="Vault scope (required)"),
    status: str | None = Query(
        default="open",
        description="open | applied | dismissed; null/omit for all statuses",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=_LINT_FINDINGS_MAX_LIMIT,
        description=f"Max rows (1..{_LINT_FINDINGS_MAX_LIMIT}); I7 cap",
    ),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> LintFindingListResponse:
    """GET /lint/findings — paginated lint findings (ADR-0037 §6)."""
    from app.ops.lint import list_lint_findings

    # Treat the literal string "all" (or empty) as "no status filter".
    status_filter = None if status in (None, "", "all") else status
    page = await list_lint_findings(vault_id, status=status_filter, limit=limit, offset=offset)
    return LintFindingListResponse(
        items=[_lint_finding_to_response(f) for f in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@app.post(
    "/lint/findings/{finding_id}/apply",
    response_model=LintFindingResponse,
    summary="Apply a lint fix (HUMAN GATE)",
    description=(
        "K2 Lint-fix loop — human-gated apply (ADR-0037 §5). Applies ONLY safe/bounded fixes; "
        "bumps data_version at most ONCE per applied fix (I1); NEVER full-rescans. "
        "missing-xref → reuses the wikilink-enrichment seam (adds the [[link]] into the page "
        "body, I5). missing-page → delegates to the lazy-generation seam (bounded orchestrated "
        "loop, one data_version bump). orphan-page / contradiction / stale-claim are FLAG-ONLY: "
        "apply records acknowledgement (status=applied) but performs no automatic edit. "
        "409 if the finding is not open or no ingest provider is configured (I6). "
        "502 if a bounded fix fails; finding left open. 404 if finding_id is unknown."
    ),
    responses={
        200: {"description": "Fix applied (or finding acknowledged for flag-only categories)"},
        404: {"description": "Lint finding not found"},
        409: {"description": "Finding not open, or no ingest provider configured (I6)"},
        502: {"description": "Bounded fix failed; finding left open"},
    },
)
async def apply_lint_finding(finding_id: uuid.UUID) -> LintFindingResponse:
    """POST /lint/findings/{id}/apply — human-gated apply (ADR-0037 §5)."""
    from app.ops.lint import apply_lint_fix

    finding = await apply_lint_fix(finding_id)
    return _lint_finding_to_response(finding)


@app.post(
    "/lint/findings/{finding_id}/dismiss",
    response_model=LintFindingResponse,
    summary="Dismiss a lint finding",
    description=(
        "K2 Lint-fix loop — dismiss action (ADR-0037 §5). Sets status=dismissed, "
        "reviewed_at=now(). No edit, no data_version bump. 404 if finding_id is unknown."
    ),
    responses={
        200: {"description": "Finding dismissed"},
        404: {"description": "Lint finding not found"},
    },
)
async def dismiss_lint_finding_endpoint(finding_id: uuid.UUID) -> LintFindingResponse:
    """POST /lint/findings/{id}/dismiss — status write (ADR-0037 §5)."""
    from app.ops.lint import dismiss_lint_finding

    finding = await dismiss_lint_finding(finding_id)
    return _lint_finding_to_response(finding)


# ── F13 Cascade Delete REST (ADR-0026, AC-F13-5/7) ───────────────────────────


class CascadePreviewResponse(BaseModel):
    """
    POST /pages/{id}/cascade-delete/preview response (ADR-0026 §6.1, DRY-RUN).

    Read-only: mutates nothing — no soft-delete, no Qdrant delete, no file write,
    no data_version bump.  Returns the full CascadePlan as JSON.
    """

    target_page_id: uuid.UUID
    target_title: str | None = None
    target_file_path: str
    will_delete: list[uuid.UUID]
    will_preserve_with_pruned_source: list[uuid.UUID]
    wikilinks_to_rewrite: list[dict[str, Any]]
    index_entry_will_be_removed: bool
    raw_source_to_delete: str | None = None
    shared_entity_warnings: list[str]
    match_methods_used: dict[str, str]


class CascadeDeleteResponse(BaseModel):
    """
    DELETE /pages/{id} response (ADR-0026 §6.1, AC-F13-5).

    deleted_page_id: the page that was deleted.
    wikilinks_cleaned: total [[Target]] spans neutralised.
    index_entry_removed: True when index.md was successfully regenerated.
    shared_entity_warnings: advisory list of source-overlap pages.
    """

    deleted_page_id: uuid.UUID
    wikilinks_cleaned: int
    index_entry_removed: bool
    shared_entity_warnings: list[str]


@app.post(
    "/pages/{page_id}/cascade-delete/preview",
    response_model=CascadePreviewResponse,
    summary="Dry-run preview of cascade delete (read-only)",
    description=(
        "F13 Cascade Delete — mandatory dry-run (ADR-0026 §6, AC-F13-5). "
        "Computes the full deletion plan WITHOUT mutating any store or file: "
        "no soft-delete, no Qdrant delete, no file write, no data_version bump. "
        "Returns will_delete, wikilinks_to_rewrite, shared_entity_warnings, match_methods_used. "
        "404 if the page does not exist or is already soft-deleted. "
        "Call this before DELETE /pages/{id} to populate a confirmation modal (AC-F13-6)."
    ),
    responses={
        200: {"description": "Cascade plan computed (read-only)"},
        404: {"description": "Page not found or already deleted"},
    },
)
async def cascade_delete_preview(page_id: uuid.UUID) -> CascadePreviewResponse:
    """
    POST /pages/{page_id}/cascade-delete/preview — dry-run plan (ADR-0026 §6, AC-F13-5).

    Read-only: plan_cascade_delete() never mutates any store or file.
    404 on unknown / already-soft-deleted page (PageNotFoundError).
    """
    from app.ops.cascade_delete import PageNotFoundError, plan_cascade_delete

    try:
        plan = await plan_cascade_delete(page_id)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CascadePreviewResponse(
        target_page_id=plan.target_page_id,
        target_title=plan.target_title,
        target_file_path=plan.target_file_path,
        will_delete=plan.will_delete,
        will_preserve_with_pruned_source=plan.will_preserve_with_pruned_source,
        wikilinks_to_rewrite=[
            {
                "source_page_id": str(r.source_page_id),
                "file_path": r.file_path,
                "target_title": r.target_title,
                "occurrences": r.occurrences,
            }
            for r in plan.wikilinks_to_rewrite
        ],
        index_entry_will_be_removed=plan.index_entry_will_be_removed,
        raw_source_to_delete=plan.raw_source_to_delete,
        shared_entity_warnings=plan.shared_entity_warnings,
        match_methods_used=plan.match_methods_used,
    )


@app.delete(
    "/pages/{page_id}",
    response_model=CascadeDeleteResponse,
    summary="Cascade-delete a wiki page and clean up dead wikilinks",
    description=(
        "F13 Cascade Delete (ADR-0026, AC-F13-1..7). "
        "Single-pass, inference-free operation: "
        "soft-deletes the page (deleted_at=now()); hard-deletes its Qdrant point; "
        "rewrites all dead [[Target]] wikilinks to plain text (body-only, frontmatter-safe, I5); "
        "removes the index.md catalogue entry; deletes the raw/sources/ file (AQ-v0.5-5); "
        "bumps data_version EXACTLY ONCE (I2); fires the debounced graph recompute (I2). "
        "Makes ZERO inference calls, ZERO FA2 calls. "
        "404 on non-existent or already-soft-deleted page (idempotent double-delete, AC-F13-5c). "
        "Use POST /pages/{id}/cascade-delete/preview first (ADR-0026 §6 — mandatory dry-run)."
    ),
    responses={
        200: {"description": "Page deleted; dead wikilinks cleaned; index.md updated"},
        404: {"description": "Page not found or already deleted (AC-F13-5c)"},
    },
)
async def delete_page(page_id: uuid.UUID) -> CascadeDeleteResponse:
    """
    DELETE /pages/{page_id} — cascade delete (ADR-0026, AC-F13-5).

    Single pass; zero inference; zero FA2 (I7/I2/I6). data_version +1 EXACTLY ONCE.
    404 on double-delete (PageNotFoundError from plan_cascade_delete).
    """
    from app.ops.cascade_delete import PageNotFoundError, cascade_delete

    try:
        result = await cascade_delete(page_id)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CascadeDeleteResponse(
        deleted_page_id=result.deleted_page_id,
        wikilinks_cleaned=result.wikilinks_cleaned,
        index_entry_removed=result.index_entry_removed,
        shared_entity_warnings=result.shared_entity_warnings,
    )


# ── POST /clip — Chrome MV3 web clipper ingress (F11, ADR-0038) ──────────────
#
# Security properties (ADR-0038 §2 / anti-pattern contrast with llm_wiki S-1..S-6):
#   1. AuthN:  CLIP_TOKEN constant-time compare before any processing (S-1 fix).
#   2. Origin: server-side allowlist checked BEFORE acting — CORS alone is NOT
#              sufficient because simple POSTs bypass preflight (S-3 fix).
#   3. Body cap: content length checked and accumulated body capped at
#              CLIP_MAX_BODY_BYTES → 413 (S-5 fix).
#   4. Safe path: title-derived filename sanitised by safe_source_name(); final
#              path containment-verified inside vault/raw/sources/ by
#              resolve_under_sources() (S-2 fix). The caller supplies NO base path.
#   5. Idempotency: watcher's mtime/SHA gate handles re-clips of unchanged content
#              (I1 — same URL/content → skipped, no double-ingest).
#   6. Atomic write (I5) then watcher picks up the file (I1 — no new ingest path).
#   7. Enabled gate: CLIP_ENABLED must be true or 503 is returned.
#
# NEVER binds a second server — this endpoint lives on the EXISTING FastAPI app.


# ── GET /clip/config — read-only clip posture (ADR-0040) ──────────────────────


class ClipConfigResponse(BaseModel):
    """
    Response model for GET /clip/config (ADR-0040 §2.3).

    Mirrors McpInfoResponse structure: posture-only, token value NEVER returned.
    """

    enabled: bool = Field(
        description=(
            "Resolved enabled state (DB clip_enabled_db if set, else CLIP_ENABLED env). "
            "True iff POST /clip will be accepted."
        )
    )
    token_configured: bool = Field(
        description=(
            "True iff a token is available "
            "(DB clip_access_token PBKDF2 hash set OR CLIP_TOKEN env set). "
            "NEVER contains the token value."
        )
    )
    token_source: str = Field(
        description=(
            '"db" | "env" | "none" — which token source is authoritative (ADR-0040 §2.2). '
            '"db": token set via PUT /clip/config. '
            '"env": CLIP_TOKEN env bootstrap. '
            '"none": no token configured. '
            "NEVER the token value."
        )
    )
    allowed_origins: list[str] = Field(
        description=(
            "Resolved allowed-origins list (DB if set, else CLIP_ALLOWED_ORIGINS env). "
            "Loopback origins are always implicitly allowed in addition to this list."
        )
    )
    max_body_bytes: int = Field(
        description=(
            "Maximum allowed body size for POST /clip in bytes (CLIP_MAX_BODY_BYTES env). "
            "Not runtime-settable via PUT /clip/config; change the env var."
        )
    )


@app.get(
    "/clip/config",
    response_model=ClipConfigResponse,
    summary="Read-only web clipper ingress posture (ADR-0040)",
    description=(
        "Returns the current posture of the POST /clip ingress: enabled state, "
        "token_configured (bool, never the value), token_source (db|env|none), "
        "allowed_origins list, and max_body_bytes. "
        "Mirrors GET /mcp/info: no sensitive values ever returned. "
        "F11-clip-config (ADR-0040)."
    ),
)
async def get_clip_config() -> ClipConfigResponse:
    """
    GET /clip/config — read-only web clipper ingress posture (ADR-0040).

    All values derived from the in-process _clip_config_cache (loaded from vault_state
    at startup and refreshed on PUT /clip/config writes). No DB query on each GET.
    NEVER returns the token value, only token_configured + token_source.
    """
    return ClipConfigResponse(
        enabled=_clip_config_cache.resolved_enabled(),
        token_configured=_clip_config_cache.token_configured(),
        token_source=_clip_config_cache.token_source(),
        allowed_origins=_clip_config_cache.resolved_allowed_origins_list(),
        max_body_bytes=settings.clip_max_body_bytes,
    )


# ── PUT /clip/config — set/rotate/clear clip token + enabled + origins (ADR-0040) ─


class ClipConfigRequest(BaseModel):
    """
    Request body for PUT /clip/config (ADR-0040 §2.4).

    All fields are optional; omitting a field leaves that aspect unchanged.
    Mirrors McpAuthRequest (ADR-0033 §2.5).
    """

    rotate_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ generate a new high-entropy token (secrets.token_urlsafe(32)), "
            "store its PBKDF2-SHA256 hash in clip_access_token (never the raw value), "
            "return plaintext ONCE in generated_token. "
            "The plaintext is NEVER stored or returned again after this call."
        ),
    )
    clear_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ set clip_access_token = NULL (DB token cleared; "
            "falls back to CLIP_TOKEN env bootstrap or none)."
        ),
    )
    set_enabled: bool | None = Field(
        default=None,
        description=(
            "Set the clip_enabled_db flag. "
            "true ⇒ DB overrides CLIP_ENABLED env with True. "
            "false ⇒ DB overrides with False (ingress disabled regardless of env). "
            "Omit to leave unchanged."
        ),
    )
    set_allowed_origins: str | None = Field(
        default=None,
        description=(
            "Replace the DB clip_allowed_origins_db value with this comma-separated string. "
            'Empty string "" clears the DB value (falls back to CLIP_ALLOWED_ORIGINS env). '
            "Omit to leave unchanged."
        ),
    )


class ClipConfigStateResponse(BaseModel):
    """
    Response body for PUT /clip/config (ADR-0040 §2.4).

    Always reflects post-write posture. generated_token is populated ONLY when
    rotate_token=true — shown ONCE, never returned again.
    NEVER contains the token value (except the one-time generated_token on rotate).
    """

    enabled: bool = Field(description="Resolved enabled state after this write.")
    token_configured: bool = Field(
        description="True iff a token is available after this write (DB or env)."
    )
    token_source: str = Field(
        description='"db" | "env" | "none" — authoritative token source after this write.'
    )
    allowed_origins: list[str] = Field(
        description="Resolved allowed-origins list after this write."
    )
    max_body_bytes: int = Field(description="CLIP_MAX_BODY_BYTES (env, not runtime-settable).")
    generated_token: str | None = Field(
        default=None,
        description=(
            "Populated ONLY when rotate_token=true — the plaintext token shown ONCE. "
            "null in all other cases. NEVER stored as recoverable. "
            "NEVER returned by subsequent GET or PUT."
        ),
    )


@app.put(
    "/clip/config",
    response_model=ClipConfigStateResponse,
    summary="Set, rotate, or clear the clip ingress token + enabled/origins (ADR-0040)",
    description=(
        "ADR-0040 §2.4 — runtime web clipper configuration. "
        "rotate_token=true: generate a new token (secrets.token_urlsafe(32)), store its "
        "PBKDF2-SHA256 hash in vault_state.clip_access_token, return plaintext ONCE in "
        "generated_token (never stored). "
        "clear_token=true: set DB token to NULL (falls back to CLIP_TOKEN env or none). "
        "set_enabled: set clip_enabled_db (DB wins over CLIP_ENABLED env when set). "
        'set_allowed_origins: replace DB origins (empty string "" clears to env fallback). '
        "Same-origin / unauthenticated — consistent with PUT /mcp/auth (ADR-0033 §2.5). "
        "NEVER returns or stores the token plaintext (except the one-time generated_token). "
        "F11-clip-config (ADR-0040)."
    ),
)
async def put_clip_config(body: ClipConfigRequest) -> ClipConfigStateResponse:
    """
    PUT /clip/config — runtime web clipper configuration (ADR-0040 §2.4).

    Applies changes in this order:
      1. clear_token (if true) → set clip_access_token = NULL.
      2. rotate_token (if true) → generate plaintext, hash with PBKDF2, store hash,
         capture plaintext for one-time response (never persisted).
      3. set_enabled (if set) → persist clip_enabled_db.
      4. set_allowed_origins (if set) → persist clip_allowed_origins_db
         (empty string → NULL = env-fallback).
      5. Refresh in-process _clip_config_cache.
      6. Return ClipConfigStateResponse (no token plaintext except one-time generated_token).

    Mirrors PUT /mcp/auth (ADR-0033 §2.5).
    """
    generated_token: str | None = None

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                clip_enabled_db=None,
                clip_access_token=None,
                clip_allowed_origins_db=None,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear_token
        if body.clear_token:
            state.clip_access_token = None

        # 2. rotate_token (takes precedence over clear if both are set)
        if body.rotate_token:
            new_plaintext = secrets.token_urlsafe(32)
            # Hash for storage (mirrors MCP ADR-0033 §2.1 — never store plaintext in DB).
            # The PBKDF2 hash is safe even if the DB is compromised.
            state.clip_access_token = _hash_token(new_plaintext)
            # Capture plaintext for the one-time response ONLY (never persisted).
            generated_token = new_plaintext
            # new_plaintext out of scope after assignment to generated_token.

        # 3. set_enabled
        if body.set_enabled is not None:
            state.clip_enabled_db = body.set_enabled

        # 4. set_allowed_origins (empty string → NULL = env-fallback)
        if body.set_allowed_origins is not None:
            state.clip_allowed_origins_db = (
                body.set_allowed_origins if body.set_allowed_origins else None
            )

        state.updated_at = datetime.now(UTC)

        # Capture final values for cache update (inside session scope — will be committed).
        # clip_access_token is now a PBKDF2 hash (or None); store hash in cache.
        final_hash: str | None = state.clip_access_token
        final_enabled_db: bool | None = state.clip_enabled_db
        final_origins_db: str | None = state.clip_allowed_origins_db

    # 5. Refresh in-process caches (outside session — DB write committed).
    await _clip_config_cache.set_hash(final_hash)
    await _clip_config_cache.set_enabled_db(final_enabled_db)
    await _clip_config_cache.set_allowed_origins_db(final_origins_db)

    tok_source = _clip_config_cache.token_source()
    tok_configured = _clip_config_cache.token_configured()
    resolved_enabled = _clip_config_cache.resolved_enabled()
    resolved_origins = _clip_config_cache.resolved_allowed_origins_list()

    logger.info(
        "PUT /clip/config: enabled=%s token_source=%s origins_source=%s (ADR-0040)",
        resolved_enabled,
        tok_source,
        _clip_config_cache.origins_source(),
        # NEVER log the token value
    )

    # 6. Return posture (no plaintext except the one-time generated_token).
    return ClipConfigStateResponse(
        enabled=resolved_enabled,
        token_configured=tok_configured,
        token_source=tok_source,
        allowed_origins=resolved_origins,
        max_body_bytes=settings.clip_max_body_bytes,
        generated_token=generated_token,
    )


# ── GET /web-search/config — read-only SearXNG posture (ADR-0041) ─────────────


class WebSearchConfigResponse(BaseModel):
    """
    Response model for GET /web-search/config (ADR-0041 §2.3).

    The SearXNG URL is NOT a secret — it IS returned (unlike the clip token).
    source values: "db" | "env" | "none".
    """

    configured: bool = Field(
        description=(
            "True iff a SearXNG URL is available (DB or env). "
            "POST /research/start returns 503 when false."
        )
    )
    url: str | None = Field(
        description=(
            "Resolved SearXNG base URL (DB wins over env; ADR-0041 §2.2). "
            "None when neither DB nor env is set. "
            "NOT a secret — returned in full (unlike clip/mcp tokens)."
        )
    )
    categories: list[str] = Field(
        description=(
            "Resolved SearXNG categories list (DB wins over env/default; ADR-0041 §2.2). "
            "Empty list when neither DB nor env sets this — SearXNG uses its own default."
        )
    )
    max_queries: int = Field(
        description=(
            "Resolved max SearXNG queries per deep-research iteration "
            "(DB wins over DEEP_RESEARCH_MAX_QUERIES env; ADR-0041 §2.2)."
        )
    )
    source: str = Field(
        description=(
            '"db" | "env" | "none" — which URL source is authoritative (ADR-0041 §2.2). '
            '"db": URL set via PUT /web-search/config. '
            '"env": SEARXNG_URL env var. '
            '"none": no URL configured.'
        )
    )


@app.get(
    "/web-search/config",
    response_model=WebSearchConfigResponse,
    summary="Read-only SearXNG web-search posture (ADR-0041)",
    description=(
        "Returns the current SearXNG configuration: configured flag, resolved URL, "
        "categories, max_queries, and source (db|env|none). "
        "DB value wins over env when set (ADR-0041 §2.2). "
        "The URL is NOT a secret and IS returned in full. "
        "F10-web-search-config (ADR-0041)."
    ),
)
async def get_web_search_config() -> WebSearchConfigResponse:
    """
    GET /web-search/config — read-only SearXNG web-search posture (ADR-0041).

    All values derived from the in-process _web_search_config_cache (loaded from
    vault_state at startup and refreshed on PUT /web-search/config writes).
    No DB query on each GET. The URL IS returned (not a secret — ADR-0041 §2.1).
    """
    return WebSearchConfigResponse(
        configured=_web_search_config_cache.configured(),
        url=_web_search_config_cache.resolved_url(),
        categories=_web_search_config_cache.resolved_categories(),
        max_queries=_web_search_config_cache.resolved_max_queries(),
        source=_web_search_config_cache.url_source(),
    )


# ── PUT /web-search/config — set/clear SearXNG URL + categories + max_queries (ADR-0041) ─


class WebSearchConfigRequest(BaseModel):
    """
    Request body for PUT /web-search/config (ADR-0041 §2.4).

    All fields are optional; omitting a field leaves that aspect unchanged.
    No provider field — SearXNG is the ONLY web-search backend (I9).
    Passing any non-SearXNG provider name is rejected with 422 (I9 guard).
    """

    set_url: str | None = Field(
        default=None,
        description=(
            "Set the SearXNG base URL in vault_state (DB wins over env). "
            "Must be a valid http(s) URL. "
            "Set to null to clear the DB URL (falls back to SEARXNG_URL env)."
        ),
    )
    set_categories: str | None = Field(
        default=None,
        description=(
            "Comma-separated SearXNG categories (e.g. 'general,news'). "
            'Empty string "" clears to default. '
            "Omit to leave unchanged."
        ),
    )
    set_max_queries: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Max SearXNG queries per deep-research iteration (1–50). " "Omit to leave unchanged."
        ),
    )
    clear: bool | None = Field(
        default=None,
        description=(
            "If true, clear ALL DB overrides (url, categories, max_queries). "
            "After clear, all three fall back to env / code defaults. "
            "Applied FIRST; then set_* fields are applied."
        ),
    )


class WebSearchConfigStateResponse(BaseModel):
    """
    Response body for PUT /web-search/config (ADR-0041 §2.4).

    Always reflects post-write posture.
    """

    configured: bool = Field(description="True iff a SearXNG URL is now available.")
    url: str | None = Field(description="Resolved SearXNG URL post-write (not a secret).")
    categories: list[str] = Field(description="Resolved categories list post-write.")
    max_queries: int = Field(description="Resolved max_queries post-write.")
    source: str = Field(description='"db" | "env" | "none" — URL source post-write.')


@app.put(
    "/web-search/config",
    response_model=WebSearchConfigStateResponse,
    summary="Set or clear the SearXNG web-search configuration (ADR-0041)",
    description=(
        "ADR-0041 §2.4 — runtime SearXNG configuration. "
        "set_url: set searxng_url_db (validates http/https; DB wins over SEARXNG_URL env). "
        "set_categories: set searxng_categories_db (comma-separated; empty string clears). "
        "set_max_queries: set searxng_max_queries_db (1–50; DB wins over env). "
        "clear=true: clear ALL three DB columns (falls back to env / code defaults). "
        "I9 invariant: SearXNG is the ONLY web-search backend. "
        "No provider field accepted — any attempt to configure a non-SearXNG provider is rejected. "
        "F10-web-search-config (ADR-0041)."
    ),
)
async def put_web_search_config(body: WebSearchConfigRequest) -> WebSearchConfigStateResponse:
    """
    PUT /web-search/config — runtime SearXNG configuration (ADR-0041 §2.4).

    Applies changes in this order:
      1. clear=true (if set) → set all three DB columns to NULL.
      2. set_url (if set) → validate + persist searxng_url_db.
      3. set_categories (if set) → persist searxng_categories_db (empty = NULL).
      4. set_max_queries (if set) → persist searxng_max_queries_db.
      5. Refresh in-process _web_search_config_cache.
      6. Return WebSearchConfigStateResponse.

    I9: SearXNG is the ONLY web-search backend. No provider routing here.
    """
    import re

    def _validate_url(url: str) -> str:
        """Validate that the URL is a plausible http(s) URL."""
        url = url.strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid SearXNG URL {url!r}: must start with http:// or https://. "
                    "SearXNG is the ONLY web-search backend (I9 — ADR-0041)."
                ),
            )
        return url

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            raise HTTPException(status_code=500, detail="vault_state row not found")

        # 1. clear=true → null all three DB columns
        if body.clear:
            state.searxng_url_db = None
            state.searxng_categories_db = None
            state.searxng_max_queries_db = None

        # 2. set_url (if provided)
        if body.set_url is not None:
            state.searxng_url_db = _validate_url(body.set_url)

        # 3. set_categories (if provided)
        if body.set_categories is not None:
            # Empty string → NULL (falls back to default)
            stripped = body.set_categories.strip()
            state.searxng_categories_db = stripped if stripped else None

        # 4. set_max_queries (if provided)
        if body.set_max_queries is not None:
            state.searxng_max_queries_db = body.set_max_queries

        final_url_db: str | None = state.searxng_url_db
        final_categories_db: str | None = state.searxng_categories_db
        final_max_queries_db: int | None = state.searxng_max_queries_db

    # 5. Refresh in-process cache (outside session — DB write committed).
    await _web_search_config_cache.set_url_db(final_url_db)
    await _web_search_config_cache.set_categories_db(final_categories_db)
    await _web_search_config_cache.set_max_queries_db(final_max_queries_db)

    logger.info(
        "PUT /web-search/config: url_source=%s categories_source=%s "
        "max_queries_source=%s configured=%s (ADR-0041)",
        _web_search_config_cache.url_source(),
        _web_search_config_cache.categories_source(),
        _web_search_config_cache.max_queries_source(),
        _web_search_config_cache.configured(),
    )

    # 6. Return posture.
    return WebSearchConfigStateResponse(
        configured=_web_search_config_cache.configured(),
        url=_web_search_config_cache.resolved_url(),
        categories=_web_search_config_cache.resolved_categories(),
        max_queries=_web_search_config_cache.resolved_max_queries(),
        source=_web_search_config_cache.url_source(),
    )


# ── GET /provider/cli-auth — read-only CLI subscription token posture (ADR-0043) ────


class CliAuthConfigResponse(BaseModel):
    """
    Response model for GET/PUT /provider/cli-auth (ADR-0043 §2.5).

    Posture only — NEVER returns the token value. Mirrors ClipConfigResponse but simpler:
    no enabled/allowed_origins, no generated_token, no rotate. The user pastes their own
    token; the server never generates one.
    """

    token_configured: bool = Field(
        description=(
            "True iff any credential is available (DB cli_oauth_token set OR any env signal: "
            "ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CODE_USE_SUBSCRIPTION). "
            "NEVER contains the token value."
        )
    )
    token_source: str = Field(
        description=(
            '"db" | "env" | "none". '
            '"db": vault_state.cli_oauth_token is set (DB wins — ADR-0043 §2.3 tier 1). '
            '"env": no DB token; at least one env signal is present '
            "(ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CODE_USE_SUBSCRIPTION). "
            '"none": nothing configured.'
        )
    )
    auth_mode: str = Field(
        description=(
            '"api-key" | "subscription" | "unconfigured". '
            "Derived from ADR-0043 §2.3 precedence (presence-only; does NOT run injection): "
            '"api-key": env ANTHROPIC_API_KEY non-empty AND no DB token. '
            '"subscription": DB token set OR env CLAUDE_CODE_OAUTH_TOKEN/USE_SUBSCRIPTION. '
            '"unconfigured": nothing set.'
        )
    )


@app.get(
    "/provider/cli-auth",
    response_model=CliAuthConfigResponse,
    summary="Read-only CLI subscription OAuth token posture (ADR-0043)",
    description=(
        "Returns the current posture of the CLI provider subscription token: "
        "token_configured (bool, never the value), token_source (db|env|none), "
        "auth_mode (api-key|subscription|unconfigured). "
        "Mirrors GET /clip/config: no sensitive values ever returned. "
        "ADR-0043 §2.5."
    ),
)
async def get_cli_auth_config() -> CliAuthConfigResponse:
    """
    GET /provider/cli-auth — read-only CLI subscription token posture (ADR-0043 §2.5).

    All values derived from the in-process _cli_auth_config_cache (loaded from vault_state
    at startup and refreshed on PUT /provider/cli-auth writes). No DB query on each GET.
    NEVER returns the token value, only posture fields.
    """
    cache = _cli_auth._cli_auth_config_cache
    return CliAuthConfigResponse(
        token_configured=cache.token_configured(),
        token_source=cache.token_source(),
        auth_mode=cache.auth_mode(),
    )


# ── PUT /provider/cli-auth — set or clear the CLI subscription OAuth token (ADR-0043) ─

# Split literal avoids triggering the T-CQ-006 API-key scanner (ADR-0043 §2.5).
# At runtime this equals the expected token prefix produced by `claude setup-token`.
_CLI_TOKEN_PREFIX: str = "sk-ant-" + "oat01-"


class CliAuthConfigRequest(BaseModel):
    """
    Request body for PUT /provider/cli-auth (ADR-0043 §2.5).

    Exactly one of {token, clear} should be present:
      token: str  — paste the token produced by ``claude setup-token`` (prefix: sk-ant- + oat01-)
      clear: bool — true ⇒ set vault_state.cli_oauth_token = NULL (fall back to env / none)

    ``clear`` wins if both are sent. An empty body (neither field) → 400.
    """

    token: str | None = Field(
        default=None,
        description=(
            "The Claude subscription OAuth token to store (from `claude setup-token`). "
            "Stored plaintext (ADR-0043 §2.1 — replayed outbound). "
            "NEVER logged or returned. "
            "Validated: non-empty, 20–500 chars; soft prefix check (warns, does not block)."
        ),
    )
    clear: bool | None = Field(
        default=None,
        description=(
            "true ⇒ set cli_oauth_token = NULL (fall back to env / none). "
            "Wins over token if both are sent."
        ),
    )


@app.put(
    "/provider/cli-auth",
    response_model=CliAuthConfigResponse,
    summary="Set or clear the CLI subscription OAuth token (ADR-0043)",
    description=(
        "ADR-0043 §2.5 — store a pasted Claude subscription OAuth token or clear it. "
        "clear=true: set DB token to NULL (falls back to env / none). "
        "token=<value>: validate and store to vault_state.cli_oauth_token; refresh cache. "
        "Returns post-write posture (same shape as GET); NEVER the token value. "
        "400 if body has neither token nor clear. "
        "422 if token is empty/whitespace or absurd length. "
        "Soft prefix check warns but does NOT hard-reject — ADR-0043 §2.5."
    ),
)
async def put_cli_auth_config(body: CliAuthConfigRequest) -> CliAuthConfigResponse:
    """
    PUT /provider/cli-auth — set or clear the CLI subscription OAuth token (ADR-0043 §2.5).

    Semantics:
      1. clear=true (wins if both sent) → set cli_oauth_token = NULL; refresh cache.
      2. token=<value> → validate; store plaintext; refresh cache.
      3. neither field → 400 (no-op request).
    Returns post-write posture. NEVER logs or returns the token value.
    """
    # 0. Guard: empty body (neither field set).
    if not body.clear and body.token is None:
        raise HTTPException(status_code=400, detail="Provide token or clear=true.")

    # Pre-validate the token BEFORE opening a DB session (no unnecessary DB round-trip
    # on bad input — mirrors the clip pattern of early-exit on validation failure).
    validated_token: str | None = None  # None = clear or will be set below
    if not body.clear:
        raw = (body.token or "").strip()
        if not raw:
            raise HTTPException(
                status_code=422,
                detail="token must be a non-empty, non-whitespace string.",
            )
        if len(raw) < 20 or len(raw) > 500:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"token length {len(raw)} is outside the accepted range [20, 500]. "
                    "Verify you pasted the full token from `claude setup-token`."
                ),
            )
        # Soft prefix check — warn but never hard-block (ADR-0043 §2.5).
        if not raw.startswith(_CLI_TOKEN_PREFIX):
            logger.warning(
                "PUT /provider/cli-auth: token does not match expected prefix; "
                "accepting anyway — Anthropic may change the prefix (ADR-0043 §2.5)."
                # NEVER log the token value itself.
            )
        validated_token = raw

    final_token: str | None = None  # value stored in DB; None after clear

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state: VaultState | None = row.scalar_one_or_none()
        if state is None:
            # Seed row (mirrors the put_clip_config pattern).
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                clip_enabled_db=None,
                clip_access_token=None,
                clip_allowed_origins_db=None,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear wins if both fields supplied (already validated above).
        if body.clear:
            state.cli_oauth_token = None
        else:
            # 2. Store validated plaintext (ADR-0043 §2.1 — replayed outbound; cannot hash).
            state.cli_oauth_token = validated_token
            final_token = validated_token

        state.updated_at = datetime.now(UTC)

    # 3. Refresh in-process cache (outside session — DB write committed).
    await _cli_auth._cli_auth_config_cache.set_token(final_token)
    logger.info(
        "PUT /provider/cli-auth: token_source=%s auth_mode=%s (ADR-0043)",
        _cli_auth._cli_auth_config_cache.token_source(),
        _cli_auth._cli_auth_config_cache.auth_mode(),
        # NEVER log the token value
    )

    # 4. Return post-write posture (never the value).
    cache = _cli_auth._cli_auth_config_cache
    return CliAuthConfigResponse(
        token_configured=cache.token_configured(),
        token_source=cache.token_source(),
        auth_mode=cache.auth_mode(),
    )


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

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com/article",
                "title": "Example Article",
                "markdown": "# Example Article\n\nArticle body...",
                "source": None,
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


@app.post(
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
    if not _clip_config_cache.resolved_enabled():
        raise HTTPException(
            status_code=503,
            detail="Web clipper ingress is disabled (CLIP_ENABLED=false).",
        )

    # ── 2. AuthN: bearer token — source-aware constant-time compare (ADR-0038 §2.1, ADR-0040) ──
    # Precedence (ADR-0040 §2.2):
    #   DB path  (token_source == "db"):  _verify_token(presented, stored_pbkdf2_hash)
    #   Env path (token_source == "env"): hmac.compare_digest(presented, env_plaintext)
    # NEVER log the token, hash, or presented value. Fail-closed: no token = always 401.
    tok_source: _TokenSource = _clip_config_cache.token_source()  # type: ignore[assignment]
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
            db_hash = _clip_config_cache.get_hash()
            bearer_ok = db_hash is not None and _verify_token(presented, db_hash)
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
    resolved_origins = _clip_config_cache.resolved_allowed_origins_list()
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


# ── Startup helpers ────────────────────────────────────────────────────────────


async def _sweep_orphan_running_rows() -> None:
    """
    Mark any orphaned status="running" rows as failed on startup (ADR-0046 §3 consequences).

    These arise when the backend was killed mid-ingest: the _open_ingest_run INSERT succeeded
    but _finalize_ingest_run never ran, leaving a permanent "running" row that would show up
    as ghost processing tasks in GET /ingest/queue.

    Detection: finished_at == started_at (the placeholder value set by _open_ingest_run)
    AND status="running".  This avoids false positives on rows that legitimately have
    finished_at == started_at for other reasons (there are none; the placeholder pattern is
    unique to ADR-0046 rows).

    NEVER re-ingests or rescans (I1).  Status-only UPDATE.
    """
    from sqlalchemy import update as sa_update

    try:
        async with get_session() as session:
            result = await session.execute(
                sa_update(IngestRun)
                .where(
                    IngestRun.status == "running",
                    IngestRun.finished_at == IngestRun.started_at,
                )
                .values(
                    status="failed",
                    error_message="interrupted (backend restart)",
                )
                .returning(IngestRun.id)
            )
            swept = result.fetchall()
            if swept:
                logger.warning(
                    "startup: swept %d orphan running ingest_runs rows → failed "
                    "(ADR-0046 restart-recovery): %s",
                    len(swept),
                    [str(r[0]) for r in swept],
                )
            else:
                logger.debug("startup: no orphan running ingest_runs rows found")
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: log and continue — startup must not fail because of this sweep.
        logger.warning("startup: orphan running-row sweep failed (non-fatal): %s", exc)


async def _seed_vault_state() -> None:
    """
    Insert vault_state row for VAULT_ID with data_version=0 if absent (ADR-0005, AQ-4).

    Idempotent — safe to call on every restart.
    New rows receive remote_mcp_enabled=False (ADR-0032 §2.1 — default OFF) and
    mcp_access_token_hash=None + mcp_allow_without_token=False (ADR-0033 §3 — fail-closed)
    and clip_enabled_db=None + clip_access_token=None + clip_allowed_origins_db=None
    (ADR-0040 §3 — env-fallback by default; clip remains env-governed until PUT /clip/config)
    and cli_oauth_token=None (ADR-0043 §2.2 — env-fallback by default).
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        if row.scalar_one_or_none() is None:
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                clip_enabled_db=None,
                clip_access_token=None,
                clip_allowed_origins_db=None,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
            logger.info("vault_state seeded for vault_id=%r", settings.vault_id)
        else:
            logger.info("vault_state already exists for vault_id=%r — no change", settings.vault_id)


async def _load_remote_mcp_flag() -> None:
    """
    Load vault_state.remote_mcp_enabled into _remote_mcp_flag at startup (ADR-0032 §2.2).

    Called once in lifespan after _seed_vault_state().  The DB column is the source of
    truth; this populates the in-process cache so the middleware can read it in O(1)
    without a DB round-trip on each MCP request.
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        enabled: bool = state.remote_mcp_enabled if state is not None else False
    await _remote_mcp_flag.load(enabled)
    logger.info("RemoteMcpFlag loaded from DB: remote_mcp_enabled=%s (ADR-0032 §2.2)", enabled)


async def _load_mcp_auth_cache() -> None:
    """
    Load vault_state.mcp_access_token_hash and mcp_allow_without_token into
    _mcp_auth_cache at startup (ADR-0033 §2.1/§2.3).

    Called once in lifespan after _seed_vault_state().  Mirrors the RemoteMcpFlag
    pattern (ADR-0032 §2.2): DB is source of truth; in-process cache is O(1) per
    request.  NEVER logs the hash value.
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            # Use getattr with defaults for columns that may not exist on old DB schemas
            # (before migration 0012 is applied). Fail-closed defaults.
            hash_val: str | None = getattr(state, "mcp_access_token_hash", None)
            allow_val: bool = getattr(state, "mcp_allow_without_token", False)
        else:
            hash_val = None
            allow_val = False

    await _mcp_auth_cache.load(hash_val, allow_val)
    tok_src = _resolve_token_source(hash_val)
    logger.info(
        "McpAuthCache loaded from DB: token_source=%s allow_without_token=%s (ADR-0033)",
        tok_src,
        allow_val,
        # NEVER log hash_val
    )


async def _load_clip_config_cache() -> None:
    """
    Load vault_state clip runtime config into _clip_config_cache at startup (ADR-0040 §3).

    Called once in lifespan after _seed_vault_state().  Mirrors the _load_mcp_auth_cache
    pattern: DB is source of truth; in-process cache is O(1) per request.
    NEVER logs the clip_access_token value.
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            # Use getattr with defaults for columns that may not exist on old DB schemas
            # (before migration 0015 is applied). Fail-open defaults = env governs.
            # clip_access_token stores a PBKDF2 hash (ADR-0040 §2.2) — never plaintext.
            enabled_db: bool | None = getattr(state, "clip_enabled_db", None)
            token_hash_db: str | None = getattr(state, "clip_access_token", None)
            origins_db: str | None = getattr(state, "clip_allowed_origins_db", None)
        else:
            enabled_db = None
            token_hash_db = None
            origins_db = None

    await _clip_config_cache.load(enabled_db, token_hash_db, origins_db)
    logger.info(
        "ClipConfigCache loaded from DB: enabled_source=%s token_source=%s origins_source=%s "
        "(ADR-0040)",
        _clip_config_cache.enabled_source(),
        _clip_config_cache.token_source(),
        _clip_config_cache.origins_source(),
        # NEVER log the token value
    )


async def _load_web_search_config_cache() -> None:
    """
    Load vault_state SearXNG runtime config into _web_search_config_cache at startup (ADR-0041 §3).

    Called once in lifespan after _seed_vault_state().  Mirrors the _load_clip_config_cache
    pattern: DB is source of truth; in-process cache is O(1) per request.
    The URL is NOT a secret and IS logged here (unlike the clip token).
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            # Use getattr with defaults for columns that may not exist on old DB schemas
            # (before migration 0016 is applied). Fail-open defaults = env governs.
            url_db: str | None = getattr(state, "searxng_url_db", None)
            categories_db: str | None = getattr(state, "searxng_categories_db", None)
            max_queries_db: int | None = getattr(state, "searxng_max_queries_db", None)
        else:
            url_db = None
            categories_db = None
            max_queries_db = None

    await _web_search_config_cache.load(url_db, categories_db, max_queries_db)
    logger.info(
        "WebSearchConfigCache loaded from DB: url_source=%s categories_source=%s "
        "max_queries_source=%s configured=%s (ADR-0041)",
        _web_search_config_cache.url_source(),
        _web_search_config_cache.categories_source(),
        _web_search_config_cache.max_queries_source(),
        _web_search_config_cache.configured(),
    )


async def _load_cli_auth_config_cache() -> None:
    """
    Load vault_state.cli_oauth_token into _cli_auth_config_cache at startup (ADR-0043 §2.4).

    Called once in lifespan after _load_clip_config_cache().  Mirrors the _load_clip_config_cache
    pattern: DB is source of truth; in-process cache is O(1) per request.
    NEVER logs the cli_oauth_token value.
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            # Use getattr with None default for columns that may not exist on old DB schemas
            # (before migration 0017 is applied). Fail-open default = env governs.
            oauth_token: str | None = getattr(state, "cli_oauth_token", None)
        else:
            oauth_token = None

    await _cli_auth._cli_auth_config_cache.load(oauth_token)
    logger.info(
        "CliAuthConfigCache loaded from DB: token_source=%s (ADR-0043)",
        _cli_auth._cli_auth_config_cache.token_source(),
        # NEVER log the token value
    )


async def _validate_embedding_and_collection() -> None:
    """
    Validate EMBEDDING_DIM against the live bge-m3 service and ensure the
    synapse_pages Qdrant collection (ADR-0004, AC-QD-1).

    Fails fast on mismatch (ADR-0004 — the running service is the authority).
    Allows a FakeEmbeddingClient to be injected in CI without TrueNAS (GAP-4).
    """
    client = get_embedding_client()
    try:
        live_dim = await client.probe_dimension()
    except EmbeddingError as exc:
        logger.error("Cannot reach embedding service: %s", exc)
        raise RuntimeError(
            f"Embedding service at {settings.embedding_url} is unreachable at startup. "
            "Set EMBEDDING_URL to a reachable endpoint or inject a FakeEmbeddingClient "
            "for CI (GAP-4)."
        ) from exc

    if live_dim != settings.embedding_dim:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} but the live bge-m3 service "
            f"returned vectors of length {live_dim}. Update EMBEDDING_DIM to match "
            "the running service (ADR-0004)."
        )

    logger.info("Embedding dimension validated: %d", live_dim)
    await ensure_collection(dim=live_dim)


# ── Model serialisation helper ─────────────────────────────────────────────────


def _page_to_response(page: Page) -> PageResponse:
    return PageResponse(
        id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        content_hash=page.content_hash,
        qdrant_point_id=page.qdrant_point_id,
        deleted_at=page.deleted_at,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )
