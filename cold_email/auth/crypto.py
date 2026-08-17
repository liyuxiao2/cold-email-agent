"""Fernet encryption for secrets at rest (Gmail refresh tokens, LLM API keys)."""

from functools import lru_cache

from cryptography.fernet import Fernet

from cold_email.config import settings


class EncryptionKeyMissing(RuntimeError):
    """Raised when ENCRYPTION_KEY is unset or malformed."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    """Build the Fernet cipher once."""
    if not settings.encryption_key:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(settings.encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionKeyMissing(f"ENCRYPTION_KEY is malformed: {exc}") from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a secret for storage in a BYTEA column."""
    return _cipher().encrypt(plaintext.encode())


def decrypt(token: bytes) -> str:
    """Decrypt a stored secret. Raises InvalidToken if tampered or wrong key."""
    return _cipher().decrypt(bytes(token)).decode()
