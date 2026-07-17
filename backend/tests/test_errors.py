"""
Stable error-envelope tests (ADR-0086, 2.0.0).

Contract under test: every error source -- a ``SynapseError`` subclass, a Pydantic
``RequestValidationError``, and a raw ``fastapi.HTTPException`` -- is rendered as the ONE
stable envelope::

    {"error": {"code": <snake_case>, "message": <str>, "status": <int>, "details": <any|null>}}

This retires the 1.9.2 "byte-for-byte identical to a plain HTTPException / {"detail": ...}"
contract: the comparison-control route now asserts the plain HTTPException ALSO gets the
envelope (via the fallback handler), which is the new invariant worth locking down.

Infra-free: a minimal FastAPI app with throwaway routes, no DB/Qdrant/Postgres.
"""

from __future__ import annotations

import pytest
from app.errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    NotImplementedFeatureError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    SynapseError,
    UnsupportedMediaTypeError,
    UpstreamError,
    ValidationError,
    code_for_status,
    error_code_for,
    register_exception_handlers,
)
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

# ── Domain exception -> (expected status, expected stable code) ───────────────
_CASES: list[tuple[type[SynapseError], int, str]] = [
    (BadRequestError, 400, "bad_request"),
    (AuthenticationError, 401, "authentication"),
    (ForbiddenError, 403, "forbidden"),
    (NotFoundError, 404, "not_found"),
    (GoneError, 410, "gone"),
    (ConflictError, 409, "conflict"),
    (PayloadTooLargeError, 413, "payload_too_large"),
    (UnsupportedMediaTypeError, 415, "unsupported_media_type"),
    (ValidationError, 422, "validation"),
    (NotImplementedFeatureError, 501, "not_implemented_feature"),
    (UpstreamError, 502, "upstream"),
    (ServiceUnavailableError, 503, "service_unavailable"),
]


class _Body(BaseModel):
    name: str
    count: int


def _build_app() -> FastAPI:
    """Minimal app: one route per domain exception + a matching /http/<code> control
    route that raises the plain HTTPException directly, plus a Pydantic-validated route."""
    app = FastAPI()
    register_exception_handlers(app)

    for exc_cls, code, _ in _CASES:

        def _make_domain_route(exc_cls: type[SynapseError] = exc_cls) -> object:
            async def _route() -> None:
                raise exc_cls(f"boom-{exc_cls.__name__}")

            return _route

        def _make_http_route(code: int = code, exc_cls: type[SynapseError] = exc_cls) -> object:
            async def _route() -> None:
                raise HTTPException(status_code=code, detail=f"boom-{exc_cls.__name__}")

            return _route

        app.add_api_route(f"/domain/{exc_cls.__name__}", _make_domain_route(), methods=["GET"])
        app.add_api_route(f"/http/{exc_cls.__name__}", _make_http_route(), methods=["GET"])

    # Headers pass-through case (e.g. Retry-After style usage).
    @app.get("/domain-with-headers")
    async def _domain_with_headers() -> None:
        raise ConflictError("locked", headers={"Retry-After": "30"})

    @app.get("/http-with-headers")
    async def _http_with_headers() -> None:
        raise HTTPException(status_code=409, detail="locked", headers={"Retry-After": "30"})

    # Structured (non-string) detail case.
    @app.get("/domain-structured")
    async def _domain_structured() -> None:
        raise BadRequestError({"field": "x", "reason": "bad"})

    # Pydantic request-validation (422 NOT via SynapseError).
    @app.post("/validate")
    async def _validate(body: _Body) -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.fixture()
async def client() -> AsyncClient:
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── SynapseError subclasses ───────────────────────────────────────────────────


@pytest.mark.parametrize("exc_cls,code,slug", _CASES)
async def test_domain_exception_envelope(
    client: AsyncClient, exc_cls: type[SynapseError], code: int, slug: str
) -> None:
    """A domain exception renders the stable envelope with the derived code + message."""
    resp = await client.get(f"/domain/{exc_cls.__name__}")
    assert resp.status_code == code
    assert resp.json() == {
        "error": {
            "code": slug,
            "message": f"boom-{exc_cls.__name__}",
            "status": code,
            "details": None,
        }
    }


