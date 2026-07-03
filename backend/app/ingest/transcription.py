"""
R8-3 / F12 — AV transcription seam for the ingest orchestrator.

extract.py stays PURE (path in, text out — no inference; ADR-0051 / ADR-0025 §4.1). AV
transcription is a HOST-SIDE external service call, so it lives HERE in the orchestrator
layer, not in extract.py. When an AV file is ingested and AV_TRANSCRIPTION_ENABLED is True,
the orchestrator:

  1. Checks the per-run cap (AV_MAX_FILES_PER_RUN — I7), falls back to placeholder if reached.
  2. Reads the raw bytes, POSTs to {WHISPER_SERVICE_URL}/transcribe with a bounded timeout
     (WHISPER_TIMEOUT_SECONDS — I7).
  3. On success returns the transcript text (capped at EXTRACT_MAX_CHARS — I7) and logs
     INFO with duration_seconds (local service → total_cost_usd=0.00, I7 accounting).
  4. On ANY failure (connection refused, timeout, non-200, invalid JSON, missing field)
     logs a WARNING and returns None → the caller uses the extract.py placeholder path.
     Ingest never breaks (placeholder is always available).

When AV_TRANSCRIPTION_ENABLED is False (default), this module returns None immediately
without any network call — zero behaviour change from pre-R8-3.

The per-run cap is enforced via an AvRunBudget counter, mirroring VisionRunBudget in
vision.py. The caller (ingest_file in orchestrator.py) creates one budget per file by
default; a batch caller can share one budget across many files by threading it through.

Pattern: identical to vision.py / R8-1, establishing the standard Synapse host-seam
template for GPU workloads (ADR-0051 §3 Consequences — "reusable template").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

# AV extensions that route through the Whisper transcription seam.
# Must match PLACEHOLDER_EXTENSIONS AV subset in extract.py.
AV_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".mp4"})


@dataclass
class AvRunBudget:
    """
    Run-scoped counter enforcing AV_MAX_FILES_PER_RUN (I7, R8-3).

    `max_files` is read from config at construction; `used` counts Whisper service CALLS
    only. Once `used >= max_files`, `try_consume()` returns False and the caller falls
    back to the placeholder — no network call is made.
    """

    max_files: int = field(default_factory=lambda: max(0, int(settings.av_max_files_per_run)))
    used: int = 0

    def try_consume(self) -> bool:
        """Reserve one Whisper call; True if under the cap (and increments), else False."""
        if self.used >= self.max_files:
            return False
        self.used += 1
        return True


async def maybe_transcribe_av(
    *,
    raw_bytes: bytes,
    origin_source: str,
    budget: AvRunBudget | None = None,
) -> str | None:
    """
    Return a transcript string to use as the AV file's extracted text, or None to fall back
    to the placeholder (pre-R8-3 behaviour — ingest never breaks).

    Order (R8-3):
      1. Master gate: AV_TRANSCRIPTION_ENABLED False → return None immediately (no call).
      2. Per-run cap check (AV_MAX_FILES_PER_RUN): if reached → return None.
      3. POST raw_bytes to {WHISPER_SERVICE_URL}/transcribe with WHISPER_TIMEOUT_SECONDS.
      4. Expect {"text": str, "language": str, "duration_seconds": float}.
      5. On success: log INFO (duration_seconds, zero cost I7), return text (capped at
         EXTRACT_MAX_CHARS).
      6. On ANY failure (connection, timeout, non-200, bad JSON, missing field): log WARNING,
         return None.

    Parameters
    ----------
    raw_bytes:
        Raw AV file bytes to transcribe.
    origin_source:
        Relative path / label for log messages (the DB rel path or upload filename).
    budget:
        Optional shared AvRunBudget; if None, a fresh single-use budget is created so a
        single-file ingest stays within the cap without the caller managing state.
    """
    if not settings.av_transcription_enabled:
        return None

    run_budget = budget if budget is not None else AvRunBudget()
    if not run_budget.try_consume():
        logger.info(
            "transcription: run cap (%d) reached — placeholder for %s",
            run_budget.max_files,
            origin_source,
        )
        return None

    import httpx  # noqa: PLC0415 — lazy import; httpx is a backend dep

    whisper_url = settings.whisper_service_url.rstrip("/") + "/transcribe"
    timeout = settings.whisper_timeout_seconds

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                whisper_url,
                files={"file": (origin_source, raw_bytes, "application/octet-stream")},
            )
        if response.status_code != 200:
            logger.warning(
                "transcription: non-200 %d from %s for %s — placeholder",
                response.status_code,
                whisper_url,
                origin_source,
            )
            return None

        data = response.json()
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            logger.warning(
                "transcription: invalid/empty 'text' in response from %s for %s — placeholder",
                whisper_url,
                origin_source,
            )
            return None

        duration = float(data.get("duration_seconds", 0.0))
        # Cap at EXTRACT_MAX_CHARS (I7 — consistent with extract.py output cap)
        max_chars = int(getattr(settings, "extract_max_chars", 2_000_000))
        if len(text) > max_chars:
            logger.warning(
                "transcription: transcript truncated from %d to %d chars "
                "(EXTRACT_MAX_CHARS) for %s",
                len(text),
                max_chars,
                origin_source,
            )
            text = text[:max_chars]

        # I7 cost accounting: local Whisper has zero LLM cost; log 0.00 to keep ledger consistent.
        logger.info(
            "transcription: transcribed %s via Whisper — %d chars, duration=%.1fs, cost_usd=0.00",
            origin_source,
            len(text),
            duration,
        )
        return text

    except Exception as exc:  # noqa: BLE001 — ANY failure → placeholder, never break ingest
        logger.warning(
            "transcription: call to %s failed for %s: %s — placeholder",
            whisper_url,
            origin_source,
            exc,
        )
        return None
