"""
Runtime config-override layer (R11-2 / ADR-0053; extended by ADR-0054 for S9;
extended by R12-7/A5 for S10/S11; extended by R12-8 for S12; extended by R12-9 for S13;
extended for S14-S18 loop-bounds / invariant I7).

Merges env baseline (settings.<key>) with DB overrides (app_config table).
Load once at lifespan startup; O(1) reads from in-memory cache thereafter (I7).

Public API
----------
ALLOWED_CONFIG_KEYS  : frozenset[str]   — the 18 keys the UI may override (§2.2)
load_overrides(session) → None          — called ONCE at lifespan startup
get_effective(key, env_default) → str   — O(1) cache read (override-else-default)
source_of(key) → str                    — "override" | "env"
get_override(key) → str | None          — raw cached value or None
set_override(session, key, value) → None — allow-list + validate + upsert + cache refresh
clear_override(session, key) → None     — allow-list + DELETE + cache refresh
effective_str(key, default) → str | None
effective_bool(key, default) → bool
effective_float(key, default) → float
effective_int(key, default) → int       — typed int accessor (S14–S18, fail-closed to default)
effective_domain_vocabulary() → list[str]  — S9 typed accessor (ADR-0054 §2.1)
effective_schedule(key) → str              — S10/S11/S12/S13 typed accessor (R12-7/A5, R12-8, R12-9)

Invariants
----------
I7  : single bounded SELECT at startup; no per-request DB read; refresh only on PUT/DELETE.
      S14–S18 are the loop-bound keys (deep_research_max_iter, deep_research_token_budget,
      deep_research_max_queries, lint_max_iter, lint_token_budget); overriding them caps
      the relevant bounded loops without any mid-loop re-read.
I6  : S5/S6 are routed through the EXISTING embedding gate/adapter (callers do this);
      this module only stores and retrieves the effective string — it never hardcodes shapes.
      S9: empty vocabulary ⇒ zero provider calls at auto-tag (dormant — I6 satisfied).
      S10/S11/S12/S13: schedule keys are read by OpsScheduler; no provider call in this module.
I1  : no vault re-scan, no Qdrant re-embed — changing S5/S6/S9 applies to SUBSEQUENT
      ingests/queries only (documented behaviour, not a bug).
ADR-0053 §2.5, §2.6, §3, §4.1 · ADR-0054 §2.1 (domain_vocabulary) ·
R12-7/A5 (lint_schedule, backfill_schedule) · R12-8 (schema_review_schedule) ·
R12-9 (reclassify_schedule).
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Allow-list (security boundary — ADR-0053 §2.2; extended by ADR-0054 §2.1,
#   further extended by R12-7/A5 for S10/S11; further extended by R12-8 for S12;
#   further extended by R12-9 for S13; further extended for S14-S18 loop bounds) ─
# ONLY these 18 keys may be written via PUT /config/app/{key}.
# Infra/secret keys are structurally unreachable through this surface (§2.4).
ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "pdf_extractor",  # S1  (ADR-0051)
        "marker_service_url",  # S2  (ADR-0051)
        "marker_timeout_seconds",  # S3  (ADR-0051)
        "cost_alert_threshold_usd",  # S4  (R9-1)
        "embeddings_enabled",  # S5  (ADR-0030) — routes through embedding data-plane gate
        "embedding_format",  # S6  (ADR-0031) — routes through EmbeddingClient adapter seam (I6)
        "overview_language",  # S7  (F3)
        "wikilink_enrich_enabled",  # S8  (ADR-0036)
        "domain_vocabulary",  # S9  (ADR-0054, F18) — JSON array of domain names
        "lint_schedule",  # S10 (R12-7/A5) — enum: off|hourly|daily|weekly; default "off"
        "backfill_schedule",  # S11 (R12-7/A5) — enum: off|hourly|daily|weekly; default "off"
        "schema_review_schedule",  # S12 (R12-8) — enum: off|hourly|daily|weekly; default "off"
        "reclassify_schedule",  # S13 (R12-9) — enum: off|hourly|daily|weekly; default "off"
        "deep_research_max_iter",  # S14 — int 1–10; caps DeepResearch loop (I7)
        "deep_research_token_budget",  # S15 — int 1000–1_000_000; caps DeepResearch token spend (I7)
        "deep_research_max_queries",  # S16 — int 1–10; caps SearXNG query fan-out (I7)
        "lint_max_iter",  # S17 — int 1–10; caps LintScan semantic loop (I7)
        "lint_token_budget",  # S18 — int 1000–500_000; caps LintScan token spend (I7)
    }
)

# ── Stable GET /config/app ordering (S1..S18) ────────────────────────────────
# The FE snapshot test needs a stable order; always emit in this sequence.
ORDERED_KEYS: list[str] = [
    "pdf_extractor",
    "marker_service_url",
    "marker_timeout_seconds",
    "cost_alert_threshold_usd",
    "embeddings_enabled",
    "embedding_format",
    "overview_language",
    "wikilink_enrich_enabled",
    "domain_vocabulary",  # S9  (ADR-0054 §2.1)
    "lint_schedule",  # S10 (R12-7/A5)
    "backfill_schedule",  # S11 (R12-7/A5)
    "schema_review_schedule",  # S12 (R12-8)
    "reclassify_schedule",  # S13 (R12-9)
    "deep_research_max_iter",  # S14 — loop bound (I7)
    "deep_research_token_budget",  # S15 — loop bound (I7)
    "deep_research_max_queries",  # S16 — loop bound (I7)
    "lint_max_iter",  # S17 — loop bound (I7)
    "lint_token_budget",  # S18 — loop bound (I7)
]

# ── Per-key value validation rules (ADR-0053 §2.3) ───────────────────────────
_PDF_EXTRACTOR_VALUES: frozenset[str] = frozenset({"pypdf", "marker"})
_EMBEDDING_FORMAT_VALUES: frozenset[str] = frozenset({"ollama", "openai"})
_BOOL_TRUE: frozenset[str] = frozenset({"true", "1", "yes"})
_BOOL_FALSE: frozenset[str] = frozenset({"false", "0", "no"})
_BOOL_VALUES: frozenset[str] = _BOOL_TRUE | _BOOL_FALSE


def validate_value(key: str, value: str) -> str | None:
    """
    Validate *value* for *key* against the per-key rule (ADR-0053 §2.3).

    Returns an error message string on failure, or None on success.
    Callers turn a non-None return into an HTTP 422.

    S7 `(auto)` sentinel: the caller converts this to a DELETE before calling here;
    this function sees only the raw value and rejects empty strings.
    """
    if key == "pdf_extractor":
        if value not in _PDF_EXTRACTOR_VALUES:
            return f"pdf_extractor must be one of {sorted(_PDF_EXTRACTOR_VALUES)}, got {value!r}"

    elif key == "marker_service_url":
        if not (value.startswith("http://") or value.startswith("https://")):
            return "marker_service_url must start with http:// or https://, " f"got {value!r}"

    elif key == "marker_timeout_seconds":
        try:
            f = float(value)
        except ValueError:
            return f"marker_timeout_seconds must be a float > 0 and ≤ 3600, got {value!r}"
        if not (0 < f <= 3600):
            return f"marker_timeout_seconds must be > 0 and ≤ 3600, got {f!r}"

    elif key == "cost_alert_threshold_usd":
        try:
            f = float(value)
        except ValueError:
            return f"cost_alert_threshold_usd must be a float ≥ 0, got {value!r}"
        if f < 0:
            return f"cost_alert_threshold_usd must be ≥ 0 (0 disables the alert), got {f!r}"

    elif key in ("embeddings_enabled", "wikilink_enrich_enabled"):
        if value.lower() not in _BOOL_VALUES:
            return f"{key} must be 'true' or 'false' (case-insensitive), " f"got {value!r}"

    elif key == "embedding_format":
        if value not in _EMBEDDING_FORMAT_VALUES:
            return (
                f"embedding_format must be one of {sorted(_EMBEDDING_FORMAT_VALUES)}, "
                f"got {value!r}"
            )

    elif key == "overview_language":
        # Free text ISO code; only constraint is non-empty (caller routes "(auto)" to DELETE)
        if not value.strip():
            return (
                "overview_language must be a non-empty ISO language code "
                "(use DELETE to reset to auto)"
            )

    elif key == "domain_vocabulary":
        # ADR-0054 §2.1: JSON array of non-empty strings, ≤ 100 elements.
        # Normalisation (dedupe, strip) is applied in set_override before upsert.
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return 'domain_vocabulary must be a JSON array of strings (e.g. ["ServiceNow","SAM"])'
        if not isinstance(parsed, list):
            return "domain_vocabulary must be a JSON array (list), got a non-list JSON value"
        for i, elem in enumerate(parsed):
            if not isinstance(elem, str) or not elem.strip():
                return (
                    f"domain_vocabulary element at index {i} must be a non-empty string, "
                    f"got {elem!r}"
                )
        if len(parsed) > 100:
            return f"domain_vocabulary must have at most 100 entries, got {len(parsed)}"
        # Empty array [] is VALID (explicit dormant state — ADR-0054 §2.1)

    elif key in (
        "lint_schedule",
        "backfill_schedule",
        "schema_review_schedule",
        "reclassify_schedule",
    ):
        # S10/S11 (R12-7/A5): enum off|hourly|daily|weekly.
        # S12 (R12-8): schema_review_schedule uses the same enum.
        # S13 (R12-9): reclassify_schedule uses the same enum.
        _SCHEDULE_VALUES: frozenset[str] = frozenset({"off", "hourly", "daily", "weekly"})
        if value not in _SCHEDULE_VALUES:
            return f"{key} must be one of {sorted(_SCHEDULE_VALUES)}, got {value!r}"

    elif key in ("deep_research_max_iter", "deep_research_max_queries", "lint_max_iter"):
        # S14 / S16 / S17: int in [1, 10] — loop iteration / query fan-out caps (I7).
        try:
            i = int(value)
        except ValueError:
            return f"{key} must be an integer between 1 and 10, got {value!r}"
        if not (1 <= i <= 10):
            return f"{key} must be between 1 and 10, got {i!r}"

    elif key == "deep_research_token_budget":
        # S15: int in [1000, 1_000_000] — DeepResearch token-spend cap (I7).
        try:
            i = int(value)
        except ValueError:
            return (
                f"deep_research_token_budget must be an integer between 1000 and 1000000, "
                f"got {value!r}"
            )
        if not (1_000 <= i <= 1_000_000):
            return (
                f"deep_research_token_budget must be between 1000 and 1000000, got {i!r}"
            )

    elif key == "lint_token_budget":
        # S18: int in [1000, 500_000] — LintScan token-spend cap (I7).
        try:
            i = int(value)
        except ValueError:
            return (
                f"lint_token_budget must be an integer between 1000 and 500000, "
                f"got {value!r}"
            )
        if not (1_000 <= i <= 500_000):
            return f"lint_token_budget must be between 1000 and 500000, got {i!r}"

    return None


# ── In-memory cache ───────────────────────────────────────────────────────────
# Single module-level dict protected by an asyncio.Lock (mirrors _ClipConfigCache).
# key → raw TEXT value as stored in app_config (or missing ⇒ env governs).
_cache: dict[str, str] = {}
_cache_lock: asyncio.Lock = asyncio.Lock()


async def load_overrides(session: AsyncSession) -> None:
    """
    Lifespan startup: read ALL app_config rows ONCE, cache in memory (I7 — single SELECT).

    Rows whose key ∉ ALLOWED_CONFIG_KEYS are IGNORED (forward/back compat — ADR-0053 §2.6).
    Tolerates a missing table (startup before migration applied — env governs, log once).
    """
    global _cache
    try:
        from app.models import AppConfig  # noqa: PLC0415 — deferred; models loaded after config

        result = await session.execute(select(AppConfig.key, AppConfig.value))
        rows: dict[str, str] = {}
        for key, value in result:
            if key in ALLOWED_CONFIG_KEYS:
                rows[key] = value
            else:
                logger.debug(
                    "config_overrides.load_overrides: ignoring unknown key %r "
                    "(not in ALLOWED_CONFIG_KEYS — forward/back compat §2.6)",
                    key,
                )
        async with _cache_lock:
            _cache = rows
        logger.info(
            "config_overrides.load_overrides: loaded %d override(s): %s",
            len(rows),
            list(rows.keys()),
        )
    except Exception as exc:  # noqa: BLE001
        # Tolerate "relation does not exist" (migration not yet applied) — env governs.
        err_msg = str(exc).lower()
        if "does not exist" in err_msg or "no such table" in err_msg:
            logger.warning(
                "config_overrides.load_overrides: app_config table not found — "
                "env vars govern all settings (ADR-0053 §2.6 belt-and-braces). "
                "Apply migration 0023 to activate the override layer."
            )
            async with _cache_lock:
                _cache = {}
        else:
            logger.error(
                "config_overrides.load_overrides: unexpected error — env vars govern: %s",
                exc,
            )
            async with _cache_lock:
                _cache = {}


def get_effective(key: str, env_default: str) -> str:
    """
    Return the cached override string for *key* if present, else *env_default*.
    Pure in-memory O(1); never touches the DB (I7).
    """
    return _cache.get(key, env_default)


def source_of(key: str) -> str:
    """Return "override" iff a cached row exists for *key*, else "env"."""
    return "override" if key in _cache else "env"


def get_override(key: str) -> str | None:
    """Return the raw cached value for *key*, or None if not overridden."""
    return _cache.get(key)


# ── Typed accessors (coercion lives in one place — ADR-0053 §2.5) ────────────


def effective_str(key: str, default: str | None) -> str | None:
    """Return the effective string value for *key*, falling back to *default*."""
    raw = _cache.get(key)
    if raw is not None:
        return raw
    return default


def effective_bool(key: str, default: bool) -> bool:
    """
    Return the effective boolean for *key* (coercing "true"/"1"/"yes" → True).
    Falls back to *default* on a coercion error (fail-closed to env default — §8 trade-offs).
    """
    raw = _cache.get(key)
    if raw is None:
        return default
    lower = raw.lower()
    if lower in _BOOL_TRUE:
        return True
    if lower in _BOOL_FALSE:
        return False
    # Malformed stored value (bypassed §2.3 validation — treat as default, log warning).
    logger.warning(
        "config_overrides.effective_bool: key=%r has malformed value %r — "
        "falling back to env default %r",
        key,
        raw,
        default,
    )
    return default


def effective_float(key: str, default: float) -> float:
    """
    Return the effective float for *key*.
    Falls back to *default* on a coercion error (fail-closed — §8 trade-offs).
    """
    raw = _cache.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "config_overrides.effective_float: key=%r has malformed value %r — "
            "falling back to env default %r",
            key,
            raw,
            default,
        )
        return default


def effective_int(key: str, default: int) -> int:
    """
    Return the effective integer for *key* (S14–S18 loop-bound accessors — I7).
    Falls back to *default* on a coercion error (fail-closed — §8 trade-offs).

    Same pattern as effective_float; callers pass the env-baseline default so the
    module never needs to know per-key semantics.
    """
    raw = _cache.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "config_overrides.effective_int: key=%r has malformed value %r — "
            "falling back to env default %r",
            key,
            raw,
            default,
        )
        return default


def effective_domain_vocabulary() -> list[str]:
    """
    S9 typed accessor — parse the cached domain_vocabulary JSON array → list[str].

    Returns [] if the key is unset, the stored value is "[]", or the stored value
    is malformed (fail-closed — a malformed stored value can only exist if it bypassed
    validate_value, which re-serialises canonically at write).

    Pure in-memory O(1) on the ADR-0053 cache; never touches the DB.  Mypy-strict —
    return type is always list[str] with no Any (ADR-0054 §2.1).
    """
    raw = _cache.get("domain_vocabulary")
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "config_overrides.effective_domain_vocabulary: malformed stored value — "
            "returning [] (fail-closed, ADR-0054 §2.1)"
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "config_overrides.effective_domain_vocabulary: stored value is not a list — "
            "returning [] (fail-closed, ADR-0054 §2.1)"
        )
        return []
    # Filter to non-empty strings only (defensive; canonical write guarantees this)
    return [s for s in parsed if isinstance(s, str) and s]


_VALID_SCHEDULE_VALUES: frozenset[str] = frozenset({"off", "hourly", "daily", "weekly"})


def effective_schedule(key: str) -> str:
    """
    S10/S11/S12/S13 typed accessor — return the effective schedule value for *key*
    (one of: off|hourly|daily|weekly). Falls back to "off" when unset or malformed.

    The schedule keys (lint_schedule / backfill_schedule / schema_review_schedule /
    reclassify_schedule) have no env-var baseline; the deployment default is "off"
    (feature dormant — R12-7/A5, R12-8, R12-9).

    Pure in-memory O(1) on the ADR-0053 cache; never touches the DB.
    """
    raw = _cache.get(key)
    if raw is None:
        return "off"
    if raw not in _VALID_SCHEDULE_VALUES:
        logger.warning(
            "config_overrides.effective_schedule: key=%r has unknown value %r — "
            "returning 'off' (fail-closed, R12-7/A5)",
            key,
            raw,
        )
        return "off"
    return raw


# ── Cache write helpers ───────────────────────────────────────────────────────


async def set_override(session: AsyncSession, key: str, value: str) -> None:
    """
    Allow-list check + validate + (normalise for S9) + upsert into app_config + refresh cache.

    Raises ValueError if key ∉ ALLOWED_CONFIG_KEYS or value fails validation.
    (Callers convert ValueError → HTTP 400 / 422 as appropriate.)

    For S9 (domain_vocabulary): strips each name, drops empties, dedupes case-insensitively
    preserving first spelling, caps at 100, then re-serialises as canonical JSON before upsert
    (ADR-0054 §2.1 normalisation).
    """
    if key not in ALLOWED_CONFIG_KEYS:
        raise ValueError(f"invalid_key: {key!r}")
    err = validate_value(key, value)
    if err is not None:
        raise ValueError(f"invalid_value: {err}")

    # S9: normalise the domain vocabulary before upsert (ADR-0054 §2.1).
    # validate_value already confirmed it is a valid JSON list — safe to parse here.
    if key == "domain_vocabulary":
        raw_list: list[str] = json.loads(value)
        seen_lower: set[str] = set()
        normalised: list[str] = []
        for name in raw_list:
            stripped = name.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if lower not in seen_lower:
                seen_lower.add(lower)
                normalised.append(stripped)
        value = json.dumps(normalised)

    from app.models import AppConfig  # noqa: PLC0415 — deferred to avoid circular import

    # Upsert by primary key (key is the PK)
    existing_result = await session.execute(select(AppConfig).where(AppConfig.key == key))
    row = existing_result.scalar_one_or_none()
    if row is not None:
        row.value = value
        from datetime import UTC, datetime  # noqa: PLC0415

        row.updated_at = datetime.now(UTC)
    else:
        from datetime import UTC, datetime  # noqa: PLC0415

        session.add(AppConfig(key=key, value=value, updated_at=datetime.now(UTC)))

    await session.flush()

    # Refresh cache AFTER flush (inside session scope — write committed by caller's context manager)
    async with _cache_lock:
        _cache[key] = value

    logger.info(
        "config_overrides.set_override: key=%r source=override (value NOT logged — ADR-0053 §6.8)",
        key,
    )


async def clear_override(session: AsyncSession, key: str) -> None:
    """
    Allow-list check + DELETE the app_config row for *key* + refresh cache.

    Raises ValueError if key ∉ ALLOWED_CONFIG_KEYS.
    No-op if the row does not exist (idempotent DELETE).
    """
    if key not in ALLOWED_CONFIG_KEYS:
        raise ValueError(f"invalid_key: {key!r}")

    from app.models import AppConfig  # noqa: PLC0415

    await session.execute(delete(AppConfig).where(AppConfig.key == key))
    await session.flush()

    async with _cache_lock:
        _cache.pop(key, None)

    logger.info(
        "config_overrides.clear_override: key=%r source=env (override removed — ADR-0053 §3.3)",
        key,
    )
