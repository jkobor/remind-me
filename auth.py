"""LDAP authentication and session helpers for Remind Me."""
import os
from functools import wraps

from flask import redirect, request, session, url_for


def is_configured() -> bool:
    """Return True if LDAP environment variables are set."""
    return bool(os.environ.get("LDAP_SERVER") and os.environ.get("LDAP_BASE_DN"))


def authenticate(username: str, password: str) -> dict | None:
    """Authenticate a user against LDAP.

    Performs a simple bind using the DN template, then searches for the user
    entry to retrieve their email and display name.

    Returns a user dict ``{username, email, display_name}`` on success,
    or ``None`` on failure (wrong credentials, LDAP unreachable, etc.).

    Configuration via environment variables:

    - ``LDAP_SERVER``           – LDAP server URL, e.g. ``ldap://ldap.example.com``
    - ``LDAP_PORT``             – TCP port (default: ``389``)
    - ``LDAP_BASE_DN``          – Base DN, e.g. ``dc=example,dc=com``
    - ``LDAP_BIND_DN_TEMPLATE`` – DN template for binding; ``{username}``
                                   is replaced with the submitted username.
                                   Default: ``uid={username},{base_dn}``
    - ``LDAP_USER_SEARCH_BASE`` – Search base for the user lookup
                                   (defaults to ``LDAP_BASE_DN``)
    - ``LDAP_USER_ATTR``        – Attribute that holds the login name
                                   (default: ``uid``)
    """
    from ldap3 import ALL, SIMPLE, Connection, Server
    from ldap3.core.exceptions import LDAPException

    server_url = os.environ.get("LDAP_SERVER", "ldap://localhost")
    port = int(os.environ.get("LDAP_PORT", "389"))
    base_dn = os.environ.get("LDAP_BASE_DN", "")
    user_attr = os.environ.get("LDAP_USER_ATTR", "uid")
    bind_dn_template = os.environ.get(
        "LDAP_BIND_DN_TEMPLATE",
        f"{user_attr}={{username}},{base_dn}",
    )
    search_base = os.environ.get("LDAP_USER_SEARCH_BASE", base_dn)

    bind_dn = bind_dn_template.format(username=username)

    try:
        server = Server(server_url, port=port, get_info=ALL)
        conn = Connection(
            server,
            user=bind_dn,
            password=password,
            authentication=SIMPLE,
            auto_bind=True,
        )

        # Fetch user attributes after a successful bind.
        conn.search(
            search_base,
            f"({user_attr}={username})",
            attributes=["mail", "cn", "displayName"],
        )

        email = display_name = None
        if conn.entries:
            entry = conn.entries[0]
            email = _get_attr(entry, "mail")
            display_name = _get_attr(entry, "displayName") or _get_attr(entry, "cn")

        conn.unbind()

        return {
            "username": username,
            "email": email or f"{username}@localhost",
            "display_name": display_name or username,
        }
    except LDAPException:
        return None


def _get_attr(entry, attr: str) -> str | None:
    """Safely extract a string value from an ldap3 entry attribute."""
    try:
        val = getattr(entry, attr)
        if val and val.value:
            s = str(val.value)
            # ldap3 represents missing multi-value attrs as "[]"
            return s if s not in ("[]", "None") else None
    except Exception:
        pass
    return None


def login_required(f):
    """Route decorator: redirect unauthenticated users to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def current_user() -> dict | None:
    """Return the logged-in user from the session, or ``None``."""
    if "username" not in session:
        return None
    return {
        "username": session["username"],
        "email": session.get("email", ""),
        "display_name": session.get("display_name", session["username"]),
    }
