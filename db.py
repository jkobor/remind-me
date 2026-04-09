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
                notified_at TEXT
            )
        """)
        conn.commit()


def add_reminder(task: str, remind_at: datetime) -> str:
    reminder_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reminders (id, task, remind_at) VALUES (?, ?, ?)",
            (reminder_id, task, remind_at.isoformat()),
        )
        conn.commit()
    return reminder_id


def get_upcoming() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE notified_at IS NULL ORDER BY remind_at ASC"
        ).fetchall()


def get_past() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE notified_at IS NOT NULL ORDER BY notified_at DESC"
        ).fetchall()


def get_due_reminders() -> list[sqlite3.Row]:
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


def delete_reminder(reminder_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
