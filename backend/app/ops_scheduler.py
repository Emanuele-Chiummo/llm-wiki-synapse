"""
OpsScheduler — R12-7 / A5 (SPRINT-v1.2-SCOPE §10 A5).

A SINGLE asyncio interval background task that:
1. Reads the effective ``lint_schedule`` and ``backfill_schedule`` config keys on each
   tick (S10/S11 — ADR-0053 allow-list; values: off|hourly|daily|weekly).
2. If a schedule is not "off" and the op's interval has elapsed since its last run,
   triggers the op exactly once (no overlap — single-flight guard per op, I7).
3. Is non-fatal: an op error is logged + recorded, never crashes the scheduler loop (I7).
4. Tick interval: every 60 s (idle poll; the op intervals are hourly/daily/weekly).

Ops:
  lint     → app.ops.lint.run_lint_scan(vault_id, ...) — findings-only; fix-apply is
             ALWAYS human-gated (I7/K8). Scheduled runs use DEFAULT bounds.
  backfill → app.ops.backfill_domains.run_backfill(vault_id, force=False) — only new/
             untagged pages, cheap (ADR-0054 §4 default bounds). Skipped when
             backfill_domains.is_running() (single-flight inherited from that module).

Per-op state: last_run_at (datetime|None), last_status ("ok"|"error:<msg>"|None),
in_flight bool. State is IN-MEMORY — restarts reset the clock. This is the same
trade-off as ImportScheduler (which persists to import_schedules for its own state;
OpsScheduler does not introduce a new table — no ER change required).

Lifecycle:
  start(loop) called in FastAPI lifespan AFTER load_overrides (config is source of truth).
  stop()      called in lifespan shutdown alongside ImportScheduler.stop().

Injectable clock + op functions for unit tests (mirrors ImportScheduler pattern).

ADR references: ADR-0053 (config keys S10/S11), ADR-0037 (lint scan), ADR-0054 (backfill).
Invariants: I7 (bounded, non-fatal loop), I1 (lint scan reads only — no vault re-scan),
            I6 (backfill routes through InferenceProvider — no hardcoded backend).
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
OpName = Literal["lint", "backfill"]
_OP_NAMES: tuple[OpName, ...] = ("lint", "backfill")


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


# ── OpsScheduler ──────────────────────────────────────────────────────────────


class OpsScheduler:
    """
    Single asyncio background task for schedulable ops: lint scan + domain backfill (A5/R12-7).

    Lifecycle:
      - start(loop) called in FastAPI lifespan AFTER load_overrides (config is source of truth).
      - stop()      called in lifespan shutdown.

    Config changes (PUT /config/app/lint_schedule or /backfill_schedule) take effect on the
    next tick (re-reads effective schedule value every 60 s — mirrors ImportScheduler pattern).

    Injectable clock and op functions for infra-free unit tests.
    """

    def __init__(
        self,
        clock: _ClockProtocol | None = None,
        lint_fn: LintFn | None = None,
        backfill_fn: BackfillFn | None = None,
    ) -> None:
        self._clock: _ClockProtocol = clock or _RealClock()
        self._lint_fn: LintFn = lint_fn or _default_lint_fn
        self._backfill_fn: BackfillFn = backfill_fn or _default_backfill_fn
        self._stopping: bool = False
        self._task: asyncio.Task[None] | None = None
        self._state: dict[OpName, OpState] = {
            "lint": OpState(),
            "backfill": OpState(),
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

        await self._trigger_op(op)

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

        schedule_key = f"{op}_schedule"  # "lint_schedule" or "backfill_schedule"
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

        await self._trigger_op(op)

    async def _trigger_op(self, op: OpName) -> None:
        """
        Run the op. Non-fatal: errors are logged + recorded in state.last_status (I7).
        Sets in_flight=True for the duration; always clears it in finally.
        """
        state = self._state[op]
        state.in_flight = True
        logger.info("ops_scheduler: starting op=%s", op)
        try:
            if op == "lint":
                await self._lint_fn()
            else:
                await self._backfill_fn()
            state.last_status = "ok"
            logger.info("ops_scheduler: op=%s completed (status=ok)", op)
        except Exception as exc:  # noqa: BLE001 — never crash the scheduler loop (I7)
            msg = str(exc)
            state.last_status = f"error:{msg[:120]}"
            logger.error("ops_scheduler: op=%s failed: %s", op, exc)
        finally:
            state.last_run_at = self._clock.now()
            state.in_flight = False


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


def _check_backfill_not_running() -> None:
    """
    Check the backfill_domains module single-flight guard.
    Raises RuntimeError if a backfill is already in flight (from the external endpoint).
    """
    from app.ops.backfill_domains import is_running  # noqa: PLC0415

    if is_running():
        raise RuntimeError("backfill already in-flight (external trigger)")


# ── Module-level singleton (initialised in main.py lifespan) ─────────────────
_ops_scheduler: OpsScheduler | None = None
