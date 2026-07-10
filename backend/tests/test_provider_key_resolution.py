"""
Provider-layer stored-key resolution tests (W1 / F17, I6).

Covers:
    - _settings_from_row decrypts api_key_encrypted into ProviderSettings.api_key
    - a tampered/foreign ciphertext or missing master key degrades api_key to None (env fallback)
    - reasoning_effort threads from the row into ProviderSettings
    - ApiProvider prefers the stored (config) key over the env-var key
    - the Alembic 0026 migration module is well-formed (revision chain + up/down callables)
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from app import secrets_crypto
from app.ingest.provider import _settings_from_row
from app.ingest.provider.api import ApiProvider
from app.ingest.provider.config import ProviderSettings
from cryptography.fernet import Fernet


@dataclass
class _Row:
    provider_type: str = "api"
    model_id: str = "test-model"
    base_url: str | None = None
    max_iter: int = 3
    token_budget: int = 60000
    is_fallback: bool = False
    api_key_encrypted: bytes | None = None
    reasoning_effort: str | None = None


def test_settings_decrypts_stored_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", Fernet.generate_key().decode())
    row = _Row(api_key_encrypted=secrets_crypto.encrypt("sk-stored-key"), reasoning_effort="high")
    settings = _settings_from_row(row)
    assert settings.api_key == "sk-stored-key"
    assert settings.reasoning_effort == "high"


def test_settings_degrades_when_no_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Encrypt under a key, then remove it → resolution degrades to None (env fallback), no raise.
    key = Fernet.generate_key()
    ciphertext = Fernet(key).encrypt(b"sk-stored-key")
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    settings = _settings_from_row(_Row(api_key_encrypted=ciphertext))
    assert settings.api_key is None


def test_settings_degrades_on_tamper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", Fernet.generate_key().decode())
    tampered = bytearray(secrets_crypto.encrypt("sk-stored-key"))
    tampered[-1] ^= 0x01
    settings = _settings_from_row(_Row(api_key_encrypted=bytes(tampered)))
    assert settings.api_key is None


def test_settings_none_when_no_stored_key() -> None:
    settings = _settings_from_row(_Row(api_key_encrypted=None))
    assert settings.api_key is None
    assert settings.reasoning_effort is None


def test_api_provider_prefers_stored_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    provider = ApiProvider(
        ProviderSettings(provider_type="api", model_id="m", api_key="stored-key")
    )
    assert provider._anthropic_key() == "stored-key"


def test_api_provider_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    provider = ApiProvider(ProviderSettings(provider_type="api", model_id="m"))
    assert provider._anthropic_key() == "env-key"


def test_reasoning_applied_only_when_opted_in() -> None:
    # Default (no reasoning) leaves the request body untouched (degrade-safe).
    provider = ApiProvider(ProviderSettings(provider_type="api", model_id="m"))
    body: dict[str, object] = {"max_tokens": 4096}
    provider._apply_reasoning(body, anthropic=True)
    assert "thinking" not in body

    # High effort on Anthropic adds a thinking block and bumps max_tokens above the budget.
    provider2 = ApiProvider(
        ProviderSettings(provider_type="api", model_id="m", reasoning_effort="high")
    )
    body2: dict[str, object] = {"max_tokens": 4096}
    provider2._apply_reasoning(body2, anthropic=True)
    assert body2["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert int(body2["max_tokens"]) > 8192  # bumped above the budget

    # OpenAI-compatible path maps to reasoning_effort.
    body3: dict[str, object] = {}
    provider2._apply_reasoning(body3, anthropic=False)
    assert body3["reasoning_effort"] == "high"


def test_migration_0026_is_well_formed() -> None:
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "0026_provider_config_api_key_and_reasoning.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0026", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0026"
    assert mod.down_revision == "0025"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)
