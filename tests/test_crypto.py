import pytest

from cold_email.auth.crypto import EncryptionKeyMissing, _cipher, decrypt, encrypt
from cold_email.config import settings


def test_round_trip():
    secret = "1//0eXaMpLeRefreshToken"  # noqa: S105 (test fixture, not a real credential)
    assert decrypt(encrypt(secret)) == secret


def test_ciphertext_is_not_plaintext():
    secret = "1//0eXaMpLeRefreshToken"  # noqa: S105 (test fixture, not a real credential)
    assert secret.encode() not in encrypt(secret)


def test_same_plaintext_gives_different_ciphertexts():
    """Fernet is randomized (it embeds an IV and a timestamp).

    Two encryptions of the same value must differ, or an attacker with read
    access to the table could tell which users share a token.
    """
    secret = "same-value"  # noqa: S105 (test fixture, not a real credential)
    a, b = encrypt(secret), encrypt(secret)
    assert a != b
    assert decrypt(a) == decrypt(b) == secret


def test_decrypt_rejects_tampered_ciphertext():
    from cryptography.fernet import InvalidToken

    token = bytearray(encrypt("value"))
    token[-1] ^= 0xFF
    with pytest.raises(InvalidToken):
        decrypt(bytes(token))


def test_missing_encryption_key_raises(monkeypatch):
    """`_cipher` is `@lru_cache(maxsize=1)`, so a naive test would pass by
    cache-warming rather than exercising the guard — the cache must be
    cleared before AND after so this test actually calls the guard and later
    tests are unaffected."""
    _cipher.cache_clear()
    monkeypatch.setattr(settings, "encryption_key", "")
    try:
        with pytest.raises(EncryptionKeyMissing):
            encrypt("x")
    finally:
        _cipher.cache_clear()


def test_malformed_encryption_key_raises(monkeypatch):
    _cipher.cache_clear()
    monkeypatch.setattr(settings, "encryption_key", "not-a-valid-fernet-key")
    try:
        with pytest.raises(EncryptionKeyMissing):
            encrypt("x")
    finally:
        _cipher.cache_clear()
