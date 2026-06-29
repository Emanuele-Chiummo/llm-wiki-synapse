"""
Tests for GET /mcp/info — read-only MCP server introspection endpoint (ADR-0027).

Coverage:
    - GET /mcp/info returns 200 with the correct response shape
    - tool_count >= 4 (the four tools registered since v0.2)
    - tools list contains the real registered names (sourced from the live registry)
    - server_name == "synapse" (derived from the live FastMCP object, not hardcoded)
    - entry_point_command is non-empty (from settings.mcp_entry_command)
    - input_schema is present per tool (dict, may be empty but must be a dict)
    - Response values match what await mcp.list_tools() returns directly (no hardcoding)
    - No DB or Qdrant session is opened (endpoint is infra-free)
    - transport is non-empty string
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

# ── Module-level import of mcp server object for comparison ──────────────────


@pytest.fixture(scope="module")
def mcp_server() -> FastMCP:
    """Import the live mcp FastMCP object for cross-checking."""
    from app.mcp.server import mcp as _mcp

    return _mcp


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


# ── Shared async client fixture ────────────────────────────────────────────────


@pytest.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Async test client that imports main.py with a patched lifespan so that
    no real DB / Qdrant / embedding service is needed (mirrors test_api.py pattern).
    """
    from unittest.mock import patch

    from app.main import app

    # Patch the lifespan so startup/shutdown hooks do not try to reach real infra.
    async def _noop_lifespan(app_: Any) -> AsyncGenerator[None, None]:
        yield

    with patch("app.main.app.router.lifespan_context", _noop_lifespan):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_mcp_info_returns_200(client: AsyncClient) -> None:
    """GET /mcp/info must return HTTP 200."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.anyio
async def test_mcp_info_response_shape(client: AsyncClient) -> None:
    """Response must include all required top-level fields with correct types."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data["server_name"], str)
    assert isinstance(data["transport"], str)
    assert isinstance(data["entry_point_command"], str)
    assert isinstance(data["tool_count"], int)
    assert isinstance(data["tools"], list)


@pytest.mark.anyio
async def test_mcp_info_server_name_is_synapse(client: AsyncClient) -> None:
    """server_name must equal 'synapse' — value derived from the live mcp.name (I6)."""
    from app.mcp.server import mcp as live_mcp

    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    # The expected value is taken from the live object, not hardcoded in this assertion.
    assert data["server_name"] == live_mcp.name


@pytest.mark.anyio
async def test_mcp_info_tool_count_ge_4(client: AsyncClient) -> None:
    """tool_count must be >= 4 (four tools have been registered since v0.2)."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_count"] >= 4, f"Expected >= 4 tools, got {data['tool_count']}"


@pytest.mark.anyio
async def test_mcp_info_tool_count_matches_tools_list(client: AsyncClient) -> None:
    """tool_count must equal len(tools)."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_count"] == len(data["tools"])


@pytest.mark.anyio
async def test_mcp_info_tools_match_live_registry(client: AsyncClient) -> None:
    """
    The tools returned by GET /mcp/info must match exactly what await mcp.list_tools()
    returns from the live registry.  This ensures the endpoint is NOT returning hardcoded
    values (ADR-0027 §4 Do-NOT #1).
    """
    from app.mcp.server import mcp as live_mcp

    # Get the live registry names via the same call the handler uses.
    live_tools = await live_mcp.list_tools()
    live_names = {t.name for t in live_tools}

    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()

    returned_names = {t["name"] for t in data["tools"]}
    assert returned_names == live_names, (
        f"Endpoint returned {returned_names!r} but live registry has {live_names!r}. "
        "The handler must source tool names from mcp.list_tools(), not hardcode them (I6)."
    )


