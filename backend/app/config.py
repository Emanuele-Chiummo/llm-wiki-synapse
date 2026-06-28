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


# Module-level singleton — import with `from app.config import settings`
settings = Settings()
