import os

_REQUIRED = ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE", "ZULIP_TO")

def _snooze_hint(token: str) -> str:
    return (
        f"\n\n*Snooze ID: `{token}` — reply `snooze {token} 1h` to be reminded "
        f"again (e.g. `snooze {token} 30m`, `snooze {token} tomorrow`).*"
    )


def is_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED)


def _make_client():
    import zulip  # deferred so missing package gives a clear error at call time

    return zulip.Client(
        email=os.environ["ZULIP_EMAIL"],
        api_key=os.environ["ZULIP_API_KEY"],
        site=os.environ["ZULIP_SITE"],
    )


def send(task: str, snooze_token: str):
    """Send a reminder DM.  ``snooze_token`` is included so the recipient can
    reference it when replying ``snooze <token> <duration>``."""
    if not is_configured():
        raise RuntimeError(
            "Zulip is not configured. Set ZULIP_EMAIL, ZULIP_API_KEY, "
            "ZULIP_SITE, and ZULIP_TO environment variables."
        )

    client = _make_client()
    result = client.send_message({
        "type": "direct",
        "to": [os.environ["ZULIP_TO"]],
        "content": f":alarm_clock: **Reminder:** {task}{_snooze_hint(snooze_token)}",
    })
    if result.get("result") != "success":
        raise RuntimeError(f"Zulip send failed: {result}")


def poll_snooze_commands(anchor_id: int | None) -> tuple[list[dict], int | None]:
    """Poll for DMs sent by ZULIP_TO to the bot since ``anchor_id``.

    If ``anchor_id`` is None this is the first call: fetch the newest message
    ID to use as the starting anchor, return no commands, and let the caller
    persist the anchor.  Subsequent calls pass the stored anchor and receive
    only messages that arrived after it.

    Returns ``(commands, new_anchor_id)``.  On any API failure returns
    ``([], anchor_id)`` so the sweep can continue safely.
    """
    if not is_configured():
        return [], anchor_id

    try:
        client = _make_client()
        user_email = os.environ["ZULIP_TO"]

        if anchor_id is None:
            # Initialise: find the current newest message ID without processing anything.
            result = client.get_messages({
                "anchor": "newest",
                "num_before": 0,
                "num_after": 0,
                "narrow": [{"operator": "dm-with", "operand": user_email}],
            })
            if result.get("result") != "success":
                return [], None
            messages = result.get("messages", [])
            new_anchor = messages[-1]["id"] if messages else 0
            return [], new_anchor

        result = client.get_messages({
            "anchor": anchor_id,
            "num_before": 0,
            "num_after": 100,
            "narrow": [{"operator": "dm-with", "operand": user_email}],
        })
        if result.get("result") != "success":
            return [], anchor_id

        messages = result.get("messages", [])
        new_anchor = max((m["id"] for m in messages), default=anchor_id)

        # Only messages from the user (not from the bot) that arrived after anchor.
        commands = [
            m for m in messages
            if m.get("sender_email") == user_email and m["id"] > anchor_id
        ]
        return commands, new_anchor

    except Exception:
        return [], anchor_id


def send_snooze_ack(task: str, target_dt) -> None:
    """Send a confirmation DM to ZULIP_TO about the snoozed reminder."""
    if not is_configured():
        return
    client = _make_client()
    formatted = target_dt.strftime("%Y-%m-%d %H:%M UTC")
    client.send_message({
        "type": "direct",
        "to": [os.environ["ZULIP_TO"]],
        "content": f":zzz: Got it! I'll remind you about **{task}** again at {formatted}.",
    })
