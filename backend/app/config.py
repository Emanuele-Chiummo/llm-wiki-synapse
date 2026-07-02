"""
Synapse backend configuration — all values from environment variables.

No secrets, no hardcoded URLs, no hardcoded model IDs or dimensions (I9, AC-DC-5).
All required vars fail fast if missing (pydantic-settings raises on startup).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration read entirely from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str
    """asyncpg DSN, e.g. postgresql+asyncpg://user:pass@host:5432/synapse"""

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_url: str
    """Qdrant HTTP base URL, e.g. http://truenas:6333"""

    qdrant_collection: str = "synapse_pages"
    """Name of the Qdrant collection; override only for test isolation."""

    # ── Embedding (bge-m3 via existing Ollama/service on TrueNAS) ─────────────
    embedding_url: str
    """
    HTTP endpoint for bge-m3 embeddings (I9 — reuse the already-running service).
    Example: http://truenas:11434/api/embeddings
    """

    embedding_dim: int
    """
    Vector dimension of the embedding model (ADR-0004).
    Required — no silent default; the running bge-m3 is the authority.
    Documented default in .env.example: 1024 (bge-m3 standard variant).
    """

    embedding_model: str = "bge-m3"
    """
    Model name to pass in the embedding request body.
    Read from env — never hardcoded (I9 / CLAUDE.md §12).
    """

    embedding_format: str = "ollama"
    """
    Embedding request/response adapter selector (ADR-0031, Feature C).
    Allowed values: "ollama" (default) | "openai".
      - "ollama": POST {"model","prompt"} → parse {"embedding":[...]} (current bge-m3 behavior).
      - "openai": POST {"model","input"} → parse {"data":[{"embedding":[...]}]} for
        OpenAI-compatible /v1/embeddings endpoints (Gemini, hosted gateways, etc.).
    Explicit (not auto-detected) by owner decision — deterministic, fail-fast (I9 / I6-spirit).
    Env var: EMBEDDING_FORMAT.
    """

    embedding_api_key: str | None = None
    """
    SECRET. Optional bearer token for the embedding endpoint (ADR-0031, Feature C).
    When set, every embedding request carries `Authorization: Bearer <key>` (both formats).
    When unset (local bge-m3 on the internal network), no auth header is sent (unchanged).
    Never logged, never returned by GET /config/embedding — env-sourced only.
    Env var: EMBEDDING_API_KEY.
    """

    embeddings_enabled: bool = True
    """
    Global toggle for the embedding data plane (ADR-0030, Feature B).
    When True (default): ingest vectorizes pages into Qdrant; retrieval uses Phase-1 dense
    Qdrant search (bge-m3). When False: ingest skips Qdrant; startup skips
    ``_validate_embedding_and_collection``; retrieval Phase 1 degrades to a bounded
    Postgres keyword/title search (``_phase1_lexical_search``). Phases 2–4 (graph-expansion,
    budget, assembly) are UNCHANGED in both modes. Toggling does NOT trigger a re-scan or
    bulk re-embed (I1). Env var: EMBEDDINGS_ENABLED.
    """

    embed_max_chars: int = 8_000
    """
    Hard cap on characters sent to the embedding endpoint in ONE request (I7 — oversize guard).
    bge-m3 accepts ~8192 TOKENS; sending more makes the embedding server (Ollama) return HTTP 500,
    which would otherwise crash the ingest (upsert_vector). Text longer than this is TRUNCATED (a
    WARNING is logged) before embedding, so the vector represents the document's head rather than
    failing. 8 000 chars is safe even for *token-dense* content (tables/technical prose can run
    ~1.2 chars/token → ~6.5k tokens < 8192; empirically dense pages 500 above ~10k chars while
    "8 000 chars" passes). Query/normal-page embeds are far shorter and unaffected. Callers that
    still 500 degrade to a vector-less page (see connectors.importer). Env var: EMBED_MAX_CHARS.
    """

    # ── Vault ─────────────────────────────────────────────────────────────────
    vault_id: str = "default"
    """Logical vault identifier (one vault in v0.1; supports multi-vault later)."""

    vault_path: str = "vault"
    """
    Absolute or repo-relative path to the vault root directory.
    The watcher watches <vault_path>/raw/sources/.
    """

    # ── Frontend / CORS ─────────────────────────────────────────────────────────
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    """
    Comma-separated list of browser origins allowed to call the API (CORS).
    Default covers the Vite dev server; set CORS_ALLOW_ORIGINS in prod (PWA/Tauri origin).
    Use "*" to allow any origin (dev only).
    """

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS allow-origins as a list (split + trimmed)."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def vault_root(self) -> Path:
        """Resolved absolute Path to the vault root."""
        return Path(self.vault_path).resolve()

    @property
    def raw_sources_dir(self) -> Path:
        """vault/raw/sources/ — the watched directory."""
        return self.vault_root / "raw" / "sources"

    @property
    def wiki_dir(self) -> Path:
        """vault/wiki/ — the Obsidian-compatible wiki output dir."""
        return self.vault_root / "wiki"

    @property
    def log_md_path(self) -> Path:
        """vault/wiki/log.md — append-only ingest history (K4)."""
        return self.wiki_dir / "log.md"

    # ── Deep Research (F10, ADR-0024) ────────────────────────────────────────────
    searxng_url: str | None = None
    """
    HTTP base URL for the SearXNG search backend (R8 — already running on TrueNAS).
    Example: http://searxng:8080
    Required when deep research is used; POST /research/start returns 503 if unset (I9).
    No API key — SearXNG is open-access on the internal network.
    NEVER falls back to another search engine when unset (I9 / Do-NOT #3).
    """

    deep_research_max_iter: int = 3
    """
    Default max iterations for run_deep_research (ADR-0024 §3.1).
    Caller-overridable via POST /research/start body (bounded 1..10).
    Env var: DEEP_RESEARCH_MAX_ITER.
    """

    deep_research_token_budget: int = 100_000
    """
    Default token budget for run_deep_research (ADR-0024 §3.1).
    Caller-overridable via POST /research/start body (bounded 1_000..1_000_000).
    Env var: DEEP_RESEARCH_TOKEN_BUDGET.
    """

    deep_research_max_queries: int = 5
    """
    Maximum SearXNG queries generated per iteration (ADR-0024 §3.1).
    Env var: DEEP_RESEARCH_MAX_QUERIES.
    """

    deep_research_fetch_max_chars: int = 20_000
    """
    Per-source content cap (chars) after HTML→markdown extraction (ADR-0024 §4).
    Prevents a single large page from blowing the token budget.
    Env var: DEEP_RESEARCH_FETCH_MAX_CHARS.
    """

    # ── F9: Review queue (ADR-0025 §3.2) ────────────────────────────────────────

    review_query_timeout_seconds: float = 30.0
    """
    Timeout in seconds for the single pre-generated-query provider call (F9, I7).
    On timeout → pre_generated_query=NULL; item still enqueued (AC-F9-4).
    Env var: REVIEW_QUERY_TIMEOUT_SECONDS.
    """

    review_query_token_budget: int = 2_000
    """
    Token budget for the single query-gen call (F9, I7).
    Small: the prompt + 1-3 questions should comfortably fit in 2K tokens.
    Env var: REVIEW_QUERY_TOKEN_BUDGET.
    """

    # ── F9 redesign: proposal model bounds (ADR-0034 §4/§6, [AI] §11.2) ──────────
    # All three new LLM call sites (proposal emission, sweep Pass-2, on-demand Create)
    # are bounded by these + the resolved provider row's max_iter/token_budget (I7).

    review_propose_min_chars: int = 10_000
    """
    Anti-spam gate (ADR-0034 §4.2): the proposal LLM call runs only if the total written
    content is at least this many characters (one of several OR'd gate conditions). Below the
    gate (and absent any dangling-link / not-written-suggested signal) → zero proposals, zero
    cost. Env var: REVIEW_PROPOSE_MIN_CHARS.
    """

    review_propose_min_pages: int = 4
    """
    Anti-spam gate (ADR-0034 §4.2): the proposal LLM call runs if at least this many pages were
    written in the run (OR'd with the char / dangling-link / suggested-page conditions).
    Env var: REVIEW_PROPOSE_MIN_PAGES.
    """

    review_propose_max_items: int = 8
    """
    Hard cap on proposals emitted per ingest run (ADR-0034 §4.3, Do-NOT #9). The single LLM
    proposal call's output is truncated to this many items — never an unbounded enqueue.
    Env var: REVIEW_PROPOSE_MAX_ITEMS.
    """

    review_propose_token_budget: int = 4_000
    """
    Fallback token budget for the single proposal call (ADR-0034 §4.3, I7) when the resolved
    provider row carries none. Small: a compact analysis digest + ≤8 proposals fits comfortably.
    Env var: REVIEW_PROPOSE_TOKEN_BUDGET.
    """

    review_propose_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping the single proposal provider call (ADR-0034 §4.3, I7).
    On timeout → emit only the rule-based proposals (degrade, never fail ingest).
    Env var: REVIEW_PROPOSE_TIMEOUT_SECONDS.
    """

    review_sweep_max_items: int = 200
    """
    Max pending missing-page/duplicate items processed by the sweep Pass-1 rule pass per run
    (ADR-0034 §6.2, I7 — bounded indexed read, no vault re-scan).
    Env var: REVIEW_SWEEP_MAX_ITEMS.
    """

    # ── F3: auto-maintained Overview (nashsu/llm_wiki parity) ────────────────────
    # The single overview.md note is REGENERATED (full overwrite) on every ingest via ONE
    # bounded provider call (I6/I7), then indexed as a Page(type=overview). Degrade-safe:
    # on any failure the previous overview.md is kept and ingest still succeeds.

    overview_title: str = "Overview"
    """
    Frontmatter title for the auto-maintained overview.md note (F3, I5). The wiki's big-picture
    page; shown under the nav "Overview" section (count 1). Env var: OVERVIEW_TITLE.
    """

    overview_max_titles: int = 200
    """
    Max existing page titles+types fed into the overview regeneration prompt (bounded indexed
    read — I1, no vault re-scan). Env var: OVERVIEW_MAX_TITLES.
    """

    overview_token_budget: int = 3_000
    """
    Fallback token budget for the single overview regeneration call (I7) when the resolved
    provider row carries none. Small: the overview is a concise narrative. Env var:
    OVERVIEW_TOKEN_BUDGET.
    """

    overview_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping the single overview regeneration provider call (I7). On timeout
    the previous overview.md is kept (degrade, never fail ingest). Env var:
    OVERVIEW_TIMEOUT_SECONDS.
    """

    review_sweep_llm_enabled: bool = True
    """
    Gate for the sweep Pass-2 conservative LLM judgment (ADR-0034 §6.3). Default on (a single
    bounded call). Set false for zero-cost operation: Pass-1 still runs; Pass-2 returns set()
    (keep all pending). Env var: REVIEW_SWEEP_LLM_ENABLED.
    """

    review_sweep_llm_max_items: int = 8
    """
    Cap on the number of candidate items batched into the single sweep Pass-2 LLM call
    (ADR-0034 §6.3, Do-NOT #9). Env var: REVIEW_SWEEP_LLM_MAX_ITEMS.
    """

    review_sweep_llm_token_budget: int = 4_000
    """
    Fallback token budget for the single sweep Pass-2 call (ADR-0034 §6.3, I7) when the resolved
    provider row carries none. Env var: REVIEW_SWEEP_LLM_TOKEN_BUDGET.
    """

    review_sweep_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping the single sweep Pass-2 provider call (ADR-0034 §6.3, I7).
    On timeout / ambiguity → keep ALL pending (default-to-keep, Do-NOT #7).
    Env var: REVIEW_SWEEP_TIMEOUT_SECONDS.
    """

    # ── ADR-0044 (F9 depth pass) — contextual depth + bulk bounds ────────────────

    review_referenced_pages_max: int = 8
    """
    Cap on referenced existing pages carried per proposal (ADR-0044 §2/§4.3). The proposal
    call's `referenced_page_titles` list is truncated to this at parse; resolution drops
    non-resolving titles. Bounds referenced_page_ids to this length (I7).
    Env var: REVIEW_REFERENCED_PAGES_MAX.
    """

    review_search_queries_max: int = 3
    """
    Cap on pre-generated search queries carried per proposal (ADR-0044 §2.3/§4.3). The proposal
    call's `search_queries` list is truncated to this at parse. search_queries[0] seeds Deep
    Research. Rides the SAME single proposal call (no extra provider call, I6/I7).
    Env var: REVIEW_SEARCH_QUERIES_MAX.
    """

    review_bulk_max_ids: int = 200
    """
    Cap on the number of ids in a POST /review/queue/bulk request (ADR-0044 §6, I7). Over the
    cap → HTTP 400 (never an unbounded bulk write). DELETE /review/queue/resolved is one bounded
    vault-scoped statement (no id list). Env var: REVIEW_BULK_MAX_IDS.
    """

    # ── F4: wikilink-enrichment post-pass bounds (ADR-0036, [AI] §9) ─────────────
    # The once-per-run enrichment call is bounded by these + the resolved provider row's
    # token_budget (I7). Substitution-apply (R1): the LLM returns {mention, target_title}
    # pairs; code validates + applies them single-mention into page BODIES only (I5).

    wikilink_enrich_enabled: bool = True
    """
    Master gate for the wikilink-enrichment post-pass (ADR-0036 §4). Default on (one bounded
    call per orchestrated ingest run). Set false for zero-cost ingest: pages are still written
    and indexed; no enrichment call is made. Env var: WIKILINK_ENRICH_ENABLED.
    """

    wikilink_enrich_min_chars: int = 200
    """
    Anti-spam / cost gate (ADR-0036 §2.1 step 2): the enrichment LLM call runs only if the
    combined body length of the written pages is at least this many characters. Below the gate
    → zero substitutions, zero cost, zero LLM call. Env var: WIKILINK_ENRICH_MIN_CHARS.
    """

    wikilink_enrich_max_candidates: int = 500
    """
    Cap on the existing-page-title candidate list sent to the model (ADR-0036 §4, I7). Bounds
    the prompt size; when the vault exceeds this, the most-recent titles are kept (best-effort,
    §6 risk 2). Env var: WIKILINK_ENRICH_MAX_CANDIDATES.
    """

    wikilink_enrich_max_subs: int = 100
    """
    Hard cap on applied substitutions per run (ADR-0036 §4, Do-NOT #3/#6). The single LLM call's
    substitution list is truncated to this many — never an unbounded edit set.
    Env var: WIKILINK_ENRICH_MAX_SUBS.
    """

    wikilink_enrich_token_budget: int = 4_000
    """
    Fallback token budget for the single enrichment call (ADR-0036 §4, I7) when the resolved
    provider row carries none. Small: a compact body digest + a short substitution list fits.
    Env var: WIKILINK_ENRICH_TOKEN_BUDGET.
    """

    wikilink_enrich_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping the single enrichment provider call (ADR-0036 §4, I7).
    On timeout → apply zero substitutions (degrade, never fail ingest).
    Env var: WIKILINK_ENRICH_TIMEOUT_SECONDS.
    """

    # ── F11: Web clipper ingress (ADR-0038) ──────────────────────────────────────

    clip_enabled: bool = False
    """
    Master gate for the POST /clip ingress endpoint (F11, ADR-0038).
    Default OFF — must be explicitly enabled. When OFF the endpoint returns 503.
    Set CLIP_ENABLED=true to open the ingress (still requires CLIP_TOKEN).
    Env var: CLIP_ENABLED.
    """

    clip_token: str | None = None
    """
    SECRET. Bearer token required on every POST /clip request (F11, ADR-0038 §2.1).
    Compared constant-time (hmac.compare_digest). Missing/invalid → 401.
    NEVER logged. Set CLIP_TOKEN to a high-entropy random string.
    Env var: CLIP_TOKEN.
    """

    clip_allowed_origins: str = ""
    """
    Comma-separated allowlist of permitted request Origins (F11, ADR-0038 §2.2).
    Each entry is an exact origin string (scheme+host, no path/query).
    Example: "chrome-extension://abcdefghijklmnopqrstuvwxyz,http://127.0.0.1:5173"
    Empty string means only loopback/localhost requests are allowed (implicit).
    Env var: CLIP_ALLOWED_ORIGINS.
    """

    clip_max_body_bytes: int = 2 * 1024 * 1024  # 2 MB
    """
    Maximum allowed body size for POST /clip (F11, ADR-0038 §2.3, I7).
    Requests with Content-Length or accumulated body exceeding this value → 413.
    Default 2 MB — generous for any realistic Markdown clip.
    Env var: CLIP_MAX_BODY_BYTES.
    """

    @property
    def clip_allowed_origins_list(self) -> list[str]:
        """CLIP_ALLOWED_ORIGINS as a trimmed list (may be empty)."""
        return [o.strip() for o in self.clip_allowed_origins.split(",") if o.strip()]

    # ── K2: Lint-fix loop (ADR-0037 §4) ──────────────────────────────────────────
    # The K2 lint scan is a BOUNDED, HUMAN-GATED health check of the wiki. The single
    # semantic provider call (missing-xref / contradiction / stale-claim / missing-page)
    # is bounded by these + the resolved provider row's token_budget (I7). Deterministic
    # checks (orphan-page via the graph engine) make NO provider call.

    lint_max_iter: int = 3
    """
    Iteration cap for the bounded lint scan loop (ADR-0037 §4, I7). The loop is structurally
    capped at ``for n in range(1, LINT_MAX_ITER + 1)`` AND a token_budget gate at the top of
    each round. A caller may override (bounded 1..10) via POST /lint/scan; the value is FROZEN
    on the lint_runs row at INSERT and never re-read mid-loop. Env var: LINT_MAX_ITER.
    """

    lint_token_budget: int = 20_000
    """
    Token budget for one lint scan run (ADR-0037 §4, I7). The semantic provider call(s) stop
    when ``accumulator.total_tokens >= token_budget``. Caller-overridable (bounded 1_000..
    1_000_000); FROZEN on the lint_runs row at INSERT. Env var: LINT_TOKEN_BUDGET.
    """

    lint_max_findings: int = 50
    """
    Hard cap on findings emitted per lint run (ADR-0037 §4, Do-NOT — never an unbounded
    enqueue). Deterministic + semantic findings are merged and truncated to this many.
    Env var: LINT_MAX_FINDINGS.
    """

    lint_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping each semantic lint provider call (ADR-0037 §4, I7). On timeout →
    emit only the deterministic findings (degrade, never fail the scan). Env var:
    LINT_TIMEOUT_SECONDS.
    """

    # ── F12: Multi-format ingest (ADR-0025 §4.1) ─────────────────────────────────

    extract_max_chars: int = 2_000_000
    """
    Maximum characters of extracted text output (F12, I7 — pathological-file guard).
    Default ~2M chars (~500K tokens at 4 chars/token) — generous but bounded.
    Env var: EXTRACT_MAX_CHARS.
    """

    # ── M4-EXT: upload + scheduled import caps (ADR-0020 §2.4 / §4.4) ──────────

    max_upload_bytes: int = 26_214_400
    """
    Maximum file size for POST /ingest/upload (Feature U). Default 25 MB (I7).
    Env var: MAX_UPLOAD_BYTES.
    """

    import_scan_max_files: int = 200
    """
    Maximum number of files copied per scheduled scan tick (Feature S, I7).
    Env var: IMPORT_SCAN_MAX_FILES.
    """

    import_scan_max_seconds: int = 60
    """
    Wall-clock deadline (seconds) for one scheduled scan tick (Feature S, I7).
    Env var: IMPORT_SCAN_MAX_SECONDS.
    """

    # ── MCP server introspection (F1-MCP-UI, ADR-0027 §2.3) ──────────────────────

    mcp_transport: str = "stdio"
    """
    MCP server transport type (ADR-0010 §1; ADR-0027 §2.3).
    Default: "stdio" — the transport the synapse MCP server uses.
    Env var: MCP_TRANSPORT.
    """

    mcp_entry_command: str = "python -m app.mcp.server"
    """
    Shell command to launch the MCP server (ADR-0027 §2.3).
    Default: "python -m app.mcp.server" — the documented stdio entry point.
    Env var: MCP_ENTRY_COMMAND.
    """

    # ── MCP HTTP remote surface (ADR-0029 §2.2 / §2.3; amended by ADR-0033) ─────

    mcp_auth_token: str | None = None
    """
    SECRET. Bootstrap bearer token for the /mcp/server HTTP surface (ADR-0029 §2.2).

    ADR-0033 §2.1 — precedence (most specific wins):
      1. vault_state.mcp_access_token_hash (UI-set token, PBKDF2 hash) — checked first.
      2. MCP_AUTH_TOKEN (this var, plaintext env bootstrap) — used iff DB hash is NULL.
      3. none — no token is configured.

    Existing deployments continue to work unchanged: if MCP_AUTH_TOKEN is set and no
    DB token has been set via PUT /mcp/auth, this env value is the active token.
    Never logged, never returned by any API endpoint.
    Env var: MCP_AUTH_TOKEN.
    """

    mcp_trusted_proxies: str = ""
    """
    Comma-separated list of trusted reverse-proxy IPs or CIDRs (ADR-0033 §2.3).

    When a request's immediate TCP peer (scope["client"][0]) matches one of these
    entries, the gateway reads the LAST X-Forwarded-For hop as the resolved client IP
    (the proxy-attested origin). When empty (default) — or when the peer is NOT in
    this list — X-Forwarded-For is IGNORED entirely (fail-safe against XFF spoofing).

    CF-Connecting-IP / CF-Ray are treated as PUBLIC *signals* regardless of this
    setting — their presence always forces PUBLIC classification, never grants private
    access (ADR-0033 §2.3 fail-safe).

    Default: "" (empty) ⇒ trust only the transport peer; XFF ignored.
    Env var: MCP_TRUSTED_PROXIES
    Example: "10.0.0.1,172.16.0.0/12"
    """

    mcp_remote_write_enabled: bool = False
    """
    Whether write_page is exposed on the HTTP MCP surface (ADR-0029 §2.3).
    Default false — read-only by default (defence-in-depth: even a leaked token
    cannot mutate the vault unless this flag is explicitly set).
    true  → write_page is included on the HTTP surface (still bearer-gated;
            still routes through write_wiki_page — ADR-0010 §2).
    false → only search_wiki, get_page, list_pages are exposed over HTTP.
    The stdio mcp server ALWAYS has all four tools, regardless of this flag.
    Env var: MCP_REMOTE_WRITE_ENABLED.
    """

    @property
    def mcp_http_enabled(self) -> bool:
        """
        True unconditionally (ADR-0033 §2.4 always-mount).

        The MCP HTTP capability is always compiled in. The middleware (_McpGate)
        is the sole per-request arbiter of reachability; mount condition is no
        longer "token set." The boolean is retained for backward-compat fields
        in McpInfoResponse (http_enabled alias).
        """
        return True

    @property
    def mcp_trusted_proxies_list(self) -> list[str]:
        """Parsed MCP_TRUSTED_PROXIES as a trimmed list (may be empty)."""
        return [p.strip() for p in self.mcp_trusted_proxies.split(",") if p.strip()]


# Module-level singleton — import with `from app.config import settings`
settings = Settings()
