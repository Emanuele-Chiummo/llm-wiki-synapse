"""review_items — proposal-model redesign (ADR-0034)

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-30

Schema changes (ADR-0034 §3 — review_items proposal-model redesign):

ADD columns (all nullable — online-safe additive change):
  source_page_id        UUID NULL FK → pages.id    (provenance page)
  proposed_title        Text NULL                  (title to create; sweep match key)
  proposed_page_type    Text NULL                  (entity|concept|source|synthesis|comparison)
  proposed_dir          Text NULL                  (wiki/ subdir, display only)
  rationale             Text NULL                  (replaces pre_generated_query)
  resolution            Text NULL                  (how the item closed)
  created_page_id       UUID NULL FK → pages.id    (page a Create produced)

DROP column:
  pre_generated_query   — superseded by rationale + the suggestion type.

ADD index:
  ix_review_items_vault_proposed_title ON (vault_id, proposed_title)
  (rule-based sweep: cheap title-lookup, ADR-0034 §3.1 / §6.2)

DATA step (legacy-row left-shift, ADR-0034 §3.2):
  Any rows with item_type IN ('new_page','update_page','approved') or
  status='approved' are transitioned to:
    status='skipped', resolution='skipped'
  They referenced auto-created pages that already exist (obsolete under the new model).

The item_type and status columns remain Text — no type alteration required.
Accepted value sets change only at the application layer.

Downgrade:
  Drops all added columns and the new index.
  Restores pre_generated_query (NULL for all rows — no data recovery possible).
  NOTE: downgrade does NOT undo the legacy-row data step (rows remain skipped).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    ADR-0034 §3 — proposal-model redesign for review_items.

    Execution order:
      1. ADD all new nullable columns.
      2. DROP pre_generated_query.
      3. ADD ix_review_items_vault_proposed_title index.
      4. DATA step: left-shift obsolete legacy rows.
    """

    # ── 1. ADD new nullable columns ───────────────────────────────────────────

    # source_page_id: provenance — the page whose ingest produced this proposal
    op.add_column(
        "review_items",
        sa.Column(
            "source_page_id",
            sa.Text(),  # stored as string for SQLite compat (same as page_id pattern)
            nullable=True,
            comment=(
                "FK → pages.id (stored as text for SQLite compat); "
                "the page whose ingest produced this proposal (provenance). "
                "Distinct from page_id (the conflicting/target page). ADR-0034 §3.1 ADD."
            ),
        ),
    )

    # proposed_title: title the LLM proposes to create
    op.add_column(
        "review_items",
        sa.Column(
            "proposed_title",
            sa.Text(),
            nullable=True,
            comment=(
                "The title the LLM proposes to create (required for missing-page; advisory for "
                "others). Drives the lazy skeleton (ADR-0034 §5.2) and rule-based sweep title "
                "match (§6.2). ADR-0034 §3.1 ADD."
            ),
        ),
    )

    # proposed_page_type: inferred PageType for the lazy skeleton
    op.add_column(
        "review_items",
        sa.Column(
            "proposed_page_type",
            sa.Text(),
            nullable=True,
            comment=(
                "Inferred PageType: entity|concept|source|synthesis|comparison. "
                "NULL → heuristic applied at Create time (ADR-0034 §5.2). "
                "source is never a valid Create target. ADR-0034 §3.1 ADD."
            ),
        ),
    )

    # proposed_dir: target wiki/ subdir (display only)
    op.add_column(
        "review_items",
        sa.Column(
            "proposed_dir",
            sa.Text(),
            nullable=True,
            comment=(
                "Target wiki/ subdir derived from proposed_page_type (DISPLAY ONLY). "
                "Recomputed at Create time — never trusted blindly (ADR-0034 §5.2). "
                "ADR-0034 §3.1 ADD."
            ),
        ),
    )

    # rationale: why this matters (replaces pre_generated_query)
    op.add_column(
        "review_items",
        sa.Column(
            "rationale",
            sa.Text(),
            nullable=True,
            comment=(
                "Short human-readable 'why this matters' (ADR-0034 §3.1 ADD). "
                "Replaces pre_generated_query. For suggestion: the gap/follow-up; "
                "for contradiction: conflict description; for confirm: what needs confirming. "
                "Used as topic hint for Deep Research."
            ),
        ),
    )

    # resolution: how the item closed
    op.add_column(
        "review_items",
        sa.Column(
            "resolution",
            sa.Text(),
            nullable=True,
            comment=(
                "How the item closed (ADR-0034 §3.1 ADD): "
                "created | skipped | researched | rule_resolved | llm_resolved. "
                "NULL while pending."
            ),
        ),
    )

    # created_page_id: the page a Create produced
    op.add_column(
        "review_items",
        sa.Column(
            "created_page_id",
            sa.Text(),  # stored as text for SQLite compat (same pattern as page_id)
            nullable=True,
            comment=(
                "FK → pages.id (stored as text for SQLite compat); "
                "the page a successful Create action produced (ADR-0034 §5). "
                "NULL while status != 'created'. ADR-0034 §3.1 ADD."
            ),
        ),
    )

    # ── 2. DROP pre_generated_query ───────────────────────────────────────────
    op.drop_column("review_items", "pre_generated_query")

    # ── 3. ADD ix_review_items_vault_proposed_title index ─────────────────────
    op.create_index(
        "ix_review_items_vault_proposed_title",
        "review_items",
        ["vault_id", "proposed_title"],
    )

    # ── 4. DATA step: left-shift obsolete legacy rows ─────────────────────────
    # Any row whose item_type is a pre-ADR-0034 value (new_page/update_page/
    # deep_research_candidate) OR whose status is the now-removed 'approved'
    # is left-shifted to status='skipped', resolution='skipped'.
    # These rows referenced auto-created pages that already exist and are
    # obsolete under the new proposal model (ADR-0034 §3.2 / M5 pre-release).
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE review_items "
            "SET status = 'skipped', resolution = 'skipped' "
            "WHERE item_type IN ('new_page', 'update_page', 'deep_research_candidate') "
            "   OR status = 'approved'"
        )
    )


def downgrade() -> None:
    """
    Reverse ADR-0034 §3 schema additions.

    NOTE: the legacy-row data step (left-shift to skipped) is NOT reversed —
    those rows are obsolete and cannot meaningfully be restored to the old model.
    pre_generated_query is restored as a NULL column for all rows (data is gone).
    """

    # Drop the new index
    op.drop_index("ix_review_items_vault_proposed_title", table_name="review_items")

    # Drop all added columns
    op.drop_column("review_items", "created_page_id")
    op.drop_column("review_items", "resolution")
    op.drop_column("review_items", "rationale")
    op.drop_column("review_items", "proposed_dir")
    op.drop_column("review_items", "proposed_page_type")
    op.drop_column("review_items", "proposed_title")
    op.drop_column("review_items", "source_page_id")

    # Restore pre_generated_query (NULL for all rows — data unrecoverable)
    op.add_column(
        "review_items",
        sa.Column(
            "pre_generated_query",
            sa.Text(),
            nullable=True,
            comment=(
                "RESTORED by downgrade from 0013. "
                "Newline-sep 1-3 follow-up questions; data is NULL (unrecoverable). "
                "ADR-0025 §3.1 / ADR-0034 §3.2 downgrade."
            ),
        ),
    )
