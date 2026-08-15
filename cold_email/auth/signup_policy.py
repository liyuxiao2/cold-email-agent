"""Signup allowlist — default deny.

Kept out of `deps.py` on purpose: `deps.py`'s docstring promises it is "the
only auth surface routes should import" for the two FastAPI `Depends`
functions (`get_current_user`, `require_admin`). `is_signup_allowed` is a
plain predicate consulted once, from the OAuth callback, before a `User` row
ever exists — it isn't a request dependency — so it gets its own module
rather than blurring that file's contract.

A signed-in `role='user'` account can already call `POST /api/leads/{id}/approve`,
which sends real mail through the single shared `GMAIL_REFRESH_TOKEN` mailbox.
So unlike a typical app, "anyone with a Google account" is not a safe signup
policy — only default-deny is.
"""

from cold_email.config import settings


def is_signup_allowed(email: str) -> bool:
    """True if `email` may create an account.

    Evaluated case-insensitively against the full address:
      1. `email == settings.admin_email` — the seeded admin must always be
         able to log in, even with both allowlist settings left empty.
      2. `email in settings.allowed_signup_emails`.
      3. `settings.allowed_signup_domain` is non-empty and matches the
         email's domain.
    Otherwise denied.
    """
    email = email.strip().lower()

    if settings.admin_email and email == settings.admin_email.strip().lower():
        return True

    if email in {allowed.strip().lower() for allowed in settings.allowed_signup_emails}:
        return True

    if settings.allowed_signup_domain:
        domain = email.rsplit("@", 1)[-1]
        if domain == settings.allowed_signup_domain.strip().lower():
            return True

    return False
