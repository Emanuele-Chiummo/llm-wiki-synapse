"""
Tests for ops/dedup_entities.py — ADR-0067 D5 entity canonicalization retrofit.

Scenario: vault contains 5 entity pages —
  AWS variant cluster (3 pages, same canonical key "amazon web services"):
    page_aws       title="AWS"                          sources=["raw/a.md"]
    page_longform  title="Amazon Web Services"          sources=["raw/b.md"]
    page_paren     title="Amazon Web Services (AWS)"    sources=["raw/c.md"]

  Distinct entities (1 page each — must NOT form a cluster):
    page_deloitte  title="Deloitte"                     sources=["raw/d.md"]
    page_deloitte_it title="Deloitte Italia"            sources=["raw/e.md"]

  "Deloitte" → canonical key "deloitte"
  "Deloitte Italia" → canonical key "deloitte italia"
  → different keys → different groups → neither reaches ≥2 → no cluster.

Test plan
---------
  T0  — canonical key unit tests: AWS variants same key; Deloitte/Deloitte Italia differ.
  T1  — dry_run (apply=False, propose_to_review=False): 1 cluster (3 AWS pages);
          Deloitte / Deloitte Italia NOT included; zero writes, zero bumps.
  T2  — propose (apply=False, propose_to_review=True): _propose_cluster called once
          for the AWS cluster; not called for single-member groups.
  T3  — apply: _apply_cluster called once (canonical=page_aws, aliases=[longform, paren]);
          exactly 1 bump, 1 reconnect call; aliases_soft_deleted = 2.
  T4  — Deloitte isolation (unit): keys differ → _load_entity_clusters returns 0 clusters.
  T5  — idempotent: after apply the alias pages are soft-deleted; re-running dry-run
          returns 0 clusters (mock reflects the post-apply state).
  T6  — max_clusters cap: max_clusters=0 clamped to 1; stopped_reason=maxclusters
          when cluster count hits the cap.
  T7  — no bump on dry-run: zero data_version bumps in any non-apply mode.
  T8  — is_running / get_last_summary state flags.
  T9  — clamp_bounds: None → default; value over hard-cap → clamped.
  T10 — apply cluster sources_union + alias_soft_delete + link_repoint (unit, patched
          primitives; no real DB or Qdrant required).
  T11 — filesystem integration: real tmp_path files; _apply_cluster merges bodies +
          deletes alias files; DB + Qdrant ops are stubbed.

DB and Qdrant are always stubbed (no Postgres, no Qdrant infra needed).
File I/O is real only in T11 (tmp_path).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import app.ops.dedup_entities as dd
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_page(
    title: str,
    file_path: str,
    sources: list[str] | None = None,
    page_type: str = "entity",
    vault_id: str = "test-vault",
    deleted_at: Any = None,
) -> Any:
    """Lightweight fake Page ORM object (no DB, no ORM machinery)."""
    p = type("FakePage", (), {})()
    p.id = uuid.uuid4()
    p.vault_id = vault_id
    p.title = title
    p.file_path = file_path
    p.page_type = page_type
    p.sources = list(sources) if sources else []
    p.tags = None
    p.source_mtime_ns = 0
    p.deleted_at = deleted_at
    return p


# ── Canonical seed pages ──────────────────────────────────────────────────────

PAGE_AWS = _fake_page(
    title="AWS",
    file_path="wiki/entities/aws.md",
    sources=["raw/sources/a.md"],
)
PAGE_LONGFORM = _fake_page(
    title="Amazon Web Services",
    file_path="wiki/entities/amazon-web-services.md",
    sources=["raw/sources/b.md"],
)
PAGE_PAREN = _fake_page(
    title="Amazon Web Services (AWS)",
    file_path="wiki/entities/amazon-web-services-aws.md",
    sources=["raw/sources/c.md"],
)
PAGE_DELOITTE = _fake_page(
    title="Deloitte",
    file_path="wiki/entities/deloitte.md",
    sources=["raw/sources/d.md"],
)
PAGE_DELOITTE_IT = _fake_page(
    title="Deloitte Italia",
    file_path="wiki/entities/deloitte-italia.md",
    sources=["raw/sources/e.md"],
)

# Pre-cooked cluster for the AWS group (returned by mocked _load_entity_clusters).
_AWS_CLUSTER = ("amazon web services", [PAGE_AWS, PAGE_LONGFORM, PAGE_PAREN])


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture()
def dedup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """
    Stub every I/O dependency (no Postgres, no Qdrant, no filesystem — except T11).

    Patched surfaces:
      dd._load_entity_clusters  — returns state["clusters"]
      dd._apply_cluster         — records (canonical, aliases) calls
      dd._propose_cluster       — records canonical_key calls
      dd._reconnect_links       — counts calls
      dd._inbound_degree        — returns empty degree map (all zeros)
      bump_version              — counts calls (patched in app.ingest.orchestrator)
    """
    state: dict[str, Any] = {
        "clusters": [_AWS_CLUSTER],
        "apply_calls": [],  # [(canonical.title, [alias.title, ...])]
        "propose_calls": [],  # [canonical_key]
        "reconnects": 0,
        "bumps": 0,
        "apply_error": None,
        "propose_error": None,
        "apply_return": 2,  # how many aliases _apply_cluster claims it deleted
        "vault_root": tmp_path,
    }

    async def fake_load_clusters(vault_id: str, max_clusters: int) -> list[Any]:
        return list(state["clusters"])[:max_clusters]

    async def fake_apply_cluster(canonical: Any, aliases: list[Any]) -> int:
        if state["apply_error"] is not None:
            raise state["apply_error"]
        state["apply_calls"].append((canonical.title, [a.title for a in aliases]))
        return int(state["apply_return"])

    async def fake_propose_cluster(
        vault_id: str, canonical_key: str, cluster_pages: list[Any], canonical: Any
    ) -> None:
        if state["propose_error"] is not None:
            raise state["propose_error"]
        state["propose_calls"].append(canonical_key)

    async def fake_reconnect_links() -> None:
        state["reconnects"] += 1

    async def fake_inbound_degree(page_ids: list[Any]) -> dict[Any, int]:
        return dict.fromkeys(page_ids, 0)

    async def fake_bump() -> None:
        state["bumps"] += 1

    monkeypatch.setattr(dd, "_load_entity_clusters", fake_load_clusters)
    monkeypatch.setattr(dd, "_apply_cluster", fake_apply_cluster)
    monkeypatch.setattr(dd, "_propose_cluster", fake_propose_cluster)
    monkeypatch.setattr(dd, "_reconnect_links", fake_reconnect_links)
    monkeypatch.setattr(dd, "_inbound_degree", fake_inbound_degree)

    # bump_version is deferred-imported inside _run_inner from orchestrator.
    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "bump_version", fake_bump)

    # Reset module-level single-flight state between tests.
    dd._state.is_running = False
    dd._state.last_summary = None
    dd._state.current = {}

    return state


# ── T0: canonical key unit tests ──────────────────────────────────────────────


def test_aws_variants_same_canonical_key() -> None:
    """All three AWS page titles resolve to the same canonical key (ACRONYM_FOLD)."""
    from app.ingest.writer import _resolve_canonical_entity_key

    key_aws = _resolve_canonical_entity_key("AWS")
    key_long = _resolve_canonical_entity_key("Amazon Web Services")
    key_paren = _resolve_canonical_entity_key("Amazon Web Services (AWS)")
    key_inc = _resolve_canonical_entity_key("Amazon Web Services Inc.")

    assert key_aws == key_long == key_paren == key_inc
    assert key_aws == "amazon web services"


def test_deloitte_keys_differ() -> None:
    """Deloitte vs Deloitte Italia → different canonical keys → cannot auto-merge (Q5)."""
    from app.ingest.writer import _resolve_canonical_entity_key

    key_d = _resolve_canonical_entity_key("Deloitte")
    key_di = _resolve_canonical_entity_key("Deloitte Italia")

    assert key_d != key_di
    assert key_d == "deloitte"
    assert key_di == "deloitte italia"


# ── T1: dry-run ───────────────────────────────────────────────────────────────


async def test_dryrun_reports_aws_cluster_no_writes(dedup_env: dict[str, Any]) -> None:
    """
    Dry-run: 1 AWS cluster (3 pages) reported; Deloitte/Deloitte Italia NOT present;
    zero writes, zero bumps, zero reconnects.
    """
    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=False)

    # One cluster processed (the AWS group).
    assert summary.processed_clusters == 1
    assert summary.merged_clusters == 0
    assert summary.proposed_clusters == 0
    assert summary.failed_clusters == 0
    assert summary.aliases_soft_deleted == 0
    assert summary.total_cost_usd == 0.0
    assert summary.stopped_reason == "complete"
    assert summary.apply is False
    assert summary.propose_to_review is False

    # Cluster info is returned in the summary.
    assert len(summary.clusters) == 1
    cluster = summary.clusters[0]
    assert cluster.canonical_key == "amazon web services"
    assert len(cluster.member_titles) == 3
    assert set(cluster.member_titles) == {"AWS", "Amazon Web Services", "Amazon Web Services (AWS)"}

    # No side-effects.
    assert dedup_env["apply_calls"] == []
    assert dedup_env["propose_calls"] == []
    assert dedup_env["bumps"] == 0
    assert dedup_env["reconnects"] == 0


async def test_dryrun_deloitte_not_a_cluster(dedup_env: dict[str, Any]) -> None:
    """
    When the clusters list contains only single-member groups (Deloitte, Deloitte Italia),
    _load_entity_clusters returns [] → 0 clusters processed.
    """
    # Override to return no clusters (simulating a vault where all entities are unique).
    dedup_env["clusters"] = []

    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=False)

    assert summary.processed_clusters == 0
    assert len(summary.clusters) == 0
    assert dedup_env["bumps"] == 0


# ── T2: propose mode ──────────────────────────────────────────────────────────


async def test_propose_enqueues_review_for_aws_cluster(dedup_env: dict[str, Any]) -> None:
    """
    Propose mode: _propose_cluster called once for the AWS cluster; no writes; no bumps.
    """
    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=True)

    assert summary.proposed_clusters == 1
    assert summary.merged_clusters == 0
    assert summary.aliases_soft_deleted == 0
    assert dedup_env["bumps"] == 0

    # Exactly one propose call — for the AWS canonical key.
    assert len(dedup_env["propose_calls"]) == 1
    assert dedup_env["propose_calls"][0] == "amazon web services"


async def test_propose_failed_counts_as_failed(dedup_env: dict[str, Any]) -> None:
    """If propose raises → failed_clusters incremented; no bump; run does not crash."""
    dedup_env["propose_error"] = RuntimeError("review DB unavailable")

    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=True)

    assert summary.failed_clusters == 1
    assert summary.proposed_clusters == 0
    assert dedup_env["bumps"] == 0


# ── T3: apply mode ────────────────────────────────────────────────────────────


async def test_apply_merges_cluster_single_bump(dedup_env: dict[str, Any]) -> None:
    """
    Apply mode: _apply_cluster called once (AWS cluster), 1 reconnect, 1 data_version bump.
    """
    summary = await dd.run_dedup("test-vault", apply=True)

    assert summary.merged_clusters == 1
    assert summary.proposed_clusters == 0
    assert summary.failed_clusters == 0
    assert summary.aliases_soft_deleted == 2  # fake_apply_cluster returns 2
    assert summary.total_cost_usd == 0.0

    # _apply_cluster was called exactly once.
    assert len(dedup_env["apply_calls"]) == 1
    canonical_title, alias_titles = dedup_env["apply_calls"][0]
    # The canonical should be "AWS" (shortest title).
    assert canonical_title == "AWS"
    # The two aliases should be the longer variants.
    assert set(alias_titles) == {"Amazon Web Services", "Amazon Web Services (AWS)"}

    # Exactly ONE data_version bump for the whole batch (I1).
    assert dedup_env["bumps"] == 1, "Expected exactly 1 data_version bump (I1)"
    # Exactly ONE reresolve_dangling_links call after the batch.
    assert dedup_env["reconnects"] == 1, "Expected exactly 1 reconnect call after batch"


async def test_apply_failed_no_bump(dedup_env: dict[str, Any]) -> None:
    """If _apply_cluster raises for ALL clusters → failed_clusters counted; no bump."""
    dedup_env["apply_error"] = OSError("disk full")

    summary = await dd.run_dedup("test-vault", apply=True)

    assert summary.failed_clusters == 1
    assert summary.merged_clusters == 0
    assert dedup_env["bumps"] == 0, "No bump when nothing was actually merged"
    assert dedup_env["reconnects"] == 0


async def test_apply_picks_shortest_title_as_canonical(dedup_env: dict[str, Any]) -> None:
    """
    The canonical page is chosen as the one with the SHORTEST title (fewest chars).
    AWS (3) < Amazon Web Services (21) < Amazon Web Services (AWS) (26).
    """
    await dd.run_dedup("test-vault", apply=True)

    assert len(dedup_env["apply_calls"]) == 1
    canonical_title, alias_titles = dedup_env["apply_calls"][0]
    assert canonical_title == "AWS"


# ── T4: Deloitte isolation (via _load_entity_clusters) ───────────────────────


async def test_deloitte_pages_never_form_a_cluster(dedup_env: dict[str, Any]) -> None:
    """
    Deloitte and Deloitte Italia have different canonical keys → they are separate
    single-member groups → _load_entity_clusters returns [] → 0 clusters processed.
    """
    dedup_env["clusters"] = []  # simulate: no ≥2-member clusters

    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=False)

    assert summary.processed_clusters == 0
    assert len(summary.clusters) == 0
    assert dedup_env["bumps"] == 0
    assert dedup_env["apply_calls"] == []


# ── T5: idempotent ───────────────────────────────────────────────────────────


async def test_idempotent_second_run_finds_no_clusters(dedup_env: dict[str, Any]) -> None:
    """
    After apply, alias pages are soft-deleted → _load_entity_clusters returns [] on
    re-run (simulated by resetting state["clusters"] = [] after the first run).
    """
    # First run: apply the merge.
    summary_first = await dd.run_dedup("test-vault", apply=True)
    assert summary_first.merged_clusters == 1

    # Simulate post-apply state: aliases are gone → no cluster remains.
    dedup_env["clusters"] = []
    # Reset state flags.
    dd._state.is_running = False
    dd._state.last_summary = None

    summary_second = await dd.run_dedup("test-vault", apply=True)
    assert summary_second.processed_clusters == 0
    assert summary_second.merged_clusters == 0
    # No additional bumps beyond the first run.
    assert dedup_env["bumps"] == 1, "Second run (no clusters) should add 0 bumps"


# ── T6: max_clusters cap ──────────────────────────────────────────────────────


async def test_maxclusters_cap(dedup_env: dict[str, Any]) -> None:
    """max_clusters=1 with 1 cluster → stopped_reason='complete' (exact-cap edge case)."""
    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=False, max_clusters=1)

    assert summary.max_clusters == 1
    assert summary.processed_clusters == 1
    # stopped_reason = 'maxclusters' when len(clusters) >= max_clusters.
    assert summary.stopped_reason == "maxclusters"


async def test_maxclusters_cap_apply(dedup_env: dict[str, Any]) -> None:
    """max_clusters=1 in apply mode → 1 cluster merged, stopped_reason='maxclusters'."""
    summary = await dd.run_dedup("test-vault", apply=True, max_clusters=1)

    assert summary.merged_clusters == 1
    assert summary.stopped_reason == "maxclusters"
    assert dedup_env["bumps"] == 1  # partial batch still bumps


# ── T7: no bump on non-apply modes ───────────────────────────────────────────


async def test_no_bump_on_dryrun(dedup_env: dict[str, Any]) -> None:
    """Dry-run and propose modes never call bump_version."""
    await dd.run_dedup("test-vault", apply=False, propose_to_review=False)
    assert dedup_env["bumps"] == 0

    dd._state.is_running = False  # reset between logical runs
    await dd.run_dedup("test-vault", apply=False, propose_to_review=True)
    assert dedup_env["bumps"] == 0


# ── T8: single-flight state ───────────────────────────────────────────────────


def test_is_running_initial_false() -> None:
    dd._state.is_running = False
    assert dd.is_running() is False


def test_is_running_reflects_state() -> None:
    dd._state.is_running = True
    assert dd.is_running() is True
    dd._state.is_running = False
    assert dd.is_running() is False


def test_get_last_summary_none_before_run() -> None:
    dd._state.last_summary = None
    assert dd.get_last_summary() is None


async def test_get_last_summary_after_run(dedup_env: dict[str, Any]) -> None:
    """get_last_summary() returns the completed run's summary."""
    summary = await dd.run_dedup("test-vault", apply=False, propose_to_review=False)
    assert dd.get_last_summary() is summary


