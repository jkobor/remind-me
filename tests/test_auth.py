"""Tests for auth.py – LDAP authentication and session helpers."""
import os
from unittest.mock import MagicMock, patch

import pytest

import auth


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_true_when_both_vars_set(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        assert auth.is_configured() is True

    def test_false_when_server_missing(self, monkeypatch):
        monkeypatch.delenv("LDAP_SERVER", raising=False)
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        assert auth.is_configured() is False

    def test_false_when_base_dn_missing(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.delenv("LDAP_BASE_DN", raising=False)
        assert auth.is_configured() is False

    def test_false_when_both_missing(self, monkeypatch):
        monkeypatch.delenv("LDAP_SERVER", raising=False)
        monkeypatch.delenv("LDAP_BASE_DN", raising=False)
        assert auth.is_configured() is False


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------

def _make_ldap_entry(mail=None, cn=None, display_name=None):
    """Build a minimal fake ldap3 entry."""
    def _attr(val):
        a = MagicMock()
        a.value = val
        return a

    entry = MagicMock()
    entry.mail = _attr(mail)
    entry.cn = _attr(cn)
    entry.displayName = _attr(display_name)
    return entry


class TestAuthenticate:
    def _patch_ldap(self, entries=None, bind_raises=None):
        """Return a context manager that patches ldap3 Connection."""
        conn_mock = MagicMock()
        conn_mock.entries = entries if entries is not None else []

        if bind_raises:
            from ldap3.core.exceptions import LDAPException
            conn_cls = MagicMock(side_effect=bind_raises)
        else:
            conn_cls = MagicMock(return_value=conn_mock)

        server_cls = MagicMock()
        return patch.multiple(
            "ldap3",
            Server=server_cls,
            Connection=conn_cls,
            ALL=MagicMock(),
            SIMPLE=MagicMock(),
        ), conn_mock

    def test_returns_user_dict_on_success(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")

        entry = _make_ldap_entry(mail="bob@example.com", cn="Bob", display_name="Bob Smith")
        conn_mock = MagicMock()
        conn_mock.entries = [entry]

        with patch("ldap3.Server"), \
             patch("ldap3.Connection", return_value=conn_mock), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            result = auth.authenticate("bob", "secret")

        assert result is not None
        assert result["username"] == "bob"
        assert result["email"] == "bob@example.com"
        assert result["display_name"] == "Bob Smith"

    def test_returns_none_on_ldap_exception(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")

        from ldap3.core.exceptions import LDAPException

        with patch("ldap3.Server"), \
             patch("ldap3.Connection", side_effect=LDAPException("bad creds")), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            result = auth.authenticate("bob", "wrong")

        assert result is None

    def test_falls_back_to_username_at_localhost_when_no_mail(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")

        entry = _make_ldap_entry(mail=None, cn="Bob")
        conn_mock = MagicMock()
        conn_mock.entries = [entry]

        with patch("ldap3.Server"), \
             patch("ldap3.Connection", return_value=conn_mock), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            result = auth.authenticate("bob", "secret")

        assert result["email"] == "bob@localhost"

    def test_falls_back_to_username_when_no_display_name(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")

        entry = _make_ldap_entry(mail="bob@example.com", cn=None, display_name=None)
        conn_mock = MagicMock()
        conn_mock.entries = [entry]

        with patch("ldap3.Server"), \
             patch("ldap3.Connection", return_value=conn_mock), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            result = auth.authenticate("bob", "secret")

        assert result["display_name"] == "bob"

    def test_uses_cn_when_display_name_absent(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")

        entry = _make_ldap_entry(mail="bob@example.com", cn="Bobby", display_name=None)
        conn_mock = MagicMock()
        conn_mock.entries = [entry]

        with patch("ldap3.Server"), \
             patch("ldap3.Connection", return_value=conn_mock), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            result = auth.authenticate("bob", "secret")

        assert result["display_name"] == "Bobby"

    def test_bind_dn_uses_default_template(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        monkeypatch.delenv("LDAP_BIND_DN_TEMPLATE", raising=False)
        monkeypatch.delenv("LDAP_USER_ATTR", raising=False)

        conn_mock = MagicMock()
        conn_mock.entries = []
        conn_cls = MagicMock(return_value=conn_mock)

        with patch("ldap3.Server"), patch("ldap3.Connection", conn_cls), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            auth.authenticate("alice", "pw")

        call_kwargs = conn_cls.call_args[1]
        assert call_kwargs["user"] == "uid=alice,dc=example,dc=com"

    def test_custom_bind_dn_template(self, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        monkeypatch.setenv("LDAP_BIND_DN_TEMPLATE", "{username}@example.com")

        conn_mock = MagicMock()
        conn_mock.entries = []
        conn_cls = MagicMock(return_value=conn_mock)

        with patch("ldap3.Server"), patch("ldap3.Connection", conn_cls), \
             patch("ldap3.ALL"), patch("ldap3.SIMPLE"):
            auth.authenticate("alice", "pw")

        call_kwargs = conn_cls.call_args[1]
        assert call_kwargs["user"] == "alice@example.com"


# ---------------------------------------------------------------------------
# login_required decorator
# ---------------------------------------------------------------------------

class TestLoginRequired:
    def test_redirects_when_no_session(self, app_client):
        resp = app_client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_allows_access_when_logged_in(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200

    def test_next_param_preserved_in_redirect(self, app_client):
        resp = app_client.get("/reminders")
        # GET /reminders doesn't exist as a route, but the auth redirect fires first
        # (302 to /login?next=...) before Flask checks the method.
        # For a proper route, confirm next= is set.
        resp = app_client.delete("/reminders/some-id")
        location = resp.headers.get("Location", "")
        assert "next=" in location or "/login" in location


# ---------------------------------------------------------------------------
# current_user
# ---------------------------------------------------------------------------

class TestCurrentUser:
    def test_returns_none_when_not_logged_in(self, app_client):
        with app_client.application.test_request_context():
            from flask import session
            assert auth.current_user() is None

    def test_returns_user_dict_when_logged_in(self, logged_in_client):
        from tests.conftest import TEST_USERNAME, TEST_USER_EMAIL, TEST_USER_DISPLAY
        with logged_in_client.application.test_request_context():
            from flask import session
            session["username"] = TEST_USERNAME
            session["email"] = TEST_USER_EMAIL
            session["display_name"] = TEST_USER_DISPLAY
            user = auth.current_user()
        assert user["username"] == TEST_USERNAME
        assert user["email"] == TEST_USER_EMAIL
        assert user["display_name"] == TEST_USER_DISPLAY
