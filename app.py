import atexit
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import dateparser
from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

import db
from notifiers import zulip_notifier

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Background sweep
# ---------------------------------------------------------------------------

_RECURRENCE_DELTAS = {
    "daily":   lambda dt: dt + timedelta(days=1),
    "weekly":  lambda dt: dt + timedelta(weeks=1),
    "monthly": lambda dt: dt + relativedelta(months=1),
}

RECURRENCE_OPTIONS = [
    ("none",    "Once"),
    ("daily",   "Daily"),
    ("weekly",  "Weekly"),
    ("monthly", "Monthly"),
]

# Pattern matching "snooze <duration>" (case-insensitive).
_SNOOZE_RE = re.compile(r'^\s*snooze\s+(.+?)\s*$', re.IGNORECASE)

# Normalise short abbreviations so dateparser can handle them.
_ABBREV_RE = re.compile(
    r'(?<!\w)(\d+)\s*(h|hr|hrs|m|min|mins|d|dy|days?|w|wk|wks)\b',
    re.IGNORECASE,
)
_ABBREV_MAP = {
    'h': 'hours', 'hr': 'hours', 'hrs': 'hours',
    'm': 'minutes', 'min': 'minutes', 'mins': 'minutes',
    'd': 'days', 'dy': 'days', 'day': 'days', 'days': 'days',
    'w': 'weeks', 'wk': 'weeks', 'wks': 'weeks',
}


def parse_snooze_duration(text: str) -> datetime | None:
    """Parse a snooze duration string and return an absolute UTC datetime.

    Accepts abbreviations like ``1h``, ``30m``, ``2d`` as well as natural
    language understood by dateparser (``tomorrow``, ``in 2 hours``, …).
    Returns None if the text cannot be parsed or resolves to the past.
    """
    normalized = _ABBREV_RE.sub(
        lambda m: f"{m.group(1)} {_ABBREV_MAP[m.group(2).lower()]}",
        text,
    )
    result = dateparser.parse(
        normalized,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TO_TIMEZONE": "UTC",
        },
    )
    if result is None or result <= datetime.now(timezone.utc):
        return None
    return result


def process_snoozes():
    """Poll Zulip for snooze replies and reschedule the last-sent reminder.

    Only the *last* valid snooze command received since the previous sweep is
    acted upon, so multiple rapid replies don't create duplicate reminders.
    """
    if not zulip_notifier.is_configured():
        return

    anchor_str = db.get_meta("snooze_anchor_id")
    anchor = int(anchor_str) if anchor_str is not None else None

    messages, new_anchor = zulip_notifier.poll_snooze_commands(anchor)

    if new_anchor is not None:
        db.set_meta("snooze_anchor_id", str(new_anchor))

    if not messages:
        return

    task = db.get_meta("snooze_last_task")
    if not task:
        return

    # Collect valid snooze targets; act only on the last one.
    last_target_dt = None
    for msg in messages:
        content = msg.get("content", "")
        m = _SNOOZE_RE.match(content)
        if not m:
            continue
        target_dt = parse_snooze_duration(m.group(1))
        if target_dt is not None:
            last_target_dt = target_dt

    if last_target_dt is None:
        return

    db.add_reminder(task, last_target_dt, recurrence="none")
    try:
        zulip_notifier.send_snooze_ack(task, last_target_dt)
    except Exception as exc:
        app.logger.warning("Could not send snooze ack: %s", exc)
    app.logger.info("Snoozed '%s' until %s", task, last_target_dt)


def sweep():
    due = db.get_due_reminders()
    for row in due:
        try:
            zulip_notifier.send(row["task"])
            recurrence = row["recurrence"]
            if recurrence and recurrence != "none":
                remind_at = datetime.fromisoformat(row["remind_at"])
                if remind_at.tzinfo is None:
                    remind_at = remind_at.replace(tzinfo=timezone.utc)
                next_dt = _RECURRENCE_DELTAS[recurrence](remind_at)
                db.reschedule_reminder(row["id"], next_dt)
                app.logger.info("Rescheduled (%s): %s -> %s", recurrence, row["task"], next_dt)
            else:
                db.mark_notified(row["id"])
            db.set_meta("snooze_last_task", row["task"])
            app.logger.info("Notified: %s", row["task"])
        except Exception as exc:
            app.logger.error("Failed to notify for reminder %s: %s", row["id"], exc)

    try:
        process_snoozes()
    except Exception as exc:
        app.logger.error("Snooze processing failed: %s", exc)


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(sweep, "interval", seconds=60)
scheduler.start()
atexit.register(scheduler.shutdown)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    upcoming = db.get_upcoming()
    past = db.get_past()
    zulip_ok = zulip_notifier.is_configured()
    return render_template(
        "index.html",
        upcoming=upcoming,
        past=past,
        zulip_ok=zulip_ok,
        recurrence_options=RECURRENCE_OPTIONS,
    )


@app.route("/reminders", methods=["POST"])
def create_reminder():
    task = request.form.get("task", "").strip()
    when_str = request.form.get("when", "").strip()

    if not task:
        flash("Please enter a task description.", "error")
        return redirect(url_for("index"))

    if not when_str:
        flash("Please enter a time for the reminder.", "error")
        return redirect(url_for("index"))

    parsed_time = dateparser.parse(
        when_str,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TO_TIMEZONE": "UTC",
        },
    )

    if parsed_time is None:
        flash(f'Could not understand "{when_str}". Try "in 5 minutes" or "tomorrow at 9am".', "error")
        return redirect(url_for("index"))

    now = datetime.now(timezone.utc)
    if parsed_time <= now:
        flash("That time is in the past. Please choose a future time.", "error")
        return redirect(url_for("index"))

    recurrence = request.form.get("recurrence", "none")
    if recurrence not in _RECURRENCE_DELTAS and recurrence != "none":
        recurrence = "none"

    db.add_reminder(task, parsed_time, recurrence)
    return redirect(url_for("index"))


@app.route("/reminders/<reminder_id>", methods=["DELETE"])
def delete_reminder(reminder_id):
    db.delete_reminder(reminder_id)
    return "", 204


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, use_reloader=False)
