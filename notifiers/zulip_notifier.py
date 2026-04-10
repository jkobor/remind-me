import os

_REQUIRED = ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE", "ZULIP_TO")


def is_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED)


def _make_client():
    import zulip  # deferred so missing package gives a clear error at call time

    return zulip.Client(
        email=os.environ["ZULIP_EMAIL"],
        api_key=os.environ["ZULIP_API_KEY"],
        site=os.environ["ZULIP_SITE"],
    )


def send(task: str, snooze_url: str) -> int:
    """Send a reminder DM and return the Zulip message ID.

    The message includes a link to the web snooze view so the user can
    postpone the reminder by clicking the link rather than by replying.
    """
    if not is_configured():
        raise RuntimeError(
            "Zulip is not configured. Set ZULIP_EMAIL, ZULIP_API_KEY, "
            "ZULIP_SITE, and ZULIP_TO environment variables."
        )

    client = _make_client()
    result = client.send_message({
        "type": "direct",
        "to": [os.environ["ZULIP_TO"]],
        "content": (
            f":alarm_clock: **Reminder:** {task}"
            f"\n\n[Snooze this reminder]({snooze_url})"
        ),
    })
    if result.get("result") != "success":
        raise RuntimeError(f"Zulip send failed: {result}")
    return result["id"]
