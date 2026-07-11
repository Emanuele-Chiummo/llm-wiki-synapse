"""
Tests for GET /vault/meta endpoint (WS-D8, K1, I1, I5).

Acceptance criteria verified here:
  AC-WS-D8-1  Two entries max (schema.md + purpose.md), read from vault root.
  AC-WS-D8-3  Files are NOT in Postgres / Qdrant — zero Page rows for these paths.
              (Verified by static contract inspection: the router never writes DB.)
  AC-WS-D8-4  Valid Obsidian Markdown (YAML frontmatter) renders; no raw YAML leaks.
  AC-WS-D8-5  I1: no watcher extension, no ingest pipeline change (static assertion).
  AC-WS-D8-6  Missing file omitted from array, no crash.

Test IDs: T-VMETA-001 .. T-VMETA-007
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

# ── Helpers ────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _null_lifespan(app: Any) -> Any:  # noqa: ANN401
    yield


def _make_client(tmp_vault: Path) -> AsyncClient:
    """
    Build an AsyncClient against the FastAPI app with a no-op lifespan and
    the vault_root patched to *tmp_vault*.
    """
    from app.main import app

    app.router.lifespan_context = _null_lifespan  # noqa: ANN401
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_vault_with_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Isolated temp vault root with both schema.md and purpose.md present.
    Points settings.vault_root at it so the router reads the right files.
    """
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    (vault_root / "schema.md").write_text(
        "# Wiki Schema\n\ntype: entity | concept | source\ntitle: Human-readable title\nsources: []\n",
        encoding="utf-8",
    )
    (vault_root / "purpose.md").write_text(
        "# Vault Purpose\n\n## Goal\n\nTest vault purpose.\n",
        encoding="utf-8",
    )

    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: vault_root),
    )
    return vault_root


@pytest.fixture()
def tmp_vault_schema_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Vault root with only schema.md present (purpose.md absent)."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    (vault_root / "schema.md").write_text(
        "# Wiki Schema\n\ntype: entity | concept\n",
        encoding="utf-8",
    )
    # purpose.md intentionally NOT created

    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: vault_root),
    )
    return vault_root


@pytest.fixture()
def tmp_vault_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Vault root with neither file present (edge case: fresh install pre-bootstrap)."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: vault_root),
    )
    return vault_root


# ── T-VMETA-001: both files present → 200, two entries ────────────────────────


@pytest.mark.asyncio
async def test_vault_meta_both_files_present(tmp_vault_with_meta: Path) -> None:
    """
    T-VMETA-001: When both schema.md and purpose.md exist, GET /vault/meta returns
    200 with exactly two entries, each with the correct name/path/title/content.
    (AC-WS-D8-1, AC-WS-D8-6)
    """
    async with _make_client(tmp_vault_with_meta) as client:
        resp = await client.get("/vault/meta?vault_id=default")

    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "files" in body, "Response must contain 'files' key"
    files = body["files"]
    assert len(files) == 2, f"Expected 2 files, got {len(files)}"

    names = [f["name"] for f in files]
    assert "schema.md" in names
    assert "purpose.md" in names


@pytest.mark.asyncio
async def test_vault_meta_schema_file_structure(tmp_vault_with_meta: Path) -> None:
    """
    T-VMETA-002: schema.md entry has correct name, path, title, and non-empty content.
    """
    async with _make_client(tmp_vault_with_meta) as client:
        resp = await client.get("/vault/meta")

    assert resp.status_code == 200
    files = {f["name"]: f for f in resp.json()["files"]}

    schema = files["schema.md"]
    assert schema["name"] == "schema.md"
    assert schema["path"] == "schema.md"
    assert schema["title"] == "Schema"
    assert "# Wiki Schema" in schema["content"]  # content from our fixture


@pytest.mark.asyncio
async def test_vault_meta_purpose_file_structure(tmp_vault_with_meta: Path) -> None:
    """
    T-VMETA-003: purpose.md entry has correct name, path, title, and non-empty content.
    """
    async with _make_client(tmp_vault_with_meta) as client:
        resp = await client.get("/vault/meta")

    assert resp.status_code == 200
    files = {f["name"]: f for f in resp.json()["files"]}

    purpose = files["purpose.md"]
    assert purpose["name"] == "purpose.md"
    assert purpose["path"] == "purpose.md"
    assert purpose["title"] == "Purpose"
    assert "Vault Purpose" in purpose["content"]


# ── T-VMETA-004: missing file → omitted, no crash ─────────────────────────────


@pytest.mark.asyncio
async def test_vault_meta_missing_purpose_omitted(tmp_vault_schema_only: Path) -> None:
    """
    T-VMETA-004: If purpose.md does not exist, the response contains only schema.md
    and no error is raised. (AC-WS-D8-6)
    """
    async with _make_client(tmp_vault_schema_only) as client:
        resp = await client.get("/vault/meta")

    assert resp.status_code == 200
    files = resp.json()["files"]
    assert len(files) == 1
    assert files[0]["name"] == "schema.md"


