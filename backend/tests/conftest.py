"""
Shared test fixtures and environment setup for Synapse backend tests.

Sets the required environment variables BEFORE any app module is imported,
so Settings() does not blow up in tests that don't use a real DB/Qdrant (GAP-4).
"""

from __future__ import annotations

import os

# ── Set dummy env vars before any app import resolves Settings() ──────────────
# These values are intentionally fake — unit tests that are infra-free
# (test_watcher_hash, test_frontmatter) don't connect to any service.
# Integration tests override these via fixtures or monkeypatch.

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("EMBEDDING_URL", "http://localhost:11434/api/embeddings")
os.environ.setdefault("EMBEDDING_DIM", "1024")
os.environ.setdefault("VAULT_ID", "test")
os.environ.setdefault("VAULT_PATH", "/tmp/synapse-test-vault")

# Disable rate limiting in the test suite.  The module-level _limiter singleton
# would accumulate request counts across all test runs in the same session,
# causing legitimate requests to hit the 429 limit after ~20 calls from the same
# IP ("testclient").  Disabling the limiter here is equivalent to running in CI
# with RATE_LIMIT_ENABLED=false.  The limiter itself is exercised directly in
# test_r13_security.py::TestFixedWindowLimiter using fresh _FixedWindowLimiter
# instances that bypass this flag.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
