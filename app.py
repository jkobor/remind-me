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
    "hourly":      lambda dt: dt + timedelta(hours=1),
    "daily":       lambda dt: dt + timedelta(days=1),
    "every2days":  lambda dt: dt + timedelta(days=2),
    "weekly":      lambda dt: dt + timedelta(weeks=1),
    "biweekly":    lambda dt: dt + timedelta(weeks=2),
    "monthly":     lambda dt: dt + relativedelta(months=1),
    "quarterly":   lambda dt: dt + relativedelta(months=3),
    "yearly":      lambda dt: dt + relativedelta(years=1),
}

RECURRENCE_OPTIONS = [
    ("none",       "Once"),
    ("hourly",     "Hourly"),
    ("daily",      "Daily"),
    ("every2days", "Every 2 Days"),
    ("weekly",     "Weekly"),
    ("biweekly",   "Every 2 Weeks"),
    ("monthly",    "Monthly"),
    ("quarterly",  "Quarterly"),
    ("yearly",     "Yearly"),
]

# Matches "snooze <8-hex-char token> <duration>" (case-insensitive).
# The token is the first 8 characters of the reminder UUID shown in the DM.
_SNOOZE_RE = re.compile(r'^\s*snooze\s+([0-9a-f]{8})\s+(.+?)\s*$', re.IGNORECASE)

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


# How long a queued reminder stays eligible for snoozing.
_SNOOZE_QUEUE_TTL = timedelta(hours=24)


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


def process_snoozes():
    """Poll Zulip for snooze replies and reschedule queued reminders.

    Each ``snooze <token> <duration>`` reply is matched to the queued reminder
    with that token; multiple reminders can be snoozed independently in one
    sweep.  Bot-sent message IDs are explicitly excluded from polling so that
    the bot's own reminder and ack messages are never misread as commands —
    this is the correct fix for the case where ZULIP_EMAIL == ZULIP_TO.
    """
    if not zulip_notifier.is_configured():
        return

    anchor_str = db.get_meta("snooze_anchor_id")
    anchor = int(anchor_str) if anchor_str is not None else None

    # Exclude message IDs the bot itself sent (safe for same-account setups).
    queue = _snooze_queue_get()
    bot_msg_ids = frozenset(
        item["bot_msg_id"] for item in queue if "bot_msg_id" in item
    )

    messages, new_anchor = zulip_notifier.poll_snooze_commands(anchor, exclude_ids=bot_msg_ids)

    if new_anchor is not None:
        db.set_meta("snooze_anchor_id", str(new_anchor))

    if not messages:
        return

    if not queue:
        return

    # Index queue by token for O(1) lookup.
    queue_by_token = {item["token"]: item for item in queue}
    snoozed_tokens: set[str] = set()

    for msg in messages:
        content = msg.get("content", "")
        m = _SNOOZE_RE.match(content)
        if not m:
            continue
        token, duration_str = m.group(1).lower(), m.group(2)
        if token not in queue_by_token:
            app.logger.debug("Snooze token not found in queue: %s", token)
            continue
        if token in snoozed_tokens:
            app.logger.debug("Duplicate snooze for token %s; ignoring", token)
            continue
        target_dt = parse_snooze_duration(duration_str)
        if target_dt is None:
            continue
        item = queue_by_token[token]
        task = item["task"]
        db.add_reminder(task, target_dt, recurrence="none")
        snoozed_tokens.add(token)
        try:
            zulip_notifier.send_snooze_ack(task, target_dt)
        except Exception as exc:
            app.logger.warning("Could not send snooze ack: %s", exc)
        app.logger.info("Snoozed '%s' (token %s) until %s", task, token, target_dt)

    # Remove consumed entries from the queue and persist.
    remaining = [item for item in queue if item["token"] not in snoozed_tokens]
    _snooze_queue_save(remaining)


def sweep():
    due = db.get_due_reminders()
    for row in due:
        try:
            token = row["id"][:8]
            bot_msg_id = zulip_notifier.send(row["task"], token)
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
                "bot_msg_id": bot_msg_id,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })
            _snooze_queue_save(queue)
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
