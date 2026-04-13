import os

# Only ZULIP_EMAIL, ZULIP_API_KEY, and ZULIP_SITE are required globally.
# The recipient is supplied per-call (from the user's LDAP email) with
# ZULIP_TO as a fallback for single-user / legacy deployments.
_REQUIRED = ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE")


def is_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED)


def _make_client():
    import zulip  # deferred so missing package gives a clear error at call time

    return zulip.Client(
        email=os.environ["ZULIP_EMAIL"],
        api_key=os.environ["ZULIP_API_KEY"],
        site=os.environ["ZULIP_SITE"],
    )


def send(task: str, snooze_url: str, to_email: str | None = None) -> int:
    """Send a reminder DM and return the Zulip message ID.

    *to_email* is the recipient address (the user's email from LDAP).
    Falls back to the ``ZULIP_TO`` environment variable for single-user /
    legacy deployments when *to_email* is not supplied.

    The message includes a link to the web snooze view so the user can
    postpone the reminder by clicking the link rather than replying.
    """
    if not is_configured():
        raise RuntimeError(
            "Zulip is not configured. Set ZULIP_EMAIL, ZULIP_API_KEY, "
            "and ZULIP_SITE environment variables."
        )

    recipient = to_email or os.environ.get("ZULIP_TO")
    if not recipient:
        raise RuntimeError(
            "No Zulip recipient. Pass to_email or set the ZULIP_TO environment variable."
        )

    client = _make_client()
    result = client.send_message({
        "type": "direct",
        "to": [recipient],
        "content": (
            f":alarm_clock: **Reminder:** {task}"
            f"\n\n[Snooze this reminder]({snooze_url})"
        ),
    })
    if result.get("result") != "success":
        raise RuntimeError(f"Zulip send failed: {result}")
    return result["id"]
