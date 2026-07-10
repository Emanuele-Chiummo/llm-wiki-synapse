"""
Symmetric at-rest encryption for UI-supplied provider API keys (W1 / F17, §12 amendment).

Emanuele's decision: per-vendor API keys entered in the Settings UI are stored in Postgres
(``provider_config.api_key_encrypted``) **encrypted at rest** with Fernet (AES-128-CBC +
HMAC-SHA256, from the ``cryptography`` lib). The master key is a urlsafe-base64 32-byte Fernet
key read from the ``SYNAPSE_SECRET_KEY`` environment variable — NEVER hardcoded, NEVER stored
in the DB, NEVER logged.

Design rules (security-sensitive — do not weaken):
  * The plaintext key is encrypted on write and decrypted ONLY when a provider is built or a
    provider-test call is made. It is NEVER returned by any API response (GET endpoints expose
    only ``api_key_configured`` + an optional masked ``sk-…last4`` derived at read time).
  * If ``SYNAPSE_SECRET_KEY`` is unset/invalid the service does NOT crash: ``is_configured()``
    returns False, ``encrypt()`` raises :class:`SecretsNotConfiguredError` (the CRUD layer turns
    that into a clear HTTP 400), and the provider layer falls back to env-var keys.
  * A tampered/foreign ciphertext fails closed: ``decrypt()`` raises :class:`InvalidToken` (the
    provider layer catches it and falls back to the env-var key rather than serving a corrupt
    secret).

The master key is read from ``os.environ`` at call time (not import time) so tests and hot
config edits observe the current value without a process restart — the same pattern the API
provider uses for ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "InvalidToken",
    "SecretsNotConfiguredError",
    "decrypt",
    "encrypt",
    "is_configured",
    "mask_from_encrypted",
]

logger = logging.getLogger(__name__)

_SECRET_KEY_ENV = "SYNAPSE_SECRET_KEY"  # noqa: S105 — env-var NAME, not a secret value


class SecretsNotConfiguredError(RuntimeError):
    """
    Raised by :func:`encrypt` when ``SYNAPSE_SECRET_KEY`` is unset or invalid.

    The CRUD layer maps this to HTTP 400 ("server not configured for key storage") rather than
    crashing — key storage is an opt-in capability, not a hard startup requirement (I6: the
    provider layer still works with env-var keys).
    """


def _load_fernet() -> Fernet | None:
    """
    Build a Fernet from ``SYNAPSE_SECRET_KEY`` or return None when unset/invalid.

    Never raises — callers decide whether a missing key is fatal (encrypt) or a degrade
    (is_configured / decrypt). The key value is never logged.
    """
    raw = os.environ.get(_SECRET_KEY_ENV)
    if not raw:
        return None
    try:
        return Fernet(raw.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        # Malformed key material (wrong length / not urlsafe-base64). Log the failure class
        # only — NEVER the key value.
        logger.warning(
            "%s is set but is not a valid urlsafe-base64 32-byte Fernet key (%s); "
            "key storage is disabled — falling back to env-var provider keys.",
            _SECRET_KEY_ENV,
            type(exc).__name__,
        )
        return None


def is_configured() -> bool:
    """Return True iff a usable ``SYNAPSE_SECRET_KEY`` is present (key storage is available)."""
    return _load_fernet() is not None


def encrypt(plaintext: str) -> bytes:
    """
    Encrypt a plaintext API key to a Fernet token (bytes) for at-rest storage.

    Raises:
        SecretsNotConfiguredError: when ``SYNAPSE_SECRET_KEY`` is unset/invalid — the caller
            (CRUD layer) surfaces this as HTTP 400 and refuses to store the key.
    """
    fernet = _load_fernet()
    if fernet is None:
        raise SecretsNotConfiguredError(
            "server not configured for key storage; set SYNAPSE_SECRET_KEY or use env-var "
            "provider keys"
        )
    return fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    """
    Decrypt a Fernet token produced by :func:`encrypt` back to the plaintext API key.

    Raises:
        SecretsNotConfiguredError: when ``SYNAPSE_SECRET_KEY`` is unset/invalid.
        InvalidToken: when the ciphertext is tampered, truncated, or was produced under a
            different master key (fail-closed — the provider layer catches this and falls back
            to the env-var key).
    """
    fernet = _load_fernet()
    if fernet is None:
        raise SecretsNotConfiguredError(
            "server not configured for key storage; SYNAPSE_SECRET_KEY is unset/invalid"
        )
    return fernet.decrypt(token).decode("utf-8")


def mask_from_encrypted(token: bytes | None) -> str | None:
    """
    Best-effort masked hint (``"sk-…1234"``) for a stored ciphertext — for the Settings UI.

    Decrypts internally and returns only a non-reversible fragment (prefix marker + last 4
    chars). Returns None when there is no token, no master key, or the ciphertext is invalid —
    NEVER raises and NEVER returns the full key. Only the last 4 characters are ever exposed.
    """
    if not token:
        return None
    try:
        plaintext = decrypt(token)
    except (SecretsNotConfiguredError, InvalidToken):
        return None
    if len(plaintext) <= 4:
        # Too short to mask safely — never echo it; report presence only.
        return "…"
    return f"…{plaintext[-4:]}"
