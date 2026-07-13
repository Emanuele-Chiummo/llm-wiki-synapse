"""Additive review API contract for Synapse 1.6.0 (ADR-0073)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from app.routers import review as router


def _item(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "vault_id": "v",
        "item_type": "suggestion",
        "status": "pending",
        "proposal_origin": "ai",
        "proposed_title": "Open question",
        "proposed_page_type": "query",
        "proposed_dir": "queries",
        "rationale": "Source-grounded gap",
        "page_id": None,
        "source_page_id": None,
        "created_page_id": None,
        "resolution": None,
        "deep_research_run_id": None,
        "content_key": None,
        "referenced_page_ids": None,
        "search_queries": ["contextual query"],
        "created_at": datetime.now(UTC),
        "reviewed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_response_exposes_origin_and_created_page_type() -> None:
    response = router._review_item_to_response(
        _item(status="created", created_page_id=uuid.uuid4()),
        created_page_type="comparison",
    )
    assert response.proposal_origin == "ai"
    assert response.proposed_page_type == "query"
    assert response.created_page_type == "comparison"


@pytest.mark.asyncio
async def test_list_forwards_server_side_generation_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list_queue(vault_id: str, **kwargs: Any) -> Any:
        captured.update({"vault_id": vault_id, **kwargs})
        return SimpleNamespace(items=[_item()], total=1, limit=50, offset=0)

    import app.ops.review as review_ops

    monkeypatch.setattr(review_ops, "list_queue", fake_list_queue)
    result = await router.list_review_queue(
        vault_id="v",
        status="pending",
        item_type="suggestion",
        proposal_origin="ai",
        proposed_page_type="query",
        limit=50,
        offset=0,
    )
    assert captured["item_type"] == "suggestion"
    assert captured["proposal_origin"] == "ai"
    assert captured["proposed_page_type"] == "query"
    assert result.items[0].proposal_origin == "ai"
