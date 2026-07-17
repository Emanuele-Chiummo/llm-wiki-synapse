"""
app.runtime_state — process-runtime state & singletons, split out of app.main (BE-ARCH-3).

This module owns the RUNTIME STATE that used to live inside ``app.main`` but has nothing
to do with FastAPI app assembly (middleware wiring, router mounting, lifespan). Keeping it
here lets the per-domain routers read the state directly with TYPED accessors instead of
each duplicating a ``_LazyMain`` ``sys.modules["app.main"]`` proxy (BE-REFAC-1 precursor).

What lives here (single source of truth):
  * MCP mount constants + private-CIDR source classification (ADR-0032/0033).
  * PBKDF2 token hashing / verification helpers (ADR-0033 §2.1).
  * The DB-backed flag & config caches (+ their process singletons):
      - ``RemoteMcpFlag``            → remote_mcp_flag / mcp_write_flag
      - ``McpAuthCache``            → mcp_auth_cache
      - ``ClipConfigCache``        → clip_config_cache
      - ``WebSearchConfigCache``   → web_search_config_cache
  * ``BearerAuthMiddleware`` — the MCP access gate (ADR-0033 §2.4).

What stays in ``app.main`` (legitimately part of app assembly / lifespan):
  * The FastAPI ``app`` object, middleware registration, router mounting.
  * The lifespan and its thin startup cache-LOADERS (``_load_*``) — they read the DB and
    populate the singletons owned here. They remain in ``app.main`` because the test-suite
    monkeypatches them (and ``app.main.get_session``) on the ``app.main`` module.
  * The app-lifespan singletons: graph cache, import/ops schedulers, ``_started_at``.

Legacy ``app.main.*`` seam: ``app.main`` re-imports every public name below under its old
private alias, so existing ``from app.main import X`` / ``patch("app.main.X")`` call-sites keep
working for ONE release. The ``bridge`` accessors at the bottom read the app-lifespan
singletons (and the ``get_session`` factory) back out of ``app.main`` dynamically so those
monkeypatch seams are preserved too. Both are slated for removal in 2.0.0.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import logging
import secrets
import sys
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.client_ip import resolve_source_ip
from app.config import settings

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.graph.cache import GraphCache
    from app.import_scheduler import ImportScheduler
    from app.models import ProviderConfig
    from app.ops_scheduler import OpsScheduler

logger = logging.getLogger(__name__)

# ── MCP mount-path constant (ADR-0032 I6; retained ADR-0033) ─────────────────
# Single source of truth: used by the mount, the middleware gate, and /mcp/info.
# Never duplicate this literal elsewhere (I6).
MCP_MOUNT_PATH: str = "/mcp/server"

# ── Private CIDR ranges for source classification (ADR-0033 §2.3) ─────────────
# Named constant (I6 — no scattered literals). Used by classify_source() to
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


def ip_is_private(ip_str: str) -> bool:
    """
    Return True iff the given IP string falls in MCP_PRIVATE_CIDRS.

    Fail-safe: parse errors or unexpected types return False (treated as PUBLIC).
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in MCP_PRIVATE_CIDRS)
    except (ValueError, TypeError):
        return False  # unknown → PUBLIC (fail-safe)


