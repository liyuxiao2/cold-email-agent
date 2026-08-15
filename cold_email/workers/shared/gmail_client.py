"""Gmail API client — per-user OAuth2 refresh-token flow.

Each user sends from their own mailbox, so credentials are an ARGUMENT, never
read from settings. The split is easy to get backwards:

  * gmail_client_id / gmail_client_secret are APP-level and stay in settings.
    Google requires them to refresh ANY user's token.
  * refresh_token / sender_email are USER-level and live on the users row.

Moving all four to the user row is the classic multi-tenant OAuth mistake —
nothing can then be refreshed.

Scope: gmail.compose is the minimum for creating and sending drafts.
"""

import base64
import logging
from dataclasses import dataclass
from email.message import EmailMessage

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from cold_email.config import settings

logger = logging.getLogger(__name__)

GMAIL_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 (endpoint, not a secret)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


@dataclass(frozen=True)
class GmailCredentials:
    """One user's mailbox identity."""

    refresh_token: str
    sender_email: str


def _build_service(creds: GmailCredentials):
    """Authenticated Gmail service for one user's mailbox."""
    credentials = Credentials(
        token=None,
        refresh_token=creds.refresh_token,
        client_id=settings.gmail_client_id,  # app-level
        client_secret=settings.gmail_client_secret,  # app-level
        token_uri=GMAIL_TOKEN_URI,
        scopes=GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=credentials)


def create_draft(
    creds: GmailCredentials,
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    attachment: tuple[str, bytes] | None = None,
) -> str:
    """Create a draft in the user's mailbox; return its draft ID.

    `attachment` is (filename, data) rather than a path: after the multi-tenant
    migration there is no file on disk — the bytes come from resume_store. The
    résumé is always a PDF, so the old mimetypes.guess_type fallback is gone.
    """
    message = EmailMessage()
    message["To"] = to
    message["From"] = creds.sender_email
    message["Subject"] = subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    if attachment:
        filename, data = attachment
        message.add_attachment(data, maintype="application", subtype="pdf", filename=filename)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service = _build_service(creds)
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    logger.info(f"Created Gmail draft {draft['id']} to {to} from {creds.sender_email}")
    return draft["id"]


def send_draft(creds: GmailCredentials, draft_id: str) -> str:
    """Send an existing draft; return the sent message ID."""
    service = _build_service(creds)
    sent = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    logger.info(f"Sent Gmail draft {draft_id} as message {sent['id']}")
    return sent["id"]