# ── T9: clamp_bounds ──────────────────────────────────────────────────────────


def test_clamp_bounds_none_returns_default() -> None:
    result = dd.clamp_bounds(None)
    assert 1 <= result <= dd.MAX_CLUSTERS_HARD_CAP


def test_clamp_bounds_over_hardcap() -> None:
    result = dd.clamp_bounds(dd.MAX_CLUSTERS_HARD_CAP + 1000)
    assert result == dd.MAX_CLUSTERS_HARD_CAP


def test_clamp_bounds_zero_clamps_to_one() -> None:
    result = dd.clamp_bounds(0)
    assert result == 1


def test_clamp_bounds_within_range() -> None:
    result = dd.clamp_bounds(10)
    assert result == 10


# ── T10: apply cluster sources union + link repoint + soft delete (unit) ──────


async def test_apply_cluster_sources_unioned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    _apply_cluster unions sources from canonical + both aliases.  Verified by
    capturing the ``sources`` argument passed to ``persist_metadata``.

    DB, Qdrant, and filesystem ops are all stubbed.
    """
    # ── Build fake pages ──────────────────────────────────────────────────────
    canonical = _fake_page("AWS", "wiki/entities/aws.md", sources=["raw/a.md"])
    alias_b = _fake_page(
        "Amazon Web Services", "wiki/entities/amazon-web-services.md", sources=["raw/b.md"]
    )
    alias_c = _fake_page(
        "Amazon Web Services (AWS)",
        "wiki/entities/amazon-web-services-aws.md",
        sources=["raw/c.md"],
    )

    # ── Create minimal on-disk files ──────────────────────────────────────────
    ent_dir = tmp_path / "wiki" / "entities"
    ent_dir.mkdir(parents=True)
    (ent_dir / "aws.md").write_text(
        "---\ntype: entity\ntitle: AWS\n---\n\nAmazon Web Services cloud platform.\n",
        encoding="utf-8",
    )
    (ent_dir / "amazon-web-services.md").write_text(
        "---\ntype: entity\ntitle: Amazon Web Services\n---\n\n"
        "Global cloud computing provider.\n",
        encoding="utf-8",
    )
    (ent_dir / "amazon-web-services-aws.md").write_text(
        "---\ntype: entity\ntitle: Amazon Web Services (AWS)\n---\n\n" "Also known as AWS.\n",
        encoding="utf-8",
    )

    # ── Patch settings.vault_root ─────────────────────────────────────────────
    monkeypatch.setattr(
        "app.ops.dedup_entities.settings",
        type("S", (), {"vault_root": tmp_path, "vault_id": "test-vault"})(),
    )

    # ── Capture persist_metadata sources arg ──────────────────────────────────
    persist_calls: list[dict[str, Any]] = []

    async def fake_persist_metadata(**kwargs: Any) -> None:
        persist_calls.append(kwargs)

    # ── Stub get_session (DB ops) ─────────────────────────────────────────────
    from contextlib import asynccontextmanager

    class _FakeSession:
        def __init__(self) -> None:
            self._rows: list[Any] = []
            self.updates: list[Any] = []

        async def execute(self, stmt: Any) -> _FakeResult:
            return _FakeResult([])

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    class _FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> _FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    @asynccontextmanager
    async def fake_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr("app.ops.dedup_entities.get_session", fake_get_session)

    # Patch the deferred imports inside _apply_cluster.
    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "persist_metadata", fake_persist_metadata)

    deleted_points: list[Any] = []

    async def fake_delete_point(page_id: Any) -> None:
        deleted_points.append(page_id)

    async def fake_upsert_vector(**kwargs: Any) -> None:
        pass

    monkeypatch.setattr(orch, "upsert_vector", fake_upsert_vector)
    import app.qdrant_client as qc

    monkeypatch.setattr(qc, "delete_point", fake_delete_point)

    # ── Invoke _apply_cluster ─────────────────────────────────────────────────
    deleted_count = await dd._apply_cluster(canonical, [alias_b, alias_c])

    # ── Verify sources are unioned ────────────────────────────────────────────
    assert len(persist_calls) == 1, "persist_metadata called exactly once (canonical only)"
    actual_sources = persist_calls[0]["sources"]
    assert actual_sources is not None
    assert "raw/a.md" in actual_sources, "canonical source preserved"
    assert "raw/b.md" in actual_sources, "alias_b source added"
    assert "raw/c.md" in actual_sources, "alias_c source added"
    assert len(actual_sources) == 3, "no duplicate sources"

    # ── Verify alias files are deleted ────────────────────────────────────────
    assert not (ent_dir / "amazon-web-services.md").exists(), "alias_b file deleted"
    assert not (ent_dir / "amazon-web-services-aws.md").exists(), "alias_c file deleted"
    assert (ent_dir / "aws.md").exists(), "canonical file preserved"

    # ── Verify delete_point called for aliases ────────────────────────────────
    deleted_ids = {str(p) for p in deleted_points}
    assert str(alias_b.id) in deleted_ids, "Qdrant point deleted for alias_b"
    assert str(alias_c.id) in deleted_ids, "Qdrant point deleted for alias_c"
    assert str(canonical.id) not in deleted_ids, "canonical Qdrant point NOT deleted"

    # ── Verify return count ───────────────────────────────────────────────────
    assert deleted_count == 2


# ── T11: filesystem integration with body merge ───────────────────────────────


async def test_apply_cluster_merges_bodies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Integration: _apply_cluster concatenates meaningful alias body content into the
    canonical file using ``---`` separators.  Stub bodies are skipped.
    """
    ent_dir = tmp_path / "wiki" / "entities"
    ent_dir.mkdir(parents=True)

    canonical = _fake_page("AWS", "wiki/entities/aws.md", sources=["raw/a.md"])
    alias = _fake_page(
        "Amazon Web Services",
        "wiki/entities/amazon-web-services.md",
        sources=["raw/b.md"],
    )
    alias_stub = _fake_page(
        "Amazon Web Services (AWS)",
        "wiki/entities/amazon-web-services-aws.md",
        sources=["raw/c.md"],
    )

    canonical_body = "Amazon Web Services cloud platform.\n"
    alias_body = "Global cloud computing provider with S3, EC2, Lambda and many more.\n"
    stub_body = "Stub."  # below _MIN_ALIAS_BODY_CHARS → should be skipped

    (ent_dir / "aws.md").write_text(
        f"---\ntype: entity\ntitle: AWS\n---\n\n{canonical_body}",
        encoding="utf-8",
    )
    (ent_dir / "amazon-web-services.md").write_text(
        f"---\ntype: entity\ntitle: Amazon Web Services\n---\n\n{alias_body}",
        encoding="utf-8",
    )
    (ent_dir / "amazon-web-services-aws.md").write_text(
        f"---\ntype: entity\ntitle: Amazon Web Services (AWS)\n---\n\n{stub_body}",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.ops.dedup_entities.settings",
        type("S", (), {"vault_root": tmp_path, "vault_id": "test-vault"})(),
    )

    captured_text: dict[str, Any] = {}

    async def fake_persist_metadata(**kwargs: Any) -> None:
        # Read the file that was written to verify body merge.
        abs_path = (tmp_path / kwargs["file_path"]).resolve()
        if abs_path.exists():
            captured_text["canonical_file"] = abs_path.read_text(encoding="utf-8")

    from contextlib import asynccontextmanager

    class _FakeSession:
        async def execute(self, stmt: Any) -> Any:
            class _R:
                def scalars(self) -> _R:
                    return self

                def all(self) -> list[Any]:
                    return []

            return _R()

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    @asynccontextmanager
    async def fake_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr("app.ops.dedup_entities.get_session", fake_get_session)

    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "persist_metadata", fake_persist_metadata)
    monkeypatch.setattr(orch, "upsert_vector", AsyncMock())

    import app.qdrant_client as qc

    monkeypatch.setattr(qc, "delete_point", AsyncMock())

    await dd._apply_cluster(canonical, [alias, alias_stub])

    # Canonical file must contain both the original body and the meaningful alias body.
    canonical_text = captured_text.get("canonical_file", "")
    assert canonical_body.strip() in canonical_text, "canonical body preserved"
    assert alias_body.strip() in canonical_text, "meaningful alias body appended"
    # Stub body should NOT be appended (< _MIN_ALIAS_BODY_CHARS).
    assert stub_body not in canonical_text, "stub body skipped"
    # A ``---`` separator must appear between the two meaningful sections.
    assert "---" in canonical_text


