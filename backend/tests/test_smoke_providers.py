"""
3-Provider smoke matrix — pytest entry point (EC-M2-5, F17, GAP-v0.2-1).

Tests marked @pytest.mark.live are SKIPPED in CI unit-test runs.
They require live infrastructure (Ollama + Anthropic API key + claude-agent-sdk).

Tests NOT marked live run the deterministic mock provider (SYNAPSE_SMOKE_MOCK=1)
and DO run in CI — they validate the harness wiring (schema, routing, cost logging,
wikilink parsing, index.md update) without live inference.

EC-M2-5 HUMAN CHECKPOINT:
  The @pytest.mark.live tests must be run on TrueNAS by Emanuele before M2 sign-off.
  Results are confirmed at EC-M2-17. The CI mock tests satisfy the automated gate;
  the live tests satisfy the "3-provider live smoke matrix" requirement.

DEFERRED-TO-LIVE ACs (GAP-v0.2-1):
  AC-K2-1  OllamaProvider live ingest (requires Ollama on TrueNAS)
  AC-K2-2  ApiProvider live ingest (requires ANTHROPIC_API_KEY)
  AC-K2-3  CliAgentProvider live ingest (requires claude-agent-sdk + ANTHROPIC_API_KEY)
  AC-MCP-8 CLI provider uses MCP write_page tool (requires full live run)
"""

from __future__ import annotations

import importlib.util
import os
import sys  # needed in _load_run_smoke_backend for sys.modules registration
import tempfile
from pathlib import Path

import pytest

# Add backend/ to sys.path so imports work when pytest is run from backend/
_BACKEND = Path(__file__).resolve().parent.parent

FIXTURE_SOURCE = _BACKEND / "tests" / "fixtures" / "sample-source.md"
FIXTURE_ORIGIN = "raw/sources/sample-source.md"
_SMOKE_SCRIPT = _BACKEND / "scripts" / "smoke_providers.py"


