"""
GET /provider/vendors — vendor catalog endpoint tests (W1 / F17).

Covers:
    - endpoint returns the full catalog with the required per-vendor fields
    - every provider_type is one of api|local|cli; local/cli have needs_api_key=False
    - Anthropic advertises the real current model ids (CLAUDE.md §12) and no base_url
    - the expected vendor ids from the W1 brief are all present
"""

from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient

_EXPECTED_IDS = {
    "anthropic",
    "claude-cli",
    "codex-cli",
    "openai",
    "gemini",
    "azure-openai",
    "deepseek",
    "atlas-cloud",
    "groq",
    "xai",
    "nvidia-nim",
    "kimi-moonshot",
    "kimi-cn",
    "kimi-coding",
    "ollama",
}


def test_vendors_endpoint_returns_catalog() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/provider/vendors")
    assert resp.status_code == 200
    vendors = resp.json()["vendors"]
    ids = {v["id"] for v in vendors}
    assert _EXPECTED_IDS <= ids

    required = {
        "id",
        "display_name",
        "provider_type",
        "default_base_url",
        "needs_api_key",
        "model_presets",
        "notes",
    }
    for v in vendors:
        assert required <= set(v.keys())
        assert v["provider_type"] in {"api", "local", "cli"}
        assert isinstance(v["model_presets"], list)
        if v["provider_type"] in {"local", "cli"}:
            assert v["needs_api_key"] is False


def test_anthropic_vendor_uses_real_model_ids() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    vendors = client.get("/provider/vendors").json()["vendors"]
    anthropic = next(v for v in vendors if v["id"] == "anthropic")
    assert anthropic["provider_type"] == "api"
    assert anthropic["default_base_url"] is None  # Anthropic-native path
    assert anthropic["needs_api_key"] is True
    assert "claude-opus-4-8" in anthropic["model_presets"]
    assert "claude-sonnet-4-6" in anthropic["model_presets"]
    assert "claude-haiku-4-5-20251001" in anthropic["model_presets"]


def test_ollama_vendor_is_local_keyless() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    vendors = client.get("/provider/vendors").json()["vendors"]
    ollama = next(v for v in vendors if v["id"] == "ollama")
    assert ollama["provider_type"] == "local"
    assert ollama["needs_api_key"] is False
    assert ollama["default_base_url"] is None
