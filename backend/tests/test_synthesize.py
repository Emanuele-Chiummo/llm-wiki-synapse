"""
Corpus-level synthesis/comparison generator tests (ADR-0067 D3 · audit P0-3 / SC-D1/SC-D3).

Infra-free (mirrors test_reclassify_types.py): DB reads + the orchestrator write seam + the review
seeder + provider resolution are monkeypatched, so the suite runs with no Postgres / Qdrant / LLM.

Covers:
  * cluster seeding: a planted high-overlap same-domain cluster → one deterministic candidate;
    union sources + member slugs are correct; confidence lands in the right band.
  * auto-write: a synthesis page is written with type=synthesis, related=cluster slugs, DB sources
    = union; a comparison with ≥2 entities yields a markdown table.
  * gating: a below-threshold cluster is PROPOSED to the F9 review queue, not written as a page.
  * bounds (I7): stops at token_budget / max_pages; single data_version bump per written page
    (owned by write_wiki_page); provider-absent → clean no-op (I6).
  * SC-D3: review.propose_corpus_shape_review enqueues a `suggestion` with the right
    proposed_page_type + referenced ids (additive, does not touch existing propose behaviour).
  * the single-doc ingest prohibition on synthesis/comparison remains intact (unchanged).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ingest.schemas import PageType
from app.ops import synthesize as sy

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_provider(response: str) -> Any:
    """A MagicMock provider whose chat() yields *response* and counts calls (mirror reclassify)."""
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1

        async def _gen() -> AsyncIterator[str]:
            yield response

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    return provider


def _pg(
    title: str,
    ptype: str,
    sources: list[str],
    *,
    domain: str | None = "cloud",
    slug: str | None = None,
    pid: str | None = None,
) -> dict[str, Any]:
    """Build a planted page dict as _load_graph_data would return it."""
    slug = slug or title.lower().replace(" ", "-")
    tags = [f"domain/{domain}"] if domain else []
    return {
        "id": pid or str(uuid.uuid4()),
        "title": title,
        "page_type": ptype,
        "file_path": f"wiki/x/{slug}.md",
        "sources": list(sources),
        "tags": tags,
    }


def _cluster(kind: str, *, confidence: float, n: int = 3) -> sy.Cluster:
    """A ready-made candidate cluster for op-level tests."""
    slugs = [f"p{i}" for i in range(n)]
    return sy.Cluster(
        kind=kind,
        page_ids=[str(uuid.uuid4()) for _ in range(n)],
        member_keys=[f"wiki/entities/p{i}.md" for i in range(n)],
        slugs=slugs,
        titles=[f"Page {i}" for i in range(n)],
        sources=["raw/a.md", "raw/b.md", "raw/c.md"],
        domain="cloud",
        confidence=confidence,
    )


# ── Cluster-seeding heuristic (pure function, no stubs) ───────────────────────


def test_seed_synthesis_picks_high_overlap_cluster() -> None:
    # 3 same-domain concept pages, each pair sharing all 3 sources → one synthesis cluster.
    shared = ["raw/1.md", "raw/2.md", "raw/3.md"]
    pages = [
        _pg("Alpha", "concept", shared, slug="alpha"),
        _pg("Beta", "concept", shared, slug="beta"),
        _pg("Gamma", "concept", shared, slug="gamma"),
    ]
    clusters = sy._build_clusters(pages, [])
    synth = [c for c in clusters if c.kind == "synthesis"]
    assert len(synth) == 1
    c = synth[0]
    assert sorted(c.slugs) == ["alpha", "beta", "gamma"]
    assert c.sources == sorted(shared), "union of the cluster's sources"
    assert c.confidence >= sy.AUTO_CONFIDENCE_THRESHOLD, "high-overlap cluster auto-writes"
    # No comparison cluster: there are no entity pages.
    assert not [x for x in clusters if x.kind == "comparison"]


def test_seed_comparison_two_cocited_entities() -> None:
    # 2 same-class entities sharing 3 sources → one comparison cluster (auto band).
    shared = ["raw/1.md", "raw/2.md", "raw/3.md"]
    pages = [
        _pg("EKS", "entity", shared, slug="eks"),
        _pg("GKE", "entity", shared, slug="gke"),
    ]
    clusters = sy._build_clusters(pages, [])
    comp = [c for c in clusters if c.kind == "comparison"]
    assert len(comp) == 1
    assert sorted(comp[0].slugs) == ["eks", "gke"]
    assert comp[0].confidence >= sy.AUTO_CONFIDENCE_THRESHOLD
    # 2 entities cannot form a synthesis (needs ≥3).
    assert not [c for c in clusters if c.kind == "synthesis"]


def test_seed_below_threshold_lands_in_review_band() -> None:
    # 2 entities sharing exactly the minimum (2) sources → a cluster in the Review band.
    shared = ["raw/1.md", "raw/2.md"]
    pages = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]
    clusters = sy._build_clusters(pages, [])
    comp = [c for c in clusters if c.kind == "comparison"]
    assert len(comp) == 1
    conf = comp[0].confidence
    assert sy.REVIEW_CONFIDENCE_FLOOR <= conf < sy.AUTO_CONFIDENCE_THRESHOLD


def test_seed_ignores_pairs_below_min_shared() -> None:
    # Entities sharing only 1 source (< MIN_SHARED_SOURCES=2) → no cluster at all.
    pages = [
        _pg("X", "entity", ["raw/1.md"], slug="x"),
        _pg("Y", "entity", ["raw/1.md", "raw/9.md"], slug="y"),
    ]
    # Only 1 shared source between X and Y → no comparison.
    assert sy._build_clusters(pages, []) == []


def test_seed_is_deterministic() -> None:
    shared = ["raw/1.md", "raw/2.md", "raw/3.md"]
    pages = [
        _pg("Alpha", "concept", shared, slug="alpha"),
        _pg("Beta", "concept", shared, slug="beta"),
        _pg("Gamma", "concept", shared, slug="gamma"),
    ]
    first = sy._build_clusters(pages, [])
    second = sy._build_clusters(list(reversed(pages)), [])
    assert [(c.kind, tuple(c.slugs)) for c in first] == [(c.kind, tuple(c.slugs)) for c in second]


def test_seed_rejects_untagged_clusters() -> None:
    """ADR-0074: an untagged corpus must never become one global synthetic domain."""
    shared = ["raw/1.md", "raw/2.md", "raw/3.md"]
    pages = [
        _pg("Alpha", "concept", shared, domain=None),
        _pg("Beta", "concept", shared, domain=None),
        _pg("Gamma", "concept", shared, domain=None),
        _pg("EKS", "entity", shared, domain=None),
        _pg("GKE", "entity", shared, domain=None),
    ]
    assert sy._build_clusters(pages, []) == []


def test_seed_rejects_mixed_domain_synthesis() -> None:
    """Source overlap alone cannot bridge incompatible domains."""
    shared = ["raw/1.md", "raw/2.md", "raw/3.md"]
    pages = [
        _pg("Alpha", "concept", shared, domain="cloud"),
        _pg("Beta", "concept", shared, domain="cloud"),
        _pg("Gamma", "concept", shared, domain="finance"),
    ]
    assert not [c for c in sy._build_clusters(pages, []) if c.kind == "synthesis"]


def test_generation_key_survives_db_uuid_changes() -> None:
    """The corpus identity is based on canonical paths, never ephemeral database UUIDs."""
    first = _cluster("comparison", confidence=0.9, n=2)
    second = _cluster("comparison", confidence=0.9, n=2)
    second.member_keys = list(reversed(first.member_keys))
    assert first.page_ids != second.page_ids
    assert sy._generation_key(first) == sy._generation_key(second)
    assert sy._generation_key(first).startswith("corpus:comparison:")


def test_legacy_duplicate_audit_is_pure_and_non_destructive() -> None:
    records = [
        {
            "id": "a",
            "title": "AWS storage comparison",
            "kind": "comparison",
            "member_keys": ["wiki/entities/aws.md", "wiki/entities/s3.md"],
            "generation_key": None,
        },
        {
            "id": "b",
            "title": "S3 vs AWS",
            "kind": "comparison",
            "member_keys": ["wiki/entities/s3.md", "wiki/entities/aws.md"],
            "generation_key": None,
        },
        {
            "id": "c",
            "title": "Different synthesis",
            "kind": "synthesis",
            "member_keys": ["wiki/concepts/x.md", "wiki/concepts/y.md"],
            "generation_key": None,
        },
    ]
    groups = sy._find_legacy_duplicate_groups(records)
    assert len(groups) == 1
    assert {page["id"] for page in groups[0]["pages"]} == {"a", "b"}
    assert records[0]["generation_key"] is None, "audit must never mutate/backfill records"


# ── Op-level fixture (stubs the DB / write / review seams) ────────────────────


@pytest.fixture()
def sy_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.ingest.context as context
    import app.ingest.writer as writer
    from app.ops import review

    state: dict[str, Any] = {
        "clusters": [],
        "written": [],  # (WikiPage, origin_source) tuples
        "proposals": [],  # (vault_id, kind, title, page_ids) tuples
        "bumps": 0,  # models write_wiki_page's per-page data_version bump
        "provider": _make_provider('{"title": "T", "body": "Thesis prose [[Page 0]] [[Page 1]]"}'),
        "existing_keys": set(),
    }

    async def fake_resolve(vault_id: str) -> Any:
        prov = state["provider"]
        return None if prov is None else (prov, object())

    async def fake_seed(vault_id: str, force: bool) -> list[sy.Cluster]:
        return list(state["clusters"])

    async def fake_key_exists(vault_id: str, generation_key: str) -> bool:
        return generation_key in state["existing_keys"]

    async def fake_write(session: Any, page: Any, origin_source: str, **kwargs: Any) -> Any:
        state["written"].append((page, origin_source))
        state["bumps"] += 1  # write_wiki_page owns exactly one data_version bump per page (I1)
        row = type("P", (), {})()
        row.id = uuid.uuid4()
        row.title = page.title
        return row

    def fake_vault_context() -> str:
        return "# schema.md\nrules"

    async def fake_propose(
        *,
        vault_id: str,
        kind: str,
        proposed_title: str,
        cluster_page_ids: list[str],
        rationale: str,
        generation_key: str,
    ) -> Any:
        state["proposals"].append(
            (vault_id, kind, proposed_title, list(cluster_page_ids), generation_key)
        )
        return type("R", (), {"id": uuid.uuid4()})()

    monkeypatch.setattr(sy, "resolve_operation_provider", fake_resolve)
    monkeypatch.setattr(sy, "_seed_candidates", fake_seed)
    monkeypatch.setattr(sy, "_generation_key_exists", fake_key_exists)
    monkeypatch.setattr(writer, "write_wiki_page", fake_write)
    monkeypatch.setattr(context, "_load_vault_context", fake_vault_context)
    monkeypatch.setattr(review, "propose_corpus_shape_review", fake_propose)

    sy._state.is_running = False
    sy._state.last_summary = None
    sy._state.current = {}
    return state


# ── Op-level behaviour ───────────────────────────────────────────────────────


async def test_synthesis_page_written(sy_env: dict[str, Any]) -> None:
    cluster = _cluster("synthesis", confidence=0.9)
    sy_env["clusters"] = [cluster]
    summary = await sy.run_synthesize(vault_id="test-vault")

    assert summary.synthesis_written == 1
    assert summary.comparison_written == 0
    assert summary.stopped_reason == "complete"
    assert len(sy_env["written"]) == 1
    page, origin = sy_env["written"][0]
    assert page.type is PageType.SYNTHESIS
    assert page.frontmatter.type is PageType.SYNTHESIS
    assert page.frontmatter.related == cluster.slugs, "related = the cluster slugs"
    assert (
        page.frontmatter.sources == cluster.sources
    ), "DB sources = union of the cluster's sources"
    assert origin == "", "corpus page has no single raw origin doc"
    assert page.frontmatter.synapse_generation_key == sy._generation_key(cluster)
    assert sy_env["bumps"] == 1, "single data_version bump (owned by write_wiki_page)"


async def test_comparison_yields_table(sy_env: dict[str, Any]) -> None:
    import json

    table_body = (
        "Intro sentence.\n\n"
        "| Dimension | [[Page 0]] | [[Page 1]] |\n"
        "| --- | --- | --- |\n"
        "| kind | a | b |\n"
    )
    sy_env["provider"] = _make_provider(json.dumps({"title": "A vs B", "body": table_body}))
    cluster = _cluster("comparison", confidence=0.9, n=2)
    sy_env["clusters"] = [cluster]

    summary = await sy.run_synthesize(vault_id="test-vault")
    assert summary.comparison_written == 1
    page, _ = sy_env["written"][0]
    assert page.type is PageType.COMPARISON
    assert "| --- |" in page.content and "|" in page.content, "comparison body carries a table"
    assert page.frontmatter.related == cluster.slugs


async def test_comparison_without_table_is_rejected(sy_env: dict[str, Any]) -> None:
    # STRICT: a comparison body with no markdown table is unusable → failed, no page written.
    sy_env["provider"] = _make_provider('{"title": "A vs B", "body": "just prose, no table"}')
    sy_env["clusters"] = [_cluster("comparison", confidence=0.9, n=2)]
    summary = await sy.run_synthesize(vault_id="test-vault")
    assert summary.comparison_written == 0
    assert summary.failed == 1
    assert sy_env["written"] == []
    assert sy_env["bumps"] == 0


async def test_below_threshold_proposes_review_not_page(sy_env: dict[str, Any]) -> None:
    cluster = _cluster("comparison", confidence=0.45, n=2)  # inside [floor, auto)
    sy_env["clusters"] = [cluster]
    summary = await sy.run_synthesize(vault_id="test-vault")

    assert summary.proposed == 1
    assert summary.comparison_written == 0
    assert summary.synthesis_written == 0
    assert sy_env["written"] == [], "borderline cluster must NOT be auto-written"
    assert sy_env["bumps"] == 0, "a Review proposal bumps no data_version"
    assert len(sy_env["proposals"]) == 1
    vault_id, kind, _title, page_ids, generation_key = sy_env["proposals"][0]
    assert kind == "comparison"
    assert page_ids == cluster.page_ids
    assert generation_key == sy._generation_key(cluster)
    # The provider is never called on the Review path.
    assert sy_env["provider"]._chat_calls[0] == 0


async def test_below_floor_is_skipped(sy_env: dict[str, Any]) -> None:
    sy_env["clusters"] = [_cluster("synthesis", confidence=0.10, n=3)]
    summary = await sy.run_synthesize(vault_id="test-vault")
    assert summary.skipped == 1
    assert summary.proposed == 0
    assert sy_env["written"] == []


async def test_token_budget_stops(sy_env: dict[str, Any]) -> None:
    from app.ingest.schemas import Usage

    provider = sy_env["provider"]
    acc_holder: dict[str, Any] = {}

    def capture_bind(accumulator: Any) -> None:
        acc_holder["acc"] = accumulator

    provider.bind_accumulator = capture_bind

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1
        acc_holder["acc"].add(Usage(input_tokens=100, output_tokens=0, total_cost_usd=0.0))

        async def _gen() -> AsyncIterator[str]:
            yield '{"title": "T", "body": "prose [[Page 0]]"}'

        return _gen()

    provider.chat = mock_chat

    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9) for _ in range(3)]
    summary = await sy.run_synthesize(vault_id="test-vault", token_budget=50)
    assert summary.stopped_reason == "budget"
    assert summary.synthesis_written == 1, "one page before the budget gate trips"
    assert provider._chat_calls[0] == 1


async def test_max_pages_cap(sy_env: dict[str, Any]) -> None:
    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9) for _ in range(3)]
    summary = await sy.run_synthesize(vault_id="test-vault", max_pages=1)
    assert summary.stopped_reason == "maxpages"
    assert summary.pages_written == 1
    assert len(sy_env["written"]) == 1


async def test_max_candidates_caps_review_proposals(sy_env: dict[str, Any]) -> None:
    """I7: low-confidence proposals cannot bypass the auto-write max_pages bound."""
    sy_env["clusters"] = [_cluster("comparison", confidence=0.45, n=2) for _ in range(5)]
    summary = await sy.run_synthesize(vault_id="test-vault", max_candidates=2)
    assert summary.candidates == 5
    assert summary.candidates_evaluated == 2
    assert summary.proposed == 2
    assert summary.stopped_reason == "max_candidates"


async def test_existing_generation_key_skips_before_provider_call(
    sy_env: dict[str, Any],
) -> None:
    cluster = _cluster("synthesis", confidence=0.9)
    sy_env["clusters"] = [cluster]
    sy_env["existing_keys"] = {sy._generation_key(cluster)}
    summary = await sy.run_synthesize(vault_id="test-vault")
    assert summary.duplicates_skipped == 1
    assert summary.pages_written == 0
    assert sy_env["provider"]._chat_calls[0] == 0


async def test_unique_index_race_is_reported_as_duplicate_skip(
    sy_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent keyed insert after preflight is idempotent, not a failed generation."""

    async def conflicting_write(cluster: sy.Cluster, title: str, body: str) -> None:
        raise sy._GenerationKeyConflict(sy._generation_key(cluster))

    monkeypatch.setattr(sy, "_write_cluster_page", conflicting_write)
    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9)]

    summary = await sy.run_synthesize(vault_id="test-vault")

    assert summary.duplicates_skipped == 1
    assert summary.failed == 0
    assert summary.pages_written == 0


