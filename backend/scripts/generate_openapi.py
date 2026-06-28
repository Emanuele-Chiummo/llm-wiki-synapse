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

    # Sanity checks (AC-D4-3)
    openapi_ver = schema.get("openapi", "")
    assert openapi_ver.startswith("3."), f"Expected OpenAPI 3.x, got {openapi_ver!r}"
    paths = schema.get("paths", {})
    for required_path in ["/status", "/pages", "/pages/{page_id}", "/ingest/trigger", "/graph"]:
        assert required_path in paths, f"Missing path {required_path!r} in openapi.json"
    print("Sanity check passed: all 5 required endpoints present (including GET /graph)")


if __name__ == "__main__":
    main()
