import base64
from email import message_from_bytes

import pytest

from cold_email.workers.shared.gmail_client import GmailCredentials, create_draft, send_draft

CREDS = GmailCredentials(
    refresh_token="rt-123",  # noqa: S106 (test fixture, not a real credential)
    sender_email="me@example.com",
)


class _FakeDrafts:
    def __init__(self, sink):
        self.sink = sink

    def create(self, userId, body):
        self.sink["create"] = body
        return self

    def send(self, userId, body):
        self.sink["send"] = body
        return self

    def execute(self):
        return {"id": "draft-1"}


def _stub_service(monkeypatch, sink):
    class _Users:
        def drafts(self):
            return _FakeDrafts(sink)

    class _Service:
        def users(self):
            return _Users()

    monkeypatch.setattr("cold_email.workers.shared.gmail_client.build", lambda *a, **k: _Service())


def _decode(sink):
    raw = sink["create"]["message"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw))


def test_credentials_come_from_the_argument_not_settings(monkeypatch):
    """The regression that matters: if create_draft still read
    settings.gmail_refresh_token, every user's mail would silently send from
    one mailbox."""
    captured = {}

    def fake_credentials(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("cold_email.workers.shared.gmail_client.Credentials", fake_credentials)
    _stub_service(monkeypatch, {})

    create_draft(CREDS, to="them@example.com", subject="s", body="b")
    assert captured["refresh_token"] == "rt-123"  # noqa: S105 (test fixture, not a real credential)


def test_settings_no_longer_expose_a_refresh_token():
    from cold_email.config import settings

    assert not hasattr(settings, "gmail_refresh_token")
    assert not hasattr(settings, "gmail_sender_email")


def test_oauth_app_credentials_remain_in_settings():
    """client_id/secret are APP-level and required to refresh ANY user's token.
    Moving all four to the users table is the classic multi-tenant OAuth
    mistake — nothing can then be refreshed."""
    from cold_email.config import settings

    assert hasattr(settings, "gmail_client_id")
    assert hasattr(settings, "gmail_client_secret")


def test_from_header_uses_the_users_sender_email(monkeypatch):
    sink = {}
    _stub_service(monkeypatch, sink)
    monkeypatch.setattr("cold_email.workers.shared.gmail_client.Credentials", lambda **k: object())

    create_draft(CREDS, to="them@example.com", subject="s", body="b")
    assert _decode(sink)["From"] == "me@example.com"


def test_attaches_bytes_as_a_pdf(monkeypatch):
    sink = {}
    _stub_service(monkeypatch, sink)
    monkeypatch.setattr("cold_email.workers.shared.gmail_client.Credentials", lambda **k: object())

    create_draft(
        CREDS,
        to="them@example.com",
        subject="s",
        body="plain",
        html="<p>rich</p>",
        attachment=("cv.pdf", b"%PDF-1.7 bytes"),
    )

    parts = list(_decode(sink).walk())
    types = [p.get_content_type() for p in parts]
    assert "text/plain" in types
    assert "text/html" in types
    assert "application/pdf" in types

    pdf = next(p for p in parts if p.get_content_type() == "application/pdf")
    assert pdf.get_filename() == "cv.pdf"


def test_no_attachment_still_produces_a_multipart_alternative(monkeypatch):
    sink = {}
    _stub_service(monkeypatch, sink)
    monkeypatch.setattr("cold_email.workers.shared.gmail_client.Credentials", lambda **k: object())

    create_draft(CREDS, to="t@example.com", subject="s", body="plain", html="<p>rich</p>")
    types = [p.get_content_type() for p in _decode(sink).walk()]
    assert "text/plain" in types and "text/html" in types
    assert "application/pdf" not in types


def test_send_draft_sends_the_given_id(monkeypatch):
    sink = {}
    _stub_service(monkeypatch, sink)
    monkeypatch.setattr("cold_email.workers.shared.gmail_client.Credentials", lambda **k: object())

    send_draft(CREDS, "draft-9")
    assert sink["send"] == {"id": "draft-9"}


@pytest.mark.asyncio
async def test_resolve_returns_none_without_a_stored_token(async_session, admin_user_id):
    from cold_email.auth.gmail_creds import resolve_gmail_credentials
    from cold_email.database import User

    user = await async_session.get(User, admin_user_id)
    assert resolve_gmail_credentials(user) is None


@pytest.mark.asyncio
async def test_resolve_decrypts_the_stored_token(async_session, admin_user_id):
    from cold_email.auth.crypto import encrypt
    from cold_email.auth.gmail_creds import resolve_gmail_credentials
    from cold_email.database import User

    user = await async_session.get(User, admin_user_id)
    user.gmail_refresh_token_enc = encrypt("rt-real")
    user.gmail_sender_email = "me@example.com"
    await async_session.commit()

    creds = resolve_gmail_credentials(user)
    assert creds.refresh_token == "rt-real"  # noqa: S105 (test fixture, not a real credential)
    assert creds.sender_email == "me@example.com"
