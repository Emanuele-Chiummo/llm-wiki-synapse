"""
OpsScheduler — R12-7 / A5 (SPRINT-v1.2-SCOPE §10 A5); R12-8 adds schema_review (S12);
R12-9 adds reclassify (S13).

A SINGLE asyncio interval background task that:
1. Reads the effective ``lint_schedule``, ``backfill_schedule``,
   ``schema_review_schedule``, and ``reclassify_schedule`` config keys on each tick
   (S10/S11/S12/S13 — ADR-0053 allow-list; values: off|hourly|daily|weekly).
2. If a schedule is not "off" and the op's interval has elapsed since its last run,
   triggers the op exactly once (no overlap — single-flight guard per op, I7).
3. Is non-fatal: an op error is logged + recorded, never crashes the scheduler loop (I7).
4. Tick interval: every 60 s (idle poll; the op intervals are hourly/daily/weekly).

Ops:
  lint          → app.ops.lint.run_lint_scan(vault_id, ...) — findings-only; fix-apply is
                 ALWAYS human-gated (I7/K8). Scheduled runs use DEFAULT bounds.
  backfill      → app.ops.backfill_domains.run_backfill(vault_id, force=False) — only new/
                 untagged pages, cheap (ADR-0054 §4 default bounds). Skipped when
                 backfill_domains.is_running() (single-flight inherited from that module).
  schema_review → app.ops.schema_review.run_schema_review(vault_id) — bounded vault snapshot
                 + ONE provider call; files a schema-suggestion ReviewItem (K8, R12-8).
                 NEVER auto-edits schema.md. Human approves in the Review queue.
  reclassify    → app.ops.reclassify_types.run_reclassify(vault_id, force=False) — bounded
                 type re-classification (NULL/untyped/concept pages by default). Skipped when
                 reclassify_types.is_running() (single-flight inherited from that module).
                 NEVER touches reserved types (overview/index). R12-9.

Per-op state: last_run_at (datetime|None), last_status ("ok"|"error:<msg>"|None),
in_flight bool. State is IN-MEMORY — restarts reset the clock. This is the same
trade-off as ImportScheduler (which persists to import_schedules for its own state;
OpsScheduler does not introduce a new table — no ER change required).

Lifecycle:
  start(loop) called in FastAPI lifespan AFTER load_overrides (config is source of truth).
  stop()      called in lifespan shutdown alongside ImportScheduler.stop().

Injectable clock + op functions for unit tests (mirrors ImportScheduler pattern).

ADR references: ADR-0053 (config keys S10/S11/S12/S13), ADR-0037 (lint scan),
                ADR-0054 (backfill), R12-8 (schema_review), R12-9 (reclassify).
Invariants: I7 (bounded, non-fatal loop), I1 (lint scan reads only — no vault re-scan),
            I6 (backfill/schema_review/reclassify route through InferenceProvider — no
            hardcoded backend).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

# ── Schedule → interval seconds (I7 — always bounded) ────────────────────────

SCHEDULE_SECONDS: dict[str, int] = {
    "hourly": 3_600,
    "daily": 86_400,
    "weekly": 604_800,
}

# Scheduler tick — how often the loop wakes to check whether an op is due.
_TICK_SECONDS: int = 60

# Op names (literal type for type safety)
OpName = Literal["lint", "backfill", "schema_review", "reclassify"]
_OP_NAMES: tuple[OpName, ...] = ("lint", "backfill", "schema_review", "reclassify")


# ── Per-op state ──────────────────────────────────────────────────────────────


class OpState:
    """In-memory state for one scheduled op (restarts reset the clock — see module docstring)."""

    __slots__ = ("in_flight", "last_run_at", "last_status")

    def __init__(self) -> None:
        self.last_run_at: datetime | None = None
        self.last_status: str | None = None  # None | "ok" | "error:<msg>"
        self.in_flight: bool = False

    def as_dict(self, op: str, schedule: str) -> dict[str, object]:
        return {
            "op": op,
            "schedule": schedule,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at is not None else None,
            "last_status": self.last_status,
            "in_flight": self.in_flight,
        }


# ── Clock protocol (injectable for tests) ────────────────────────────────────


class _ClockProtocol(Protocol):
    async def sleep(self, seconds: float) -> None: ...

    def now(self) -> datetime: ...


class _RealClock:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(UTC)


# ── Type aliases for the injectable op functions ──────────────────────────────

LintFn = Callable[..., Awaitable[object]]
BackfillFn = Callable[..., Awaitable[object]]
SchemaReviewFn = Callable[..., Awaitable[object]]
ReclassifyFn = Callable[..., Awaitable[object]]


# ── OpsScheduler ──────────────────────────────────────────────────────────────


class OpsScheduler:
    """
    Single asyncio background task for schedulable ops:
    lint scan + domain backfill + schema review + type reclassify (A5/R12-7/R12-8/R12-9).

    Lifecycle:
      - start(loop) called in FastAPI lifespan AFTER load_overrides (config is source of truth).
      - stop()      called in lifespan shutdown.

    Config changes (PUT /config/app/lint_schedule, /backfill_schedule,
    /schema_review_schedule, or /reclassify_schedule) take effect on the next tick
    (re-reads effective schedule value every 60 s — mirrors ImportScheduler pattern).

    Injectable clock and op functions for infra-free unit tests.
    """

    def __init__(
        self,
        clock: _ClockProtocol | None = None,
        lint_fn: LintFn | None = None,
        backfill_fn: BackfillFn | None = None,
        schema_review_fn: SchemaReviewFn | None = None,
        reclassify_fn: ReclassifyFn | None = None,
    ) -> None:
        self._clock: _ClockProtocol = clock or _RealClock()
        self._lint_fn: LintFn = lint_fn or _default_lint_fn
        self._backfill_fn: BackfillFn = backfill_fn or _default_backfill_fn
        self._schema_review_fn: SchemaReviewFn = schema_review_fn or _default_schema_review_fn
        self._reclassify_fn: ReclassifyFn = reclassify_fn or _default_reclassify_fn
        self._stopping: bool = False
        self._task: asyncio.Task[None] | None = None
        self._state: dict[OpName, OpState] = {
            "lint": OpState(),
            "backfill": OpState(),
            "schema_review": OpState(),
            "reclassify": OpState(),
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the background scheduler task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        try:
            running_loop = asyncio.get_running_loop()
            self._task = running_loop.create_task(self._run(), name="ops_scheduler")
        except RuntimeError:
            if loop is not None:
                self._task = loop.create_task(self._run(), name="ops_scheduler")
        logger.info("OpsScheduler started (tick=%ds, ops=%s)", _TICK_SECONDS, list(_OP_NAMES))

    def stop(self) -> None:
        """Signal the task to stop. Called on lifespan shutdown."""
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
        logger.info("OpsScheduler stopped")

    async def initialize(self) -> None:
        """
        Load persisted last-run timestamps from app_config (R13-4 / T4).

        Must be called BEFORE start() so the first-tick due-check uses the correct
        last_run_at values and does not re-run ops that completed before the restart.
        Non-fatal: any DB error leaves state as None ("never run" — safe fallback).
        """
        from app.scheduler_state import load_scheduler_ts  # noqa: PLC0415

        for op in _OP_NAMES:
            key = f"ops_scheduler.last_run.{op}"
            ts = await load_scheduler_ts(key)
            if ts is not None:
                self._state[op].last_run_at = ts
                logger.info(
                    "OpsScheduler: loaded persisted last_run_at for op=%s: %s",
                    op,
                    ts.isoformat(),
                )
            else:
                logger.debug(
                    "OpsScheduler: no persisted state for op=%s — treating as never run",
                    op,
                )

    # ── Per-op state accessors ────────────────────────────────────────────────

    def get_state(self, op: OpName) -> OpState:
        """Return the OpState for the given op."""
        return self._state[op]

    def get_all_states(self) -> dict[OpName, OpState]:
        """Return a copy of the state dict (for GET /ops/schedules)."""
        return dict(self._state)

    # ── Manual trigger (POST /ops/schedules/{op}/run-now) ────────────────────

    async def run_now(self, op: OpName) -> None:
        """
        Trigger one op immediately regardless of schedule.

        Raises RuntimeError if the op is already in-flight (caller maps to 409).
        Raises ValueError for an unknown op name (defensive; should not happen via API).
        """
        if op not in _OP_NAMES:
            raise ValueError(f"Unknown op {op!r}; must be one of {_OP_NAMES}")

        state = self._state[op]
        if state.in_flight:
            raise RuntimeError(f"{op} is already in-flight")

        # Additional check for backfill: respect the existing backfill_domains single-flight.
        if op == "backfill":
            _check_backfill_not_running()

        # Additional check for reclassify: respect the reclassify_types module single-flight.
        if op == "reclassify":
            _check_reclassify_not_running()

        await self._trigger_op(op)

    # NOTE: schema_review has no external single-flight guard (unlike backfill/reclassify).
    # The in-flight guard above is sufficient; the op itself is idempotent
    # (anti-spam: skip if an open schema-suggestion already exists — see ops/schema_review.py).

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main scheduler loop — wakes every _TICK_SECONDS, checks each op."""
        while not self._stopping:
            try:
                await self._clock.sleep(_TICK_SECONDS)
            except asyncio.CancelledError:
                break

            if self._stopping:
                break

            for op in _OP_NAMES:
                await self._check_and_trigger(op)

    async def _check_and_trigger(self, op: OpName) -> None:
        """
        Read the effective schedule for *op*; if the interval has elapsed and the op is
        not in-flight, trigger it. Non-fatal (I7).
        """
        from app.config_overrides import get_effective  # noqa: PLC0415

        # "lint_schedule" | "backfill_schedule" | "schema_review_schedule"
        schedule_key = f"{op}_schedule"
        schedule = get_effective(schedule_key, "off")

        if schedule == "off" or schedule not in SCHEDULE_SECONDS:
            return

        interval = SCHEDULE_SECONDS[schedule]
        state = self._state[op]

        if state.in_flight:
            logger.debug("ops_scheduler: %s already in-flight — skipping tick", op)
            return

        # Check whether the interval has elapsed since the last run.
        now = self._clock.now()
        if state.last_run_at is not None:
            elapsed = (now - state.last_run_at).total_seconds()
            if elapsed < interval:
                return  # not due yet

        # For backfill: also respect the backfill_domains module single-flight guard.
        if op == "backfill":
            try:
                _check_backfill_not_running()
            except RuntimeError:
                logger.debug("ops_scheduler: backfill already running in-flight (external) — skip")
                return

        # For reclassify: also respect the reclassify_types module single-flight guard.
        if op == "reclassify":
            try:
                _check_reclassify_not_running()
            except RuntimeError:
                logger.debug(
                    "ops_scheduler: reclassify already running in-flight (external) — skip"
                )
                return

        await self._trigger_op(op)

    async def _trigger_op(self, op: OpName) -> None:
        """
        Run the op. Non-fatal: errors are logged + recorded in state.last_status (I7).
        Sets in_flight=True for the duration; always clears it in finally.
        """
        state = self._state[op]
        state.in_flight = True
        logger.info("ops_scheduler: starting op=%s", op)
        _succeeded = False
        try:
            if op == "lint":
                await self._lint_fn()
            elif op == "backfill":
                await self._backfill_fn()
            elif op == "schema_review":
                await self._schema_review_fn()
            else:
                await self._reclassify_fn()
            state.last_status = "ok"
            _succeeded = True
            logger.info("ops_scheduler: op=%s completed (status=ok)", op)
        except Exception as exc:  # noqa: BLE001 — never crash the scheduler loop (I7)
            msg = str(exc)
            state.last_status = f"error:{msg[:120]}"
            logger.error("ops_scheduler: op=%s failed: %s", op, exc)
        finally:
            now = self._clock.now()
            state.last_run_at = now
            state.in_flight = False
            # Persist last-run timestamp on success only (R13-4 / T4).
            # Failed runs do not update the persisted timestamp so the op will be
            # re-attempted on the next tick (fail-open retry semantics).
            if _succeeded:
                from app.scheduler_state import save_scheduler_ts  # noqa: PLC0415

                await save_scheduler_ts(f"ops_scheduler.last_run.{op}", now)


