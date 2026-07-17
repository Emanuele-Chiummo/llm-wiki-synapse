"""
Domain exception taxonomy + stable JSON error envelope (ADR-0086, 2.0.0).

Business/ops code raises one of the *domain* exceptions in this module instead of
``fastapi.HTTPException`` directly, decoupling application logic from the HTTP transport.
A small set of global FastAPI exception handlers translates every error -- domain
exception, Pydantic request-validation failure, or raw ``HTTPException`` -- into ONE
stable JSON envelope:

    {"error": {"code": "not_found", "message": "...", "status": 404, "details": null}}

See ADR-0086 for the full contract. Summary:

* ``code``    -- stable snake_case slug. For a ``SynapseError`` subclass it is derived
                mechanically by :func:`error_code_for` (strip a trailing ``"Error"``,
                CamelCase -> snake_case). One override: the base class -> ``internal_error``.
* ``message`` -- the human-readable string (exactly what ``detail`` carried before 2.0.0).
* ``status``  -- the HTTP status code, duplicated in the body for JSON-only consumers.
* ``details`` -- optional structured payload (field-level validation errors); ``null`` for
                simple errors.

History
-------
1.9.2 introduced this taxonomy but *deliberately* rendered it as the legacy ``{"detail":
...}`` shape (byte-for-byte identical to a raw ``HTTPException``) and deferred the envelope
to 2.0.0. This module now lands that deferred change -- the envelope above is the ONLY error
shape. There is no dual-shape/backward-compatible mode (SemVer MAJOR). Status codes and
message wording are preserved exactly; only the wrapping changed.

Usage
-----
    from app.errors import NotFoundError, ConflictError

    raise NotFoundError(f"Review item {item_id} not found")
    raise ConflictError(f"Review item {item_id} has status={item.status!r}; ...")

Both accept the same ``headers`` kwarg as ``HTTPException`` when a call site needs to set
one (e.g. ``Retry-After``).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response


class SynapseError(Exception):
    """
    Base domain exception. Carries the same ``status_code``/``detail``/``headers``
    triple as ``fastapi.HTTPException`` so the global handler can translate it into the
    stable error envelope (ADR-0086).

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
        """Build the equivalent ``HTTPException`` (kept for call sites that still need it)."""
        return HTTPException(status_code=self.status_code, detail=self.detail, headers=self.headers)


class BadRequestError(SynapseError):
    """400 -- malformed/invalid request (e.g. unknown enum value, bad params)."""

    status_code = 400


class AuthenticationError(SynapseError):
    """401 -- missing/invalid authentication credential."""

    status_code = 401


class ForbiddenError(SynapseError):
    """403 -- authenticated but not permitted to perform the action."""

    status_code = 403


class NotFoundError(SynapseError):
    """404 -- the requested resource (page, review item, lint finding, ...) does not exist."""

    status_code = 404


class GoneError(SynapseError):
    """410 -- the resource existed but has been permanently removed/cascade-deleted."""

    status_code = 410


class ConflictError(SynapseError):
    """409 -- the resource exists but is in a state that forbids the requested action
    (e.g. review item not pending, lint finding already resolved, missing prerequisite
    configuration such as no ingest provider)."""

    status_code = 409


class PayloadTooLargeError(SynapseError):
    """413 -- request/upload body exceeds a configured size cap."""

    status_code = 413


class UnsupportedMediaTypeError(SynapseError):
    """415 -- file/content type not supported by the ingest pipeline."""

    status_code = 415


class ValidationError(SynapseError):
    """422 -- semantically invalid input (fails a domain validation rule).

    Distinct from FastAPI's ``RequestValidationError`` (schema/Pydantic mismatch): that
    carries code ``validation_error``; this domain rule failure carries code ``validation``.
    """

    status_code = 422


class NotImplementedFeatureError(SynapseError):
    """501 -- feature/branch intentionally not implemented (rare; explicit stub)."""

    status_code = 501


class UpstreamError(SynapseError):
    """502 -- a call to an external/upstream dependency (InferenceProvider, SearXNG,
    Marker, Qdrant, ...) failed; the caller's own state is left unchanged."""

    status_code = 502


class ServiceUnavailableError(SynapseError):
    """503 -- a required dependency/feature is not currently configured/reachable
    (e.g. web-search disabled, embeddings backend unreachable)."""

    status_code = 503


# ── Stable error-code derivation (ADR-0086 §2) ────────────────────────────────
# Rule: strip a trailing "Error" suffix, then CamelCase -> snake_case, lowercased.
# The ONLY override is the base class (mechanical "synapse" -> the meaningful
# "internal_error"). Adding a subclass yields a code with zero hand maintenance.
_CODE_OVERRIDES: dict[str, str] = {"SynapseError": "internal_error"}
_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def error_code_for(exc_cls: type[SynapseError]) -> str:
    """Derive the stable snake_case ``code`` for a ``SynapseError`` subclass.

    Examples: ``NotFoundError`` -> ``not_found``; ``PayloadTooLargeError`` ->
    ``payload_too_large``; ``SynapseError`` -> ``internal_error`` (override).
    """
    name = exc_cls.__name__
    if name in _CODE_OVERRIDES:
        return _CODE_OVERRIDES[name]
    if name.endswith("Error") and name != "Error":
        name = name[: -len("Error")]
    return _CAMEL_BOUNDARY_RE.sub("_", name).lower()


