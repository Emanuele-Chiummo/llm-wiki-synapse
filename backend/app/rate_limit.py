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
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

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


# ── Authentication failure rate limiter (SEC-RL-1) ────────────────────────────


class _AuthFailureLimiter:
    """
    Rate limiter specifically for authentication failures (401 responses).
    Prevents token-guessing and brute-force auth attacks (SEC-RL-1).

    State per IP: (failure_count, window_start_monotonic).
    Async-safe: guarded by asyncio.Lock.
    """

    def __init__(self) -> None:
        # ip → (count, window_start)
        self._windows: dict[str, tuple[int, float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._total_calls: int = 0

    async def check_auth_failure(
        self,
        request: Request,
        *,
        max_failures: int,
        window_seconds: float,
        _now: float | None = None,
    ) -> None:
        """
        Check auth failure rate limit.

        Raises HTTPException (429) when client has exceeded max_failures within window_seconds.
        """
        if max_failures <= 0:
            return

        ip: str = resolve_source_ip(request.scope) or "unknown"
        now: float = _now if _now is not None else time.monotonic()

        async with self._lock:
            self._total_calls += 1

            if self._total_calls % _CLEANUP_EVERY == 0:
                cutoff = now - window_seconds
                stale = [k for k, (_, ws) in self._windows.items() if ws < cutoff]
                for k in stale:
                    del self._windows[k]
                if stale:
                    logger.debug("rate_limit[401]: evicted %d stale IP entries", len(stale))

            count, window_start = self._windows.get(ip, (0, now))

            if now - window_start >= window_seconds:
                self._windows[ip] = (1, now)
                return

            if count >= max_failures:
                remaining_s = window_seconds - (now - window_start)
                retry_after = max(1, int(remaining_s) + 1)
                logger.warning(
                    "rate_limit[401]: 429 for ip=%s auth_failures=%d limit=%d",
                    ip,
                    count,
                    max_failures,
                )
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Too many authentication failures. "
                        f"Max {max_failures} failures per {int(window_seconds)} s. "
                        f"Retry in approximately {retry_after} s."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            self._windows[ip] = (count + 1, window_start)


_auth_failure_limiter: _AuthFailureLimiter = _AuthFailureLimiter()


async def check_auth_failure_rate_limit(request: Request) -> None:
    """
    Dependency: enforce per-IP rate limit on authentication failures (401 responses).
    SEC-RL-1: prevents token-guessing attacks.

    Configuration via env vars:
        RATE_LIMIT_ENABLED              bool, default True
        AUTH_FAILURE_LIMIT_ATTEMPTS     int, default 10 (401 attempts per window)
        AUTH_FAILURE_LIMIT_WINDOW_SECS  int, default 300 (5 minutes)
    """
    from app.config import settings  # noqa: PLC0415

    if not settings.rate_limit_enabled:
        return

    max_failures = getattr(settings, "auth_failure_limit_attempts", 10)
    window_secs = getattr(settings, "auth_failure_limit_window_seconds", 300)

    if max_failures <= 0:
        return

    await _auth_failure_limiter.check_auth_failure(
        request,
        max_failures=max_failures,
        window_seconds=float(window_secs),
    )


# ── FastAPI middleware for auth failure rate limiting ────────────────────────


class AuthFailureRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to rate-limit authentication failures (401 responses) by IP (SEC-RL-1).

    On every 401 response, increments the failure counter for the client IP.
    When the counter exceeds the configured limit within the window, returns 429
    instead of letting the 401 propagate.

    Placement: should be added AFTER auth middleware and other layers that
    generate 401s, so it intercepts all auth failures.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Wrap the request and check rate limit on 401 responses."""
        response = await call_next(request)

        # Only rate-limit 401 responses
        if response.status_code == 401:
            try:
                await check_auth_failure_rate_limit(request)
            except HTTPException as exc:
                # Rate limit exceeded — return 429 instead of 401
                return Response(
                    content=str(exc.detail),
                    status_code=exc.status_code,
                    headers=dict(exc.headers) if exc.headers else {},
                )

        return response
