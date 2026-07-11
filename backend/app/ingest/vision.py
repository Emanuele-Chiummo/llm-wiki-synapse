"""
R8-2 / F12 — vision captioning seam for the ingest orchestrator.

extract.py stays PURE (path in, text out — no inference; ADR-0051 / ADR-0025 §4.1). Image
captioning is INFERENCE, so it lives HERE in the orchestrator layer, not in extract.py. When
an image file is ingested and the resolved ingest provider advertises
`capabilities().supports_vision` (and VISION_CAPTIONS_ENABLED is on), the orchestrator:

  1. sha256s the raw image bytes,
  2. looks up the (vault_id, sha256) row in `image_captions` — a HIT returns the cached caption
     with NO provider call (idempotent, zero cost),
  3. on a MISS, and only while under the per-run cap (VISION_MAX_IMAGES_PER_RUN — I7), makes ONE
     bounded `provider.caption_image()` call (Usage recorded on the run-scoped accumulator so the
     cost lands in the ingest_runs ledger like every other provider call), stores the caption, and
     returns it.

If the provider does not support vision, the cap is reached, or the call fails/times out, the
caller falls back to the extract.py placeholder — the pre-R8-2 behaviour is unchanged.

The per-run cap is enforced via a `VisionRunBudget` counter. `ingest_file` creates one budget per
call (a single file carries at most one image), but a batch/folder caller can share one budget
across many files by threading it through — so the cap is genuinely per-run, not per-file.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from app.config import settings
from app.config_overrides import effective_bool, effective_int
from app.db import get_session
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator

logger = logging.getLogger(__name__)

# Image extensions that route through the vision caption path (mirror extract.PLACEHOLDER images).
IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


@dataclass
class VisionRunBudget:
    """
    Run-scoped counter enforcing VISION_MAX_IMAGES_PER_RUN (I7). `max_images` is read from config
    at construction; `used` counts provider caption CALLS only (cache hits never increment). Once
    `used >= max_images`, `try_consume()` returns False and the caller falls back to the
    placeholder.
    """

    max_images: int = field(
        default_factory=lambda: max(
            0, effective_int("vision_max_images_per_run", settings.vision_max_images_per_run)
        )
    )
    used: int = 0

    def try_consume(self) -> bool:
        """Reserve one provider caption call; True if under the cap (and increments), else False."""
        if self.used >= self.max_images:
            return False
        self.used += 1
        return True


def sha256_bytes(data: bytes) -> str:
    """Lowercase hex sha256 of *data* — the content-addressed image cache key."""
    return hashlib.sha256(data).hexdigest()


async def _lookup_cached_caption(vault_id: str, digest: str) -> str | None:
    """Return the cached caption for (vault_id, sha256) or None. Degrade-safe (never raises)."""
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from app.models import ImageCaption

    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(ImageCaption).where(
                        ImageCaption.vault_id == vault_id,
                        ImageCaption.sha256 == digest,
                    )
                )
            ).scalar_one_or_none()
            return row.caption if row is not None else None
    except SQLAlchemyError:
        # Table missing (test env w/o migration) or DB down → treat as MISS (may still caption).
        logger.debug("vision cache lookup unavailable (table missing / DB down) — treating as MISS")
        return None


async def _store_caption(
    *, vault_id: str, digest: str, caption: str, file_path: str, provider_type: str
) -> None:
    """Insert a caption cache row. Degrade-safe: a duplicate/DB error never fails the ingest."""
    import uuid

    from sqlalchemy.exc import SQLAlchemyError

    from app.models import ImageCaption

    try:
        async with get_session() as session:
            session.add(
                ImageCaption(
                    id=uuid.uuid4(),
                    vault_id=vault_id,
                    sha256=digest,
                    file_path=file_path,
                    caption=caption,
                    provider_type=provider_type,
                )
            )
    except SQLAlchemyError as exc:  # noqa: BLE001 — cache write is best-effort
        logger.debug("vision cache store failed (non-fatal): %s", exc)


async def maybe_caption_image(
    *,
    provider_config_row: object,
    raw_bytes: bytes,
    origin_source: str,
    accumulator: UsageAccumulator | None = None,
    budget: VisionRunBudget | None = None,
    context: str = "",
) -> str | None:
    """
    Return a caption to use as the image's extracted text, or None to fall back to the placeholder.

    Order (R8-2): master gate (VISION_CAPTIONS_ENABLED) → provider supports_vision → cache HIT →
    per-run cap → ONE bounded provider.caption_image() call (Usage on *accumulator* → run ledger,
    I7) → store in cache. Any failure returns None (placeholder fallback) — ingest never breaks.
    """
    if not effective_bool("vision_captions_enabled", settings.vision_captions_enabled):
        return None

    provider: InferenceProvider = resolve_provider(provider_config_row)
    caps = provider.capabilities()
    if not caps.supports_vision:
        logger.info(
            "vision: provider %s does not support vision — placeholder for %s",
            caps.name,
            origin_source,
        )
        return None

    digest = sha256_bytes(raw_bytes)
    vault_id = settings.vault_id

    cached = await _lookup_cached_caption(vault_id, digest)
    if cached is not None:
        logger.info("vision: cache HIT for %s (no provider call, zero cost)", origin_source)
        return cached

    run_budget = budget if budget is not None else VisionRunBudget()
    if not run_budget.try_consume():
        logger.info(
            "vision: run cap (%d) reached — placeholder for %s",
            run_budget.max_images,
            origin_source,
        )
        return None

    if accumulator is not None:
        provider.bind_accumulator(accumulator)

    try:
        caption = await provider.caption_image(raw_bytes, context)
    except NotImplementedError:
        logger.info("vision: provider %s raised NotImplementedError — placeholder", caps.name)
        return None
    except Exception as exc:  # noqa: BLE001 — any vision failure → placeholder (never break ingest)
        logger.warning("vision: caption_image failed for %s (non-fatal): %s", origin_source, exc)
        return None

    caption = (caption or "").strip()
    if not caption:
        logger.warning("vision: empty caption for %s — placeholder", origin_source)
        return None

    await _store_caption(
        vault_id=vault_id,
        digest=digest,
        caption=caption,
        file_path=origin_source,
        provider_type=caps.mode,
    )
    logger.info(
        "vision: captioned %s via %s (%d chars, cached)", origin_source, caps.name, len(caption)
    )
    return caption
