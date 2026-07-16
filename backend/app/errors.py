"""
Domain exception taxonomy (BE-QUAL-1 partial, v1.9.2 "Real boundaries").

Today the ``ops``/``routers`` layer raises ``fastapi.HTTPException`` directly from
business-logic code (75+ call sites), coupling application logic to the HTTP transport.
This module introduces a small hierarchy of *domain* exceptions that business code can
raise instead, plus a single global FastAPI exception handler that translates them back
to HTTP — with the exact same response shape FastAPI already produces for
``HTTPException`` (``{"detail": ...}``, no extra envelope).

CRITICAL — non-regression contract
-----------------------------------
This module changes NOTHING about the JSON shape of an error response, the status code
of any existing endpoint, or any header. ``register_exception_handlers(app)`` renders a
``SynapseError`` by constructing the equivalent ``fastapi.HTTPException`` and delegating
to FastAPI's own ``http_exception_handler`` — the very function FastAPI already uses for
uncaught ``HTTPException`` — so the byte-for-bit response is identical whether a call
site raises ``HTTPException(status_code=404, detail=...)`` directly or raises
``NotFoundError(...)`` from this module. Migrating existing call sites is OPTIONAL and can
happen incrementally, call site by call site, with zero observable behaviour change.

The class-per-status-code list below was derived from an audit of the ``status_code=``
values actually used across ``backend/app/ops`` and ``backend/app/routers`` (404, 409,
422, 400, 502, 500, 503, 413, 415, 401, 403, 410, 501) — not invented up front.

Usage
-----
    from app.errors import NotFoundError, ConflictError

    raise NotFoundError(f"Review item {item_id} not found")
    raise ConflictError(f"Review item {item_id} has status={item.status!r}; ...")

Both accept the same ``headers`` kwarg as ``HTTPException`` when a call site needs to set
one (e.g. ``Retry-After``).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.responses import Response


class SynapseError(Exception):
    """
    Base domain exception. Carries the same ``status_code``/``detail``/``headers``
    triple as ``fastapi.HTTPException`` so the global handler can translate it back
    1:1 without altering the observable response.

    Business/ops code should prefer one of the subclasses below over instantiating
    ``SynapseError`` directly (mirrors why callers rarely raise a bare ``Exception``).
    """

    status_code: int = 500

    def __init__(
        self,
        detail: Any = "Internal Server Error",
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        self.headers = headers
        super().__init__(detail if isinstance(detail, str) else str(detail))

    def to_http_exception(self) -> HTTPException:
        """Build the equivalent ``HTTPException`` — used by the global handler."""
        return HTTPException(status_code=self.status_code, detail=self.detail, headers=self.headers)


class BadRequestError(SynapseError):
    """400 — malformed/invalid request (e.g. unknown enum value, bad params)."""

    status_code = 400


class AuthenticationError(SynapseError):
    """401 — missing/invalid authentication credential."""

    status_code = 401


class ForbiddenError(SynapseError):
    """403 — authenticated but not permitted to perform the action."""

    status_code = 403


class NotFoundError(SynapseError):
    """404 — the requested resource (page, review item, lint finding, ...) does not exist."""

    status_code = 404


class GoneError(SynapseError):
    """410 — the resource existed but has been permanently removed/cascade-deleted."""

    status_code = 410


class ConflictError(SynapseError):
    """409 — the resource exists but is in a state that forbids the requested action
    (e.g. review item not pending, lint finding already resolved, missing prerequisite
    configuration such as no ingest provider)."""

    status_code = 409


class PayloadTooLargeError(SynapseError):
    """413 — request/upload body exceeds a configured size cap."""

    status_code = 413


class UnsupportedMediaTypeError(SynapseError):
    """415 — file/content type not supported by the ingest pipeline."""

    status_code = 415


class ValidationError(SynapseError):
    """422 — semantically invalid input (fails a domain validation rule)."""

    status_code = 422


class NotImplementedFeatureError(SynapseError):
    """501 — feature/branch intentionally not implemented (rare; explicit stub)."""

    status_code = 501


class UpstreamError(SynapseError):
    """502 — a call to an external/upstream dependency (InferenceProvider, SearXNG,
    Marker, Qdrant, ...) failed; the caller's own state is left unchanged."""

    status_code = 502


class ServiceUnavailableError(SynapseError):
    """503 — a required dependency/feature is not currently configured/reachable
    (e.g. web-search disabled, embeddings backend unreachable)."""

    status_code = 503


async def synapse_error_handler(request: Request, exc: SynapseError) -> Response:
    """
    Global handler: translate any ``SynapseError`` to the exact HTTP response FastAPI
    would already produce for the equivalent ``HTTPException`` (same shape, same
    status code, same headers). Registered once in ``main.py`` for the ``SynapseError``
    base class — Starlette's exception-handler lookup walks the MRO, so every subclass
    in this module is routed here automatically.
    """
    return await http_exception_handler(request, exc.to_http_exception())


def register_exception_handlers(app: Any) -> None:
    """Register the global ``SynapseError`` → HTTP translation handler on *app*."""
    app.add_exception_handler(SynapseError, synapse_error_handler)
