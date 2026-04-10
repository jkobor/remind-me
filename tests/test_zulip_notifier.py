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
            zulip_notifier.send("call mom", "http://localhost:5000/snooze/a1b2c3d4")

    def test_sends_direct_message_to_configured_user(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 7}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("call mom", "http://localhost:5000/snooze/a1b2c3d4")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["type"] == "direct"
        assert call_args["to"] == ["user@example.com"]
        assert ":alarm_clock: **Reminder:** call mom" in call_args["content"]

    def test_message_includes_snooze_link(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 1}
        snooze_url = "http://localhost:5000/snooze/deadbeef"
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("pick up dry cleaning", snooze_url)

        content = mock_client.send_message.call_args[0][0]["content"]
        assert "pick up dry cleaning" in content
        assert snooze_url in content
        assert "[Snooze this reminder]" in content

    def test_send_returns_message_id(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 123}
        with patch("zulip.Client", return_value=mock_client):
            msg_id = zulip_notifier.send("call mom", "http://localhost:5000/snooze/a1b2c3d4")
        assert msg_id == 123

    def test_creates_client_with_correct_credentials(self, monkeypatch):
        _set_env(monkeypatch)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 1}
        with patch("zulip.Client", return_value=mock_client) as mock_cls:
            zulip_notifier.send("task", "http://localhost:5000/snooze/a1b2c3d4")

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
                zulip_notifier.send("task", "http://localhost:5000/snooze/a1b2c3d4")

    def test_uses_zulip_to_env_var(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": "other@example.com"})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 1}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("task", "http://localhost:5000/snooze/a1b2c3d4")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["to"] == ["other@example.com"]
