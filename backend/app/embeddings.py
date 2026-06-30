"""
Embedding client — calls the already-running bge-m3 service via EMBEDDING_URL (I9).

Injectable interface: EmbeddingClient is an ABC; HttpEmbeddingClient is the real
implementation; FakeEmbeddingClient is the in-process test double (GAP-4 — CI without
TrueNAS injects this so tests don't need a live bge-m3 service).

No model is loaded in-process; no subprocess is spawned (AC-WATCH-6, AC-QD-4).
"""

from __future__ import annotations

import abc
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── Abstract interface ─────────────────────────────────────────────────────────


class EmbeddingClient(abc.ABC):
    """
    Minimal interface for producing dense embedding vectors.

    Implement this ABC to substitute a fake in tests (GAP-4).
    The real backend uses the already-running bge-m3 via EMBEDDING_URL.
    """

    @abc.abstractmethod
    async def embed(self, text: str) -> list[float]:
        """
        Return a dense float vector for *text*.

        Raises:
            EmbeddingError: if the underlying service is unreachable or returns
                            an unexpected response shape.
        """

    @abc.abstractmethod
    async def probe_dimension(self) -> int:
        """
        Request one embedding and return its dimension.

        Used at startup to validate EMBEDDING_DIM against the live service (ADR-0004).
        """


class EmbeddingError(RuntimeError):
    """Raised when the embedding service returns an error or unexpected shape."""


# ── Real HTTP implementation ───────────────────────────────────────────────────


class HttpEmbeddingClient(EmbeddingClient):
    """
    Calls the embedding model at EMBEDDING_URL via a config-driven request adapter (I9).

    A single seam, two request/response shapes selected by EMBEDDING_FORMAT (ADR-0031):

      - "ollama" (default): POST {"model": "<model>", "prompt": "<text>"}
        → parse {"embedding": [<floats>]}    (current bge-m3 behavior, unchanged).
      - "openai": POST {"model": "<model>", "input": "<text>"}
        → parse {"data": [{"embedding": [<floats>]}]}    (OpenAI-compatible /v1/embeddings).

    When EMBEDDING_API_KEY is set, every request carries `Authorization: Bearer <key>`
    (both formats). The key is a secret: never logged.

    All config (URL, model, format, key) comes from Settings — no hardcoded values
    (ADR-0004, ADR-0031, I9 / I6-spirit).
    """

    def __init__(
        self,
        *,
        embedding_url: str | None = None,
        model: str | None = None,
        embedding_format: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._url = (embedding_url or settings.embedding_url).rstrip("/")
        self._model = model or settings.embedding_model
        # `or` is safe: format is never an empty string (validated default "ollama").
        self._format = (embedding_format or settings.embedding_format).lower()
        # `is None` check, NOT `or`: an explicitly-passed empty key should stay falsy/unset,
        # but we must not let a constructor None override a settings-provided key.
        self._api_key = api_key if api_key is not None else settings.embedding_api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        """Build request headers; add bearer auth only when a key is configured.

        The key is never logged here or anywhere else (ADR-0031 §2.2).
        """
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _build_body(self, text: str) -> dict[str, str]:
        """Build the request body for the configured format (ADR-0031 §2.3)."""
        if self._format == "openai":
            return {"model": self._model, "input": text}
        return {"model": self._model, "prompt": text}

    def _parse_response(self, payload: object) -> list[float]:
        """Extract the embedding vector per the configured format (ADR-0031 §2.3).

        Raises EmbeddingError on a missing/empty/malformed vector — same error path and
        shape as the historical ollama guard (no silent empty vector).
        """
        if self._format == "openai":
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                vector = data[0].get("embedding")
            else:
                vector = None
        else:
            vector = payload.get("embedding") if isinstance(payload, dict) else None

        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(f"Embedding service returned unexpected payload: {payload!r}")
        return vector

    async def embed(self, text: str) -> list[float]:
        """Fetch embedding vector for *text* from the embedding service (I9)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    self._url,
                    json=self._build_body(text),
                    headers=self._headers(),
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(
                    f"Embedding service at {self._url} returned an error: {exc}"
                ) from exc

        return self._parse_response(resp.json())

    async def probe_dimension(self) -> int:
        """
        Send a minimal request to discover the live embedding dimension.

        Used at startup to validate EMBEDDING_DIM vs the real service (ADR-0004).
        """
        vector = await self.embed("probe")
        return len(vector)


# ── Test double (injectable in CI without TrueNAS) ────────────────────────────


class FakeEmbeddingClient(EmbeddingClient):
    """
    In-process fake for unit/integration tests that don't have a live bge-m3.

    Generates a zero vector of length *dim* (configurable).  Tests can override
    the vector per call by pushing to `self.responses`.

    GAP-4: inject this via dependency injection or monkeypatching in test fixtures.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim: int = dim if dim is not None else settings.embedding_dim
        self.call_count = 0
        self.last_text: str | None = None
        # Queue of pre-loaded responses; if empty, returns a zero vector
        self.responses: list[list[float]] = []

    async def embed(self, text: str) -> list[float]:
        self.call_count += 1
        self.last_text = text
        if self.responses:
            return self.responses.pop(0)
        return [0.0] * self.dim

    async def probe_dimension(self) -> int:
        vector = await self.embed("probe")
        return len(vector)


# ── Module-level default instance (swappable for tests) ───────────────────────

_default_client: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    """
    Return the active embedding client.

    Tests replace the default by calling ``set_embedding_client(FakeEmbeddingClient())``.
    """
    global _default_client  # noqa: PLW0603
    if _default_client is None:
        _default_client = HttpEmbeddingClient()
    return _default_client


def set_embedding_client(client: EmbeddingClient) -> None:
    """Override the active embedding client (test / CI injection point — GAP-4)."""
    global _default_client  # noqa: PLW0603
    _default_client = client
