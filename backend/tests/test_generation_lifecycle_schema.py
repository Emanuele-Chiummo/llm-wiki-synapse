"""Schema and migration guards for ADR-0073/ADR-0074 (v1.6.0)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.ingest.schemas import PageType, WikiFrontmatter
from app.models import IngestRun, Page, ReviewItem


def test_migration_0031_chain_and_callables() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0031_generation_lifecycle_parity.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0031", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0031"
    assert module.down_revision == "0030"
    assert callable(module.upgrade) and callable(module.downgrade)


def test_model_metadata_exposes_additive_generation_columns() -> None:
    assert "generation_key" in Page.__table__.columns
    assert "proposal_origin" in ReviewItem.__table__.columns
    assert "page_type_counts" in IngestRun.__table__.columns
    index = next(
        idx for idx in Page.__table__.indexes if idx.name == "uix_pages_vault_generation_key_live"
    )
    assert index.unique is True


def test_review_origin_defaults_to_legacy() -> None:
    column = ReviewItem.__table__.columns.proposal_origin
    assert column.nullable is False
    assert column.server_default is not None
    assert "legacy" in str(column.server_default.arg)


def test_reserved_generation_key_is_bounded_and_typed() -> None:
    key = "corpus:synthesis:" + "a" * 64
    fm = WikiFrontmatter(
        type=PageType.SYNTHESIS,
        title="S",
        synapse_generation_key=key.upper(),
    )
    assert fm.synapse_generation_key == key

    with pytest.raises(ValueError):
        WikiFrontmatter(
            type=PageType.ENTITY,
            title="E",
            synapse_generation_key="external:unsafe",
        )

    with pytest.raises(ValueError):
        WikiFrontmatter(
            type=PageType.COMPARISON,
            title="Mismatch",
            synapse_generation_key="corpus:synthesis:" + "b" * 64,
        )


def test_page_type_counts_exposes_all_six_types() -> None:
    from app.ingest.orchestrator import _page_type_counts

    counts = _page_type_counts(
        [
            SimpleNamespace(page_type=PageType.SOURCE.value),
            SimpleNamespace(page_type=PageType.QUERY.value),
            SimpleNamespace(page_type=PageType.QUERY.value),
            SimpleNamespace(page_type="overview"),
        ]
    )
    assert counts == {
        "entity": 0,
        "concept": 0,
        "source": 1,
        "query": 2,
        "synthesis": 0,
        "comparison": 0,
    }
