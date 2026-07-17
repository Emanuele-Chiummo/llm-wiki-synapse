"""
provider_config REST CRUD endpoint tests (F17, §12, ADR-0008).

Uses FastAPI TestClient with mocked DB session (no live Postgres).

Coverage:
    - GET /provider/config returns a list (ProviderConfigListResponse)
    - POST /provider/config creates a row and validates provider_type
    - POST rejects invalid provider_type values (only local|api|cli accepted)
    - POST rejects unknown scope values
    - POST requires vault_id when scope='vault' or 'operation'
    - POST requires operation when scope='operation'
    - POST body does NOT accept an api_key field (§12 — keys are env-only)
    - DELETE /provider/config/{id} returns 204 on success, 404 on missing
    - GET /ingest/trigger returns typed IngestTriggerResponse with task_id in schema (AC-D4u)
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from fastapi.testclient import TestClient

# ── Minimal session factory stub ──────────────────────────────────────────────


def _make_session_ctx(execute_return: Any = None, flush_ok: bool = True) -> Any:
    """Build an async context manager that returns a fake session."""

    class _FakeSession:
        def __init__(self) -> None:
            self.added: list[Any] = []
            self._execute_return = execute_return

        async def execute(self, stmt: Any) -> Any:
            if self._execute_return is not None:
                return self._execute_return
            mock = MagicMock()
            mock.scalars.return_value.all.return_value = []
            return mock

        def add(self, obj: Any) -> None:
            self.added.append(obj)

        async def flush(self) -> None:
            if not flush_ok:
                raise RuntimeError("flush failed")

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

        def expunge(self, obj: Any) -> None:
            pass

    ctx = MagicMock()
    sess = _FakeSession()
    ctx.__aenter__ = AsyncMock(return_value=sess)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, sess


# ── Helpers ────────────────────────────────────────────────────────────────────


def _valid_create_body(**overrides: Any) -> dict[str, Any]:
    """Return a valid POST /provider/config body."""
    base: dict[str, Any] = {
        "scope": "global",
        "provider_type": "api",
        "model_id": "claude-sonnet-4-6",
        "max_iter": 3,
        "token_budget": 60000,
        "is_fallback": False,
    }
    base.update(overrides)
    return base


# ── GET /provider/config ───────────────────────────────────────────────────────


class TestListProviderConfigs:
    def test_get_returns_list_response(self) -> None:
        """GET /provider/config returns a ProviderConfigListResponse."""
        ctx, sess = _make_session_ctx()
        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/provider/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_get_does_not_include_api_key_field(self) -> None:
        """API key must NOT appear in the response schema (§12)."""
        ctx, _ = _make_session_ctx()
        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/provider/config")
        assert resp.status_code == 200
        # No item should have an api_key field
        for item in resp.json().get("items", []):
            assert "api_key" not in item
            assert "key" not in item


# ── POST /provider/config ──────────────────────────────────────────────────────


class TestCreateProviderConfig:
    def _post(self, body: dict[str, Any]) -> Any:
        """POST /provider/config via TestClient with patched session."""
        fake_id = uuid.uuid4()
        from datetime import UTC, datetime

        now = datetime.now(UTC)

        # Build a fake ProviderConfig ORM row that will be returned after flush
        class _FakeRow:
            id = fake_id
            scope = body.get("scope", "global")
            operation = body.get("operation")
            vault_id = body.get("vault_id")
            provider_type = body.get("provider_type", "api")
            model_id = body.get("model_id", "test-model")
            base_url = body.get("base_url")
            max_iter = body.get("max_iter", 3)
            token_budget = body.get("token_budget", 60000)
            is_fallback = body.get("is_fallback", False)
            created_at = now
            updated_at = now

        ctx = MagicMock()
        fake_sess = MagicMock()
        fake_sess.add = MagicMock()
        fake_sess.flush = AsyncMock()
        fake_sess.commit = AsyncMock()
        fake_sess.rollback = AsyncMock()
        fake_sess.refresh = AsyncMock()
        # Upsert (v1.5.2): create_provider_config first SELECTs an existing identical row.
        # For the create-path tests, return None so it takes the INSERT branch.
        _no_match = MagicMock()
        _no_match.scalar_one_or_none.return_value = None
        fake_sess.execute = AsyncMock(return_value=_no_match)

        # Make ProviderConfig() return a fake row with the expected attributes
        ctx.__aenter__ = AsyncMock(return_value=fake_sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.main.get_session", return_value=ctx),
            patch("app.main.ProviderConfig") as mock_cls,
        ):
            mock_cls.return_value = _FakeRow()
            client = TestClient(app, raise_server_exceptions=False)
            return client.post("/provider/config", json=body)

    def test_valid_api_provider_creates_row(self) -> None:
        """Valid POST with provider_type='api' returns 201."""
        resp = self._post(_valid_create_body())
        assert resp.status_code == 201

    def test_valid_local_provider_creates_row(self) -> None:
        """Valid POST with provider_type='local' returns 201."""
        resp = self._post(_valid_create_body(provider_type="local"))
        assert resp.status_code == 201

    def test_valid_cli_provider_creates_row(self) -> None:
        """Valid POST with provider_type='cli' returns 201."""
        resp = self._post(_valid_create_body(provider_type="cli"))
        assert resp.status_code == 201

    def test_invalid_provider_type_rejected(self) -> None:
        """provider_type not in {local, api, cli} → 422 Unprocessable Entity."""
        resp = self._post(_valid_create_body(provider_type="openai"))
        assert resp.status_code == 422

    def test_invalid_scope_rejected(self) -> None:
        """scope not in {global, vault, operation} → 422."""
        resp = self._post(_valid_create_body(scope="datacenter"))
        assert resp.status_code == 422

    def test_invalid_operation_value_rejected(self) -> None:
        """operation not a routing op, vendor id, nor null → 422."""
        resp = self._post(_valid_create_body(scope="operation", vault_id="v1", operation="export"))
        assert resp.status_code == 422

    def test_vendor_id_operation_accepted(self) -> None:
        """The catalog toggle tags rows with operation=<vendor-id>; must be accepted (v1.5.1).

        Regression: the vendor-catalog activation POSTs operation='claude-cli' (etc.). Before the
        fix the validator only allowed {ingest, chat, lint} and 422'd, so the CLI provider row was
        never created and the toggle silently failed.
        """
        resp = self._post(
            _valid_create_body(scope="global", provider_type="cli", operation="claude-cli")
        )
        assert resp.status_code == 201

    def test_operation_scope_requires_operation_field(self) -> None:
        """scope='operation' without operation field → 422."""
        body = _valid_create_body(scope="operation", vault_id="v1")
        # operation not provided → should fail
        body.pop("operation", None)
        # This hits the Pydantic validator path; operation defaults to None,
        # but the endpoint requires it when scope='operation'.
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/provider/config", json=body)
        assert resp.status_code == 422

    def test_vault_scope_requires_vault_id(self) -> None:
        """scope='vault' without vault_id → 422."""
        body = _valid_create_body(scope="vault")
        # vault_id is None (not provided)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/provider/config", json=body)
        assert resp.status_code == 422

    def test_api_key_without_master_key_is_400(self, monkeypatch: Any) -> None:
        """
        W1: supplying api_key when key storage is unavailable must 400 (refuse to store),
        never crash and never store plaintext.

        secrets_crypto now falls back to an auto-generated key FILE when SYNAPSE_SECRET_KEY is
        unset (zero-setup UI key storage), so "no env var" alone no longer refuses. Force the
        genuine unavailable-storage path by disabling the key-file fallback too.
        """
        monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
        monkeypatch.setattr("app.secrets_crypto._read_or_create_key_file", lambda: None)
        body = _valid_create_body()
        body["api_key"] = "sk-secret-should-not-store"

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/provider/config", json=body)
        assert resp.status_code == 400
        assert "SYNAPSE_SECRET_KEY" in resp.json()["error"]["message"]

    def test_api_key_encrypted_and_never_echoed(self, monkeypatch: Any) -> None:
        """
        W1: with SYNAPSE_SECRET_KEY set, api_key is encrypted at rest (not plaintext) and the
        response NEVER echoes it — only api_key_configured / api_key_masked.
        """
        from app import secrets_crypto
        from cryptography.fernet import Fernet

        monkeypatch.setenv("SYNAPSE_SECRET_KEY", Fernet.generate_key().decode())
        plaintext = "sk-secret-leaked-key-6789"
        body = _valid_create_body()
        body["api_key"] = plaintext

        captured: dict[str, Any] = {}

        from datetime import UTC, datetime

        now = datetime.now(UTC)

        def _fake_ctor(**kwargs: Any) -> Any:
            captured.update(kwargs)

            class _Row:
                id = uuid.uuid4()
                scope = kwargs["scope"]
                operation = kwargs.get("operation")
                vault_id = kwargs.get("vault_id")
                provider_type = kwargs["provider_type"]
                model_id = kwargs["model_id"]
                base_url = kwargs.get("base_url")
                api_key_encrypted = kwargs.get("api_key_encrypted")
                reasoning_effort = kwargs.get("reasoning_effort")
                max_iter = kwargs["max_iter"]
                token_budget = kwargs["token_budget"]
                is_fallback = kwargs["is_fallback"]
                created_at = now
                updated_at = now

            return _Row()

        ctx = MagicMock()
        sess = MagicMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        sess.refresh = AsyncMock()
        # Upsert (v1.5.2): SELECT existing → None ⇒ INSERT path (this test asserts the insert).
        _no_match = MagicMock()
        _no_match.scalar_one_or_none.return_value = None
        sess.execute = AsyncMock(return_value=_no_match)
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.main.get_session", return_value=ctx),
            patch("app.main.ProviderConfig", side_effect=_fake_ctor),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/provider/config", json=body)

        assert resp.status_code == 201
        # Row stored ciphertext, not the plaintext.
        stored = captured["api_key_encrypted"]
        assert isinstance(stored, bytes)
        assert plaintext.encode() not in stored
        assert secrets_crypto.decrypt(stored) == plaintext
        # Response never contains the plaintext; exposes posture fields only.
        assert plaintext not in resp.text
        data = resp.json()
        assert "api_key" not in data
        assert data["api_key_configured"] is True
        assert data["api_key_masked"] == "…6789"

    def test_duplicate_identity_upserts_instead_of_inserting(self) -> None:
        """Repeated POST of the same logical identity UPDATES the existing row, not a duplicate.

        Regression (v1.5.2, verified live vs Postgres): setActive() (header dropdown) and
        addProvider() (vendor catalog) both POST here, and "active = newest row" — so pre-fix every
        activation INSERTed a new row and duplicates piled up (the user saw 3× "CLI / opus"). The
        handler now SELECTs a matching non-fallback row; when present it updates + bumps created_at
        (activate) and does NOT insert. Asserts: existing match ⇒ no session.add, row refreshed.
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC)

        class _Existing:
            id = uuid.uuid4()
            scope = "global"
            operation = None
            vault_id = None
            provider_type = "cli"
            model_id = "claude-opus-4-8"
            base_url = None
            api_key_encrypted = None
            reasoning_effort = None
            max_iter = 3
            token_budget = 60000
            is_fallback = False
            created_at = now
            updated_at = now

        existing = _Existing()
        match = MagicMock()
        match.scalar_one_or_none.return_value = existing

        async def _refresh(obj: Any) -> None:
            # Simulate the DB resolving the server-side `created_at = func.now()` bump.
            obj.created_at = now

        sess = MagicMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        sess.refresh = AsyncMock(side_effect=_refresh)
        sess.execute = AsyncMock(return_value=match)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/provider/config",
                json=_valid_create_body(provider_type="cli", model_id="claude-opus-4-8"),
            )
        assert resp.status_code == 201
        sess.add.assert_not_called()  # no duplicate row inserted
        sess.refresh.assert_awaited_once()  # existing row refreshed after the created_at bump
        assert resp.json()["model_id"] == "claude-opus-4-8"


