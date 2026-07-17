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
  POST /review/queue/bulk-resolve  — id-list bulk resolve (skip|dismiss); cap 200; B5/D2
  PATCH /review/queue/{id}         — resolve or reopen single item; B5/D2 llm_wiki parity
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
  POST /config/api-tokens          — create a scoped API token; plaintext shown ONCE (PF-AUTH-1)
  GET  /config/api-tokens          — list active tokens (no secret) (PF-AUTH-1)
  DELETE /config/api-tokens/{id}   — revoke (soft-delete) a token (PF-AUTH-1)

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
import importlib.metadata
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from starlette.types import ASGIApp

from app import cli_auth as _cli_auth
from app.auth import SynapseAuthMiddleware
from app.config import settings
from app.config_overrides import (
    effective_bool,
    load_overrides,
)
from app.db import dispose_engine, get_session
from app.embeddings import EmbeddingError, aclose_embedding_client, get_embedding_client
from app.errors import register_exception_handlers
from app.graph.cache import GraphCache
from app.graph.engine import GraphEngine
from app.import_scheduler import ImportScheduler
from app.mcp.server import build_http_mcp
from app.models import (
    IngestRun,
    VaultState,
)
from app.ops_scheduler import OpsScheduler
from app.qdrant_client import ensure_collection
from app.rate_limit import AuthFailureRateLimitMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.sources import router as sources_router
from app.vault import bootstrap_vault
from app.watcher import start_watcher, stop_watcher

# ── Module-level singletons (initialised in lifespan) ─────────────────────────
_graph_cache: GraphCache | None = None
_import_scheduler: ImportScheduler | None = None
_ops_scheduler: OpsScheduler | None = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Runtime state moved to app.runtime_state (BE-ARCH-3) ──────────────────
# The MCP/clip/web-search DB-backed caches, PBKDF2 token helpers, source
# classification, and the MCP access-gate middleware now live in
# app.runtime_state (typed, no _LazyMain proxy). They are re-imported here under
# their historical private names so existing `from app.main import X` /
# `patch("app.main.X")` call-sites keep working for one release (removed in
# 2.0.0 — BE-ARCH-3).
# Client-IP resolution lives in app.client_ip (shared with app.rate_limit / runtime_state).
# Kept as a private alias for the historical app.main._resolve_source_ip test seam (2.0 removal).
from app import runtime_state  # noqa: E402  (PF-AUTH-1: api_token_cache access)
from app.client_ip import resolve_source_ip as _resolve_source_ip  # noqa: E402,F401
from app.runtime_state import (  # noqa: E402,F401  (compat aliases for app.main.* seam)
    MCP_MOUNT_PATH,  # noqa: E402  (used by mount + /mcp/info)
    MCP_PRIVATE_CIDRS,
    RemoteMcpFlag,
)
from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware  # noqa: E402
from app.runtime_state import ClipConfigCache as _ClipConfigCache  # noqa: E402,F401
from app.runtime_state import McpAuthCache as _McpAuthCache  # noqa: E402,F401
from app.runtime_state import TokenSource as _TokenSource  # noqa: E402,F401
from app.runtime_state import WebSearchConfigCache as _WebSearchConfigCache  # noqa: E402,F401
from app.runtime_state import classify_source as _classify_source  # noqa: E402,F401
from app.runtime_state import clip_config_cache as _clip_config_cache  # noqa: E402
from app.runtime_state import hash_token as _hash_token  # noqa: E402,F401
from app.runtime_state import ip_is_private as _ip_is_private  # noqa: E402,F401
from app.runtime_state import mcp_auth_cache as _mcp_auth_cache  # noqa: E402
from app.runtime_state import mcp_write_flag as _mcp_write_flag  # noqa: E402
from app.runtime_state import remote_mcp_flag as _remote_mcp_flag  # noqa: E402
from app.runtime_state import resolve_token_source as _resolve_token_source  # noqa: E402
from app.runtime_state import token_configured as _token_configured  # noqa: E402,F401
from app.runtime_state import verify_token as _verify_token  # noqa: E402,F401
from app.runtime_state import web_search_config_cache as _web_search_config_cache  # noqa: E402

