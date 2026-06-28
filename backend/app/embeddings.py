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
    Calls the bge-m3 model via the Ollama-compatible embedding endpoint at EMBEDDING_URL.

    The request format is Ollama's POST /api/embeddings:
        {"model": "<embedding_model>", "prompt": "<text>"}

    The response is expected to contain {"embedding": [<floats>]}.

    All config (URL, model name) comes from Settings — no hardcoded values (ADR-0004, I9).
    """

    def __init__(
        self,
        *,
        embedding_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._url = (embedding_url or settings.embedding_url).rstrip("/")
        self._model = model or settings.embedding_model
        self._timeout = timeout

    async def embed(self, text: str) -> list[float]:
        """Fetch embedding vector for *text* from the bge-m3 service (I9)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    self._url,
                    json={"model": self._model, "prompt": text},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(
                    f"Embedding service at {self._url} returned an error: {exc}"
                ) from exc

        payload = resp.json()
        vector: list[float] | None = payload.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(f"Embedding service returned unexpected payload: {payload!r}")
        return vector

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
