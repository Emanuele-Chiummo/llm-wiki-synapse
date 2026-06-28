"""
ConfigResolver resolution-order tests (infra-free).

Coverage (ADR-0008 §2, AC-F17-6):
    - operation+vault scope wins over vault scope
    - vault scope wins over global scope
    - global scope is the catch-all
    - missing global row → ConfigNotFoundError (I6 hard error, never silent default)
    - is_fallback=True rows are skipped by the primary resolver
    - resolve_fallback_provider_config returns the is_fallback=True row
    - vault-scoped fallback takes priority over global fallback
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.provider_config_service import ConfigNotFoundError


def _make_config_row(
    scope: str,
    vault_id: str | None = None,
    operation: str | None = None,
    provider_type: str = "api",
    model_id: str = "test-model",
    is_fallback: bool = False,
) -> Any:
    """Build a duck-typed ProviderConfig row stub."""
    row = MagicMock()
    row.id = uuid.uuid4()
    row.scope = scope
    row.vault_id = vault_id
    row.operation = operation
    row.provider_type = provider_type
    row.model_id = model_id
    row.is_fallback = is_fallback
    row.max_iter = 3
    row.token_budget = 60_000
    row.base_url = None
    return row


# ── Helper to run the resolver against a fixed set of rows ────────────────────


async def _resolve(
    rows: list[Any],
    operation: str = "ingest",
    vault_id: str = "test-vault",
) -> Any:
    """
    Run resolve_provider_config against *rows* by patching the DB session.

    Rows are filtered in Python to mimic the SQL WHERE logic.
    """
    from app.provider_config_service import resolve_provider_config

    def _match_primary(row: Any, op: str, vid: str) -> bool:
        """Replicates the three-level precedence logic."""
        if row.is_fallback:
            return False
        if row.scope == "operation" and row.vault_id == vid and row.operation == op:
            return True
        if row.scope == "vault" and row.vault_id == vid:
            return True
        if row.scope == "global":
            return True
        return False

    # We patch _query_one to mimic SQL with in-memory filtering.
    call_count: list[int] = [0]

    async def _fake_query_one(session: Any, where_clause: Any) -> Any:
        nonlocal call_count
        call_count[0] += 1
        # Determine which level this call is for by call order.
        n = call_count[0]
        if n == 1:
            # operation+vault
            matched = [
                r
                for r in rows
                if not r.is_fallback
                and r.scope == "operation"
                and r.vault_id == vault_id
                and r.operation == operation
            ]
        elif n == 2:
            # vault
            matched = [
                r
                for r in rows
                if not r.is_fallback and r.scope == "vault" and r.vault_id == vault_id
            ]
        else:
            # global
            matched = [r for r in rows if not r.is_fallback and r.scope == "global"]
        return matched[0] if matched else None

    with (
        patch("app.provider_config_service._query_one", side_effect=_fake_query_one),
        patch("app.provider_config_service.get_session") as mock_gs,
    ):
        # Make get_session() return an async context manager
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_gs.return_value = ctx

        return await resolve_provider_config(operation, vault_id)


class TestResolutionOrder:
    @pytest.mark.asyncio
    async def test_operation_vault_wins_over_vault(self) -> None:
        """operation+vault scope takes precedence over vault scope."""
        op_row = _make_config_row(
            "operation", vault_id="v1", operation="ingest", model_id="op-model"
        )
        vault_row = _make_config_row("vault", vault_id="v1", model_id="vault-model")
        global_row = _make_config_row("global", model_id="global-model")

        result = await _resolve([op_row, vault_row, global_row], vault_id="v1")
        assert result.model_id == "op-model"

    @pytest.mark.asyncio
    async def test_vault_wins_over_global(self) -> None:
        """vault scope takes precedence over global scope."""
        vault_row = _make_config_row("vault", vault_id="v1", model_id="vault-model")
        global_row = _make_config_row("global", model_id="global-model")

        result = await _resolve([vault_row, global_row], vault_id="v1")
        assert result.model_id == "vault-model"

    @pytest.mark.asyncio
    async def test_global_is_catch_all(self) -> None:
        """global scope is used when no operation+vault or vault row matches."""
        global_row = _make_config_row("global", model_id="global-model")

        result = await _resolve([global_row], vault_id="other-vault")
        assert result.model_id == "global-model"

    @pytest.mark.asyncio
    async def test_missing_global_raises_config_not_found(self) -> None:
        """No matching row → ConfigNotFoundError (I6 hard error)."""
        with pytest.raises(ConfigNotFoundError):
            await _resolve([], vault_id="no-vault")

    @pytest.mark.asyncio
    async def test_fallback_rows_skipped_by_primary_resolver(self) -> None:
        """is_fallback=True rows must NOT be returned by the primary resolver."""
        fallback_row = _make_config_row("global", model_id="fallback", is_fallback=True)

        # Only a fallback row + no primary row → should raise ConfigNotFoundError
        with pytest.raises(ConfigNotFoundError):
            await _resolve([fallback_row], vault_id="v1")

    @pytest.mark.asyncio
    async def test_operation_vault_skips_wrong_vault(self) -> None:
        """operation+vault row for vault_id='other' must NOT be returned for vault_id='v1'."""
        op_row = _make_config_row(
            "operation", vault_id="other", operation="ingest", model_id="wrong"
        )
        global_row = _make_config_row("global", model_id="correct")

        result = await _resolve([op_row, global_row], vault_id="v1")
        assert result.model_id == "correct"

    @pytest.mark.asyncio
    async def test_operation_vault_skips_wrong_operation(self) -> None:
        """operation+vault row for operation='chat' must NOT be returned for operation='ingest'."""
        op_row = _make_config_row("operation", vault_id="v1", operation="chat", model_id="wrong")
        global_row = _make_config_row("global", model_id="correct")

        result = await _resolve([op_row, global_row], vault_id="v1", operation="ingest")
        assert result.model_id == "correct"


class TestFallbackResolution:
    @pytest.mark.asyncio
    async def test_vault_fallback_wins_over_global_fallback(self) -> None:
        """vault-scoped fallback takes priority over global fallback."""
        from unittest.mock import AsyncMock, MagicMock, patch

        vault_fb = _make_config_row("vault", vault_id="v1", model_id="vault-fb", is_fallback=True)
        global_fb = _make_config_row("global", model_id="global-fb", is_fallback=True)

        call_count: list[int] = [0]

        async def _fake_query_one(session: Any, where_clause: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                # vault fallback
                return vault_fb
            return global_fb

        from app.provider_config_service import resolve_fallback_provider_config

        with (
            patch("app.provider_config_service._query_one", side_effect=_fake_query_one),
            patch("app.provider_config_service.get_session") as mock_gs,
        ):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_gs.return_value = ctx

            result = await resolve_fallback_provider_config(vault_id="v1")

        assert result is not None
        assert result.model_id == "vault-fb"

    @pytest.mark.asyncio
    async def test_global_fallback_when_no_vault_fallback(self) -> None:
        """global fallback is returned when no vault-scoped fallback exists."""
        from unittest.mock import AsyncMock, MagicMock, patch

        global_fb = _make_config_row("global", model_id="global-fb", is_fallback=True)

        call_count: list[int] = [0]

        async def _fake_query_one(session: Any, where_clause: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # no vault fallback
            return global_fb

        from app.provider_config_service import resolve_fallback_provider_config

        with (
            patch("app.provider_config_service._query_one", side_effect=_fake_query_one),
            patch("app.provider_config_service.get_session") as mock_gs,
        ):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_gs.return_value = ctx

            result = await resolve_fallback_provider_config(vault_id="v1")

        assert result is not None
        assert result.model_id == "global-fb"

    @pytest.mark.asyncio
    async def test_none_returned_when_no_fallback_configured(self) -> None:
        """None is returned when no fallback row exists (caller handles gracefully)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.provider_config_service import resolve_fallback_provider_config

        with (
            patch("app.provider_config_service._query_one", return_value=None),
            patch("app.provider_config_service.get_session") as mock_gs,
        ):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_gs.return_value = ctx

            result = await resolve_fallback_provider_config(vault_id="v1")

        assert result is None