# ── Default op implementations ────────────────────────────────────────────────


async def _default_lint_fn() -> None:
    """
    Run one bounded lint scan (findings only, DEFAULT bounds — I7/K8).

    Deferred import avoids circular import at module load time.
    """
    from app.config import settings  # noqa: PLC0415
    from app.ops.lint import run_lint_scan  # noqa: PLC0415

    await run_lint_scan(vault_id=settings.vault_id)


async def _default_backfill_fn() -> None:
    """
    Run one bounded domain backfill (force=False — only new/untagged pages, I7).

    Deferred import avoids circular import at module load time.
    """
    from app.config import settings  # noqa: PLC0415
    from app.ops.backfill_domains import run_backfill  # noqa: PLC0415

    await run_backfill(vault_id=settings.vault_id, force=False)


async def _default_schema_review_fn() -> None:
    """
    Run one bounded schema review pass (R12-8, K8):
    vault snapshot → ONE provider call → file a schema-suggestion ReviewItem (if needed).

    The op is idempotent: ops/schema_review.run_schema_review skips (zero cost) when an
    open schema-suggestion already exists in the queue (anti-spam).

    NOTE: this scheduled path runs REGARDLESS of the SCHEMA_SUGGESTION_ENABLED env flag.
    That flag gates the automatic post-ingest trigger; an explicit schedule or run-now is
    explicit user intent and must always be honoured. This decision is documented here to
    prevent future confusion (R12-8 design decision).

    Deferred import avoids circular import at module load time (I6).
    """
    from app.config import settings  # noqa: PLC0415
    from app.ops.schema_review import run_schema_review  # noqa: PLC0415

    await run_schema_review(vault_id=settings.vault_id)