# ── PUT /provider/config/{id} ──────────────────────────────────────────────────


class TestUpdateProviderConfig:
    def _put(self, config_id: uuid.UUID, body: dict[str, Any]) -> tuple[Any, Any]:
        """PUT /provider/config/{id} via TestClient with a patched async session.

        Returns (response, fake_session) so tests can assert on the session interaction.
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC)

        class _FakeRow:
            id = config_id
            scope = "global"
            operation = "claude-cli"
            vault_id = None
            provider_type = "cli"
            model_id = "old-model"
            base_url = None
            api_key_encrypted = None
            reasoning_effort = None
            max_iter = 3
            token_budget = 60000
            is_fallback = False
            created_at = now
            updated_at = now

        row = _FakeRow()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = row

        fake_sess = MagicMock()
        fake_sess.execute = AsyncMock(return_value=exec_result)
        fake_sess.flush = AsyncMock()
        fake_sess.refresh = AsyncMock()
        fake_sess.commit = AsyncMock()
        fake_sess.rollback = AsyncMock()

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.put(f"/provider/config/{config_id}", json=body)
        return resp, fake_sess

    def test_update_model_id_returns_200_and_refreshes_row(self) -> None:
        """PUT model_id returns 200 with the new model — and the handler MUST refresh the row first.

        Regression (v1.5.2, verified live vs Postgres/asyncpg): the handler serialized the row via
        `_provider_config_to_response(row)` after the UPDATE flush, but `updated_at` is server-side
        `onupdate=now()` and is EXPIRED after that flush. Reading it from the sync serializer without
        an `await session.refresh(row)` triggered an async lazy-load → `MissingGreenlet` → HTTP 500
        (selecting a model in Settings). Assert the fix: 200 + `session.refresh` awaited exactly once.
        """
        cid = uuid.uuid4()
        resp, sess = self._put(cid, {"model_id": "claude-opus-4-8"})
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "claude-opus-4-8"
        sess.refresh.assert_awaited_once()  # the fix — without it the real endpoint 500s

    def test_update_nonexistent_returns_404(self) -> None:
        """PUT on an id that resolves to no row returns 404 (not 500)."""
        from datetime import UTC, datetime

        _ = datetime.now(UTC)
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        fake_sess = MagicMock()
        fake_sess.execute = AsyncMock(return_value=exec_result)
        fake_sess.flush = AsyncMock()
        fake_sess.refresh = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_sess)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.put(f"/provider/config/{uuid.uuid4()}", json={"model_id": "m"})
        assert resp.status_code == 404


# ── DELETE /provider/config/{id} ───────────────────────────────────────────────


class TestDeleteProviderConfig:
    def test_delete_nonexistent_returns_404(self) -> None:
        """DELETE on an id that does not exist returns 404."""
        fake_result = MagicMock()
        fake_result.rowcount = 0

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=fake_result)
        sess.commit = AsyncMock()
        sess.rollback = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.delete(f"/provider/config/{uuid.uuid4()}")

        assert resp.status_code == 404

    def test_delete_existing_returns_204(self) -> None:
        """DELETE on an existing id returns 204 No Content."""
        fake_result = MagicMock()
        fake_result.rowcount = 1

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=fake_result)
        sess.commit = AsyncMock()
        sess.rollback = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.main.get_session", return_value=ctx):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.delete(f"/provider/config/{uuid.uuid4()}")

        assert resp.status_code == 204

    def test_delete_invalid_uuid_returns_422(self) -> None:
        """DELETE with a non-UUID id returns 422."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/provider/config/not-a-uuid")
        assert resp.status_code == 422


