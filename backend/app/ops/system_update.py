"""System self-update (R12-3) — B1 (Watchtower HTTP API).

The Home / Settings "Aggiorna sistema" button pokes Watchtower's ``/v1/update`` endpoint, which
pulls the latest images and recreates every container labelled
``com.centurylinklabs.watchtower.enable=true`` on the Docker host. This keeps Docker privileges
OUT of the backend (no docker.sock here) at the cost of NO real download percentage — Watchtower's
API is fire-and-forget, so the UI shows an indeterminate "in progress" state (the deliberate B1
trade-off vs. a privileged updater sidecar).

Update availability is a read-only check against the public GitHub Releases API (latest tag vs the
running backend version), cached ~1h to respect the unauthenticated rate limit (60/h).
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GITHUB_RELEASES_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
_CACHE_TTL_SECONDS = 3600.0

# Module-level cache for the latest-version lookup (avoids GitHub's 60/h unauth rate limit).
# (fetched_at_monotonic, latest_version_or_None)
_latest_cache: tuple[float, str | None] = (0.0, None)


class UpdateNotConfiguredError(RuntimeError):
    """Raised by :func:`trigger_system_update` when Watchtower is not wired up (→ HTTP 501)."""


@dataclass(frozen=True)
class UpdateStatus:
    current_version: str
    latest_version: str | None
    update_available: bool
    update_supported: bool  # Watchtower configured → the button can actually act


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    """'v1.7.2' / '1.7.2' → (1, 7, 2); None if not a clean X.Y.Z (pre-release/junk ignored)."""
    cleaned = value.strip().lstrip("v").split("-", 1)[0].split("+", 1)[0]
    parts = cleaned.split(".")
    if not (1 <= len(parts) <= 3):
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _current_version() -> str:
    """The running backend version (main.py resolves it once at import; 'dev' fallback)."""
    return str(getattr(sys.modules.get("app.main"), "_app_version", "dev"))


async def _fetch_latest_version() -> str | None:
    """Latest published release tag from GitHub (cached ~1h). Serves stale / None on any failure —
    the update check must NEVER break the endpoint."""
    global _latest_cache
    now = time.monotonic()
    cached_at, cached = _latest_cache
    if cached is not None and (now - cached_at) < _CACHE_TTL_SECONDS:
        return cached

    url = _GITHUB_RELEASES_LATEST.format(repo=settings.update_check_repo)
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            resp = await http.get(url, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            logger.info("update-check: GitHub returned HTTP %s", resp.status_code)
            return cached  # serve stale on transient failure/rate-limit
        tag = str(resp.json().get("tag_name", "")).strip()
        latest = tag.lstrip("v") or None
        _latest_cache = (now, latest)
        return latest
    except Exception as exc:  # noqa: BLE001 — never let the update check break /ops/update-status
        logger.info("update-check: %s", exc)
        return cached


async def get_update_status() -> UpdateStatus:
    """Compare the running version with the latest published release."""
    current = _current_version()
    latest = await _fetch_latest_version()

    available = False
    if latest:
        cur, lat = _parse_semver(current), _parse_semver(latest)
        available = bool(cur and lat and lat > cur)

    supported = bool(settings.watchtower_url and settings.watchtower_http_api_token)
    return UpdateStatus(
        current_version=current,
        latest_version=latest,
        update_available=available,
        update_supported=supported,
    )


async def trigger_system_update() -> str:
    """POST Watchtower's ``/v1/update``; returns a human message.

    Raises :class:`UpdateNotConfiguredError` when Watchtower is not configured, and lets
    ``httpx.HTTPError`` propagate on a transport/HTTP failure (the caller maps these to 501 / 502).
    """
    base = settings.watchtower_url
    token = settings.watchtower_http_api_token
    if not (base and token):
        raise UpdateNotConfiguredError(
            "system update is not configured (set WATCHTOWER_URL and WATCHTOWER_HTTP_API_TOKEN)"
        )

    url = base.rstrip("/") + "/v1/update"
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    logger.info("system-update: Watchtower /v1/update returned HTTP %s", resp.status_code)
    return "Update triggered — labelled containers will pull the latest images and restart."