async def _default_reclassify_fn() -> None:
    """
    Run one bounded type re-classification with DEFAULT bounds (force=False — only
    NULL/untyped/concept pages, I7). Skipped when reclassify_types.is_running().

    Deferred import avoids circular import at module load time (I6).
    """
    from app.config import settings  # noqa: PLC0415
    from app.ops.reclassify_types import is_running, run_reclassify  # noqa: PLC0415

    if is_running():
        logger.debug("ops_scheduler: reclassify already in-flight (external) — skip")
        return
    await run_reclassify(vault_id=settings.vault_id, force=False)


def _check_backfill_not_running() -> None:
    """
    Check the backfill_domains module single-flight guard.
    Raises RuntimeError if a backfill is already in flight (from the external endpoint).
    """
    from app.ops.backfill_domains import is_running  # noqa: PLC0415

    if is_running():
        raise RuntimeError("backfill already in-flight (external trigger)")


def _check_reclassify_not_running() -> None:
    """
    Check the reclassify_types module single-flight guard.
    Raises RuntimeError if a reclassify run is already in flight (from the external endpoint).
    """
    from app.ops.reclassify_types import is_running  # noqa: PLC0415

    if is_running():
        raise RuntimeError("reclassify already in-flight (external trigger)")


# ── Module-level singleton (initialised in main.py lifespan) ─────────────────
_ops_scheduler: OpsScheduler | None = None
