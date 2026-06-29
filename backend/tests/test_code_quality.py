"""
Code quality and secrets hygiene tests (AC-DC-5, AC-PG-4 supplemental).

Coverage:
  AC-DC-5   no secrets in committed files; .env is gitignored; no hardcoded passwords
  AC-PG-4   no raw SQL strings (supplemental to test_models_schema.py)
  I9 guard  no hardcoded model IDs in backend/app/ (CLAUDE.md §12)
  I6 guard  no hardcoded provider names in backend/app/

Test IDs: T-CQ-001 .. T-CQ-010
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_APP = _BACKEND / "app"
_REPO_ROOT = _BACKEND.parent


# ── AC-DC-5: no secrets in committed files ────────────────────────────────────


class TestNoSecretsInCode:
    """T-CQ-001..004 — AC-DC-5"""

    def test_env_is_in_gitignore(self) -> None:
        """T-CQ-001: .env must appear in .gitignore (not committed)."""
        gitignore = _REPO_ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore must exist in the repo root"
        text = gitignore.read_text(encoding="utf-8")
        assert ".env" in text, ".gitignore must contain '.env' to prevent secret leakage (AC-DC-5)"

    def test_no_hardcoded_passwords_in_app(self) -> None:
        """T-CQ-002: No obvious password strings in backend/app/."""
        # Patterns that strongly suggest hardcoded credentials
        dangerous_patterns = [
            r'password\s*=\s*["\'][^"\']{3,}["\']',  # password = "actual_value"
            r'secret\s*=\s*["\'][^"\']{3,}["\']',
            r'api_key\s*=\s*["\'][^"\']{5,}["\']',
        ]
        for py_file in _APP.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for pattern in dangerous_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                # Filter out obvious test/placeholder values
                real_matches = [
                    m
                    for m in matches
                    if not re.search(
                        r"(test|fake|dummy|example|placeholder|your|xxx)", m, re.IGNORECASE
                    )
                ]
                if real_matches:
                    pytest.fail(
                        f"Possible hardcoded credential in "
                        f"{py_file.relative_to(_BACKEND)}: {real_matches}"
                    )

    def test_no_database_url_with_credentials_in_app(self) -> None:
        """T-CQ-003: No hardcoded DATABASE_URL with real credentials in app/."""
        for py_file in _APP.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            # Look for postgresql:// URLs with non-placeholder credentials
            matches = re.findall(r'postgresql\+asyncpg://[^:]+:[^@]{3,}@[^/\s"\']+', text)
            real = [
                m
                for m in matches
                if not re.search(r"(dummy|test|fake|example|user|pass)", m, re.IGNORECASE)
            ]
            if real:
                pytest.fail(
                    f"Hardcoded DB URL with credentials in "
                    f"{py_file.relative_to(_BACKEND)}: {real}"
                )

    def test_docker_compose_no_hardcoded_secrets(self) -> None:
        """T-CQ-004: docker-compose.yml must not contain hardcoded passwords."""
        compose = _REPO_ROOT / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found — skip")
        text = compose.read_text(encoding="utf-8")
        # Should reference env vars, not literal passwords
        hardcoded = re.findall(r'POSTGRES_PASSWORD\s*:\s*["\']?[a-zA-Z0-9]{8,}["\']?', text)
        real = [
            h for h in hardcoded if not re.search(r"\$\{|\$\(|dummy|test|example", h, re.IGNORECASE)
        ]
        if real:
            pytest.fail(f"docker-compose.yml appears to have a hardcoded DB password: {real}")


# ── I9 guard: no hardcoded model IDs or new services ─────────────────────────


class TestNoHardcodedModelIDs:
    """T-CQ-005..007 — CLAUDE.md §12, I9"""

    # Allowed model IDs (from CLAUDE.md §12) — these appear in .md agent files only
    ALLOWED_MODELS = {
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "bge-m3",  # embedding model — read from env var in config, but name may appear in comments
    }

    def test_no_hardcoded_claude_model_ids_in_app(self) -> None:
        """
        T-CQ-005: CLAUDE.md §12 — model IDs must not be hardcoded in backend/app/.

        Model IDs must be read from settings/env vars, not embedded as string literals
        in Python code. The embeddings.py has 'bge-m3' as a default value for an env
        var override — that is acceptable IF it's a pydantic-settings default.
        """
        for py_file in _APP.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            # Check for Claude model IDs hardcoded outside of comments/docstrings
            for line in text.splitlines():
                stripped = line.strip()
                is_comment = stripped.startswith("#")
                is_docstring = stripped.startswith('"""') or stripped.startswith("'''")
                if is_comment or is_docstring:
                    continue
                # Detect string literals containing claude model IDs
                if re.search(r'"claude-(opus|sonnet|haiku)-\d', stripped):
                    pytest.fail(
                        f"Hardcoded Claude model ID in {py_file.name}: {stripped!r}\n"
                        "Model IDs must come from settings/env vars (CLAUDE.md §12)"
                    )

    def test_no_hardcoded_anthropic_api_key_in_app(self) -> None:
        """T-CQ-006: No hardcoded Anthropic API keys (sk-ant-...) in app/."""
        for py_file in _APP.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if re.search(r"sk-ant-[a-zA-Z0-9\-]+", text):
                pytest.fail(f"Anthropic API key found in {py_file.name} (AC-DC-5)")

    def test_embedding_model_read_from_settings(self) -> None:
        """
        T-CQ-007: I9 — embedding model name must come from Settings, not be hardcoded
        as a call-site string.

        The embeddings.py default ('bge-m3' as embedding_model field in Settings)
        is allowed — it's a pydantic-settings default, overridable via env var.
        """
        embed_file = _APP / "embeddings.py"
        text = embed_file.read_text(encoding="utf-8")
        # Should reference self._model which comes from settings.embedding_model
        assert "self._model" in text or "settings.embedding_model" in text, (
            "embeddings.py must read the model name from settings, not hardcode it "
            "(I9 — CLAUDE.md §12)"
        )


