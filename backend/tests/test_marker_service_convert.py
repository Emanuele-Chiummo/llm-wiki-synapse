"""
Regression test for the Marker microservice's POST /convert request VALIDATION.

Guards the bug that made Marker "never work" over HTTP: `tools/marker-converter/service.py`
had `from __future__ import annotations` while importing FastAPI/Request/UploadFile LAZILY inside
_build_app(). PEP 563 turned the endpoint annotations into strings that FastAPI's get_type_hints()
could not resolve (the names were function-local, not module-global), so FastAPI misclassified the
`file` parameter as a QUERY param and rejected every real multipart upload with 422 — exactly the
multipart the backend sends (`files={"file": (...)}`, routers/ingest.py convert_marker).

The pre-existing test_convert_marker.py mocks the Marker service, so it never exercised this real
validation path. This test loads the ACTUAL service module and asserts that the same multipart the
backend sends gets PAST validation (i.e. is NOT a 422). A 500 (marker-pdf absent in the test venv)
is fine — it proves the `file` field was accepted and reached the converter.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SERVICE_PATH = Path(__file__).resolve().parents[2] / "tools" / "marker-converter" / "service.py"


def _load_service():
    spec = importlib.util.spec_from_file_location("marker_service_under_test", _SERVICE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _SERVICE_PATH.exists(), reason="marker service source not present")
def test_convert_accepts_file_multipart_not_422() -> None:
    """The exact multipart the backend sends must pass FastAPI validation (never 422)."""
    from starlette.testclient import TestClient

    app = _load_service()._build_app()
    client = TestClient(app)

    # Same shape as backend/app/routers/ingest.py: files={"file": (name, bytes, content_type)}.
    resp = client.post(
        "/convert",
        files={"file": ("sample.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert resp.status_code != 422, (
        "Marker /convert rejected a valid `file` multipart with 422 — FastAPI misread the "
        f"parameter (regression: future-annotations + lazy import). Body: {resp.text[:300]}"
    )


@pytest.mark.skipif(not _SERVICE_PATH.exists(), reason="marker service source not present")
def test_convert_missing_file_is_422() -> None:
    """Sanity: with NO file part, validation SHOULD 422 (the field really is required)."""
    from starlette.testclient import TestClient

    app = _load_service()._build_app()
    client = TestClient(app)
    resp = client.post("/convert")
    assert resp.status_code == 422


@pytest.mark.skipif(not _SERVICE_PATH.exists(), reason="marker service source not present")
def test_health_ok() -> None:
    from starlette.testclient import TestClient

    app = _load_service()._build_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"
