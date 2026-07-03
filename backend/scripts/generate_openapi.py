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
        # F11 Web Clipper ingress (ADR-0038)
        "/clip",
        # F11 Web Clipper runtime config (ADR-0040)
        "/clip/config",
        # Sources view (nashsu/llm_wiki parity)
        "/sources",
        "/sources/content",
        "/sources/raw",
        "/sources/derived-pages",
        # Sources ingest-all (ADR-0006 explicit user action)
        "/sources/ingest-all",
        "/sources/ingest-all/status",
        # R11-1: Marker convert endpoints (ADR-0051)
        "/ingest/convert-marker",
        "/ingest/marker-health",
        # R11-2: app config override layer (ADR-0053)
        "/config/app",
        "/config/app/{key}",
        # R12-1: dashboard stats API (ADR-0054 §5, F18)
        "/stats/overview",
        "/stats/sections",
        # R12-1 A1: dynamic Louvain community groups (SPRINT-v1.2-SCOPE §10 A1)
        "/stats/groups",
    ]:
        assert required_path in paths, f"Missing path {required_path!r} in openapi.json"

    # ADR-0030: embeddings_enabled field in GET /config/embedding (EmbeddingConfigResponse)
    schemas = schema.get("components", {}).get("schemas", {})
    emb_schema = schemas.get("EmbeddingConfigResponse", {})
    emb_props = emb_schema.get("properties", {})
    assert (
        "embeddings_enabled" in emb_props
    ), "Missing 'embeddings_enabled' field in EmbeddingConfigResponse schema (ADR-0030)"

    # ADR-0029: http_enabled + remote_write_enabled fields in GET /mcp/info (McpInfoResponse)
    mcp_schema = schemas.get("McpInfoResponse", {})
    mcp_props = mcp_schema.get("properties", {})
    assert (
        "http_enabled" in mcp_props
    ), "Missing 'http_enabled' field in McpInfoResponse schema (ADR-0029)"
    assert (
        "remote_write_enabled" in mcp_props
    ), "Missing 'remote_write_enabled' field in McpInfoResponse schema (ADR-0029)"

    # ADR-0033: token_source + allow_without_token on McpInfoResponse + McpAuthStateResponse
    assert (
        "token_source" in mcp_props
    ), "Missing 'token_source' field in McpInfoResponse schema (ADR-0033)"
    assert (
        "allow_without_token" in mcp_props
    ), "Missing 'allow_without_token' field in McpInfoResponse schema (ADR-0033)"
    auth_resp_schema = schemas.get("McpAuthStateResponse", {})
    auth_resp_props = auth_resp_schema.get("properties", {})
    assert (
        "token_source" in auth_resp_props
    ), "Missing 'token_source' field in McpAuthStateResponse schema (ADR-0033)"
    assert (
        "allow_without_token" in auth_resp_props
    ), "Missing 'allow_without_token' field in McpAuthStateResponse schema (ADR-0033)"
    # CRITICAL: no raw token/hash/salt field exposed
    for field_name in ["plaintext_token", "raw_token", "token_value", "hash_value", "salt_value"]:
        assert (
            field_name not in auth_resp_props
        ), f"SECURITY: token/hash/salt field {field_name!r} must not appear in McpAuthStateResponse"
        assert (
            field_name not in mcp_props
        ), f"SECURITY: token/hash/salt field {field_name!r} must not appear in McpInfoResponse"

    # ADR-0040: clip config endpoints + token-never-leaked check
    clip_cfg_schema = schemas.get("ClipConfigResponse", {})
    clip_cfg_props = clip_cfg_schema.get("properties", {})
    assert (
        "token_configured" in clip_cfg_props
    ), "Missing 'token_configured' field in ClipConfigResponse schema (ADR-0040)"
    assert (
        "token_source" in clip_cfg_props
    ), "Missing 'token_source' field in ClipConfigResponse schema (ADR-0040)"
    # CRITICAL: raw clip token value must never appear in any schema property
    clip_state_schema = schemas.get("ClipConfigStateResponse", {})
    clip_state_props = clip_state_schema.get("properties", {})
    for field_name in ["plaintext_token", "raw_token", "token_value", "clip_token"]:
        assert (
            field_name not in clip_cfg_props
        ), f"SECURITY: field {field_name!r} must not appear in ClipConfigResponse (ADR-0040)"
        assert (
            field_name not in clip_state_props
        ), f"SECURITY: field {field_name!r} must not appear in ClipConfigStateResponse (ADR-0040)"

    # ADR-0052 / EC-M10-4: BearerAuth security scheme must be declared.
    security_schemes = schema.get("components", {}).get("securitySchemes", {})
    assert (
        "BearerAuth" in security_schemes
    ), "Missing 'BearerAuth' securityScheme in components (ADR-0052 §2.5, EC-M10-4)"
    bearer_scheme = security_schemes["BearerAuth"]
    assert bearer_scheme.get("type") == "http", (
        f"BearerAuth.type must be 'http', got {bearer_scheme.get('type')!r} (ADR-0052 §2.5)"
    )
    assert bearer_scheme.get("scheme") == "bearer", (
        f"BearerAuth.scheme must be 'bearer', got {bearer_scheme.get('scheme')!r} (ADR-0052 §2.5)"
    )

    # ADR-0052 §2.5: all non-exempt routes reference BearerAuth; exempt routes have security:[].
    _OPENAPI_SECURITY_EXEMPT_PATHS = {"/status", "/health/detailed"}
    paths_missing_security: list[str] = []
    exempt_paths_not_empty_security: list[str] = []
    for path, path_item in paths.items():
        for method, method_obj in path_item.items():
            if not isinstance(method_obj, dict):
                continue
            sec = method_obj.get("security")
            if path in _OPENAPI_SECURITY_EXEMPT_PATHS:
                if sec != []:
                    exempt_paths_not_empty_security.append(f"{method.upper()} {path}")
            else:
                if sec != [{"BearerAuth": []}]:
                    paths_missing_security.append(f"{method.upper()} {path}")
    assert not exempt_paths_not_empty_security, (
        f"These exempt paths must have security=[] (ADR-0052 §2.5): {exempt_paths_not_empty_security}"
    )
    assert not paths_missing_security, (
        f"These paths are missing BearerAuth security (ADR-0052 §2.5): {paths_missing_security[:10]}"
    )

    # ADR-0054 §6: StatusResponse must include 'version' field
    status_schema = schemas.get("StatusResponse", {})
    status_props = status_schema.get("properties", {})
    assert (
        "version" in status_props
    ), "Missing 'version' field in StatusResponse schema (ADR-0054 §6)"

    # ADR-0054 §5: /stats/overview and /stats/sections must be present with BearerAuth
    for stats_path in ["/stats/overview", "/stats/sections"]:
        path_item = paths.get(stats_path, {})
        assert path_item, f"Missing stats path {stats_path!r} in openapi.json (ADR-0054 §5)"
        for method_obj in path_item.values():
            if isinstance(method_obj, dict):
                sec = method_obj.get("security")
                assert sec == [{"BearerAuth": []}], (
                    f"{stats_path} must require BearerAuth (ADR-0054 §5, ADR-0052)"
                )

    print(
        "Sanity check passed: all 34 required endpoints present (incl. /clip, /clip/config — "
        "ADR-0038, ADR-0040; /sources/* — Sources view; /sources/ingest-all — ADR-0006); "
        "embeddings_enabled, http_enabled, remote_write_enabled confirmed "
        "(ADR-0029, ADR-0030); token_source, allow_without_token confirmed in McpInfoResponse + "
        "McpAuthStateResponse (ADR-0033); token_configured, token_source confirmed in "
        "ClipConfigResponse (ADR-0040); no token/hash/salt field exposed (no-leak check PASS); "
        "BearerAuth securityScheme declared + all non-exempt routes reference it + "
        "/status and /health/detailed have security=[] (ADR-0052 §2.5, EC-M10-4); "
        "/ingest/convert-marker, /ingest/marker-health (R11-1 / ADR-0051); "
        "/config/app, /config/app/{key} (R11-2 / ADR-0053); "
        "/stats/overview, /stats/sections (R12-1 / ADR-0054 §5, F18); "
        "/stats/groups (R12-1 A1 / SPRINT-v1.2-SCOPE §10 A1, F18); "
        "StatusResponse.version confirmed (ADR-0054 §6)"
    )


if __name__ == "__main__":
    main()
