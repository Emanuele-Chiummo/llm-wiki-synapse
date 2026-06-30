"""
Generate docs/api/openapi.json from the FastAPI app (I8, AC-DC-4, AC-D4-*).

Run via: python backend/scripts/generate_openapi.py
Or via:  make openapi

The OpenAPI JSON is generated from the live FastAPI routes — never hand-written.
Source of truth: backend/app/main.py
Output: docs/api/openapi.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

# Set required env vars with dummy values so Settings() does not blow up
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://dummy:dummy@localhost/dummy")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("EMBEDDING_URL", "http://localhost:11434/api/embeddings")
os.environ.setdefault("EMBEDDING_DIM", "1024")

# Import the app WITHOUT triggering the lifespan (we only need the schema)
from app.main import app  # noqa: E402

OUTFILE = _REPO_ROOT / "docs" / "api" / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTFILE.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {OUTFILE}")

    # Sanity checks (AC-D4-3 + AC-F5-6 + AC-F6-5 — I8)
    openapi_ver = schema.get("openapi", "")
    assert openapi_ver.startswith("3."), f"Expected OpenAPI 3.x, got {openapi_ver!r}"
    paths = schema.get("paths", {})
    for required_path in [
        "/status",
        "/pages",
        "/pages/{page_id}",
        "/ingest/trigger",
        "/ingest/upload",
        "/ingest/from-text",
        "/search",
        "/graph",
        "/import-schedule",
        "/import-schedule/run-now",
        # F9 review queue (ADR-0025 §3.5)
        "/review/queue",
        "/review/queue/{item_id}/approve",
        "/review/queue/{item_id}/skip",
        "/review/queue/{item_id}/deep-research",
        # F10 deep research
        "/research/start",
        "/research/runs",
        # F1-MCP-UI (ADR-0027) + Remote MCP (ADR-0029) + MCP auth (ADR-0033)
        "/mcp/info",
        "/mcp/auth",
        # Embeddings config (ADR-0030)
        "/config/embedding",
    ]:
        assert required_path in paths, f"Missing path {required_path!r} in openapi.json"

    # ADR-0030: embeddings_enabled field in GET /config/embedding (EmbeddingConfigResponse)
    schemas = schema.get("components", {}).get("schemas", {})
    emb_schema = schemas.get("EmbeddingConfigResponse", {})
    emb_props = emb_schema.get("properties", {})
    assert "embeddings_enabled" in emb_props, \
        "Missing 'embeddings_enabled' field in EmbeddingConfigResponse schema (ADR-0030)"

    # ADR-0029: http_enabled + remote_write_enabled fields in GET /mcp/info (McpInfoResponse)
    mcp_schema = schemas.get("McpInfoResponse", {})
    mcp_props = mcp_schema.get("properties", {})
    assert "http_enabled" in mcp_props, \
        "Missing 'http_enabled' field in McpInfoResponse schema (ADR-0029)"
    assert "remote_write_enabled" in mcp_props, \
        "Missing 'remote_write_enabled' field in McpInfoResponse schema (ADR-0029)"

    # ADR-0033: token_source + allow_without_token on McpInfoResponse + McpAuthStateResponse
    assert "token_source" in mcp_props, \
        "Missing 'token_source' field in McpInfoResponse schema (ADR-0033)"
    assert "allow_without_token" in mcp_props, \
        "Missing 'allow_without_token' field in McpInfoResponse schema (ADR-0033)"
    auth_resp_schema = schemas.get("McpAuthStateResponse", {})
    auth_resp_props = auth_resp_schema.get("properties", {})
    assert "token_source" in auth_resp_props, \
        "Missing 'token_source' field in McpAuthStateResponse schema (ADR-0033)"
    assert "allow_without_token" in auth_resp_props, \
        "Missing 'allow_without_token' field in McpAuthStateResponse schema (ADR-0033)"
    # CRITICAL: no raw token/hash/salt field exposed
    for field_name in ["plaintext_token", "raw_token", "token_value", "hash_value", "salt_value"]:
        assert field_name not in auth_resp_props, \
            f"SECURITY: token/hash/salt field {field_name!r} must not appear in McpAuthStateResponse"
        assert field_name not in mcp_props, \
            f"SECURITY: token/hash/salt field {field_name!r} must not appear in McpInfoResponse"

    print(
        "Sanity check passed: all 19 required endpoints present; "
        "embeddings_enabled, http_enabled, remote_write_enabled confirmed (ADR-0029, ADR-0030); "
        "token_source, allow_without_token confirmed in McpInfoResponse + McpAuthStateResponse "
        "(ADR-0033); no token/hash/salt field exposed (no-leak check PASS)"
    )


if __name__ == "__main__":
    main()
