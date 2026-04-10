"""Tests for db.py – schema, CRUD, and query helpers."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import db


def _future(hours=1):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _past(hours=1):
    return datetime.now(timezone.utc) - timedelta(hours=hours)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_reminders_table(self, temp_db):
        conn = sqlite3.connect(str(temp_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "reminders" in tables

    def test_creates_expected_columns(self, temp_db):
        conn = sqlite3.connect(str(temp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
        conn.close()
        assert cols == {"id", "task", "remind_at", "notified_at", "recurrence"}

    def test_migration_adds_recurrence_to_old_schema(self, temp_db):
        """A DB created before the recurrence column exists should be migrated."""
        conn = sqlite3.connect(str(temp_db))
        conn.execute("DROP TABLE IF EXISTS reminders")
        conn.execute("""
            CREATE TABLE reminders (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                notified_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        db.init_db()  # should add the column without raising

        conn = sqlite3.connect(str(temp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
        conn.close()
        assert "recurrence" in cols

    def test_idempotent(self, temp_db):
        """Calling init_db twice must not raise."""
        db.init_db()


# ---------------------------------------------------------------------------
# add_reminder
# ---------------------------------------------------------------------------

class TestAddReminder:
    def test_returns_uuid(self, temp_db):
        rid = db.add_reminder("task", _future())
        assert len(rid) == 36
        assert rid.count("-") == 4

    def test_default_recurrence_is_none(self, temp_db):
        rid = db.add_reminder("task", _future())
        row = db.get_upcoming()[0]
        assert row["recurrence"] == "none"

    def test_custom_recurrence_stored(self, temp_db):
        db.add_reminder("task", _future(), recurrence="weekly")
        row = db.get_upcoming()[0]
        assert row["recurrence"] == "weekly"

    def test_task_stored_correctly(self, temp_db):
        db.add_reminder("call mom", _future())
        assert db.get_upcoming()[0]["task"] == "call mom"

    def test_remind_at_stored_as_isoformat(self, temp_db):
        dt = _future()
        db.add_reminder("task", dt)
        stored = db.get_upcoming()[0]["remind_at"]
        assert stored == dt.isoformat()


# ---------------------------------------------------------------------------
# get_upcoming
# ---------------------------------------------------------------------------

class TestGetUpcoming:
    def test_returns_unnotified_reminders(self, temp_db):
        db.add_reminder("task", _future())
        assert len(db.get_upcoming()) == 1

    def test_excludes_notified_reminders(self, temp_db):
        rid = db.add_reminder("task", _future())
        db.mark_notified(rid)
        assert db.get_upcoming() == []

    def test_ordered_by_remind_at_ascending(self, temp_db):
        db.add_reminder("later", _future(hours=3))
        db.add_reminder("sooner", _future(hours=1))
        db.add_reminder("middle", _future(hours=2))
        tasks = [r["task"] for r in db.get_upcoming()]
        assert tasks == ["sooner", "middle", "later"]

    def test_empty_when_no_reminders(self, temp_db):
        assert db.get_upcoming() == []


# ---------------------------------------------------------------------------
# get_past
# ---------------------------------------------------------------------------

class TestGetPast:
    def test_returns_notified_reminders(self, temp_db):
        rid = db.add_reminder("done", _future())
        db.mark_notified(rid)
        past = db.get_past()
        assert len(past) == 1
        assert past[0]["task"] == "done"

    def test_excludes_unnotified_reminders(self, temp_db):
        db.add_reminder("upcoming", _future())
        assert db.get_past() == []

    def test_empty_when_no_reminders(self, temp_db):
        assert db.get_past() == []


# ---------------------------------------------------------------------------
# get_due_reminders
# ---------------------------------------------------------------------------

class TestGetDueReminders:
    def test_past_remind_at_is_due(self, temp_db):
        db.add_reminder("overdue", _past())
        assert len(db.get_due_reminders()) == 1

    def test_future_remind_at_not_due(self, temp_db):
        db.add_reminder("future", _future())
        assert db.get_due_reminders() == []

    def test_already_notified_not_due(self, temp_db):
        rid = db.add_reminder("done", _past())
        db.mark_notified(rid)
        assert db.get_due_reminders() == []

    def test_empty_when_no_reminders(self, temp_db):
        assert db.get_due_reminders() == []


# ---------------------------------------------------------------------------
# mark_notified
# ---------------------------------------------------------------------------

class TestMarkNotified:
    def test_sets_notified_at(self, temp_db):
        rid = db.add_reminder("task", _future())
        db.mark_notified(rid)
        past = db.get_past()
        assert len(past) == 1
        assert past[0]["notified_at"] is not None

    def test_removes_from_upcoming(self, temp_db):
        rid = db.add_reminder("task", _future())
        db.mark_notified(rid)
        assert db.get_upcoming() == []


# ---------------------------------------------------------------------------
# reschedule_reminder
# ---------------------------------------------------------------------------

class TestRescheduleReminder:
    def test_updates_remind_at(self, temp_db):
        rid = db.add_reminder("task", _future(hours=1))
        new_dt = _future(hours=25)
        db.reschedule_reminder(rid, new_dt)
        assert db.get_upcoming()[0]["remind_at"] == new_dt.isoformat()

    def test_clears_notified_at(self, temp_db):
        rid = db.add_reminder("task", _past())
        db.mark_notified(rid)
        db.reschedule_reminder(rid, _future())
        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["notified_at"] is None

    def test_reminder_reappears_in_upcoming(self, temp_db):
        rid = db.add_reminder("task", _past())
        db.mark_notified(rid)
        assert db.get_upcoming() == []
        db.reschedule_reminder(rid, _future())
        assert len(db.get_upcoming()) == 1


# ---------------------------------------------------------------------------
# delete_reminder
# ---------------------------------------------------------------------------

class TestDeleteReminder:
    def test_deletes_existing_row(self, temp_db):
        rid = db.add_reminder("task", _future())
        db.delete_reminder(rid)
        assert db.get_upcoming() == []

    def test_nonexistent_id_is_noop(self, temp_db):
        db.delete_reminder("00000000-0000-0000-0000-000000000000")  # must not raise

    def test_only_deletes_target_row(self, temp_db):
        rid1 = db.add_reminder("keep", _future())
        rid2 = db.add_reminder("delete me", _future())
        db.delete_reminder(rid2)
        upcoming = db.get_upcoming()
        assert len(upcoming) == 1
        assert upcoming[0]["id"] == rid1


# ---------------------------------------------------------------------------
# meta key-value store
# ---------------------------------------------------------------------------

class TestMeta:
    def test_get_meta_returns_none_when_missing(self, temp_db):
        assert db.get_meta("nonexistent_key") is None

    def test_set_and_get_meta_roundtrip(self, temp_db):
        db.set_meta("my_key", "my_value")
        assert db.get_meta("my_key") == "my_value"

    def test_set_meta_overwrites_existing(self, temp_db):
        db.set_meta("k", "first")
        db.set_meta("k", "second")
        assert db.get_meta("k") == "second"

    def test_multiple_keys_are_independent(self, temp_db):
        db.set_meta("a", "1")
        db.set_meta("b", "2")
        assert db.get_meta("a") == "1"
        assert db.get_meta("b") == "2"
