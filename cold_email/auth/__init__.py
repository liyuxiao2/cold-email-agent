"""Authentication: identity, sessions, and secret encryption.

Four single-purpose modules. Routes import only from `deps`; nothing outside
this package touches Fernet keys or JWT internals.
"""

from cold_email.auth.deps import get_current_user, require_admin

__all__ = ["get_current_user", "require_admin"]