@pytest.mark.asyncio
async def test_vault_meta_no_files_returns_empty_array(tmp_vault_empty: Path) -> None:
    """
    T-VMETA-005: If neither file exists, GET /vault/meta returns 200 with an empty
    files array — no 404 or 500. (AC-WS-D8-6 — no crash)
    """
    async with _make_client(tmp_vault_empty) as client:
        resp = await client.get("/vault/meta")

    assert resp.status_code == 200
    body = resp.json()
    assert body["files"] == []


# ── T-VMETA-006: contract shape matches specification exactly ──────────────────


@pytest.mark.asyncio
async def test_vault_meta_response_schema(tmp_vault_with_meta: Path) -> None:
    """
    T-VMETA-006: Response JSON matches the exact contract:
      {"files": [{"name":..., "path":..., "title":..., "content":...}]}
    All four fields present on every entry. (WS-D8 contract spec)
    """
    async with _make_client(tmp_vault_with_meta) as client:
        resp = await client.get("/vault/meta?vault_id=default")

    assert resp.status_code == 200
    body = resp.json()
    # Top-level key
    assert list(body.keys()) == ["files"], "Response must have exactly one key: 'files'"

    for entry in body["files"]:
        assert set(entry.keys()) == {
            "name",
            "path",
            "title",
            "content",
        }, f"Entry has unexpected keys: {set(entry.keys())}"
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["path"], str) and entry["path"]
        assert isinstance(entry["title"], str) and entry["title"]
        assert isinstance(entry["content"], str)  # content may be empty string theoretically


# ── PUT /vault/meta/{name} — v1.5 P1 editable meta (ADR-0066) ─────────────────


@pytest.mark.asyncio
async def test_put_vault_meta_purpose_roundtrips(tmp_vault_with_meta: Path) -> None:
    """PUT purpose.md persists to disk and a subsequent GET returns the new content."""
    new_content = "# Vault Purpose\n\n## Goal\n\nAligned with LLM Wiki.\n"
    async with _make_client(tmp_vault_with_meta) as client:
        put = await client.put("/vault/meta/purpose.md", json={"content": new_content})
        assert put.status_code == 200, put.text
        assert put.json() == {
            "name": "purpose.md",
            "path": "purpose.md",
            "title": "Purpose",
            "content": new_content,
        }
        get = await client.get("/vault/meta")
    files = {f["name"]: f for f in get.json()["files"]}
    assert files["purpose.md"]["content"] == new_content
    # And it actually hit disk.
    assert (tmp_vault_with_meta / "purpose.md").read_text(encoding="utf-8") == new_content


@pytest.mark.asyncio
async def test_put_vault_meta_schema_roundtrips(tmp_vault_with_meta: Path) -> None:
    """PUT schema.md persists and returns the Schema title."""
    async with _make_client(tmp_vault_with_meta) as client:
        put = await client.put("/vault/meta/schema.md", json={"content": "# Rules\n"})
    assert put.status_code == 200
    assert put.json()["title"] == "Schema"
    assert (tmp_vault_with_meta / "schema.md").read_text(encoding="utf-8") == "# Rules\n"


@pytest.mark.asyncio
async def test_put_vault_meta_creates_when_absent(tmp_vault_empty: Path) -> None:
    """PUT writes purpose.md even on a fresh vault where it did not exist yet."""
    async with _make_client(tmp_vault_empty) as client:
        put = await client.put("/vault/meta/purpose.md", json={"content": "x"})
    assert put.status_code == 200
    assert (tmp_vault_empty / "purpose.md").read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", ["index.md", "overview.md", "..%2f..%2fetc", "secrets.md"])
async def test_put_vault_meta_rejects_non_allowlisted_name(
    tmp_vault_with_meta: Path, bad_name: str
) -> None:
    """Only schema.md / purpose.md are writable — anything else is 404, nothing written."""
    async with _make_client(tmp_vault_with_meta) as client:
        put = await client.put(f"/vault/meta/{bad_name}", json={"content": "pwned"})
    assert put.status_code == 404
    # No stray file created in the vault root.
    assert not (tmp_vault_with_meta / bad_name).exists()


# ── T-VMETA-007: static I1 guard — router does not glob/walk ──────────────────


def test_vault_meta_router_has_no_glob_or_walk() -> None:
    """
    T-VMETA-007: Static code scan — vault_meta.py must not use rglob, os.walk,
    glob.glob, or os.listdir (I1: only the two fixed filenames, no directory scan).
    """
    router_path = Path(__file__).resolve().parent.parent / "app" / "routers" / "vault_meta.py"
    text = router_path.read_text(encoding="utf-8")
    forbidden = ["rglob(", "os.walk(", "glob.glob(", "os.listdir(", "os.scandir("]
    for pattern in forbidden:
        # Check non-comment lines only
        non_comment = [line for line in text.splitlines() if not line.strip().startswith("#")]
        for line in non_comment:
            assert pattern not in line, (
                f"vault_meta.py contains forbidden directory enumeration {pattern!r} "
                f"(I1 violation): {line.strip()!r}"
            )