# Reverse map {status_code: code} built from the subclass table, so raw HTTPExceptions
# (still ~190 call sites; ADR-0086 §4) get a meaningful status-derived code without every
# site being migrated to a SynapseError subclass. Extras cover statuses with no subclass.
def _build_status_code_map() -> dict[int, str]:
    mapping: dict[int, str] = {}
    stack: list[type[SynapseError]] = list(SynapseError.__subclasses__())
    seen: set[type[SynapseError]] = set()
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
        # First subclass wins for a given status (no collisions in the current taxonomy).
        mapping.setdefault(cls.status_code, error_code_for(cls))
    mapping.setdefault(429, "rate_limited")
    mapping.setdefault(500, "internal_error")
    return mapping


_STATUS_TO_CODE: dict[int, str] = _build_status_code_map()


def code_for_status(status_code: int) -> str:
    """Map a bare HTTP status code to a stable ``code`` for the envelope.

    Used by the raw-``HTTPException`` fallback handler. Falls back to ``http_<status>`` for
    codes with no taxonomy entry so the code is always present and unambiguous.
    """
    code = _STATUS_TO_CODE.get(status_code)
    if code is not None:
        return code
    return f"http_{status_code}"


def _envelope(*, code: str, message: str, status: int, details: Any = None) -> dict[str, Any]:
    """Build the stable error envelope body (ADR-0086 §1)."""
    return {"error": {"code": code, "message": message, "status": status, "details": details}}


def _message_and_details(detail: Any) -> tuple[str, Any]:
    """Split a ``detail`` value into (human message, structured details).

    A string ``detail`` is the message with no structured details. A non-string ``detail``
    (dict/list -- rare, but some call sites pass structured payloads) is stringified for the
    message and preserved verbatim under ``details``.
    """
    if isinstance(detail, str):
        return detail, None
    return str(detail), detail


async def synapse_error_handler(request: Request, exc: SynapseError) -> Response:
    """Render any ``SynapseError`` as the stable envelope (ADR-0086 §3.1).

    Registered once for the ``SynapseError`` base class; Starlette walks the MRO so every
    subclass routes here. Status code, message wording, and headers are preserved exactly.
    """
    message, details = _message_and_details(exc.detail)
    body = _envelope(
        code=error_code_for(type(exc)),
        message=message,
        status=exc.status_code,
        details=details,
    )
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


def _clean_validation_errors(raw_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce FastAPI's field-error list to ``[{loc, msg, type}]``.

    FastAPI's ``input``/``url``/``ctx`` fields are dropped: ``input`` can echo request
    content (and may not be JSON-serialisable), ``url`` points at pydantic docs. ``loc`` is
    normalised to a list of strings so the shape is stable across pydantic versions.
    """
    cleaned: list[dict[str, Any]] = []
    for err in raw_errors:
        loc = err.get("loc", ())
        cleaned.append(
            {
                "loc": [str(part) for part in loc],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return cleaned


def _summarise_validation(cleaned: list[dict[str, Any]]) -> str:
    """Join field errors into a concise human message for JSON-only consumers."""
    parts: list[str] = []
    for err in cleaned:
        # loc[0] is typically the source ("body"/"query"); keep the field path after it.
        loc_parts = err["loc"][1:] if len(err["loc"]) > 1 else err["loc"]
        field = ".".join(loc_parts)
        parts.append(f"{field}: {err['msg']}" if field else err["msg"])
    return "; ".join(parts) if parts else "Request validation failed"


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """Render a Pydantic ``RequestValidationError`` (422) as the stable envelope (ADR-0086 §3.2).

    ``code`` is ``validation_error`` (distinct from the domain ``ValidationError``'s
    ``validation``); ``details`` carries the cleaned field-error list; ``message`` is a
    concise join of the field errors.
    """
    cleaned = _clean_validation_errors(list(exc.errors()))
    body = _envelope(
        code="validation_error",
        message=_summarise_validation(cleaned),
        status=422,
        details=cleaned,
    )
    return JSONResponse(status_code=422, content=body)


async def http_exception_handler_envelope(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Render a raw ``HTTPException`` (not raised via ``SynapseError``) as the stable
    envelope (ADR-0086 §3.3). ``code`` is derived from the status code; message/headers
    preserved. Covers the ~190 raw ``raise HTTPException`` sites still in the codebase.
    """
    message, details = _message_and_details(exc.detail)
    body = _envelope(
        code=code_for_status(exc.status_code),
        message=message,
        status=exc.status_code,
        details=details,
    )
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


def register_exception_handlers(app: Any) -> None:
    """Register the three envelope handlers (ADR-0086 §3) on *app*.

    Order does not matter (Starlette dispatches by exception type), but all three are
    required so every error source -- domain exception, Pydantic validation, and raw
    ``HTTPException`` -- emits the one stable envelope.
    """
    app.add_exception_handler(SynapseError, synapse_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler_envelope)