# ── MCP HTTP surface (ADR-0033 §2.4 — always-mount) ──────────────────────────
# Built unconditionally at module load (ADR-0033 §2.4: mount condition is no longer
# "token set"). The _McpGate middleware is the sole per-request arbiter.
# _http_mcp_asgi_app lifespan MUST be chained into the FastAPI lifespan (FastMCP
# session manager). The sub-app is always started/stopped once (no remount —
# ADR-0032 §2.3 stands).
_http_mcp_asgi_app: ASGIApp | None = None

# path="/" makes the Streamable-HTTP endpoint answer at the MOUNT ROOT, so the
# public URL is exactly MCP_MOUNT_PATH (/mcp/server) — matching the docs, the UI
# connection snippet, and GET /mcp/info.mount_path. FastMCP's default (path="/mcp")
# would have put the real endpoint at /mcp/server/mcp, so a client POSTing to
# /mcp/server would 404 even with the sub-app mounted (ADR-0033 §2.4).
# ADR-0072 §3: inject a runtime getter so write tools are always registered but
# each body checks the flag at call time.  _mcp_write_flag is the in-process cache
# loaded from vault_state in lifespan (DB-wins-else-env).  The getter closure
# captures the module-level singleton by name; mcp/server.py never imports main.py.
_http_mcp_instance = build_http_mcp(write_enabled_getter=lambda: _mcp_write_flag.is_enabled())
_http_mcp_asgi_app = _http_mcp_instance.http_app(path="/")
logger.info(
    "MCP HTTP surface always-mounted (ADR-0033 §2.4 / ADR-0072 §3): %s, "
    "write_enabled_getter=<runtime> bootstrap=%s",
    MCP_MOUNT_PATH,
    settings.mcp_remote_write_enabled,
)


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
    global _started_at, _graph_cache, _import_scheduler, _ops_scheduler
    _started_at = datetime.now(UTC)

    # 0. H1 — auth posture warning (defense-in-depth). SynapseAuthMiddleware is a transparent
    #    pass-through when SYNAPSE_AUTH_TOKEN is empty (auth.py), so the entire API — read
    #    pages, ingest, cascade-delete, rewrite provider_config — is unauthenticated. That is
    #    fine for local dev, but in a real deployment it makes the perimeter (Cloudflare Access
    #    / Tailscale) a single point of failure with no app-layer backstop. Warn loudly; never
    #    block startup (setting a token is the operator's call).
    if not settings.auth_token:
        logger.warning(
            "SYNAPSE_AUTH_TOKEN is unset — the API is UNAUTHENTICATED (every route open). "
            "Any perimeter gate (Cloudflare Access / Tailscale) is your only protection and "
            "has no app-layer backstop. Set SYNAPSE_AUTH_TOKEN to require a Bearer token."
        )

    # 1. Vault skeleton (K1, I5, AC-K7-1)
    bootstrap_vault()

    # 2. Seed vault_state (ADR-0005, AC-F16dv-1) + load runtime caches
    #    (ADR-0032/0033/0040/0041/0072)
    await _seed_vault_state()
    await _load_remote_mcp_flag()
    await _load_mcp_write_flag()  # ADR-0072 §2: DB-wins-else-env precedence
    await _load_mcp_auth_cache()
    await _load_clip_config_cache()
    await _load_web_search_config_cache()
    await _load_cli_auth_config_cache()
    await _load_api_token_cache()  # PF-AUTH-1 (1.9.4 W4)
    # P3-e (ADR-0071): decrypt UI-stored cloud web-search API keys into the sync cache.
    from app.ops.web_search.keys import load_cache_from_db as _load_ws_keys  # noqa: PLC0415

    await _load_ws_keys()

    # 2b. Load app_config override layer (ADR-0053 §4.1).
    #     MUST run BEFORE _validate_embedding_and_collection so effective S5
    #     (EMBEDDINGS_ENABLED override) governs the startup embedding validation.
    async with get_session() as _co_session:
        await load_overrides(_co_session)

    # 3. Validate EMBEDDING_DIM vs live bge-m3 + ensure collection (ADR-0004).
    #    Skipped when EMBEDDINGS_ENABLED=false (ADR-0030 §2.5) so the app boots
    #    with no embedding service reachable — startup must not fail in lexical mode.
    #    Uses the EFFECTIVE embeddings_enabled (env baseline + DB override — ADR-0053 §2.5).
    _effective_embeddings_enabled = effective_bool(
        "embeddings_enabled", settings.embeddings_enabled
    )
    if _effective_embeddings_enabled:
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

    # 4b. WS-C (ADR-0079): register queue-drain sweep callback.
    #     llm_wiki parity (ingest-queue.ts:636 onQueueDrained): sweep_reviews runs ONCE
    #     when the ingest queue empties after completing work — not after every run.
    #     Replaces the per-run sweep_reviews calls removed from pipeline.py.
    from app.ingest.queue_manager import ingest_queue as _iq_ref  # noqa: PLC0415
    from app.ops.review import sweep_reviews as _sweep_on_drain  # noqa: PLC0415

    async def _queue_drain_sweep() -> None:
        logger.info(
            "queue: drain — overview regen + sweep_reviews (ADR-0078/0079, vault=%s)",
            settings.vault_id,
        )
        # ADR-0078 refinement: regenerate the whole-wiki overview.md ONCE per drained batch (not
        # per-doc — that would compete with entity/concept extraction for the generation budget and
        # rewrite the overview N times). _update_overview reads purpose + the full existing-page
        # digest and is degrade-safe, so a None analysis at drain still yields a rich synthesis.
        try:
            from app.ops.overview import regenerate_overview as _regen_overview  # noqa: PLC0415

            await _regen_overview(analysis=None, origin_source="queue-drain")
        except Exception as exc:  # noqa: BLE001 — overview is best-effort; never break the drain.
            logger.warning("queue: drain overview regen failed (non-fatal): %s", exc)
        await _sweep_on_drain(settings.vault_id)

    _iq_ref.set_on_drained(_queue_drain_sweep)
    logger.info("queue: drain sweep callback registered (ADR-0078/0079)")

    # 5. Initialise GraphCache + background debounce loop (I2, ADR-0014)
    _graph_cache = GraphCache(
        engine=GraphEngine(),
        vault_id=settings.vault_id,
    )
    _graph_cache.start_background_loop()
    logger.info("GraphCache initialised and background loop started")

    # 6. Start ImportScheduler asyncio task (ADR-0020 §4.5; after watcher so copies are seen)
    _import_scheduler = ImportScheduler()
    # R13-4 / T4: load persisted last-run timestamp BEFORE start() so the first
    # sleep is shortened by time already elapsed since the last scan.
    await _import_scheduler.initialize()
    _import_scheduler.start()
    logger.info("ImportScheduler started")

    # 6c. Start OpsScheduler asyncio task (R12-7/A5; AFTER load_overrides so schedule keys
    #     are effective on the first tick — the scheduler reads them from the in-memory cache).
    _ops_scheduler = OpsScheduler()
    # R13-4 / T4: load persisted last-run timestamps BEFORE start() so ops that ran
    # before the container restart are not immediately re-triggered on the first tick.
    await _ops_scheduler.initialize()
    _ops_scheduler.start()
    logger.info("OpsScheduler started")

    # 6b. Inject singletons into the health details router (R9-2) so it can read
    #     GraphCache and ImportScheduler state without circular imports.
    from app.health import set_health_singletons  # noqa: PLC0415

    set_health_singletons(_graph_cache, _import_scheduler)
    logger.info("Health singletons injected (R9-2)")

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
    if _ops_scheduler is not None:
        _ops_scheduler.stop()
    if _import_scheduler is not None:
        _import_scheduler.stop()
    if _graph_cache is not None:
        _graph_cache.stop_background_loop()
    stop_watcher()
    await aclose_embedding_client()
    await dispose_engine()


