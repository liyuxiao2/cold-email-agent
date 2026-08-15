from cold_email.config import settings


def test_cors_origins_is_not_wildcard():
    """A wildcard origin with allow_credentials=True is rejected by browsers.

    Cookie-based sessions from Vercel to Cloud Run silently fail if this
    regresses, so it is asserted rather than left to review.
    """
    assert "*" not in settings.cors_origins


def test_auth_settings_exist():
    for attr in (
        "session_secret",
        "encryption_key",
        "google_redirect_uri",
        "frontend_url",
        "admin_email",
        "cookie_secure",
    ):
        assert hasattr(settings, attr), f"missing setting: {attr}"