# ── AC-D4u: typed IngestTriggerResponse in OpenAPI schema ─────────────────────


class TestIngestTriggerResponseSchema:
    def test_task_id_appears_in_openapi_schema(self) -> None:
        """
        AC-D4u: POST /ingest/trigger must declare IngestTriggerResponse so that
        'task_id' appears in the OpenAPI schema (not just in the example block).
        """
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()

        # Find the schema for IngestTriggerResponse in components/schemas
        schemas = schema.get("components", {}).get("schemas", {})
        assert (
            "IngestTriggerResponse" in schemas
        ), "IngestTriggerResponse must appear in OpenAPI components/schemas (AC-D4u)"
        trigger_schema = schemas["IngestTriggerResponse"]
        props = trigger_schema.get("properties", {})
        assert (
            "task_id" in props
        ), "task_id must be a declared property of IngestTriggerResponse, not just an example"
        assert "status" in props
        assert "page_id" in props

    def test_provider_config_response_in_schema(self) -> None:
        """ProviderConfigResponse must appear in OpenAPI schema and have no api_key."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/openapi.json")
        schema = resp.json()
        schemas = schema.get("components", {}).get("schemas", {})
        assert "ProviderConfigResponse" in schemas

        config_schema = schemas["ProviderConfigResponse"]
        props = config_schema.get("properties", {})
        assert "api_key" not in props, "api_key must not appear in the OpenAPI schema (§12)"