# ── FastAPI app ────────────────────────────────────────────────────────────────


def _resolve_backend_version() -> str:
    """Backend version, truthful in every deployment mode (ADR-0054 §6, R12-3).

    Priority:
      1. APP_VERSION env var (release-stamped by the GHCR image build from the git tag,
         Dockerfile ARG/ENV). A leading 'v' is stripped ('v1.2.0' → '1.2.0').
      2. pyproject.toml NEXT TO THE SOURCE — always current for source-mounted dev
         containers and editable installs, where importlib.metadata is frozen at
         `pip install` time (observed lagging at 0.1.0 on the dev container).
      3. importlib.metadata of the installed package (non-editable installs).
      4. 'dev' fallback.
    """
    env_version = os.environ.get("APP_VERSION", "").strip().lstrip("v")
    if env_version and env_version != "dev":
        return env_version

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        import tomllib  # noqa: PLC0415 — stdlib (py3.11+), lazy: only this fallback needs it

        with pyproject.open("rb") as fh:
            version = tomllib.load(fh).get("project", {}).get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except OSError:
        pass

    try:
        installed: str = importlib.metadata.version("synapse-backend")
    except importlib.metadata.PackageNotFoundError:
        return "dev"
    return installed


_app_version: str = _resolve_backend_version()

