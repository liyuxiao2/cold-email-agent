import pytest

from cold_email.auth.crypto import decrypt, encrypt


def test_round_trip():
    secret = "1//0eXaMpLeRefreshToken"
    assert decrypt(encrypt(secret)) == secret


def test_ciphertext_is_not_plaintext():
    secret = "1//0eXaMpLeRefreshToken"
    assert secret.encode() not in encrypt(secret)


def test_same_plaintext_gives_different_ciphertexts():
    """Fernet is randomized (it embeds an IV and a timestamp).

    Two encryptions of the same value must differ, or an attacker with read
    access to the table could tell which users share a token.
    """
    secret = "same-value"
    a, b = encrypt(secret), encrypt(secret)
    assert a != b
    assert decrypt(a) == decrypt(b) == secret


def test_decrypt_rejects_tampered_ciphertext():
    from cryptography.fernet import InvalidToken

    token = bytearray(encrypt("value"))
    token[-1] ^= 0xFF
    with pytest.raises(InvalidToken):
        decrypt(bytes(token))
