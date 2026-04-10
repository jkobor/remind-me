import atexit
import json
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


# How long a queued reminder stays eligible for snoozing via the web view.
_SNOOZE_QUEUE_TTL = timedelta(hours=24)

# Preset snooze durations offered in the web view (label → minutes).
SNOOZE_PRESETS = [
    ("15 min",   15),
    ("30 min",   30),
    ("1 hour",   60),
    ("2 hours",  120),
    ("4 hours",  240),
    ("Tomorrow", 1440),
    ("Next week", 10080),
]


def _snooze_queue_get() -> list[dict]:
    """Load the current snooze queue, dropping entries older than the TTL."""
    raw = db.get_meta("snooze_queue")
    if not raw:
        return []
    items = json.loads(raw)
    now = datetime.now(timezone.utc)
    return [
        item for item in items
        if now - datetime.fromisoformat(item["sent_at"]) < _SNOOZE_QUEUE_TTL
    ]


def _snooze_queue_save(items: list[dict]) -> None:
    db.set_meta("snooze_queue", json.dumps(items))


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


def sweep():
    due = db.get_due_reminders()
    base_url = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
    for row in due:
        try:
            token = row["id"][:8]
            snooze_url = f"{base_url}/snooze/{token}"
            zulip_notifier.send(row["task"], snooze_url)
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
            queue = _snooze_queue_get()
            queue.append({
                "task": row["task"],
                "token": token,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })
            _snooze_queue_save(queue)
            app.logger.info("Notified: %s", row["task"])
        except Exception as exc:
            app.logger.error("Failed to notify for reminder %s: %s", row["id"], exc)


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


@app.route("/snooze/<token>", methods=["GET", "POST"])
def snooze_view(token):
    queue = _snooze_queue_get()
    item = next((i for i in queue if i["token"] == token), None)

    if item is None:
        return render_template("snooze.html", token=token, task=None, expired=True,
                               presets=SNOOZE_PRESETS), 404

    task = item["task"]

    if request.method == "GET":
        return render_template("snooze.html", token=token, task=task, expired=False,
                               presets=SNOOZE_PRESETS)

    # POST: process the chosen snooze duration.
    minutes_val = request.form.get("minutes", "").strip()
    custom_when = request.form.get("custom_when", "").strip()

    if minutes_val == "custom":
        if not custom_when:
            flash("Please enter a custom time.", "error")
            return redirect(url_for("snooze_view", token=token))
        target_dt = parse_snooze_duration(custom_when)
        if target_dt is None:
            flash(
                f'Could not understand "{custom_when}". '
                'Try "in 3 hours" or "tomorrow at 9am".',
                "error",
            )
            return redirect(url_for("snooze_view", token=token))
    else:
        try:
            minutes = int(minutes_val)
            if minutes <= 0:
                raise ValueError
        except (ValueError, TypeError):
            flash("Please select a snooze duration.", "error")
            return redirect(url_for("snooze_view", token=token))
        target_dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    db.add_reminder(task, target_dt, recurrence="none")

    remaining = [i for i in queue if i["token"] != token]
    _snooze_queue_save(remaining)

    app.logger.info("Snoozed '%s' (token %s) until %s via web", task, token, target_dt)

    formatted = target_dt.strftime("%Y-%m-%d %H:%M UTC")
    return render_template("snooze_done.html", task=task, formatted_time=formatted)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    app.run(host="0.0.0.0", debug=True, use_reloader=False)
