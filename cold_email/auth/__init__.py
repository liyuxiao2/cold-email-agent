"""Authentication: identity, sessions, and secret encryption.

Four single-purpose modules. Routes import only from `deps`; nothing outside
this package touches Fernet keys or JWT internals.
"""