# ── I6 guard: no hardcoded provider in v0.1 ──────────────────────────────────


class TestNoHardcodedProvider:
    """T-CQ-008..009 — I6, ADR-0003"""

    @staticmethod
    def _executable_code_lines(path: Path) -> list[str]:
        """Return executable code lines, excluding comments and docstring content."""
        lines = path.read_text(encoding="utf-8").splitlines()
        result = []
        in_docstring = False
        triple_quote_char: str | None = None
        for line in lines:
            stripped = line.strip()
            # Handle triple-quote docstrings
            if not in_docstring:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    tq = '"""' if stripped.startswith('"""') else "'''"
                    count = stripped.count(tq)
                    if count == 1:
                        # Opening docstring
                        in_docstring = True
                        triple_quote_char = tq
                        continue
                    elif count >= 2:
                        # Single-line docstring — skip
                        continue
            else:
                if triple_quote_char and triple_quote_char in stripped:
                    in_docstring = False
                    triple_quote_char = None
                continue

            if stripped.startswith("#"):
                continue
            result.append(line)
        return result

    def test_orchestrator_has_no_provider_code(self) -> None:
        """
        T-CQ-008: I6, ADR-0003 — orchestrator.py must have no provider class
        instantiation or LLM call code in v0.1 executable lines.

        Provider names in docstrings/comments are allowed (they document the
        v0.2 extension point). Only actual code use is forbidden.
        """
        orch = _APP / "ingest" / "orchestrator.py"
        code_lines = self._executable_code_lines(orch)
        code_text = "\n".join(code_lines)

        # v0.1 must NOT have any of these as actual Python code (not comments/docstrings)
        forbidden_in_v01 = [
            "OllamaProvider(",
            "ApiProvider(",
            "CliAgentProvider(",
            "InferenceProvider(",
            "anthropic.messages",
            "ollama.chat(",
        ]
        for term in forbidden_in_v01:
            assert term not in code_text, (
                f"orchestrator.py must not call {term!r} in v0.1 executable code "
                "(ADR-0003 / I6); provider layer is v0.2 work"
            )

        # The extension point comment must exist in the full text (including docstrings)
        full_text = orch.read_text(encoding="utf-8")
        assert (
            "F17" in full_text or "EXTENSION POINT" in full_text
        ), "orchestrator.py must contain the F17 extension point comment (ADR-0003)"

    def test_main_has_no_provider_imports(self) -> None:
        """
        T-CQ-009: I6 — main.py must not import any InferenceProvider concrete class.

        NB-2 hardening: scope to import-statement lines only (lines starting with
        'import' or 'from ... import'), so a docstring or comment mentioning these
        class names does not trigger a false failure.
        """
        main = _APP / "main.py"
        lines = main.read_text(encoding="utf-8").splitlines()
        # Only check actual import lines
        import_lines = [ln for ln in lines if ln.strip().startswith(("import ", "from "))]
        import_text = "\n".join(import_lines)
        # InferenceProvider is the ABC; concrete subclasses are the real guard
        forbidden = ["OllamaProvider", "ApiProvider", "CliAgentProvider"]
        for term in forbidden:
            assert term not in import_text, (
                f"main.py must not import provider class {term!r} (I6 / ADR-0003). "
                "Provider references in docstrings/comments are allowed."
            )


