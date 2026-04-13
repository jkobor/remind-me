"""Tests for Flask routes and the sweep background job."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import app as flask_app
import db
from tests.conftest import TEST_USERNAME, TEST_USER_EMAIL


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

class TestLoginPage:
    def test_login_page_returns_200(self, app_client):
        assert app_client.get("/login").status_code == 200

    def test_redirects_to_login_when_unauthenticated(self, app_client):
        resp = app_client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_already_logged_in_redirects_to_index(self, logged_in_client):
        resp = logged_in_client.get("/login")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_login_missing_fields_shows_error(self, app_client, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        resp = app_client.post("/login", data={"username": "", "password": ""})
        assert b"enter" in resp.data.lower() or b"username" in resp.data.lower()

    def test_login_invalid_credentials_shows_error(self, app_client, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        with patch("auth.authenticate", return_value=None):
            resp = app_client.post(
                "/login",
                data={"username": "bob", "password": "wrong"},
            )
        assert b"Invalid" in resp.data or b"invalid" in resp.data.lower()

    def test_login_success_redirects_to_index(self, app_client, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        with patch("auth.authenticate", return_value={
            "username": "bob",
            "email": "bob@example.com",
            "display_name": "Bob",
        }):
            resp = app_client.post(
                "/login",
                data={"username": "bob", "password": "secret"},
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_login_success_sets_session(self, app_client, monkeypatch):
        monkeypatch.setenv("LDAP_SERVER", "ldap://localhost")
        monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=com")
        with patch("auth.authenticate", return_value={
            "username": "bob",
            "email": "bob@example.com",
            "display_name": "Bob",
        }):
            app_client.post("/login", data={"username": "bob", "password": "secret"})
        with app_client.session_transaction() as sess:
            assert sess["username"] == "bob"

    def test_shows_ldap_warning_when_unconfigured(self, app_client, monkeypatch):
        monkeypatch.delenv("LDAP_SERVER", raising=False)
        monkeypatch.delenv("LDAP_BASE_DN", raising=False)
        resp = app_client.get("/login")
        assert b"LDAP not configured" in resp.data or b"not configured" in resp.data.lower()


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_clears_session(self, logged_in_client):
        logged_in_client.post("/logout")
        with logged_in_client.session_transaction() as sess:
            assert "username" not in sess

    def test_logout_redirects_to_login(self, logged_in_client):
        resp = logged_in_client.post("/logout")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestIndex:
    def test_returns_200(self, logged_in_client):
        assert logged_in_client.get("/").status_code == 200

    def test_shows_zulip_warning_when_unconfigured(self, logged_in_client, monkeypatch):
        for k in ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE"):
            monkeypatch.delenv(k, raising=False)
        resp = logged_in_client.get("/")
        assert b"not configured" in resp.data

    def test_lists_upcoming_reminders(self, logged_in_client):
        dt = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add_reminder("check email", dt, user_id=TEST_USERNAME)
        resp = logged_in_client.get("/")
        assert b"check email" in resp.data

    def test_shows_past_reminders(self, logged_in_client):
        rid = db.add_reminder("old task", datetime.now(timezone.utc) + timedelta(hours=1),
                               user_id=TEST_USERNAME)
        db.mark_notified(rid)
        resp = logged_in_client.get("/")
        assert b"old task" in resp.data

    def test_does_not_show_other_users_reminders(self, logged_in_client):
        dt = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add_reminder("secret task", dt, user_id="otheruser")
        resp = logged_in_client.get("/")
        assert b"secret task" not in resp.data

    def test_shows_username_in_header(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert TEST_USERNAME.encode() in resp.data


# ---------------------------------------------------------------------------
# POST /reminders
# ---------------------------------------------------------------------------

class TestCreateReminder:
    def test_valid_input_redirects(self, logged_in_client):
        resp = logged_in_client.post(
            "/reminders",
            data={"task": "buy milk", "when": "in 1 hour", "recurrence": "none"},
        )
        assert resp.status_code == 302

    def test_valid_input_persists_reminder(self, logged_in_client):
        logged_in_client.post("/reminders", data={"task": "buy milk", "when": "in 1 hour"})
        upcoming = db.get_upcoming(TEST_USERNAME)
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "buy milk"

    def test_reminder_stored_with_current_user_id(self, logged_in_client):
        logged_in_client.post("/reminders", data={"task": "standup", "when": "in 1 hour"})
        upcoming = db.get_upcoming(TEST_USERNAME)
        assert upcoming[0]["user_id"] == TEST_USERNAME

    def test_reminder_stored_with_current_user_email(self, logged_in_client):
        logged_in_client.post("/reminders", data={"task": "standup", "when": "in 1 hour"})
        upcoming = db.get_upcoming(TEST_USERNAME)
        assert upcoming[0]["user_email"] == TEST_USER_EMAIL

    def test_missing_task_shows_error(self, logged_in_client):
        resp = logged_in_client.post(
            "/reminders",
            data={"task": "", "when": "in 1 hour"},
            follow_redirects=True,
        )
        assert b"task" in resp.data.lower()

    def test_missing_when_shows_error(self, logged_in_client):
        resp = logged_in_client.post(
            "/reminders",
            data={"task": "buy milk", "when": ""},
            follow_redirects=True,
        )
        assert b"time" in resp.data.lower()

    def test_unparseable_when_shows_error(self, logged_in_client):
        resp = logged_in_client.post(
            "/reminders",
            data={"task": "buy milk", "when": "qwerty12345notadate"},
            follow_redirects=True,
        )
        assert b"Could not understand" in resp.data

    def test_past_time_shows_error(self, logged_in_client):
        resp = logged_in_client.post(
            "/reminders",
            data={"task": "buy milk", "when": "January 1, 2020 at noon"},
            follow_redirects=True,
        )
        assert b"past" in resp.data.lower()

    def test_recurrence_stored_correctly(self, logged_in_client):
        logged_in_client.post(
            "/reminders",
            data={"task": "standup", "when": "in 1 hour", "recurrence": "daily"},
        )
        assert db.get_upcoming(TEST_USERNAME)[0]["recurrence"] == "daily"

    @pytest.mark.parametrize("recurrence", [
        "hourly", "daily", "every2days", "weekly", "biweekly", "monthly", "quarterly", "yearly",
    ])
    def test_all_valid_recurrences_accepted(self, logged_in_client, recurrence):
        logged_in_client.post(
            "/reminders",
            data={"task": "task", "when": "in 1 hour", "recurrence": recurrence},
        )
        assert db.get_upcoming(TEST_USERNAME)[0]["recurrence"] == recurrence

    def test_invalid_recurrence_falls_back_to_none(self, logged_in_client):
        logged_in_client.post(
            "/reminders",
            data={"task": "task", "when": "in 1 hour", "recurrence": "fortnightly"},
        )
        assert db.get_upcoming(TEST_USERNAME)[0]["recurrence"] == "none"

    def test_missing_recurrence_defaults_to_none(self, logged_in_client):
        logged_in_client.post("/reminders", data={"task": "task", "when": "in 1 hour"})
        assert db.get_upcoming(TEST_USERNAME)[0]["recurrence"] == "none"

    def test_unauthenticated_redirects_to_login(self, app_client):
        resp = app_client.post("/reminders", data={"task": "t", "when": "in 1 hour"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# DELETE /reminders/<id>
# ---------------------------------------------------------------------------

class TestDeleteReminder:
    def test_returns_204(self, logged_in_client):
        rid = db.add_reminder("temp", datetime.now(timezone.utc) + timedelta(hours=1),
                               user_id=TEST_USERNAME)
        resp = logged_in_client.delete(f"/reminders/{rid}")
        assert resp.status_code == 204

    def test_removes_reminder_from_db(self, logged_in_client):
        rid = db.add_reminder("temp", datetime.now(timezone.utc) + timedelta(hours=1),
                               user_id=TEST_USERNAME)
        logged_in_client.delete(f"/reminders/{rid}")
        assert db.get_upcoming(TEST_USERNAME) == []

    def test_nonexistent_id_returns_204(self, logged_in_client):
        resp = logged_in_client.delete("/reminders/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 204

    def test_cannot_delete_another_users_reminder(self, logged_in_client):
        rid = db.add_reminder("other user task",
                               datetime.now(timezone.utc) + timedelta(hours=1),
                               user_id="otheruser")
        logged_in_client.delete(f"/reminders/{rid}")
        # Reminder should still exist (delete was silently scoped to testuser)
        assert len(db.get_upcoming("otheruser")) == 1

    def test_unauthenticated_redirects_to_login(self, app_client):
        resp = app_client.delete("/reminders/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# sweep()
# ---------------------------------------------------------------------------

class TestSweep:
    def test_sends_notification_for_due_reminder(self, temp_db, mock_zulip_send):
        db.add_reminder("one-time", datetime.now(timezone.utc) - timedelta(minutes=1),
                         user_id=TEST_USERNAME, user_email=TEST_USER_EMAIL)
        flask_app.sweep()
        mock_zulip_send.assert_called_once()
        args = mock_zulip_send.call_args[0]
        assert args[0] == "one-time"
        assert "/snooze/" in args[1]

    def test_sends_to_per_user_email(self, temp_db, mock_zulip_send):
        db.add_reminder("one-time", datetime.now(timezone.utc) - timedelta(minutes=1),
                         user_id=TEST_USERNAME, user_email=TEST_USER_EMAIL)
        flask_app.sweep()
        kwargs = mock_zulip_send.call_args[1]
        assert kwargs.get("to_email") == TEST_USER_EMAIL

    def test_marks_one_time_reminder_as_notified(self, temp_db, mock_zulip_send):
        db.add_reminder("one-time", datetime.now(timezone.utc) - timedelta(minutes=1),
                         user_id=TEST_USERNAME)
        flask_app.sweep()
        assert db.get_upcoming(TEST_USERNAME) == []
        assert len(db.get_past(TEST_USERNAME)) == 1

    def test_skips_future_reminders(self, temp_db, mock_zulip_send):
        db.add_reminder("future", datetime.now(timezone.utc) + timedelta(hours=1),
                         user_id=TEST_USERNAME)
        flask_app.sweep()
        mock_zulip_send.assert_not_called()

    def test_skips_already_notified_reminders(self, temp_db, mock_zulip_send):
        rid = db.add_reminder("done", datetime.now(timezone.utc) - timedelta(minutes=1),
                               user_id=TEST_USERNAME)
        db.mark_notified(rid)
        flask_app.sweep()
        mock_zulip_send.assert_not_called()

    def test_handles_notification_failure_gracefully(self, temp_db, mock_zulip_send):
        mock_zulip_send.side_effect = Exception("Zulip is down")
        db.add_reminder("task", datetime.now(timezone.utc) - timedelta(minutes=1),
                         user_id=TEST_USERNAME)
        flask_app.sweep()  # must not raise

    def test_reschedules_daily_reminder_by_one_day(self, temp_db, mock_zulip_send):
        original = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.add_reminder("standup", original, recurrence="daily", user_id=TEST_USERNAME)
        flask_app.sweep()

        upcoming = db.get_upcoming(TEST_USERNAME)
        assert len(upcoming) == 1
        assert upcoming[0]["notified_at"] is None

        new_dt = datetime.fromisoformat(upcoming[0]["remind_at"])
        expected = original + timedelta(days=1)
        assert abs((new_dt - expected).total_seconds()) < 1

    def test_reschedules_weekly_reminder_by_seven_days(self, temp_db, mock_zulip_send):
        original = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.add_reminder("weekly review", original, recurrence="weekly", user_id=TEST_USERNAME)
        flask_app.sweep()

        upcoming = db.get_upcoming(TEST_USERNAME)
        assert len(upcoming) == 1

        new_dt = datetime.fromisoformat(upcoming[0]["remind_at"])
        expected = original + timedelta(weeks=1)
        assert abs((new_dt - expected).total_seconds()) < 1

    def test_reschedules_monthly_reminder_by_one_month(self, temp_db, mock_zulip_send):
        from dateutil.relativedelta import relativedelta
        original = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
        rid = db.add_reminder("monthly report", original, recurrence="monthly",
                               user_id=TEST_USERNAME)
        import sqlite3
        conn = sqlite3.connect(str(temp_db))
        past_dt = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (past_dt, rid))
        conn.commit()
        conn.close()

        flask_app.sweep()

        upcoming = db.get_upcoming(TEST_USERNAME)
        assert len(upcoming) == 1

        new_dt = datetime.fromisoformat(upcoming[0]["remind_at"])
        stored_past = datetime.fromisoformat(past_dt)
        expected = stored_past + relativedelta(months=1)
        assert abs((new_dt - expected).total_seconds()) < 1

    def test_recurring_reminder_stays_upcoming_after_sweep(self, temp_db, mock_zulip_send):
        db.add_reminder("daily", datetime.now(timezone.utc) - timedelta(minutes=1),
                         recurrence="daily", user_id=TEST_USERNAME)
        flask_app.sweep()
        assert len(db.get_upcoming(TEST_USERNAME)) == 1
        assert db.get_past(TEST_USERNAME) == []

    def test_processes_multiple_due_reminders(self, temp_db, mock_zulip_send):
        for i in range(3):
            db.add_reminder(f"task {i}", datetime.now(timezone.utc) - timedelta(minutes=1),
                             user_id=TEST_USERNAME)
        flask_app.sweep()
        assert mock_zulip_send.call_count == 3

    def test_sweep_appends_task_and_token_to_queue(self, temp_db, mock_zulip_send):
        rid = db.add_reminder("buy milk", datetime.now(timezone.utc) - timedelta(minutes=1),
                               user_id=TEST_USERNAME)
        flask_app.sweep()
        queue = json.loads(db.get_meta("snooze_queue"))
        assert len(queue) == 1
        assert queue[0]["task"] == "buy milk"
        assert queue[0]["token"] == rid[:8]

    def test_sweep_stores_user_info_in_queue(self, temp_db, mock_zulip_send):
        db.add_reminder("buy milk", datetime.now(timezone.utc) - timedelta(minutes=1),
                         user_id=TEST_USERNAME, user_email=TEST_USER_EMAIL)
        flask_app.sweep()
        queue = json.loads(db.get_meta("snooze_queue"))
        assert queue[0]["user_id"] == TEST_USERNAME
        assert queue[0]["user_email"] == TEST_USER_EMAIL

    def test_sweep_appends_all_notified_tasks_to_queue(self, temp_db, mock_zulip_send):
        earlier = datetime.now(timezone.utc) - timedelta(minutes=2)
        later = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add_reminder("first task", earlier, user_id=TEST_USERNAME)
        db.add_reminder("second task", later, user_id=TEST_USERNAME)
        flask_app.sweep()
        queue = json.loads(db.get_meta("snooze_queue"))
        tasks = [item["task"] for item in queue]
        assert "first task" in tasks
        assert "second task" in tasks

    def test_snooze_url_contains_token(self, temp_db, mock_zulip_send):
        rid = db.add_reminder("call mom", datetime.now(timezone.utc) - timedelta(minutes=1),
                               user_id=TEST_USERNAME)
        flask_app.sweep()
        task, snooze_url = mock_zulip_send.call_args[0]
        assert rid[:8] in snooze_url
        assert "/snooze/" in snooze_url

    def test_snooze_url_uses_base_url_env(self, temp_db, mock_zulip_send, monkeypatch):
        monkeypatch.setenv("BASE_URL", "https://remind.example.com")
        db.add_reminder("standup", datetime.now(timezone.utc) - timedelta(minutes=1),
                         user_id=TEST_USERNAME)
        flask_app.sweep()
        task, snooze_url = mock_zulip_send.call_args[0]
        assert snooze_url.startswith("https://remind.example.com/snooze/")


# ---------------------------------------------------------------------------
# parse_snooze_duration
# ---------------------------------------------------------------------------

class TestParseSnooze:
    def test_parses_hour_abbreviation(self):
        result = flask_app.parse_snooze_duration("1h")
        assert result is not None
        expected = datetime.now(timezone.utc) + timedelta(hours=1)
        assert abs((result - expected).total_seconds()) < 5

    def test_parses_minute_abbreviation(self):
        result = flask_app.parse_snooze_duration("30m")
        assert result is not None
        expected = datetime.now(timezone.utc) + timedelta(minutes=30)
        assert abs((result - expected).total_seconds()) < 5

    def test_parses_day_abbreviation(self):
        result = flask_app.parse_snooze_duration("2d")
        assert result is not None
        expected = datetime.now(timezone.utc) + timedelta(days=2)
        assert abs((result - expected).total_seconds()) < 5

    def test_parses_natural_language(self):
        result = flask_app.parse_snooze_duration("2 hours")
        assert result is not None

    def test_returns_none_for_garbage(self):
        assert flask_app.parse_snooze_duration("notadate!!!") is None


# ---------------------------------------------------------------------------
# GET /snooze/<token>
# ---------------------------------------------------------------------------

class TestSnoozeView:
    def _add_queue_entry(self, task, token, user_id="", user_email=""):
        now = datetime.now(timezone.utc).isoformat()
        db.set_meta("snooze_queue", json.dumps([
            {"task": task, "token": token, "sent_at": now,
             "user_id": user_id, "user_email": user_email}
        ]))

    def test_valid_token_returns_200(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.get("/snooze/aabbccdd")
        assert resp.status_code == 200

    def test_shows_task_name(self, app_client):
        self._add_queue_entry("buy groceries", "aabbccdd")
        resp = app_client.get("/snooze/aabbccdd")
        assert b"buy groceries" in resp.data

    def test_shows_preset_durations(self, app_client):
        self._add_queue_entry("standup", "aabbccdd")
        resp = app_client.get("/snooze/aabbccdd")
        assert b"1 hour" in resp.data or b"30 min" in resp.data

    def test_expired_token_returns_404(self, app_client):
        resp = app_client.get("/snooze/00000000")
        assert resp.status_code == 404

    def test_expired_token_shows_message(self, app_client):
        resp = app_client.get("/snooze/00000000")
        assert b"expired" in resp.data.lower() or b"not found" in resp.data.lower()

    def test_expired_queue_entry_returns_404(self, app_client):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        db.set_meta("snooze_queue", json.dumps([
            {"task": "old task", "token": "aabbccdd", "sent_at": old_time,
             "user_id": "", "user_email": ""}
        ]))
        resp = app_client.get("/snooze/aabbccdd")
        assert resp.status_code == 404

    def test_snooze_accessible_without_login(self, app_client):
        """Snooze links from Zulip must work without an active session."""
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.get("/snooze/aabbccdd")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /snooze/<token>
# ---------------------------------------------------------------------------

class TestSnoozeSubmit:
    def _add_queue_entry(self, task, token, user_id="", user_email=""):
        now = datetime.now(timezone.utc).isoformat()
        db.set_meta("snooze_queue", json.dumps([
            {"task": task, "token": token, "sent_at": now,
             "user_id": user_id, "user_email": user_email}
        ]))

    def test_preset_creates_reminder(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd",
                               user_id=TEST_USERNAME, user_email=TEST_USER_EMAIL)
        resp = app_client.post("/snooze/aabbccdd", data={"minutes": "60"})
        assert resp.status_code == 200
        upcoming = db.get_upcoming(TEST_USERNAME)
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "call mom"

    def test_snoozed_reminder_assigned_to_original_user(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd",
                               user_id=TEST_USERNAME, user_email=TEST_USER_EMAIL)
        app_client.post("/snooze/aabbccdd", data={"minutes": "60"})
        upcoming = db.get_upcoming(TEST_USERNAME)
        assert upcoming[0]["user_id"] == TEST_USERNAME
        assert upcoming[0]["user_email"] == TEST_USER_EMAIL

    def test_preset_schedules_correct_duration(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        before = datetime.now(timezone.utc)
        app_client.post("/snooze/aabbccdd", data={"minutes": "30"})
        after = datetime.now(timezone.utc)
        upcoming = db.get_upcoming()
        remind_at = datetime.fromisoformat(upcoming[0]["remind_at"])
        assert abs((remind_at - before).total_seconds() - 1800) < 5
        assert remind_at <= after + timedelta(minutes=30, seconds=5)

    def test_reminder_set_with_no_recurrence(self, app_client):
        self._add_queue_entry("task", "aabbccdd")
        app_client.post("/snooze/aabbccdd", data={"minutes": "60"})
        assert db.get_upcoming()[0]["recurrence"] == "none"

    def test_token_removed_from_queue_after_snooze(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        app_client.post("/snooze/aabbccdd", data={"minutes": "60"})
        queue = json.loads(db.get_meta("snooze_queue"))
        assert all(item["token"] != "aabbccdd" for item in queue)

    def test_only_snoozed_token_removed_from_queue(self, app_client):
        now = datetime.now(timezone.utc).isoformat()
        db.set_meta("snooze_queue", json.dumps([
            {"task": "task A", "token": "aaaaaaaa", "sent_at": now,
             "user_id": "", "user_email": ""},
            {"task": "task B", "token": "bbbbbbbb", "sent_at": now,
             "user_id": "", "user_email": ""},
        ]))
        app_client.post("/snooze/aaaaaaaa", data={"minutes": "60"})
        queue = json.loads(db.get_meta("snooze_queue"))
        assert len(queue) == 1
        assert queue[0]["token"] == "bbbbbbbb"

    def test_shows_confirmation_page(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.post("/snooze/aabbccdd", data={"minutes": "60"})
        assert b"call mom" in resp.data
        assert b"Snoozed" in resp.data or b"snoozed" in resp.data.lower()

    def test_custom_duration_creates_reminder(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.post(
            "/snooze/aabbccdd",
            data={"minutes": "custom", "custom_when": "in 2 hours"},
        )
        assert resp.status_code == 200
        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "call mom"

    def test_custom_duration_empty_shows_error(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.post(
            "/snooze/aabbccdd",
            data={"minutes": "custom", "custom_when": ""},
            follow_redirects=True,
        )
        assert db.get_upcoming() == []
        assert b"enter" in resp.data.lower() or b"custom" in resp.data.lower()

    def test_custom_duration_unparseable_shows_error(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.post(
            "/snooze/aabbccdd",
            data={"minutes": "custom", "custom_when": "notadate!!!"},
            follow_redirects=True,
        )
        assert db.get_upcoming() == []
        assert b"Could not understand" in resp.data

    def test_missing_minutes_shows_error(self, app_client):
        self._add_queue_entry("call mom", "aabbccdd")
        resp = app_client.post(
            "/snooze/aabbccdd",
            data={},
            follow_redirects=True,
        )
        assert db.get_upcoming() == []

    def test_expired_token_returns_404(self, app_client):
        resp = app_client.post("/snooze/00000000", data={"minutes": "60"})
        assert resp.status_code == 404
