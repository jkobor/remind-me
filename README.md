# Remind Me

A web app for setting reminders and receiving them as Zulip direct messages. Inspired by [zeke/remind-me](https://github.com/zeke/remind-me).

## Features

- **Natural language scheduling** — "in 5 minutes", "tomorrow at 9am", "next Friday at 3pm"
- **Recurring reminders** — daily, weekly, or monthly
- **Zulip notifications** — reminders are delivered as direct messages to a configured user
- **Simple web UI** — create and delete reminders from the browser
- **Persistent storage** — reminders survive restarts via SQLite

## How it works

A background job checks every 60 seconds for due reminders and sends a Zulip direct message for each one. One-time reminders are marked done after firing; recurring reminders are rescheduled in-place for the next occurrence.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Zulip

Create a bot in your Zulip organisation (**Settings → Bots → Add a new bot**) and copy its credentials. Then create a `.env` file in the project root:

```bash
cp .env.example .env
```

Fill in the values:

```
ZULIP_EMAIL=reminder-bot@yourorg.zulipchat.com
ZULIP_API_KEY=your_api_key_here
ZULIP_SITE=https://yourorg.zulipchat.com
ZULIP_TO=you@yourorg.zulipchat.com
```

| Variable | Description |
|---|---|
| `ZULIP_EMAIL` | Email address of the Zulip bot |
| `ZULIP_API_KEY` | API key for the bot |
| `ZULIP_SITE` | Base URL of your Zulip server |
| `ZULIP_TO` | Email address to send reminders to |

### 3. Run

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

## Optional configuration

```
SECRET_KEY=change-me-to-a-random-secret
DATABASE_PATH=~/.remind-me/reminders.db
```

`DATABASE_PATH` defaults to `~/.remind-me/reminders.db`. The directory is created automatically on first run.

## Usage

1. Enter a task and a time in the form at the top of the page
2. Optionally choose a recurrence (Once / Daily / Weekly / Monthly)
3. Click **Set Reminder**
4. When the time arrives you'll receive a Zulip direct message from the bot

If Zulip credentials are not configured a warning is shown in the UI — reminders are still saved but no messages will be sent.

## Project structure

```
app.py                  # Flask routes and background scheduler
db.py                   # SQLite helpers
notifiers/
└── zulip_notifier.py   # Zulip direct-message sender
templates/
└── index.html          # Single-page UI
static/
└── style.css
```