async def test_force_regenerates_same_generation_key_without_duplicate(
    sy_env: dict[str, Any],
) -> None:
    cluster = _cluster("synthesis", confidence=0.9)
    key = sy._generation_key(cluster)
    sy_env["clusters"] = [cluster]
    sy_env["existing_keys"] = {key}
    summary = await sy.run_synthesize(vault_id="test-vault", force=True)
    assert summary.pages_written == 1
    assert summary.duplicates_skipped == 0
    assert sy_env["provider"]._chat_calls[0] == 1
    page, _ = sy_env["written"][0]
    assert page.frontmatter.synapse_generation_key == key


async def test_review_only_mode_never_auto_writes(sy_env: dict[str, Any]) -> None:
    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9)]
    summary = await sy.run_synthesize(vault_id="test-vault", mode="review-only")
    assert summary.pages_written == 0
    assert summary.proposed == 1
    assert sy_env["provider"]._chat_calls[0] == 0


async def test_no_provider_clean_noop(sy_env: dict[str, Any]) -> None:
    sy_env["provider"] = None
    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9)]
    summary = await sy.run_synthesize(vault_id="test-vault")
    assert summary.stopped_reason == "no_provider"
    assert summary.pages_written == 0
    assert summary.proposed == 0
    assert sy_env["written"] == []
    assert sy_env["bumps"] == 0