def classify_source(scope: Scope) -> bool:
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
      resolve_source_ip). An untrusted peer forging XFF is classified by peer IP.
    - Fail-safe: uncertain → PUBLIC (require token).
    """
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))

    # Check for Cloudflare edge headers (PUBLIC signal — fail-safe: presence → PUBLIC)
    if b"cf-connecting-ip" in headers or b"cf-ray" in headers:
        return True  # PUBLIC

    # Resolve source IP
    source_ip = resolve_source_ip(scope)
    if source_ip is None:
        return True  # cannot resolve → PUBLIC (fail-safe)

    # Private CIDR check
    if ip_is_private(source_ip):
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


def hash_token(plaintext: str) -> str:
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


def verify_token(plaintext: str, stored_hash: str) -> bool:
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

TokenSource = Literal["db", "env", "none"]


def resolve_token_source(db_hash: str | None) -> TokenSource:
    """
    Determine which token is authoritative (ADR-0033 §2.1 precedence).

    DB hash set → "db"; else env token set → "env"; else → "none".
    """
    if db_hash is not None:
        return "db"
    if settings.mcp_auth_token:
        return "env"
    return "none"


def token_configured(db_hash: str | None) -> bool:
    """True iff a token is available (DB hash or env bootstrap)."""
    return resolve_token_source(db_hash) != "none"


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


class McpAuthCache:
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


class ClipConfigCache:
    """
    In-process cache for vault_state clip runtime config columns (ADR-0040 §3).

    Loaded from vault_state at startup; refreshed on PUT /clip/config writes.
    The middleware / handler reads all three O(1) per request (no DB round-trip).
    Precedence (DB wins when set, else env fallback — ADR-0040 §2.2):
      clip_enabled:        DB clip_enabled_db (if not None) else CLIP_ENABLED env
      clip_token:          DB clip_access_token hash (if not None) else CLIP_TOKEN env plaintext
      clip_allowed_origins: DB clip_allowed_origins_db (if not None) else CLIP_ALLOWED_ORIGINS env

    Token storage strategy (mirrors McpAuthCache / ADR-0033 §2.1):
      - DB path: PBKDF2-SHA256 hash stored in vault_state.clip_access_token;
        verification via verify_token(presented, stored_hash) (constant-time).
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

        NEVER log or return this to callers. Use only for verify_token().
        """
        return self._hash

    def resolved_allowed_origins_list(self) -> list[str]:
        """Return DB origins list if set, else env list (settings.clip_allowed_origins_list)."""
        if self._allowed_origins_db is not None:
            return [o.strip() for o in self._allowed_origins_db.split(",") if o.strip()]
        return settings.clip_allowed_origins_list

    # ── Source helpers ────────────────────────────────────────────────────────

    def token_source(self) -> TokenSource:
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


class WebSearchConfigCache:
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


@dataclass(frozen=True)
class ApiTokenEntry:
    """
    In-process, read-only view of one active ``api_tokens`` row (PF-AUTH-1, 1.9.4 W4).

    Holds ``secret_hash`` (PBKDF2 string, never plaintext) purely for verify_token() —
    it is never logged or returned to any caller.
    """

    id: str
    label: str
    secret_hash: str
    vault_id: str | None
    read_only: bool


class ApiTokenCache:
    """
    In-process cache of ACTIVE (non-revoked) ``api_tokens`` rows (PF-AUTH-1, 1.9.4 W4).

    Loaded at startup from the DB (``revoked_at IS NULL``); refreshed on
    POST /config/api-tokens (add) and DELETE /config/api-tokens/{id} (remove).
    SynapseAuthMiddleware reads this O(1)-per-entry per request — no DB round-trip in
    the hot auth path except the (best-effort) last_used_at write after a match.

    ``find_match`` is O(n) over the active token set (PBKDF2 verification has no way to
    index by plaintext) — expected to stay small (operator-issued tokens, not per-user).
    NEVER exposes secret_hash to any caller outside verify_token().
    """

    def __init__(self) -> None:
        self._entries: dict[str, ApiTokenEntry] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def load(self, entries: list[ApiTokenEntry]) -> None:
        """Replace the full active-entry set (startup or full reload)."""
        async with self._lock:
            self._entries = {e.id: e for e in entries}

    async def add(self, entry: ApiTokenEntry) -> None:
        """Add (or replace) one entry — called right after a successful DB insert."""
        async with self._lock:
            self._entries[entry.id] = entry

    async def revoke(self, token_id: str) -> None:
        """Remove one entry from the active set — called right after a DB soft-delete."""
        async with self._lock:
            self._entries.pop(token_id, None)

    def find_match(self, presented: str) -> ApiTokenEntry | None:
        """
        Return the active entry whose secret_hash verifies against ``presented``, else None.

        NEVER logs ``presented`` or any entry's secret_hash.
        """
        for entry in self._entries.values():
            if verify_token(presented, entry.secret_hash):
                return entry
        return None


# ── Module-level singletons — initialised at import, populated in lifespan loaders ─
remote_mcp_flag: RemoteMcpFlag = RemoteMcpFlag()
# ADR-0072 §2: in-process cache for vault_state.remote_mcp_write_enabled.
# Reuses RemoteMcpFlag (generic boolean holder). Loaded at startup; refreshed by
# PUT /mcp/remote-write. write_enabled_getter injected into build_http_mcp so
# mcp/server.py never imports main.py (would be circular).
mcp_write_flag: RemoteMcpFlag = RemoteMcpFlag()
mcp_auth_cache: McpAuthCache = McpAuthCache()
clip_config_cache: ClipConfigCache = ClipConfigCache()
web_search_config_cache: WebSearchConfigCache = WebSearchConfigCache()
# PF-AUTH-1 (1.9.4 W4): in-process cache of active (non-revoked) api_tokens rows.
api_token_cache: ApiTokenCache = ApiTokenCache()


async def bump_api_token_last_used(token_id: str) -> None:
    """
    Persist ``api_tokens.last_used_at = now()`` for the given row id (PF-AUTH-1).

    Called (best-effort, errors swallowed by the caller) from SynapseAuthMiddleware right
    after a scoped-token match. A tiny, isolated helper — not a route handler — so it can be
    awaited from middleware without importing app.main (would be circular).
    """
    import uuid as _uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import update  # noqa: PLC0415

    from app.models import ApiToken  # noqa: PLC0415

    async with get_session() as session:
        await session.execute(
            update(ApiToken)
            .where(ApiToken.id == _uuid.UUID(token_id))
            .values(last_used_at=datetime.now(UTC))
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


class BearerAuthMiddleware:
    """
    MCP access gate — ADR-0033 §2.4 decision table.

    Formerly a static-token-only guard (ADR-0029/0032); now source-aware with
    allow-without-token support. Renamed conceptually the "MCP access gate" but
    the class is kept (aliased ``_BearerAuthMiddleware`` in app.main) for
    test-import compatibility.

    Parameters
    ----------
    app : ASGIApp
        The wrapped FastMCP sub-app.
    token : str
        The BOOTSTRAP plaintext env token (MCP_AUTH_TOKEN); used only when the DB
        hash cache holds None. May be empty string when unset (never compared then).
    flag : RemoteMcpFlag
        In-process cache of remote_mcp_enabled.
    auth_cache : McpAuthCache
        In-process cache of mcp_access_token_hash + mcp_allow_without_token.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        flag: RemoteMcpFlag,
        auth_cache: McpAuthCache | None = None,
    ) -> None:
        self._app = app
        self._token = token  # env bootstrap (plaintext; may be "")
        self._flag = flag
        self._auth_cache: McpAuthCache = auth_cache if auth_cache is not None else McpAuthCache()

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
        tok_configured = token_configured(db_hash)
        tok_source = resolve_token_source(db_hash)

        if bearer_presented is not None:
            bearer_ok = self._verify_bearer(bearer_presented, db_hash, env_token, tok_source)
            if bearer_ok:
                await self._app(scope, receive, send)
                return

        # ── Step 4: source classification ──────────────────────────────────────
        is_public = classify_source(scope)
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
        tok_source: TokenSource,
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
            return verify_token(candidate, db_hash)
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


