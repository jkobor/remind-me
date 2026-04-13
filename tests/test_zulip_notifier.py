"""Tests for notifiers/zulip_notifier.py."""
import pytest
from unittest.mock import MagicMock, patch

from notifiers import zulip_notifier

# ZULIP_TO is no longer a required global env var — notifications use the
# per-user email passed to send(), with ZULIP_TO as an optional fallback.
_REQUIRED_ENV = {
    "ZULIP_EMAIL": "bot@example.com",
    "ZULIP_API_KEY": "abc123",
    "ZULIP_SITE": "https://example.zulipchat.com",
}

_FALLBACK_TO = "user@example.com"


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

    def test_zulip_to_not_required(self, monkeypatch):
        """ZULIP_TO is an optional fallback, not required for is_configured."""
        _set_env(monkeypatch)
        monkeypatch.delenv("ZULIP_TO", raising=False)
        assert zulip_notifier.is_configured() is True


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

class TestSend:
    def test_raises_runtime_error_when_not_configured(self, monkeypatch):
        for k in _REQUIRED_ENV:
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(RuntimeError, match="not configured"):
            zulip_notifier.send("call mom", "http://localhost:5000/snooze/a1b2c3d4",
                                to_email="u@example.com")

    def test_raises_when_no_recipient_at_all(self, monkeypatch):
        _set_env(monkeypatch)
        monkeypatch.delenv("ZULIP_TO", raising=False)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 1}
        with patch("zulip.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="recipient"):
                zulip_notifier.send("task", "http://localhost:5000/snooze/tok")

    def test_sends_to_explicit_to_email(self, monkeypatch):
        _set_env(monkeypatch)
        monkeypatch.delenv("ZULIP_TO", raising=False)
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 7}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("call mom", "http://localhost:5000/snooze/tok",
                                to_email="alice@example.com")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["to"] == ["alice@example.com"]

    def test_to_email_overrides_zulip_to(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 7}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("task", "http://localhost:5000/snooze/tok",
                                to_email="alice@example.com")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["to"] == ["alice@example.com"]

    def test_falls_back_to_zulip_to_when_no_to_email(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 7}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("call mom", "http://localhost:5000/snooze/tok")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["to"] == [_FALLBACK_TO]

    def test_sends_direct_message(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 7}
        with patch("zulip.Client", return_value=mock_client):
            zulip_notifier.send("call mom", "http://localhost:5000/snooze/a1b2c3d4")

        call_args = mock_client.send_message.call_args[0][0]
        assert call_args["type"] == "direct"
        assert ":alarm_clock: **Reminder:** call mom" in call_args["content"]

    def test_message_includes_snooze_link(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
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
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "success", "id": 123}
        with patch("zulip.Client", return_value=mock_client):
            msg_id = zulip_notifier.send("call mom", "http://localhost:5000/snooze/a1b2c3d4")
        assert msg_id == 123

    def test_creates_client_with_correct_credentials(self, monkeypatch):
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
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
        _set_env(monkeypatch, {"ZULIP_TO": _FALLBACK_TO})
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"result": "error", "msg": "invalid key"}
        with patch("zulip.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Zulip send failed"):
                zulip_notifier.send("task", "http://localhost:5000/snooze/a1b2c3d4")
