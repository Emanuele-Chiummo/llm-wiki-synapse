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


# Module-level singleton — import with `from app.config import settings`
settings = Settings()
