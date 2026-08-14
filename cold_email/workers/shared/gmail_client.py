"""Gmail API client — OAuth2 refresh-token flow for a single sender mailbox.

We store a long-lived refresh token in settings and mint short-lived access
tokens on demand. google-auth handles the refresh transparently when the
Credentials object is first used, so callers never touch token expiry.

Scope: gmail.compose is the minimum needed to create drafts.
"""

import base64
import logging
from email.message import EmailMessage

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from cold_email.config import settings

logger = logging.getLogger(__name__)

GMAIL_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 (OAuth token endpoint, not a secret)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def _build_service():
    """Construct an authenticated Gmail API service from the stored refresh token."""
    creds = Credentials(
        token=None,
        refresh_token=settings.gmail_refresh_token,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        token_uri=GMAIL_TOKEN_URI,
        scopes=GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def create_draft(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    attachment_path: str | None = None,
) -> str:
    """Create a Gmail draft in the sender's mailbox and return its draft ID.

    The Gmail API wants the whole RFC 2822 message base64url-encoded as `raw`.
    When `html` is given, the message becomes multipart/alternative: the plain
    `body` is the fallback and `html` is the rich version Gmail renders.
    """
    message = EmailMessage()
    message["To"] = to
    message["From"] = settings.gmail_sender_email
    message["Subject"] = subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    if attachment_path:
        import mimetypes
        from pathlib import Path

        path = Path(attachment_path)
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        filename = path.name
        with path.open("rb") as fp:
            file_data = fp.read()
        message.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=filename)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service = _build_service()
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    logger.info(f"Created Gmail draft {draft['id']} to {to}")
    return draft["id"]


def send_draft(draft_id: str) -> str:
    """Send an existing Gmail draft by its draft resource ID; return the sent message ID.

    drafts.send moves the draft out of Drafts and delivers it in one call — no need
    to rebuild the message. The gmail.compose scope already permits sending.
    """
    service = _build_service()
    sent = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    logger.info(f"Sent Gmail draft {draft_id} as message {sent['id']}")
    return sent["id"]
