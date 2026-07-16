"""
Domain exception taxonomy tests (BE-QUAL-1 partial, v1.9.2 "Real boundaries").

Non-regression contract under test: for every SynapseError subclass, the final HTTP
response (status code, JSON body, headers) raised through the global handler MUST be
byte-for-bit identical to raising the equivalent ``fastapi.HTTPException`` directly —
i.e. this module changes NO observable client-facing behaviour, it only gives
business/ops code a way to raise domain exceptions instead of HTTPException.

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
    register_exception_handlers,
)
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

# ── Domain exception → expected equivalent HTTPException status code ──────────
_CASES: list[tuple[type[SynapseError], int]] = [
    (BadRequestError, 400),
    (AuthenticationError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (GoneError, 410),
    (ConflictError, 409),
    (PayloadTooLargeError, 413),
    (UnsupportedMediaTypeError, 415),
    (ValidationError, 422),
    (NotImplementedFeatureError, 501),
    (UpstreamError, 502),
    (ServiceUnavailableError, 503),
]


def _build_app() -> FastAPI:
    """Minimal app: one route per domain exception + a matching /http/<code> control
    route that raises the plain HTTPException directly (the pre-existing behaviour)."""
    app = FastAPI()
    register_exception_handlers(app)

    for exc_cls, code in _CASES:

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

    return app


@pytest.fixture()
async def client() -> AsyncClient:
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.parametrize("exc_cls,code", _CASES)
async def test_domain_exception_matches_http_exception(
    client: AsyncClient, exc_cls: type[SynapseError], code: int
) -> None:
    """Response shape/status for a domain exception == response for a plain HTTPException."""
    domain_resp = await client.get(f"/domain/{exc_cls.__name__}")
    http_resp = await client.get(f"/http/{exc_cls.__name__}")

    assert domain_resp.status_code == code
    assert domain_resp.status_code == http_resp.status_code
    assert domain_resp.json() == http_resp.json()
    assert domain_resp.json() == {"detail": f"boom-{exc_cls.__name__}"}
    assert domain_resp.headers["content-type"] == http_resp.headers["content-type"]


async def test_domain_exception_headers_pass_through(client: AsyncClient) -> None:
    """headers= kwarg on a domain exception must reach the response, same as HTTPException."""
    domain_resp = await client.get("/domain-with-headers")
    http_resp = await client.get("/http-with-headers")

    assert domain_resp.status_code == http_resp.status_code == 409
    assert domain_resp.json() == http_resp.json() == {"detail": "locked"}
    assert domain_resp.headers.get("retry-after") == http_resp.headers.get("retry-after") == "30"


async def test_synapse_error_default_status_code() -> None:
    """Bare SynapseError() defaults to 500 with its default detail message."""
    exc = SynapseError()
    assert exc.status_code == 500
    assert exc.detail == "Internal Server Error"


def test_subclass_status_codes_are_fixed() -> None:
    """Each subclass carries its documented fixed status_code (no accidental drift)."""
    for exc_cls, code in _CASES:
        assert exc_cls.status_code == code
        assert exc_cls("x").status_code == code
