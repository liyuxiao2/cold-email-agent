"""Signup allowlist — default deny."""

from cold_email.config import settings


def is_signup_allowed(email: str) -> bool:
    """True if `email` may create an account."""
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