app = FastAPI(
    title="Synapse",
    version=_app_version,
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

# ── Domain exception taxonomy (BE-QUAL-1 partial) ─────────────────────────────
# Translates app.errors.SynapseError subclasses to the SAME response shape FastAPI
# already produces for HTTPException — no observable behaviour change (v1.9.2).
register_exception_handlers(app)

# ── Auth + CORS middleware (ADR-0052 §2.4 — ORDER IS LOAD-BEARING) ─────────────
# In Starlette, ``add_middleware`` wraps in REVERSE registration order:
# the LAST registered middleware is the OUTERMOST layer (sees the request first
# and the response last).  We need CORS outermost so that even a 401 from the
# auth middleware carries ``Access-Control-Allow-Origin``.
#
#   REGISTRATION ORDER          EXECUTION ORDER (request in / response out)
#   ──────────────────          ──────────────────────────────────────────
#   1. SynapseAuthMiddleware  → INNER  (runs auth check; 401 exits up through CORS)
#   2. CORSMiddleware (last)  → OUTER  (stamps CORS headers on EVERY response)
#
# The OPTIONS exemption in auth.py plus this ordering means preflights are answered
# correctly (CORSMiddleware handles them before auth even sees them — OPTIONS passes
# through the auth bypass, then CORS intercepts and replies with the preflight headers).
#
# DO NOT change this order without updating the CORS-on-401 test and ADR-0052 §2.4.
app.add_middleware(
    SynapseAuthMiddleware,
    token=settings.auth_token,
    # PF-AUTH-1 (1.9.4 W4): scoped api_tokens layered on top of the bootstrap token.
    token_cache=runtime_state.api_token_cache,
    vault_id=settings.vault_id,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Graph-Cache"],  # so the viewer can read cache hit/miss (ADR-0014)
)
# H4 — hardening headers, registered LAST so it is OUTERMOST: it stamps every response
# (including 401s and CORS preflights) on the way out. Adding headers here does not affect
# the load-bearing auth↔CORS ordering above (CORS still wraps auth).
app.add_middleware(SecurityHeadersMiddleware)

# ── Auth failure rate limit middleware (SEC-RL-1) ──────────────────────────
# Rate-limits 401 responses per IP to prevent token-guessing attacks.
app.add_middleware(AuthFailureRateLimitMiddleware)

# ── Sources router (raw-source file browser — nashsu/llm_wiki parity) ────────
app.include_router(sources_router)

# ── Export router (vault ZIP + data.json backup — R8-4, AC-R8-4-4) ───────────
from app.export import router as export_router  # noqa: E402

app.include_router(export_router)

# ── Costs router (cost aggregation dashboard — R9-1, AC-R9-1-1..6) ───────────
from app.costs import router as costs_router  # noqa: E402

app.include_router(costs_router)

# ── Health details router (R9-2, AC-R9-2-1..4) ───────────────────────────────
from app.health import router as health_router  # noqa: E402

app.include_router(health_router)

# ── Stats router (R12-1 / F18 / ADR-0054 §5) ─────────────────────────────────
from app.stats import router as stats_router  # noqa: E402

app.include_router(stats_router)


# ── Per-domain APIRouter modules (R13-1 refactor) ─────────────────────────────
from app.projects import router as projects_router  # noqa: E402
from app.routers.chat import router as chat_router  # noqa: E402
from app.routers.clip import router as clip_router  # noqa: E402
from app.routers.config import router as config_router  # noqa: E402
from app.routers.events import router as events_router  # noqa: E402
from app.routers.graph import router as graph_router  # noqa: E402
from app.routers.ingest import router as ingest_router  # noqa: E402
from app.routers.lint import router as lint_router  # noqa: E402
from app.routers.ops import router as ops_router  # noqa: E402
from app.routers.ops_overview import router as ops_overview_router  # noqa: E402
from app.routers.pages import router as pages_router  # noqa: E402
from app.routers.research import router as research_router  # noqa: E402
from app.routers.review import router as review_router  # noqa: E402
from app.routers.scenarios import router as scenarios_router  # noqa: E402
from app.routers.search import router as search_router  # noqa: E402
from app.routers.status import router as status_router  # noqa: E402
from app.routers.vault_meta import router as vault_meta_router  # noqa: E402

app.include_router(ops_router)
app.include_router(ops_overview_router)  # ADR-0078: POST /ops/overview/regenerate
app.include_router(status_router)
app.include_router(pages_router)
app.include_router(ingest_router)
app.include_router(search_router)
app.include_router(config_router)
app.include_router(chat_router)
app.include_router(graph_router)
app.include_router(research_router)
app.include_router(review_router)
app.include_router(lint_router)
app.include_router(clip_router)
app.include_router(scenarios_router)
app.include_router(vault_meta_router)  # WS-D8: vault-root meta files (schema.md, purpose.md)
app.include_router(projects_router)  # v1.5 P2: multi-vault project registry (ADR-0082)
app.include_router(events_router)  # 1.9.3 W1: GET /events SSE push channel (FE-RT-2)

# ── OpenAPI security scheme (ADR-0052 §2.5, I8, EC-M10-4) ────────────────────
# Inject ``BearerAuth`` into the OpenAPI schema so docs/api/openapi.json declares
# the security scheme and every route references it — except the exempt routes
# (/status, /health/live) which carry ``security: []`` explicitly.
#
# This is a documentation concern, independent of the enforcement middleware above.
# Implementation: override ``app.openapi()`` once after all routes are registered.
#
# Exempt from ``BearerAuth`` in the schema (matches the middleware exempt set §2.3;
# /docs, /redoc, /openapi.json are framework-served and not in OpenAPI paths):
_OPENAPI_SECURITY_EXEMPT: frozenset[str] = frozenset({"/status", "/health/live"})

_original_openapi = app.openapi


def _patched_openapi() -> dict[str, Any]:
    """
    Custom OpenAPI schema generator (ADR-0052 §2.5).

    Adds:
    - ``components.securitySchemes.BearerAuth``: HTTP bearer scheme.
    - ``security: [{"BearerAuth": []}]`` on every non-exempt path+method.
    - ``security: []`` on exempt paths (/status, /health/live).
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema: dict[str, Any] = _original_openapi()

    # Inject BearerAuth scheme into components.securitySchemes.
    components: dict[str, Any] = schema.setdefault("components", {})
    security_schemes: dict[str, Any] = components.setdefault("securitySchemes", {})
    security_schemes["BearerAuth"] = {"type": "http", "scheme": "bearer"}

    # Annotate every path entry with the correct security marker.
    bearer_security: list[dict[str, list[str]]] = [{"BearerAuth": []}]
    no_security: list[dict[str, list[str]]] = []

    for path, path_item in schema.get("paths", {}).items():
        is_exempt = path in _OPENAPI_SECURITY_EXEMPT
        sec = no_security if is_exempt else bearer_security
        for method_obj in path_item.values():
            if isinstance(method_obj, dict):
                method_obj["security"] = sec

    app.openapi_schema = schema
    return schema


app.openapi = _patched_openapi  # type: ignore[method-assign]

# ── MCP HTTP mount (ADR-0033 §2.4 — always-mount; gate is the sole arbiter) ──
# Mounted at MCP_MOUNT_PATH — always, regardless of token configuration.
# The _BearerAuthMiddleware (now the full MCP access gate) is applied ONLY to
# this sub-app (scoped; REST API unaffected).
# The gate carries _remote_mcp_flag, _mcp_auth_cache, and the env bootstrap token.
# No remount on flag changes (ADR-0032 §2.3 — session manager stable).
# NOTE: restored after the R13-1 router split (2bbe195) dropped this block, which
# left /mcp/server unmounted → every remote MCP request 404'd while /mcp/info still
# reported http_enabled=true. The OpenAPI drift gate could not catch it because a
# Mount() sub-app is not an OpenAPI path.
if _http_mcp_asgi_app is not None:
    _guarded_mcp_app = _BearerAuthMiddleware(
        _http_mcp_asgi_app,
        settings.mcp_auth_token or "",
        _remote_mcp_flag,
        _mcp_auth_cache,
    )
    app.mount(MCP_MOUNT_PATH, _guarded_mcp_app)
    logger.info("MCP HTTP surface mounted at %s (ADR-0033 §2.4 always-mount)", MCP_MOUNT_PATH)

# ── Re-exports for backward-compatible test imports (R13-1) ───────────────────
# Tests use `from app.main import X` — these ensure nothing breaks.
from app.models import ProviderConfig  # noqa: E402, F401  # patched by tests via app.main
from app.routers.chat import (  # noqa: E402, F401
    ConversationRenameRequest,
    ConversationRenameResponse,
    _clean_chat_content,
    save_chat_to_wiki,
)
from app.routers.clip import (  # noqa: E402, F401
    _clip_origin_allowed,
    _clip_safe_filename,
)
from app.routers.config import EmbeddingConfigResponse, get_embedding_config  # noqa: E402, F401
from app.routers.pages import (  # noqa: E402, F401
    _MAX_PAGE_CONTENT_BYTES,
    PageCreateRequest,
    PageCreateResponse,
    _resolve_page_path,
    _resolve_wiki_page_path,
)
from app.routers.search import _SEARCH_VALID_SORTS  # noqa: E402, F401
from app.scenarios_data import SCENARIO_INDEX as _SCENARIO_INDEX  # noqa: E402, F401
from app.scenarios_data import SCENARIOS as _SCENARIOS  # noqa: E402, F401

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
    and cli_oauth_token=None + cli_oauth_token_encrypted=None
    (ADR-0043 §2.2 / W7 — env-fallback by default)
    and remote_mcp_write_enabled=None
    (ADR-0072 §1 — NULL = env-fallback; DB becomes authoritative on first PUT /mcp/remote-write).
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


async def _load_mcp_write_flag() -> None:
    """
    Load vault_state.remote_mcp_write_enabled into _mcp_write_flag at startup (ADR-0072 §2).

    Called once in lifespan after _load_remote_mcp_flag().  Mirrors the RemoteMcpFlag
    pattern (ADR-0032 §2.2): DB is source of truth; in-process cache is O(1) per request.

    Precedence (DB-wins-else-env — ADR-0072 §1):
      DB non-NULL → use DB value.
      DB NULL     → fall back to settings.mcp_remote_write_enabled (env bootstrap).
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is not None:
            db_val: bool | None = getattr(state, "remote_mcp_write_enabled", None)
            effective: bool = db_val if db_val is not None else settings.mcp_remote_write_enabled
        else:
            effective = settings.mcp_remote_write_enabled
    await _mcp_write_flag.load(effective)
    logger.info(
        "McpWriteFlag loaded from DB: remote_mcp_write_enabled=%s (ADR-0072 §2)",
        effective,
    )


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


async def _load_api_token_cache() -> None:
    """
    Load active (non-revoked) api_tokens rows into runtime_state.api_token_cache (PF-AUTH-1).

    Called once in lifespan after _seed_vault_state(). Mirrors the _load_mcp_auth_cache
    pattern: DB (api_tokens table) is source of truth; the in-process ApiTokenCache is
    read O(1)-per-entry per request by SynapseAuthMiddleware. NEVER logs secret_hash.
    """
    from app.models import ApiToken  # noqa: PLC0415
    from app.runtime_state import ApiTokenEntry  # noqa: PLC0415

    async with get_session() as session:
        rows = await session.execute(select(ApiToken).where(ApiToken.revoked_at.is_(None)))
        entries = [
            ApiTokenEntry(
                id=str(row.id),
                label=row.label,
                secret_hash=row.secret_hash,
                vault_id=row.vault_id,
                read_only=row.read_only,
            )
            for row in rows.scalars().all()
        ]

    await runtime_state.api_token_cache.load(entries)
    logger.info("ApiTokenCache loaded from DB: %d active token(s) (PF-AUTH-1)", len(entries))


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
    Load the CLI subscription OAuth token into _cli_auth_config_cache at startup (ADR-0043 §2.4,
    W7 encryption amendment).

    Called once in lifespan after _load_clip_config_cache(). DB is source of truth; in-process
    cache is O(1) per request.

    Read strategy (W7 migration 0027):
      1. Prefer cli_oauth_token_encrypted (Fernet BYTEA column — migration 0027).
         Decrypt via secrets_crypto.decrypt().
         Degrade-safe: if SYNAPSE_SECRET_KEY is absent or ciphertext tampered → load None;
         log a warning; env tiers govern (fail-open for the provider layer, fail-closed for
         the DB path).
      2. Fall back to legacy cli_oauth_token (TEXT, migration 0017) when the encrypted column
         is NULL (operator skipped migration-time encrypt-in-place — key was absent).  Log a
         security warning so the operator knows action is needed.

    NEVER logs or returns the token value.
    """
    from app import secrets_crypto as _sc  # local import — avoid circular at module level

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            oauth_token: str | None = None
        else:
            # ── W7: prefer the Fernet-encrypted column (migration 0027) ──────────
            encrypted: bytes | None = getattr(state, "cli_oauth_token_encrypted", None)
            if encrypted is not None:
                try:
                    oauth_token = _sc.decrypt(bytes(encrypted))
                except _sc.SecretsNotConfiguredError:
                    logger.warning(
                        "CliAuthConfigCache startup: cli_oauth_token_encrypted is set in DB "
                        "but SYNAPSE_SECRET_KEY is absent or invalid — loading token as None "
                        "(env tiers govern). Set SYNAPSE_SECRET_KEY to re-enable DB token. (W7)"
                    )
                    oauth_token = None
                except _sc.InvalidToken:
                    logger.error(
                        "CliAuthConfigCache startup: cli_oauth_token_encrypted ciphertext is "
                        "tampered or was produced under a different key — fail-closed (None). "
                        "Re-store the token via PUT /provider/cli-auth. (W7)"
                    )
                    oauth_token = None
            else:
                # ── Fallback: legacy plaintext column (migration 0017) ────────────
                # This path is hit when migration 0027 ran without SYNAPSE_SECRET_KEY
                # (encrypt-in-place was skipped). Log a security warning.
                legacy: str | None = getattr(state, "cli_oauth_token", None)
                if legacy:
                    logger.warning(
                        "CliAuthConfigCache startup: cli_oauth_token_encrypted is NULL but "
                        "legacy plaintext cli_oauth_token is set — using plaintext (W7 "
                        "migration incomplete). Set SYNAPSE_SECRET_KEY and re-store the token "
                        "via PUT /provider/cli-auth to complete the encryption migration."
                    )
                    oauth_token = legacy
                else:
                    oauth_token = None

    await _cli_auth._cli_auth_config_cache.load(oauth_token)
    logger.info(
        "CliAuthConfigCache loaded from DB: token_source=%s (ADR-0043 / W7)",
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
