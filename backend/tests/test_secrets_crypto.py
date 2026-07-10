"""
Unit tests for app.secrets_crypto (W1 / F17, §12 amendment).

Covers:
    - round-trip encrypt→decrypt under a valid SYNAPSE_SECRET_KEY
    - is_configured() reflects presence/absence/invalidity of the master key
    - missing master key degrades: encrypt raises, decrypt raises, is_configured False
    - a tampered/foreign ciphertext fails closed (InvalidToken)
    - mask_from_encrypted never returns the full key and degrades to None safely
"""

from __future__ import annotations

import pytest
from app import secrets_crypto as sc
from cryptography.fernet import Fernet


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set a valid SYNAPSE_SECRET_KEY for the duration of a test."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", key)
    return key


def test_roundtrip(master_key: str) -> None:
    plaintext = "sk-ant-abcdefgh1234"
    token = sc.encrypt(plaintext)
    assert isinstance(token, bytes)
    assert token != plaintext.encode()  # ciphertext, not the plaintext
    assert sc.decrypt(token) == plaintext


def test_is_configured_true_with_valid_key(master_key: str) -> None:
    assert sc.is_configured() is True


def test_is_configured_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    assert sc.is_configured() is False


def test_is_configured_false_when_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", "not-a-valid-fernet-key")
    assert sc.is_configured() is False


def test_encrypt_raises_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    with pytest.raises(sc.SecretsNotConfiguredError):
        sc.encrypt("sk-should-not-store")


def test_decrypt_raises_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Encrypt under a key, then remove the key → decrypt must not silently succeed.
    key = Fernet.generate_key()
    token = Fernet(key).encrypt(b"secret")
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    with pytest.raises(sc.SecretsNotConfiguredError):
        sc.decrypt(token)


def test_tampered_ciphertext_fails_closed(master_key: str) -> None:
    token = bytearray(sc.encrypt("sk-secret-value"))
    token[-1] ^= 0x01  # flip a bit
    with pytest.raises(sc.InvalidToken):
        sc.decrypt(bytes(token))


def test_foreign_key_ciphertext_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ciphertext produced under a DIFFERENT master key must not decrypt under ours.
    foreign = Fernet(Fernet.generate_key()).encrypt(b"sk-secret-value")
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(sc.InvalidToken):
        sc.decrypt(foreign)


def test_mask_exposes_only_last_four(master_key: str) -> None:
    token = sc.encrypt("sk-ant-abcdefgh6789")
    masked = sc.mask_from_encrypted(token)
    assert masked == "…6789"
    assert "abcdefgh" not in (masked or "")  # never the full key


def test_mask_none_for_no_token(master_key: str) -> None:
    assert sc.mask_from_encrypted(None) is None
    assert sc.mask_from_encrypted(b"") is None


def test_mask_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    token = Fernet(key).encrypt(b"sk-secret-value")
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    assert sc.mask_from_encrypted(token) is None  # degrades, never raises


def test_mask_none_on_tamper(master_key: str) -> None:
    token = bytearray(sc.encrypt("sk-secret-value"))
    token[-1] ^= 0x01
    assert sc.mask_from_encrypted(bytes(token)) is None
