"""Decrypt a user's Gmail credentials.

Lives in the auth package so the Fernet key never leaves it — workers ask for
credentials and receive plaintext, without importing crypto themselves.
"""

import logging

from cold_email.auth.crypto import decrypt
from cold_email.database import User
from cold_email.workers.shared.gmail_client import GmailCredentials

logger = logging.getLogger(__name__)


def resolve_gmail_credentials(user: User) -> GmailCredentials | None:
    """Return the user's Gmail credentials, or None if they have not connected.

    None is not an error at login — Google omits refresh_token for a user who
    consented before. It only blocks sending, and the UI surfaces it as
    "Reconnect Gmail".
    """
    if not user.gmail_refresh_token_enc:
        return None
    return GmailCredentials(
        refresh_token=decrypt(user.gmail_refresh_token_enc),
        sender_email=user.gmail_sender_email or user.email,
    )
