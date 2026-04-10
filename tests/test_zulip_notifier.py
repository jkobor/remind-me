"""Tests for notifiers/zulip_notifier.py."""
import pytest
from unittest.mock import MagicMock, patch

from notifiers import zulip_notifier

_REQUIRED_ENV = {
    "ZULIP_EMAIL": "bot@example.com",
    "ZULIP_API_KEY": "abc123",
    "ZULIP_SITE": "https://example.zulipchat.com",
    "ZULIP_TO": "user@example.com",
}


def _set_env(monkeypatch, overrides=None):
    env = {**_REQUIRED_ENV, **(overrides or {})}
    for k, v in env.items():
        monkeypatch.setenv(k, v)


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_returns_true_when_all_vars_set(self, monkeypatch):
        _set_env(monkeypatch)
        assert zulip_notifier.is_configured() is True

    @pytest.mark.parametrize("missing", list(_REQUIRED_ENV))
    def test_returns_false_when_var_missing(self, monkeypatch, missing):
        _set_env(monkeypatch)
        monkeypatch.delenv(missing)
        assert zulip_notifier.is_configured() is False

    def test_returns_false_when_all_vars_absent(self, monkeypatch):
        for k in _REQUIRED_ENV:
            monkeypatch.delenv(k, raising=False)
        assert zulip_notifier.is_configured() is False


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

class TestSend:
    def test_raises_runtime_error_when_not_configured(self, monkeypatch):
        for k in _REQUIRED_ENV:
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(RuntimeError, match="not configured"):
            zulip_notifier.send("call mom", "a1b2c3d4")

    def test_sends_direct_message_to_configured_user(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success"}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("call mom", "a1b2c3d4")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["type"] == "direct"
        assert call_args["to"] == ["user@example.com"]
        assert ":alarm_clock: **Reminder:** call mom" in call_args["content"]
        assert "a1b2c3d4" in call_args["content"]

    def test_message_content_includes_snooze_token(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success"}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("pick up dry cleaning", "deadbeef")

        content = mock_client.send_message.call_args[0][0]["content"]
        assert "pick up dry cleaning" in content
        assert "deadbeef" in content

    def test_creates_client_with_correct_credentials(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success"}
        with patch("zulip.Client", return_value=mock_client) as mock_cls:
            zulip_notifier.send("task", "a1b2c3d4")

        mock_cls.assert_called_once_with(
            email="bot@example.com",
            api_key="abc123",
            site="https://example.zulipchat.com",
        )

    def test_raises_on_api_failure(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "error", "msg": "invalid key"}
        with patch("zulip.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Zulip send failed"):
                zulip_notifier.send("task", "a1b2c3d4")

    def test_uses_zulip_to_env_var(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": "other@example.com"})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success"}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("task", "a1b2c3d4")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["to"] == ["other@example.com"]


# ---------------------------------------------------------------------------
# poll_snooze_commands
# ---------------------------------------------------------------------------

class TestPollSnoozeCommands:
    def test_returns_empty_and_none_when_not_configured(self, monkeypatch):
        for k in _REQUIRED_ENV:
            monkeypatch.delenv(k, raising=False)
        msgs, anchor = zulip_notifier.poll_snooze_commands(None)
        assert msgs == []
        assert anchor is None

    def test_initialises_anchor_on_first_call(self, monkeypatch):
        """When anchor_id is None, returns no commands and the newest message ID."""
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.return_value = {
            "result": "success",
            "messages": [{"id": 42, "sender_email": "user@example.com", "content": "hi"}],
        }
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(None)
        assert msgs == []
        assert anchor == 42

    def test_initialises_anchor_to_zero_when_no_messages(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.return_value = {"result": "success", "messages": []}
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(None)
        assert msgs == []
        assert anchor == 0

    def test_returns_new_messages_from_user_since_anchor(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.return_value = {
            "result": "success",
            "messages": [
                {"id": 10, "sender_email": "user@example.com", "content": "snooze 1h"},
                {"id": 11, "sender_email": "user@example.com", "content": "snooze 30m"},
            ],
        }
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(9)
        assert len(msgs) == 2
        assert anchor == 11

    def test_filters_out_bot_messages(self, monkeypatch):
        """Messages from the bot itself (ZULIP_EMAIL) must be excluded."""
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.return_value = {
            "result": "success",
            "messages": [
                {"id": 10, "sender_email": "bot@example.com", "content": "snooze 1h"},
                {"id": 11, "sender_email": "user@example.com", "content": "snooze 30m"},
            ],
        }
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(9)
        assert len(msgs) == 1
        assert msgs[0]["id"] == 11

    def test_excludes_anchor_message_itself(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.return_value = {
            "result": "success",
            "messages": [
                {"id": 9, "sender_email": "user@example.com", "content": "snooze 1h"},
            ],
        }
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(9)
        assert msgs == []

    def test_returns_original_anchor_on_api_failure(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.return_value = {"result": "error", "msg": "server error"}
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(5)
        assert msgs == []
        assert anchor == 5

    def test_returns_anchor_on_exception(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.get_messages.side_effect = RuntimeError("network error")
        with patch("zulip.Client", return_value=mock_client):
            msgs, anchor = zulip_notifier.poll_snooze_commands(5)
        assert msgs == []
        assert anchor == 5


# ---------------------------------------------------------------------------
# send_snooze_ack
# ---------------------------------------------------------------------------

class TestSendSnoozeAck:
    def test_sends_dm_with_task_and_time(self, monkeypatch):
        from datetime import datetime, timezone
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success"}
        dt = datetime(2026, 6, 1, 10, 30, tzinfo=timezone.utc)
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send_snooze_ack("call mom", dt)
        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["type"] == "direct"
        assert call_args["to"] == ["user@example.com"]
        assert "call mom" in call_args["content"]
        assert "2026-06-01 10:30 UTC" in call_args["content"]

    def test_does_nothing_when_not_configured(self, monkeypatch):
        from datetime import datetime, timezone
        for k in _REQUIRED_ENV:
            monkeypatch.delenv(k, raising=False)
        # Should not raise
        zulip_notifier.send_snooze_ack("task", datetime.now(timezone.utc))