def _load_run_smoke_backend():  # type: ignore[return]
    """Load _run_smoke_backend from the smoke script via importlib (avoids package naming issues).

    The module is registered in sys.modules under its spec name BEFORE exec_module so that
    Python 3.13 dataclass string-annotation resolution can look up the module by name.
    Without this, @dataclass fields with 'str | None' annotations raise AttributeError on
    'NoneType object has no attribute __dict__' (Python 3.13 regression with from __future__
    import annotations + dynamic module loading).
    """
    _MOD_NAME = "smoke_providers"
    if _MOD_NAME in sys.modules:
        # Return cached module to avoid double-exec
        return sys.modules[_MOD_NAME]._run_smoke_backend
    spec = importlib.util.spec_from_file_location(_MOD_NAME, _SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod  # register BEFORE exec so dataclass annotation lookups succeed
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod._run_smoke_backend


def _set_mock_env() -> None:
    """Set SYNAPSE_SMOKE_MOCK=1 and minimal env vars for the mock path."""
    os.environ["SYNAPSE_SMOKE_MOCK"] = "1"
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://dummy:dummy@localhost/dummy")
    os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
    os.environ.setdefault("EMBEDDING_URL", "http://localhost:11434/api/embeddings")
    os.environ.setdefault("EMBEDDING_DIM", "1024")


# ── Mock-path smoke tests (run in CI) ────────────────────────────────────────


class TestSmokeLocalMock:
    """Mock path — Local (Ollama) backend wiring (CI-safe)."""

    @pytest.mark.asyncio
    async def test_local_mock_orchestrated_route_and_schema(self) -> None:
        """
        Local provider (mock): route=orchestrated; converged=True;
        total_cost_usd==0.0; pages_written>=1; ingest_runs row written.

        AC-K2-1 (mock path — live path is DEFERRED-TO-LIVE per GAP-v0.2-1).
        """
        _set_mock_env()
        _run_smoke_backend = _load_run_smoke_backend()
        with tempfile.TemporaryDirectory(prefix="synapse-smoke-local-") as tmpdir:
            tmp_vault = Path(tmpdir) / "vault"
            result = await _run_smoke_backend("local", tmp_vault)

        assert result.passed, f"Local smoke FAILED: {result.detail}"
        assert result.route == "orchestrated", "Local must use orchestrated route (I6)"
        assert result.total_cost_usd == 0.0, "Local backend must have cost=$0.00 (ADR-0009)"
        assert result.converged is True
        assert result.pages_written >= 1


class TestSmokeApiMock:
    """Mock path — API (Anthropic) backend wiring (CI-safe)."""

    @pytest.mark.asyncio
    async def test_api_mock_orchestrated_route_and_schema(self) -> None:
        """
        API provider (mock): route=orchestrated; converged=True;
        total_cost_usd==0.0 (mock path); pages_written>=1.

        AC-K2-2 (mock path — live path is DEFERRED-TO-LIVE per GAP-v0.2-1).
        """
        _set_mock_env()
        _run_smoke_backend = _load_run_smoke_backend()
        with tempfile.TemporaryDirectory(prefix="synapse-smoke-api-") as tmpdir:
            tmp_vault = Path(tmpdir) / "vault"
            result = await _run_smoke_backend("api", tmp_vault)

        assert result.passed, f"API smoke FAILED: {result.detail}"
        assert result.route == "orchestrated", "API must use orchestrated route (I6)"
        assert result.converged is True
        assert result.pages_written >= 1


class TestSmokeCliMock:
    """Mock path — CLI (agent-sdk) backend wiring (CI-safe)."""

    @pytest.mark.asyncio
    async def test_cli_mock_delegated_route_and_cost_zero(self) -> None:
        """
        CLI provider (mock): route=delegated; converged=True;
        total_cost_usd==0.0 (ADR-0009 CLI convention); pages_written>=1.

        AC-K2-3 (mock path — live path is DEFERRED-TO-LIVE per GAP-v0.2-1).
        """
        _set_mock_env()
        _run_smoke_backend = _load_run_smoke_backend()
        with tempfile.TemporaryDirectory(prefix="synapse-smoke-cli-") as tmpdir:
            tmp_vault = Path(tmpdir) / "vault"
            result = await _run_smoke_backend("cli", tmp_vault)

        assert result.passed, f"CLI smoke FAILED: {result.detail}"
        assert result.route == "delegated", "CLI must use delegated route (I6 capability routing)"
        assert result.total_cost_usd == 0.0, "CLI cost must be $0.00 (ADR-0009)"
        assert result.converged is True
        assert result.pages_written >= 1


# ── Live-path tests (skip without env) ───────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("OLLAMA_URL") and os.environ.get("SMOKE_LOCAL_MODEL")),
    reason="OLLAMA_URL + SMOKE_LOCAL_MODEL not set — live test DEFERRED (GAP-v0.2-1)",
)
class TestSmokeLocalLive:
    """
    LIVE path — OllamaProvider on TrueNAS (EC-M2-5, AC-K2-1).
    Run: pytest tests/test_smoke_providers.py::TestSmokeLocalLive -m live
    Env: OLLAMA_URL, SMOKE_LOCAL_MODEL, DATABASE_URL, QDRANT_URL, EMBEDDING_URL
    """

    @pytest.mark.asyncio
    async def test_local_live_ingest(self) -> None:
        os.environ.pop("SYNAPSE_SMOKE_MOCK", None)
        _run_smoke_backend = _load_run_smoke_backend()
        with tempfile.TemporaryDirectory(prefix="synapse-smoke-live-local-") as tmpdir:
            tmp_vault = Path(tmpdir) / "vault"
            result = await _run_smoke_backend("local", tmp_vault)
        assert result.passed, f"Local LIVE smoke FAILED: {result.detail}"
        assert result.route == "orchestrated"
        assert result.total_cost_usd == 0.0
        assert result.pages_written >= 1


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — live test DEFERRED (GAP-v0.2-1)",
)
class TestSmokeApiLive:
    """
    LIVE path — ApiProvider (Anthropic) (EC-M2-5, AC-K2-2).
    Run: pytest tests/test_smoke_providers.py::TestSmokeApiLive -m live
    Env: ANTHROPIC_API_KEY, SMOKE_API_MODEL, PROVIDER_PRICE_MAP
    """

    @pytest.mark.asyncio
    async def test_api_live_ingest(self) -> None:
        os.environ.pop("SYNAPSE_SMOKE_MOCK", None)
        _run_smoke_backend = _load_run_smoke_backend()
        with tempfile.TemporaryDirectory(prefix="synapse-smoke-live-api-") as tmpdir:
            tmp_vault = Path(tmpdir) / "vault"
            result = await _run_smoke_backend("api", tmp_vault)
        assert result.passed, f"API LIVE smoke FAILED: {result.detail}"
        assert result.route == "orchestrated"
        assert result.total_cost_usd > 0.0, "Anthropic API ingest must log real cost >$0"
        assert result.pages_written >= 1


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — live test DEFERRED (GAP-v0.2-1)",
)
class TestSmokeCliLive:
    """
    LIVE path — CliAgentProvider (claude-agent-sdk) (EC-M2-5, AC-K2-3, AC-MCP-8).
    Run: pytest tests/test_smoke_providers.py::TestSmokeCliLive -m live
    Env: ANTHROPIC_API_KEY, SMOKE_CLI_MODEL, claude-agent-sdk installed
    """

    @pytest.mark.asyncio
    async def test_cli_live_ingest(self) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            pytest.skip("claude-agent-sdk not installed")
        os.environ.pop("SYNAPSE_SMOKE_MOCK", None)
        _run_smoke_backend = _load_run_smoke_backend()
        with tempfile.TemporaryDirectory(prefix="synapse-smoke-live-cli-") as tmpdir:
            tmp_vault = Path(tmpdir) / "vault"
            result = await _run_smoke_backend("cli", tmp_vault)
        assert result.passed, f"CLI LIVE smoke FAILED: {result.detail}"
        assert result.route == "delegated"
        assert result.total_cost_usd == 0.0
        assert result.pages_written >= 1