async def test_review_only_runs_without_provider(sy_env: dict[str, Any]) -> None:
    """Review-only is deterministic and provider-free, so missing config must not block it."""
    sy_env["provider"] = None
    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9)]

    summary = await sy.run_synthesize(vault_id="test-vault", mode="review-only")

    assert summary.stopped_reason == "complete"
    assert summary.proposed == 1
    assert summary.pages_written == 0


async def test_max_pages_hard_cap() -> None:
    mp, _tb = sy.clamp_bounds(999_999, None)
    assert mp == sy.MAX_PAGES_HARD_CAP


async def test_single_flight_state(sy_env: dict[str, Any]) -> None:
    import asyncio

    sy_env["clusters"] = [_cluster("synthesis", confidence=0.9)]
    seen: dict[str, bool] = {}

    orig_seed = sy._seed_candidates

    async def slow_seed(vault_id: str, force: bool) -> list[sy.Cluster]:
        seen["running_during"] = sy.is_running()
        await asyncio.sleep(0)
        return await orig_seed(vault_id, force)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(sy, "_seed_candidates", slow_seed)
    try:
        assert not sy.is_running()
        await sy.run_synthesize(vault_id="test-vault")
    finally:
        monkey.undo()
    assert seen["running_during"] is True
    assert not sy.is_running()
    assert sy.get_last_summary() is not None