@pytest.mark.parametrize("exc_cls,code,slug", _CASES)
async def test_raw_http_exception_envelope(
    client: AsyncClient, exc_cls: type[SynapseError], code: int, slug: str
) -> None:
    """A raw HTTPException ALSO renders the envelope (fallback handler), with a
    status-derived code that matches the subclass code for these taxonomy statuses."""
    resp = await client.get(f"/http/{exc_cls.__name__}")
    assert resp.status_code == code
    assert resp.json() == {
        "error": {
            "code": slug,
            "message": f"boom-{exc_cls.__name__}",
            "status": code,
            "details": None,
        }
    }


async def test_domain_and_raw_http_produce_identical_envelopes(client: AsyncClient) -> None:
    """The domain path and the raw-HTTPException path converge on the same envelope."""
    for exc_cls, _, _ in _CASES:
        domain = await client.get(f"/domain/{exc_cls.__name__}")
        http = await client.get(f"/http/{exc_cls.__name__}")
        assert domain.json() == http.json()
        assert domain.headers["content-type"] == http.headers["content-type"]


async def test_headers_pass_through(client: AsyncClient) -> None:
    """headers= kwarg reaches the response for both the domain and raw-HTTPException paths."""
    domain = await client.get("/domain-with-headers")
    http = await client.get("/http-with-headers")
    assert domain.status_code == http.status_code == 409
    expected = {"error": {"code": "conflict", "message": "locked", "status": 409, "details": None}}
    assert domain.json() == http.json() == expected
    assert domain.headers.get("retry-after") == http.headers.get("retry-after") == "30"


async def test_structured_detail_goes_to_details(client: AsyncClient) -> None:
    """A non-string detail is stringified for message and preserved under details."""
    resp = await client.get("/domain-structured")
    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "bad_request"
    assert body["status"] == 400
    assert body["details"] == {"field": "x", "reason": "bad"}
    assert body["message"] == str({"field": "x", "reason": "bad"})


# ── Pydantic RequestValidationError ───────────────────────────────────────────


async def test_request_validation_error_envelope(client: AsyncClient) -> None:
    """A Pydantic 422 renders the envelope with code=validation_error + field details."""
    resp = await client.post("/validate", json={"name": "x"})  # missing 'count'
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "validation_error"
    assert body["status"] == 422
    assert isinstance(body["details"], list) and body["details"]
    first = body["details"][0]
    assert set(first.keys()) == {"loc", "msg", "type"}
    assert all(isinstance(p, str) for p in first["loc"])
    # message is a concise join and mentions the missing field.
    assert "count" in body["message"]
    # No request-echoing 'input'/'url' keys leak into details.
    assert "input" not in first and "url" not in first


async def test_request_validation_error_distinct_from_domain_validation() -> None:
    """The framework 422 (validation_error) and the domain 422 (validation) differ by code."""
    assert error_code_for(ValidationError) == "validation"
    # validation_error is the constant used by the RequestValidationError handler.


# ── Code-derivation unit coverage ─────────────────────────────────────────────


def test_error_code_derivation_is_mechanical() -> None:
    """error_code_for strips trailing 'Error' + snake-cases; base class overridden."""
    assert error_code_for(NotFoundError) == "not_found"
    assert error_code_for(PayloadTooLargeError) == "payload_too_large"
    assert error_code_for(UnsupportedMediaTypeError) == "unsupported_media_type"
    assert error_code_for(SynapseError) == "internal_error"


def test_code_for_status_covers_taxonomy_and_extras() -> None:
    """code_for_status maps taxonomy statuses + extras; unknown -> http_<status>."""
    assert code_for_status(404) == "not_found"
    assert code_for_status(413) == "payload_too_large"
    assert code_for_status(429) == "rate_limited"
    assert code_for_status(500) == "internal_error"
    assert code_for_status(418) == "http_418"


async def test_synapse_error_default_status_code() -> None:
    """Bare SynapseError() defaults to 500 with its default detail message."""
    exc = SynapseError()
    assert exc.status_code == 500
    assert exc.detail == "Internal Server Error"


def test_subclass_status_codes_are_fixed() -> None:
    """Each subclass carries its documented fixed status_code (no accidental drift)."""
    for exc_cls, code, _ in _CASES:
        assert exc_cls.status_code == code
        assert exc_cls("x").status_code == code