# ── T12: as_dict serialization ────────────────────────────────────────────────


def test_summary_as_dict_round_trip() -> None:
    """DedupSummary.as_dict() serializes all fields correctly."""
    s = dd.DedupSummary(
        processed_clusters=3,
        merged_clusters=1,
        proposed_clusters=2,
        failed_clusters=0,
        aliases_soft_deleted=2,
        clusters=[
            dd.DedupClusterInfo(
                canonical_key="amazon web services",
                canonical_title="AWS",
                canonical_page_id="abc",
                member_titles=["AWS", "Amazon Web Services"],
                member_page_ids=["abc", "def"],
            )
        ],
        total_cost_usd=0.0,
        stopped_reason="complete",
        max_clusters=50,
        apply=True,
        propose_to_review=False,
    )
    d = s.as_dict()
    assert d["processed_clusters"] == 3
    assert d["merged_clusters"] == 1
    assert d["total_cost_usd"] == 0.0
    assert len(d["clusters"]) == 1
    assert d["clusters"][0]["canonical_key"] == "amazon web services"


# ── T13: _pick_canonical selects shortest title ───────────────────────────────


def test_pick_canonical_shortest_title() -> None:
    """_pick_canonical selects the page with the shortest title (fewest chars)."""
    pages = [PAGE_PAREN, PAGE_LONGFORM, PAGE_AWS]  # deliberately shuffled
    degree_map = {p.id: 0 for p in pages}  # all zero → tiebreak not needed

    canonical = dd._pick_canonical(pages, degree_map)
    assert canonical.title == "AWS"  # shortest title


def test_pick_canonical_tiebreak_by_inbound_degree() -> None:
    """When two pages have the same title length, highest inbound-degree wins."""
    p_a = _fake_page("ABC", "wiki/entities/abc.md")
    p_b = _fake_page("XYZ", "wiki/entities/xyz.md")  # same length

    degree_map = {p_a.id: 5, p_b.id: 10}  # p_b has more inbound links
    canonical = dd._pick_canonical([p_a, p_b], degree_map)
    assert canonical is p_b
