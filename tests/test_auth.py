"""Tests for the simple password authentication."""
import pytest

PASSWORD = "hunter2"


@pytest.fixture()
def auth_client(app_client, monkeypatch):
    """Test client with APP_PASSWORD enabled."""
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    return app_client


class TestAuthDisabled:
    def test_index_accessible_without_password(self, app_client):
        assert app_client.get("/").status_code == 200

    def test_login_redirects_home_when_disabled(self, app_client):
        resp = app_client.get("/login")
        assert resp.status_code == 302
        assert "/" in resp.headers["Location"]


class TestLoginWall:
    def test_index_redirects_to_login(self, auth_client):
        resp = auth_client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_post_reminders_redirects_to_login(self, auth_client):
        resp = auth_client.post("/reminders", data={"task": "x", "when": "in 5 min"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_delete_reminder_redirects_to_login(self, auth_client):
        resp = auth_client.delete("/reminders/does-not-exist")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_snooze_view_stays_exempt(self, auth_client):
        # Unknown token should render the 404 page, not redirect to login.
        resp = auth_client.get("/snooze/badtoken")
        assert resp.status_code == 404


class TestLoginFlow:
    def test_login_page_renders(self, auth_client):
        resp = auth_client.get("/login")
        assert resp.status_code == 200
        assert b"Password" in resp.data

    def test_wrong_password_rejected(self, auth_client):
        resp = auth_client.post("/login", data={"password": "wrong"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Incorrect password" in resp.data

    def test_correct_password_grants_access(self, auth_client):
        resp = auth_client.post("/login", data={"password": PASSWORD})
        assert resp.status_code == 302
        resp = auth_client.get("/")
        assert resp.status_code == 200

    def test_logout_revokes_access(self, auth_client):
        auth_client.post("/login", data={"password": PASSWORD})
        auth_client.post("/logout")
        resp = auth_client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_next_parameter_redirect(self, auth_client):
        resp = auth_client.post("/login?next=/reminders", data={"password": PASSWORD})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/reminders")

    def test_open_redirect_blocked(self, auth_client):
        resp = auth_client.post(
            "/login?next=https://evil.example/", data={"password": PASSWORD}
        )
        assert resp.status_code == 302
        assert "evil" not in resp.headers["Location"]
