import os

_REQUIRED = ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE", "ZULIP_TO")


def is_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED)


def send(task: str):
    if not is_configured():
        raise RuntimeError(
            "Zulip is not configured. Set ZULIP_EMAIL, ZULIP_API_KEY, "
            "ZULIP_SITE, and ZULIP_TO environment variables."
        )

    import zulip  # deferred so missing package gives a clear error at call time

    client = zulip.Client(
        email=os.environ["ZULIP_EMAIL"],
        api_key=os.environ["ZULIP_API_KEY"],
        site=os.environ["ZULIP_SITE"],
    )
    result = client.send_message({
        "type": "direct",
        "to": [os.environ["ZULIP_TO"]],
        "content": f":alarm_clock: **Reminder:** {task}",
    })
    if result.get("result") != "success":
        raise RuntimeError(f"Zulip send failed: {result}")