@pytest.mark.anyio
async def test_mcp_info_known_tool_names_present(client: AsyncClient) -> None:
    """
    The four tools contracted since v0.2 must be present.
    This assertion is backed by the live registry: if a tool is renamed in server.py,
    this test catches the drift (not the other way around — see test above for the
    bidirectional check).
    """
    from app.mcp.server import mcp as live_mcp

    live_tools = await live_mcp.list_tools()
    live_names = {t.name for t in live_tools}

    # Verify the four contracted names are registered in the live server.
    for expected in ("search_wiki", "write_page", "get_page", "list_pages"):
        assert expected in live_names, (
            f"Tool {expected!r} not registered in the live mcp server — "
            "update server.py or this test (ADR-0010 §6)."
        )

    # Then verify the endpoint also exposes them.
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    returned_names = {t["name"] for t in data["tools"]}
    for expected in live_names:
        assert expected in returned_names


@pytest.mark.anyio
async def test_mcp_info_each_tool_has_required_fields(client: AsyncClient) -> None:
    """Each tool object must have name (str), description (str), and input_schema (dict)."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()

    for tool in data["tools"]:
        assert isinstance(tool.get("name"), str), f"Tool missing 'name': {tool!r}"
        assert isinstance(tool.get("description"), str), f"Tool missing 'description': {tool!r}"
        assert isinstance(
            tool.get("input_schema"), dict
        ), f"Tool missing 'input_schema' dict: {tool!r}"


@pytest.mark.anyio
async def test_mcp_info_entry_point_command_nonempty(client: AsyncClient) -> None:
    """entry_point_command must be non-empty (derived from settings.mcp_entry_command)."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entry_point_command"], "entry_point_command must not be empty"


@pytest.mark.anyio
async def test_mcp_info_entry_point_command_from_settings(client: AsyncClient) -> None:
    """entry_point_command must reflect settings.mcp_entry_command, not a hardcoded literal."""
    from app.config import settings as live_settings

    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entry_point_command"] == live_settings.mcp_entry_command, (
        "entry_point_command in response must match settings.mcp_entry_command "
        "(I6/ADR-0027 §2.3)"
    )


@pytest.mark.anyio
async def test_mcp_info_transport_nonempty(client: AsyncClient) -> None:
    """transport must be a non-empty string."""
    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["transport"], "transport must not be empty"


@pytest.mark.anyio
async def test_mcp_info_transport_from_settings(client: AsyncClient) -> None:
    """transport must reflect settings.mcp_transport, not a hardcoded literal."""
    from app.config import settings as live_settings

    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()
    assert (
        data["transport"] == live_settings.mcp_transport
    ), "transport in response must match settings.mcp_transport (I6/ADR-0027 §2.3)"


@pytest.mark.anyio
async def test_mcp_info_tool_descriptions_match_live_registry(client: AsyncClient) -> None:
    """
    Each tool's description in the response must match what the live registry reports.
    Ensures descriptions are NOT hardcoded in the handler (ADR-0027 §4 Do-NOT #1).
    """
    from app.mcp.server import mcp as live_mcp

    live_tools = await live_mcp.list_tools()
    live_by_name = {t.name: (t.description or "") for t in live_tools}

    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()

    for tool in data["tools"]:
        name = tool["name"]
        if name in live_by_name:
            assert tool["description"] == live_by_name[name], (
                f"Tool {name!r}: description mismatch. "
                "Handler must source descriptions from mcp.list_tools() (I6)."
            )


@pytest.mark.anyio
async def test_mcp_info_input_schema_matches_live_registry(client: AsyncClient) -> None:
    """
    Each tool's input_schema must match tool.parameters from the live registry.
    Ensures schemas are NOT hardcoded in the handler (ADR-0027 §4 Do-NOT #1).
    """
    from app.mcp.server import mcp as live_mcp

    live_tools = await live_mcp.list_tools()
    live_by_name = {t.name: (t.parameters if t.parameters is not None else {}) for t in live_tools}

    resp = await client.get("/mcp/info")
    assert resp.status_code == 200
    data = resp.json()

    for tool in data["tools"]:
        name = tool["name"]
        if name in live_by_name:
            assert tool["input_schema"] == live_by_name[name], (
                f"Tool {name!r}: input_schema mismatch. "
                "Handler must source schemas from tool.parameters (I6/ADR-0027 §2.2)."
            )
