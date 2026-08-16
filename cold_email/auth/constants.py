"""Session, CSRF, and Google OAuth constants shared across the auth package."""

SESSION_COOKIE = "ce_session"
SESSION_TTL_DAYS = 7
STATE_TTL_MINUTES = 10
ALGORITHM = "HS256"

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (endpoint, not a secret)

GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.compose",
]

TOKEN_TIMEOUT_SECONDS = 15