# ── SC-D3: review seeder (additive) ──────────────────────────────────────────


async def test_propose_corpus_shape_review_enqueues_with_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ops import review

    recorded: dict[str, Any] = {}

    async def fake_enqueue(**kwargs: Any) -> Any:
        recorded.update(kwargs)
        return type("R", (), {"id": uuid.uuid4()})()

    monkeypatch.setattr(review, "enqueue_review", fake_enqueue)

    item = await review.propose_corpus_shape_review(
        vault_id="v1",
        kind="comparison",
        proposed_title="EKS vs GKE: comparison",
        cluster_page_ids=["id-1", "id-2"],
        rationale="graph signals",
        generation_key="corpus:comparison:" + "a" * 64,
    )
    assert item is not None
    assert recorded["item_type"] == "suggestion"
    assert recorded["proposed_page_type"] == "comparison"
    assert recorded["proposed_dir"] == "comparisons"
    assert recorded["referenced_page_ids"] == ["id-1", "id-2"]
    assert recorded["content_key"] == "corpus:comparison:" + "a" * 64
    assert recorded["proposal_origin"] == "corpus"


async def test_propose_corpus_shape_review_rejects_bad_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ops import review

    called = {"n": 0}

    async def fake_enqueue(**kwargs: Any) -> Any:
        called["n"] += 1
        return None

    monkeypatch.setattr(review, "enqueue_review", fake_enqueue)

    assert (
        await review.propose_corpus_shape_review(
            vault_id="v1",
            kind="entity",  # not a corpus shape
            proposed_title="X",
            cluster_page_ids=[],
            rationale="r",
            generation_key="corpus:entity:" + "b" * 64,
        )
        is None
    )
    assert called["n"] == 0, "invalid kind never enqueues"


# ── Guardrail: direct and corpus generation have complementary evidence gates ─


def test_direct_ingest_special_pages_are_source_grounded() -> None:
    from app.ingest.provider import _common

    scaffold = _common.GENERATION_SCAFFOLD.lower()
    for page_type in ("query", "comparison", "synthesis"):
        assert page_type in scaffold
    assert "directly supported" in scaffold
    assert "do not invent" in scaffold
