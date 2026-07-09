"""
Minimal in-process fixed-window rate limiter for inference-cost endpoints (R13-9, B4).

Design principles
-----------------
* Zero new runtime dependencies (no slowapi, no Redis, no aioredis).
* Per-client fixed-window counter, keyed by ``client_ip.resolve_source_ip`` — the SAME
  trusted-proxy-aware resolver used for source classification (ADR-0033). Behind a
  reverse proxy / tunnel listed in MCP_TRUSTED_PROXIES it keys on the proxy-attested
  client IP; otherwise it keys on the transport peer. This stops the limiter collapsing
  to a single global bucket when every request presents the tunnel's IP (H3). X-Forwarded-For
  is honoured ONLY from a trusted proxy — an untrusted peer forging XFF is keyed by peer IP.
* Config via ``app.config.settings``:
    RATE_LIMIT_ENABLED (bool, default True)
    RATE_LIMIT_REQUESTS (int, default 20) — max requests per window per IP
    RATE_LIMIT_WINDOW_SECONDS (int, default 60) — window length in seconds
* FastAPI dependency: ``Depends(rate_limit)`` declared on individual route handlers.
* 429 response with ``Retry-After`` header on excess.
* Stale-entry cleanup every ``_CLEANUP_EVERY`` calls (amortised O(1) overhead).
* Streaming endpoints: limits request STARTS, not individual tokens (I3).

Applied to: POST /chat/stream, POST /ingest/trigger, POST /ingest/upload,
            POST /ingest/from-text, POST /research/start.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import HTTPException, Request

from app.client_ip import resolve_source_ip

logger = logging.getLogger(__name__)

# Cleanup stale entries every N total requests (amortised memory bound).
_CLEANUP_EVERY: int = 200


class _FixedWindowLimiter:
    """
    Fixed-window rate limiter keyed by client IP.

    State per IP: ``(request_count, window_start_monotonic)``.
    Async-safe: guarded by an ``asyncio.Lock``.
    """

    def __init__(self) -> None:
        # ip → (count, window_start)
        self._windows: dict[str, tuple[int, float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._total_calls: int = 0

    async def check(
        self,
        request: Request,
        *,
        requests: int,
        window_seconds: float,
        _now: float | None = None,  # testing hook — do not use in production
    ) -> None:
        """
        Check rate limit for the incoming request.

        Raises :exc:`~fastapi.HTTPException` (429) when the client has exceeded
        ``requests`` within the current ``window_seconds`` window.
        Is a no-op when ``requests <= 0`` (disabled / misconfigured).

        Parameters
        ----------
        request:
            The incoming FastAPI request (used to read client IP).
        requests:
            Maximum requests allowed per window (from settings).
        window_seconds:
            Window duration in seconds (from settings).
        _now:
            Monotonic timestamp override for unit-testing window rollover.
            Pass ``None`` (the default) in production.
        """
        if requests <= 0:
            return

        # Trusted-proxy-aware key: real client IP behind a tunnel in MCP_TRUSTED_PROXIES,
        # else the transport peer. Prevents one shared bucket for all traffic (H3, ADR-0033).
        ip: str = resolve_source_ip(request.scope) or "unknown"
        now: float = _now if _now is not None else time.monotonic()

        async with self._lock:
            self._total_calls += 1

            # Periodic cleanup: evict entries whose window has expired.
            if self._total_calls % _CLEANUP_EVERY == 0:
                cutoff = now - window_seconds
                stale = [k for k, (_, ws) in self._windows.items() if ws < cutoff]
                for k in stale:
                    del self._windows[k]
                if stale:
                    logger.debug("rate_limit: evicted %d stale IP entries", len(stale))

            count, window_start = self._windows.get(ip, (0, now))

            if now - window_start >= window_seconds:
                # Window has expired → start a fresh window for this IP.
                self._windows[ip] = (1, now)
                return

            if count >= requests:
                # Over limit — compute remaining window time for Retry-After.
                remaining_s = window_seconds - (now - window_start)
                retry_after = max(1, int(remaining_s) + 1)
                logger.info(
                    "rate_limit: 429 for ip=%s count=%d limit=%d window=%ds",
                    ip,
                    count,
                    requests,
                    int(window_seconds),
                )
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Rate limit exceeded: max {requests} requests per "
                        f"{int(window_seconds)} s window. "
                        f"Retry in approximately {retry_after} s."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            # Under limit — increment counter.
            self._windows[ip] = (count + 1, window_start)


# ── Module-level singleton — one per process (no external dep, I7) ────────────
_limiter: _FixedWindowLimiter = _FixedWindowLimiter()


async def rate_limit(request: Request) -> None:
    """
    FastAPI dependency: enforce per-IP fixed-window rate limit on inference-cost routes.

    Usage (router-level, preferred)::

        @router.post("/chat/stream", dependencies=[Depends(rate_limit)])
        async def chat_stream(...): ...

    Configuration (all via env vars — no code change needed):
        ``RATE_LIMIT_ENABLED``        bool,  default ``True``
        ``RATE_LIMIT_REQUESTS``       int,   default ``20``  (requests per window)
        ``RATE_LIMIT_WINDOW_SECONDS`` int,   default ``60``  (window in seconds)

    When ``RATE_LIMIT_ENABLED=false``, returns immediately without incrementing
    any counter (useful for dev / CI where you do not want to hit the limit).
    """
    from app.config import settings  # noqa: PLC0415 — lazy import avoids circular dep

    if not settings.rate_limit_enabled:
        return

    await _limiter.check(
        request,
        requests=settings.rate_limit_requests,
        window_seconds=float(settings.rate_limit_window_seconds),
    )