# ── AC-F5-3: retrieval.py must not introduce a new embedding service ─────────


class TestRetrievalNoNewEmbeddingService:
    """
    AC-F5-3 (ADR-0022 §2.1, I9) — retrieval.py must use only the existing Qdrant wrapper
    (get_embedding_client / bge-m3) and must NOT import sentence_transformers, a raw 'bge'
    package, or create a new Qdrant collection outside the existing wrapper module.
    """

    def test_retrieval_does_not_import_sentence_transformers(self) -> None:
        """T-CQ-011: AC-F5-3 — retrieval.py must not import sentence_transformers (I9)."""
        retrieval = _APP / "rag" / "retrieval.py"
        text = retrieval.read_text(encoding="utf-8")
        assert "sentence_transformers" not in text, (
            "retrieval.py must not import sentence_transformers — "
            "reuse get_embedding_client() / bge-m3 (AC-F5-3, I9)"
        )

    def test_retrieval_does_not_create_new_qdrant_collection(self) -> None:
        """T-CQ-012: AC-F5-3 — retrieval.py must not call create_collection (I9)."""
        retrieval = _APP / "rag" / "retrieval.py"
        text = retrieval.read_text(encoding="utf-8")
        assert "create_collection" not in text, (
            "retrieval.py must not call create_collection — "
            "the existing synapse_pages collection is the only Qdrant collection (AC-F5-3, I9)"
        )

    def test_retrieval_uses_existing_embedding_wrapper(self) -> None:
        """T-CQ-013: AC-F5-3 — retrieval.py must use get_embedding_client() (I9)."""
        retrieval = _APP / "rag" / "retrieval.py"
        text = retrieval.read_text(encoding="utf-8")
        assert "get_embedding_client" in text, (
            "retrieval.py must call get_embedding_client() to embed queries — "
            "no new embedding service (AC-F5-3, I9)"
        )

    def test_no_new_embedding_service_in_retrieval_imports(self) -> None:
        """T-CQ-014: AC-F5-3 — retrieval.py import lines must not reference raw 'bge' package."""
        retrieval = _APP / "rag" / "retrieval.py"
        import_lines = [
            ln
            for ln in retrieval.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        import_text = "\n".join(import_lines)
        assert "import bge" not in import_text and "from bge" not in import_text, (
            "retrieval.py must not import the raw 'bge' package — "
            "use get_embedding_client() (AC-F5-3, I9)"
        )


# ── I7 guard: no unbounded loops in v0.1 code ────────────────────────────────


class TestNoBoundedLoopViolations:
    """T-CQ-010 — I7 (no loops in v0.1; structure must not preclude bounds later)"""

    def test_orchestrator_has_no_while_true_loop(self) -> None:
        """
        T-CQ-010: I7 — orchestrator.py must not have an unbounded while True loop.

        v0.1 has no loops at all (no LLM inference). If a while True appears,
        it means someone prematurely added the ingest loop without a max_iter bound.
        """
        orch = _APP / "ingest" / "orchestrator.py"
        text = orch.read_text(encoding="utf-8")
        assert "while True:" not in text, (
            "orchestrator.py must not have an unbounded 'while True:' loop (I7). "
            "v0.1 is straight-line; v0.2's loop must have max_iter + token_budget bounds."
        )