# ── Bridge to app.main-owned lifespan singletons & legacy test seams ──────────
# The app-lifespan singletons (graph cache, import/ops schedulers, _started_at) and the
# DB ``get_session`` factory are created/owned by ``app.main`` — they are genuinely part of
# app assembly / lifespan. Routers read them through these TYPED accessors instead of the
# old duplicated ``_LazyMain`` proxy. The dynamic ``sys.modules["app.main"]`` lookup also
# preserves the historical ``patch("app.main.<name>")`` monkeypatch seam (removed in 2.0).


def _main() -> object:
    """Return the live ``app.main`` module object (imported by the time any route runs)."""
    return sys.modules["app.main"]


def get_session() -> AbstractAsyncContextManager[AsyncSession]:
    """DB session context manager, honouring the ``app.main.get_session`` test seam."""
    return _main().get_session()  # type: ignore[attr-defined, no-any-return]


def graph_cache() -> GraphCache | None:
    """The lifespan-owned GraphCache singleton (None before startup / in some tests)."""
    return getattr(_main(), "_graph_cache", None)


def set_graph_cache(cache: GraphCache | None) -> None:
    """Set the lifespan-owned GraphCache singleton on app.main (lazy-init from routers)."""
    _main()._graph_cache = cache  # type: ignore[attr-defined]


def import_scheduler() -> ImportScheduler | None:
    """The lifespan-owned ImportScheduler singleton (None before startup)."""
    return getattr(_main(), "_import_scheduler", None)


def ops_scheduler() -> OpsScheduler | None:
    """The lifespan-owned OpsScheduler singleton (None before startup)."""
    return getattr(_main(), "_ops_scheduler", None)


def started_at() -> datetime:
    """Process start timestamp (set/refreshed by the lifespan)."""
    return _main()._started_at  # type: ignore[attr-defined, no-any-return]


def resolve_backend_version() -> str:
    """Backend version string (delegates to app.main._resolve_backend_version)."""
    return _main()._resolve_backend_version()  # type: ignore[attr-defined, no-any-return]


def provider_config_model() -> type[ProviderConfig]:
    """The ProviderConfig ORM class via the ``app.main.ProviderConfig`` constructor seam."""
    return _main().ProviderConfig  # type: ignore[attr-defined, no-any-return]
