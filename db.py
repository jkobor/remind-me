import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> str:
    raw = os.environ.get("DATABASE_PATH", "~/.remind-me/reminders.db")
    path = Path(raw).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          TEXT PRIMARY KEY,
                task        TEXT NOT NULL,
                remind_at   TEXT NOT NULL,
                notified_at TEXT,
                recurrence  TEXT NOT NULL DEFAULT 'none',
                user_id     TEXT NOT NULL DEFAULT '',
                user_email  TEXT NOT NULL DEFAULT ''
            )
        """)
        # Migrate databases that predate individual columns.
        for col, definition in (
            ("recurrence", "TEXT NOT NULL DEFAULT 'none'"),
            ("user_id",    "TEXT NOT NULL DEFAULT ''"),
            ("user_email", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE reminders ADD COLUMN {col} {definition}")
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()


def get_meta(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def add_reminder(
    task: str,
    remind_at: datetime,
    recurrence: str = "none",
    user_id: str = "",
    user_email: str = "",
) -> str:
    reminder_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reminders (id, task, remind_at, recurrence, user_id, user_email) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (reminder_id, task, remind_at.isoformat(), recurrence, user_id, user_email),
        )
        conn.commit()
    return reminder_id


def get_upcoming(user_id: str = "") -> list[sqlite3.Row]:
    """Return pending reminders, optionally filtered to a single user."""
    with _connect() as conn:
        if user_id:
            return conn.execute(
                "SELECT * FROM reminders WHERE notified_at IS NULL AND user_id = ? "
                "ORDER BY remind_at ASC",
                (user_id,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM reminders WHERE notified_at IS NULL ORDER BY remind_at ASC"
        ).fetchall()


def get_past(user_id: str = "") -> list[sqlite3.Row]:
    """Return notified reminders, optionally filtered to a single user."""
    with _connect() as conn:
        if user_id:
            return conn.execute(
                "SELECT * FROM reminders WHERE notified_at IS NOT NULL AND user_id = ? "
                "ORDER BY notified_at DESC",
                (user_id,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM reminders WHERE notified_at IS NOT NULL ORDER BY notified_at DESC"
        ).fetchall()


def get_due_reminders() -> list[sqlite3.Row]:
    """Return all due reminders across all users (used by the sweep job)."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE notified_at IS NULL AND remind_at <= ?",
            (now,),
        ).fetchall()


def mark_notified(reminder_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE reminders SET notified_at = ? WHERE id = ?",
            (now, reminder_id),
        )
        conn.commit()


def reschedule_reminder(reminder_id: str, next_remind_at: datetime):
    with _connect() as conn:
        conn.execute(
            "UPDATE reminders SET remind_at = ?, notified_at = NULL WHERE id = ?",
            (next_remind_at.isoformat(), reminder_id),
        )
        conn.commit()


def delete_reminder(reminder_id: str, user_id: str = ""):
    """Delete a reminder. If *user_id* is given, only delete if it matches."""
    with _connect() as conn:
        if user_id:
            conn.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
        else:
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
