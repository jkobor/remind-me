import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Patch APScheduler BEFORE app.py is imported so the scheduler never
# actually starts during the test run.
patch("apscheduler.schedulers.background.BackgroundScheduler", MagicMock).start()

# Make the project root importable without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import db as db_module
import app as flask_app  # noqa: E402 – imported after the scheduler patch


# Reusable test-user constants so tests and fixtures share the same identity.
TEST_USERNAME = "testuser"
TEST_USER_EMAIL = "testuser@example.com"
TEST_USER_DISPLAY = "Test User"
TEST_USER = {
    "username": TEST_USERNAME,
    "email": TEST_USER_EMAIL,
    "display_name": TEST_USER_DISPLAY,
}


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Point DATABASE_PATH at a fresh temp SQLite file and initialise the schema."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "reminders.db"))
    db_module.init_db()
    return tmp_path / "reminders.db"


@pytest.fixture()
def app_client(temp_db, monkeypatch):
    """Flask test client backed by a fresh temp database (no session)."""
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


@pytest.fixture()
def logged_in_client(temp_db, monkeypatch):
    """Flask test client with TEST_USER already in session."""
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = TEST_USERNAME
            sess["email"] = TEST_USER_EMAIL
            sess["display_name"] = TEST_USER_DISPLAY
        yield client


@pytest.fixture()
def mock_zulip_send():
    """Prevent real Zulip calls in sweep tests.

    Returns a fake Zulip message ID (42) so callers that store the return
    value (e.g. sweep() → bot_msg_id) receive a valid integer.
    """
    with patch("notifiers.zulip_notifier.send", return_value=42) as mock:
        yield mock
