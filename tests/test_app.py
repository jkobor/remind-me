"""Tests for Flask routes and the sweep background job."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import app as flask_app
import db


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestIndex:
    def test_returns_200(self, app_client):
        assert app_client.get("/").status_code == 200

    def test_shows_zulip_warning_when_unconfigured(self, app_client, monkeypatch):
        for k in ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE", "ZULIP_TO"):
            monkeypatch.delenv(k, raising=False)
        resp = app_client.get("/")
        assert b"not configured" in resp.data

    def test_lists_upcoming_reminders(self, app_client):
        dt = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add_reminder("check email", dt)
        resp = app_client.get("/")
        assert b"check email" in resp.data

    def test_shows_past_reminders(self, app_client):
        rid = db.add_reminder("old task", datetime.now(timezone.utc) + timedelta(hours=1))
        db.mark_notified(rid)
        resp = app_client.get("/")
        assert b"old task" in resp.data


# ---------------------------------------------------------------------------
# POST /reminders
# ---------------------------------------------------------------------------

class TestCreateReminder:
    def test_valid_input_redirects(self, app_client):
        resp = app_client.post(
            "/reminders",
            data={"task": "buy milk", "when": "in 1 hour", "recurrence": "none"},
        )
        assert resp.status_code == 302

    def test_valid_input_persists_reminder(self, app_client):
        app_client.post("/reminders", data={"task": "buy milk", "when": "in 1 hour"})
        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "buy milk"

    def test_missing_task_shows_error(self, app_client):
        resp = app_client.post(
            "/reminders",
            data={"task": "", "when": "in 1 hour"},
            follow_redirects=True,
        )
        assert b"task" in resp.data.lower()

    def test_missing_when_shows_error(self, app_client):
        resp = app_client.post(
            "/reminders",
            data={"task": "buy milk", "when": ""},
            follow_redirects=True,
        )
        assert b"time" in resp.data.lower()

    def test_unparseable_when_shows_error(self, app_client):
        resp = app_client.post(
            "/reminders",
            data={"task": "buy milk", "when": "qwerty12345notadate"},
            follow_redirects=True,
        )
        assert b"Could not understand" in resp.data

    def test_past_time_shows_error(self, app_client):
        resp = app_client.post(
            "/reminders",
            data={"task": "buy milk", "when": "January 1, 2020 at noon"},
            follow_redirects=True,
        )
        assert b"past" in resp.data.lower()

    def test_recurrence_stored_correctly(self, app_client):
        app_client.post(
            "/reminders",
            data={"task": "standup", "when": "in 1 hour", "recurrence": "daily"},
        )
        assert db.get_upcoming()[0]["recurrence"] == "daily"

    @pytest.mark.parametrize("recurrence", [
        "hourly", "daily", "every2days", "weekly", "biweekly", "monthly", "quarterly", "yearly",
    ])
    def test_all_valid_recurrences_accepted(self, app_client, recurrence):
        app_client.post(
            "/reminders",
            data={"task": "task", "when": "in 1 hour", "recurrence": recurrence},
        )
        assert db.get_upcoming()[0]["recurrence"] == recurrence

    def test_invalid_recurrence_falls_back_to_none(self, app_client):
        app_client.post(
            "/reminders",
            data={"task": "task", "when": "in 1 hour", "recurrence": "fortnightly"},
        )
        assert db.get_upcoming()[0]["recurrence"] == "none"

    def test_missing_recurrence_defaults_to_none(self, app_client):
        app_client.post("/reminders", data={"task": "task", "when": "in 1 hour"})
        assert db.get_upcoming()[0]["recurrence"] == "none"


# ---------------------------------------------------------------------------
# DELETE /reminders/<id>
# ---------------------------------------------------------------------------

class TestDeleteReminder:
    def test_returns_204(self, app_client):
        rid = db.add_reminder("temp", datetime.now(timezone.utc) + timedelta(hours=1))
        resp = app_client.delete(f"/reminders/{rid}")
        assert resp.status_code == 204

    def test_removes_reminder_from_db(self, app_client):
        rid = db.add_reminder("temp", datetime.now(timezone.utc) + timedelta(hours=1))
        app_client.delete(f"/reminders/{rid}")
        assert db.get_upcoming() == []

    def test_nonexistent_id_returns_204(self, app_client):
        resp = app_client.delete("/reminders/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# sweep()
# ---------------------------------------------------------------------------

class TestSweep:
    def test_sends_notification_for_due_reminder(self, temp_db, mock_zulip_send):
        db.add_reminder("one-time", datetime.now(timezone.utc) - timedelta(minutes=1))
        flask_app.sweep()
        mock_zulip_send.assert_called_once()
        args = mock_zulip_send.call_args[0]
        assert args[0] == "one-time"
        assert len(args[1]) == 8  # snooze token is 8 hex chars

    def test_marks_one_time_reminder_as_notified(self, temp_db, mock_zulip_send):
        db.add_reminder("one-time", datetime.now(timezone.utc) - timedelta(minutes=1))
        flask_app.sweep()
        assert db.get_upcoming() == []
        assert len(db.get_past()) == 1

    def test_skips_future_reminders(self, temp_db, mock_zulip_send):
        db.add_reminder("future", datetime.now(timezone.utc) + timedelta(hours=1))
        flask_app.sweep()
        mock_zulip_send.assert_not_called()

    def test_skips_already_notified_reminders(self, temp_db, mock_zulip_send):
        rid = db.add_reminder("done", datetime.now(timezone.utc) - timedelta(minutes=1))
        db.mark_notified(rid)
        flask_app.sweep()
        mock_zulip_send.assert_not_called()

    def test_handles_notification_failure_gracefully(self, temp_db, mock_zulip_send):
        mock_zulip_send.side_effect = Exception("Zulip is down")
        db.add_reminder("task", datetime.now(timezone.utc) - timedelta(minutes=1))
        flask_app.sweep()  # must not raise

    def test_reschedules_daily_reminder_by_one_day(self, temp_db, mock_zulip_send):
        original = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.add_reminder("standup", original, recurrence="daily")
        flask_app.sweep()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["notified_at"] is None

        new_dt = datetime.fromisoformat(upcoming[0]["remind_at"])
        expected = original + timedelta(days=1)
        assert abs((new_dt - expected).total_seconds()) < 1

    def test_reschedules_weekly_reminder_by_seven_days(self, temp_db, mock_zulip_send):
        original = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.add_reminder("weekly review", original, recurrence="weekly")
        flask_app.sweep()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 1

        new_dt = datetime.fromisoformat(upcoming[0]["remind_at"])
        expected = original + timedelta(weeks=1)
        assert abs((new_dt - expected).total_seconds()) < 1

    def test_reschedules_monthly_reminder_by_one_month(self, temp_db, mock_zulip_send):
        from dateutil.relativedelta import relativedelta
        original = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
        # Make it "due" by setting remind_at to the past
        rid = db.add_reminder("monthly report", original, recurrence="monthly")
        # Overwrite remind_at to be in the past so sweep picks it up
        import sqlite3
        conn = sqlite3.connect(str(temp_db))
        past_dt = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (past_dt, rid))
        conn.commit()
        conn.close()

        flask_app.sweep()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 1

        new_dt = datetime.fromisoformat(upcoming[0]["remind_at"])
        # Should be past_dt + 1 month, not original + 1 month
        stored_past = datetime.fromisoformat(past_dt)
        expected = stored_past + relativedelta(months=1)
        assert abs((new_dt - expected).total_seconds()) < 1

    def test_recurring_reminder_stays_upcoming_after_sweep(self, temp_db, mock_zulip_send):
        db.add_reminder("daily", datetime.now(timezone.utc) - timedelta(minutes=1), recurrence="daily")
        flask_app.sweep()
        # Still in upcoming (rescheduled), not in past
        assert len(db.get_upcoming()) == 1
        assert db.get_past() == []

    def test_processes_multiple_due_reminders(self, temp_db, mock_zulip_send):
        for i in range(3):
            db.add_reminder(f"task {i}", datetime.now(timezone.utc) - timedelta(minutes=1))
        flask_app.sweep()
        assert mock_zulip_send.call_count == 3

    def test_sweep_appends_task_token_and_bot_msg_id_to_queue(self, temp_db, mock_zulip_send):
        mock_zulip_send.return_value = 99
        rid = db.add_reminder("buy milk", datetime.now(timezone.utc) - timedelta(minutes=1))
        flask_app.sweep()
        queue = json.loads(db.get_meta("snooze_queue"))
        assert len(queue) == 1
        assert queue[0]["task"] == "buy milk"
        assert queue[0]["token"] == rid[:8]
        assert queue[0]["bot_msg_id"] == 99

    def test_sweep_appends_all_notified_tasks_to_queue(self, temp_db, mock_zulip_send):
        earlier = datetime.now(timezone.utc) - timedelta(minutes=2)
        later = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add_reminder("first task", earlier)
        db.add_reminder("second task", later)
        flask_app.sweep()
        queue = json.loads(db.get_meta("snooze_queue"))
        tasks = [item["task"] for item in queue]
        assert "first task" in tasks
        assert "second task" in tasks


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
# process_snoozes
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_poll():
    """Default: no new messages, no anchor change."""
    with patch("notifiers.zulip_notifier.poll_snooze_commands", return_value=([], None)) as m:
        yield m


@pytest.fixture()
def mock_snooze_ack():
    with patch("notifiers.zulip_notifier.send_snooze_ack") as m:
        yield m


@pytest.fixture()
def zulip_configured(monkeypatch):
    """Set required Zulip env vars so is_configured() returns True."""
    for k, v in {
        "ZULIP_EMAIL": "bot@example.com",
        "ZULIP_API_KEY": "key",
        "ZULIP_SITE": "https://z.example.com",
        "ZULIP_TO": "user@example.com",
    }.items():
        monkeypatch.setenv(k, v)


def _queue(*entries):
    """Build a snooze_queue JSON value.

    Each entry is either a ``task`` string or a ``(task, token)`` tuple or a
    ``(task, token, bot_msg_id)`` tuple.  Defaults: token = ``f"{i:08x}"``,
    bot_msg_id = ``1000 + i``.
    """
    now = datetime.now(timezone.utc).isoformat()
    items = []
    for i, entry in enumerate(entries):
        if isinstance(entry, tuple) and len(entry) == 3:
            task, token, bot_msg_id = entry
        elif isinstance(entry, tuple):
            task, token = entry
            bot_msg_id = 1000 + i
        else:
            task, token, bot_msg_id = entry, f"{i:08x}", 1000 + i
        items.append({"task": task, "token": token, "bot_msg_id": bot_msg_id, "sent_at": now})
    return json.dumps(items)


class TestProcessSnoozes:
    def test_skips_when_zulip_not_configured(self, temp_db, monkeypatch):
        for k in ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE", "ZULIP_TO"):
            monkeypatch.delenv(k, raising=False)
        with patch("notifiers.zulip_notifier.poll_snooze_commands") as mock_poll:
            flask_app.process_snoozes()
            mock_poll.assert_not_called()

    def test_initialises_anchor_on_first_run(self, temp_db, zulip_configured, mock_snooze_ack):
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([], 99)
        ) as mock_poll:
            flask_app.process_snoozes()
            mock_poll.assert_called_once_with(None, exclude_ids=frozenset())
        assert db.get_meta("snooze_anchor_id") == "99"

    def test_passes_stored_anchor_on_subsequent_runs(self, temp_db, zulip_configured, mock_snooze_ack):
        db.set_meta("snooze_anchor_id", "50")
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([], 50)
        ) as mock_poll:
            flask_app.process_snoozes()
            mock_poll.assert_called_once_with(50, exclude_ids=frozenset())

    def test_creates_reminder_for_valid_snooze(self, temp_db, zulip_configured, mock_snooze_ack):
        db.set_meta("snooze_queue", _queue(("call mom", "aabbccdd")))
        msg = {"id": 10, "sender_email": "u@e.com", "content": "snooze aabbccdd 1h"}
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([msg], 10)
        ):
            flask_app.process_snoozes()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "call mom"
        assert upcoming[0]["recurrence"] == "none"

    def test_two_reminders_snoozed_independently(self, temp_db, zulip_configured, mock_snooze_ack):
        """Each reminder has its own token; two snooze replies target each one directly."""
        db.set_meta("snooze_queue", _queue(("call mom", "aaaaaaaa"), ("buy milk", "bbbbbbbb")))
        messages = [
            {"id": 10, "sender_email": "u@e.com", "content": "snooze bbbbbbbb 30m"},
            {"id": 11, "sender_email": "u@e.com", "content": "snooze aaaaaaaa 2h"},
        ]
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=(messages, 11)
        ):
            flask_app.process_snoozes()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 2
        tasks = {r["task"] for r in upcoming}
        assert tasks == {"call mom", "buy milk"}

    def test_snooze_targets_correct_reminder_by_token(
        self, temp_db, zulip_configured, mock_snooze_ack
    ):
        """Token in reply determines which queued reminder is rescheduled."""
        db.set_meta("snooze_queue", _queue(("task A", "aaaaaaaa"), ("task B", "bbbbbbbb")))
        # Only snooze task B
        messages = [{"id": 10, "sender_email": "u@e.com", "content": "snooze bbbbbbbb 1h"}]
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=(messages, 10)
        ):
            flask_app.process_snoozes()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "task B"

    def test_unknown_token_is_ignored(self, temp_db, zulip_configured, mock_snooze_ack):
        """A snooze reply with an unrecognised token creates no reminder."""
        db.set_meta("snooze_queue", _queue(("call mom", "aaaaaaaa")))
        msg = {"id": 10, "sender_email": "u@e.com", "content": "snooze 99999999 1h"}
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([msg], 10)
        ):
            flask_app.process_snoozes()
        assert db.get_upcoming() == []

    def test_unsnoozed_reminder_stays_in_queue(
        self, temp_db, zulip_configured, mock_snooze_ack
    ):
        """A queued reminder that wasn't referenced stays in the queue for later."""
        db.set_meta("snooze_queue", _queue(("task A", "aaaaaaaa"), ("task B", "bbbbbbbb")))
        messages = [{"id": 10, "sender_email": "u@e.com", "content": "snooze aaaaaaaa 1h"}]
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=(messages, 10)
        ):
            flask_app.process_snoozes()

        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "task A"

        remaining = json.loads(db.get_meta("snooze_queue"))
        assert len(remaining) == 1
        assert remaining[0]["task"] == "task B"

    def test_duplicate_snooze_for_same_token_ignored(
        self, temp_db, zulip_configured, mock_snooze_ack
    ):
        """Two replies referencing the same token → only one reminder created."""
        db.set_meta("snooze_queue", _queue(("call mom", "aaaaaaaa")))
        messages = [
            {"id": 10, "sender_email": "u@e.com", "content": "snooze aaaaaaaa 30m"},
            {"id": 11, "sender_email": "u@e.com", "content": "snooze aaaaaaaa 2h"},
        ]
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=(messages, 11)
        ):
            flask_app.process_snoozes()

        assert len(db.get_upcoming()) == 1

    def test_ignores_non_snooze_messages(self, temp_db, zulip_configured, mock_snooze_ack):
        db.set_meta("snooze_queue", _queue(("task", "aaaaaaaa")))
        msg = {"id": 10, "sender_email": "u@e.com", "content": "thanks!"}
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([msg], 10)
        ):
            flask_app.process_snoozes()
        assert db.get_upcoming() == []

    def test_ignores_unparseable_duration(self, temp_db, zulip_configured, mock_snooze_ack):
        db.set_meta("snooze_queue", _queue(("task", "aaaaaaaa")))
        msg = {"id": 10, "sender_email": "u@e.com", "content": "snooze aaaaaaaa banana"}
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([msg], 10)
        ):
            flask_app.process_snoozes()
        assert db.get_upcoming() == []

    def test_skips_when_snooze_queue_is_empty(self, temp_db, zulip_configured, mock_snooze_ack):
        msg = {"id": 10, "sender_email": "u@e.com", "content": "snooze aaaaaaaa 1h"}
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([msg], 10)
        ):
            flask_app.process_snoozes()
        assert db.get_upcoming() == []

    def test_sends_ack_per_snoozed_reminder(self, temp_db, zulip_configured, mock_snooze_ack):
        db.set_meta("snooze_queue", _queue(("call mom", "aaaaaaaa"), ("buy milk", "bbbbbbbb")))
        messages = [
            {"id": 10, "sender_email": "u@e.com", "content": "snooze aaaaaaaa 30m"},
            {"id": 11, "sender_email": "u@e.com", "content": "snooze bbbbbbbb 1h"},
        ]
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=(messages, 11)
        ):
            flask_app.process_snoozes()

        assert mock_snooze_ack.call_count == 2
        acked_tasks = {call[0][0] for call in mock_snooze_ack.call_args_list}
        assert acked_tasks == {"call mom", "buy milk"}

    def test_bot_message_ids_excluded_from_poll(self, temp_db, zulip_configured, mock_snooze_ack):
        """Bot-sent message IDs from the queue are forwarded to poll as exclude_ids.

        This is the fix for ZULIP_EMAIL == ZULIP_TO: even when all messages
        share the same sender, the bot's own reminders are never treated as
        snooze commands.
        """
        db.set_meta("snooze_queue", _queue(("call mom", "aaaaaaaa", 55)))
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([], None)
        ) as mock_poll:
            flask_app.process_snoozes()

        _, kwargs = mock_poll.call_args
        passed_exclude = mock_poll.call_args[0][1] if len(mock_poll.call_args[0]) > 1 else kwargs.get("exclude_ids", frozenset())
        assert 55 in passed_exclude

    def test_same_account_bot_message_not_processed_as_snooze(
        self, temp_db, zulip_configured, mock_snooze_ack
    ):
        """When ZULIP_EMAIL == ZULIP_TO the bot message ID is excluded so the
        reminder notification is never mistaken for a snooze command."""
        db.set_meta("snooze_queue", _queue(("call mom", "aaaaaaaa", 10)))
        # Simulate the DM thread returning the bot's own reminder message (id=10)
        # alongside the user's actual snooze reply (id=11).  Only id=11 should
        # be in commands because id=10 is in exclude_ids.
        bot_msg = {"id": 10, "sender_email": "self@example.com", "content": "snooze aaaaaaaa 1h"}
        user_reply = {"id": 11, "sender_email": "self@example.com", "content": "snooze aaaaaaaa 1h"}
        with patch(
            "notifiers.zulip_notifier.poll_snooze_commands", return_value=([user_reply], 11)
        ) as mock_poll:
            flask_app.process_snoozes()

        # The bot message (id=10) should have been in the exclude set passed to poll.
        exclude_ids = mock_poll.call_args[0][1] if len(mock_poll.call_args[0]) > 1 else mock_poll.call_args[1].get("exclude_ids", frozenset())
        assert 10 in exclude_ids

        # The user reply was processed and created a new reminder.
        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["task"] == "call mom"
