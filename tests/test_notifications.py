"""Tests for core/notifications.py — factory, backends e graceful failure."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core import notifications
from core.notifications import (
    NullNotifier,
    SMTPNotifier,
    StdoutNotifier,
    WebhookNotifier,
    _build_notifier,
    notify,
    reset_notifier_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Garantir que cada teste começa com cache limpo."""
    reset_notifier_cache()
    yield
    reset_notifier_cache()


# ── Factory ───────────────────────────────────────────────────────────────


def test_default_backend_is_null(monkeypatch):
    monkeypatch.delenv("NOTIFICATION_BACKEND", raising=False)
    assert isinstance(_build_notifier(), NullNotifier)


@pytest.mark.parametrize("value", ["none", "", "off", "disabled", "NONE"])
def test_explicit_none_variants(monkeypatch, value):
    monkeypatch.setenv("NOTIFICATION_BACKEND", value)
    assert isinstance(_build_notifier(), NullNotifier)


def test_stdout_backend(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "stdout")
    assert isinstance(_build_notifier(), StdoutNotifier)


def test_webhook_requires_url(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "webhook")
    monkeypatch.delenv("NOTIFICATION_WEBHOOK_URL", raising=False)
    assert isinstance(_build_notifier(), NullNotifier)


def test_webhook_with_url(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "webhook")
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://example.com/hook")
    n = _build_notifier()
    assert isinstance(n, WebhookNotifier)
    assert n.url == "https://example.com/hook"


@pytest.mark.parametrize(
    "bad_url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://x", "not-a-url"],
)
def test_webhook_rejects_bad_scheme_in_factory(monkeypatch, bad_url):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "webhook")
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", bad_url)
    # Factory degrada para NullNotifier — nunca lança.
    assert isinstance(_build_notifier(), NullNotifier)


def test_webhook_constructor_rejects_bad_scheme():
    with pytest.raises(ValueError):
        WebhookNotifier("file:///etc/passwd")
    with pytest.raises(ValueError):
        WebhookNotifier("http:///missing-host")


def test_smtp_requires_full_config(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "smtp")
    monkeypatch.setenv("NOTIFICATION_SMTP_HOST", "mail.example.com")
    # FROM/TO em falta
    monkeypatch.delenv("NOTIFICATION_SMTP_FROM", raising=False)
    monkeypatch.delenv("NOTIFICATION_SMTP_TO", raising=False)
    assert isinstance(_build_notifier(), NullNotifier)


def test_smtp_with_full_config(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "smtp")
    monkeypatch.setenv("NOTIFICATION_SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("NOTIFICATION_SMTP_PORT", "465")
    monkeypatch.setenv("NOTIFICATION_SMTP_FROM", "sender@example.com")
    monkeypatch.setenv("NOTIFICATION_SMTP_TO", "ops@example.com")
    monkeypatch.setenv("NOTIFICATION_SMTP_STARTTLS", "0")
    n = _build_notifier()
    assert isinstance(n, SMTPNotifier)
    assert n.host == "mail.example.com"
    assert n.port == 465
    assert n.sender == "sender@example.com"
    assert n.recipient == "ops@example.com"
    assert n.use_starttls is False


def test_smtp_invalid_port_falls_back(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "smtp")
    monkeypatch.setenv("NOTIFICATION_SMTP_HOST", "h")
    monkeypatch.setenv("NOTIFICATION_SMTP_FROM", "f@x")
    monkeypatch.setenv("NOTIFICATION_SMTP_TO", "t@x")
    monkeypatch.setenv("NOTIFICATION_SMTP_PORT", "not-a-port")
    n = _build_notifier()
    assert isinstance(n, SMTPNotifier)
    assert n.port == 587


def test_unknown_backend_falls_back_to_null(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "carrier-pigeon")
    assert isinstance(_build_notifier(), NullNotifier)


# ── notify() never raises ─────────────────────────────────────────────────


def test_notify_swallows_backend_exceptions(monkeypatch):
    class Boom:
        def notify(self, *a, **kw):
            raise RuntimeError("backend exploded")

    monkeypatch.setattr(notifications, "_get_notifier", lambda: Boom())
    # Não lança
    notify("title", "message", "error")


def test_null_notifier_returns_none():
    assert NullNotifier().notify("x", "y") is None


def test_stdout_notifier_prints(capsys):
    StdoutNotifier().notify("Build", "ok", "info")
    captured = capsys.readouterr()
    assert "[INFO] Build: ok" in captured.out


# ── Webhook ───────────────────────────────────────────────────────────────


def test_webhook_posts_json():
    captured = {}

    class FakeResp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["method"] = req.get_method()
        captured["ctype"] = req.get_header("Content-type")
        return FakeResp()

    with patch("core.notifications.urllib.request.urlopen", side_effect=fake_urlopen):
        WebhookNotifier("https://example.com/hook").notify("T", "M", "warning")

    assert captured["url"] == "https://example.com/hook"
    assert captured["method"] == "POST"
    assert captured["ctype"] == "application/json"
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload == {"title": "T", "message": "M", "severity": "warning"}


def test_webhook_logs_non_2xx(caplog):
    class FakeResp:
        status = 500

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with (
        patch("core.notifications.urllib.request.urlopen", return_value=FakeResp()),
        caplog.at_level("WARNING", logger="core.notifications"),
    ):
        WebhookNotifier("https://example.com/x").notify("t", "m")
    assert any("HTTP 500" in r.message for r in caplog.records)


def test_webhook_swallows_network_errors():
    with patch(
        "core.notifications.urllib.request.urlopen",
        side_effect=OSError("network down"),
    ):
        # Não lança
        WebhookNotifier("https://example.com/x").notify("t", "m")


# ── SMTP ──────────────────────────────────────────────────────────────────


def test_smtp_sends_message():
    fake_smtp = MagicMock()
    fake_smtp.__enter__.return_value = fake_smtp

    with patch("core.notifications.smtplib.SMTP", return_value=fake_smtp):
        SMTPNotifier(
            host="mail.test",
            port=587,
            user="u",
            password="p",
            sender="from@x",
            recipient="to@x",
            use_starttls=True,
        ).notify("Subject", "Body", "error")

    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("u", "p")
    fake_smtp.send_message.assert_called_once()
    sent_msg = fake_smtp.send_message.call_args.args[0]
    assert sent_msg["Subject"] == "[ERROR] Subject"
    assert sent_msg["From"] == "from@x"
    assert sent_msg["To"] == "to@x"


def test_smtp_no_auth_when_missing_credentials():
    fake_smtp = MagicMock()
    fake_smtp.__enter__.return_value = fake_smtp

    with patch("core.notifications.smtplib.SMTP", return_value=fake_smtp):
        SMTPNotifier(
            host="mail.test",
            port=25,
            user=None,
            password=None,
            sender="from@x",
            recipient="to@x",
            use_starttls=False,
        ).notify("S", "B")

    fake_smtp.starttls.assert_not_called()
    fake_smtp.login.assert_not_called()
    fake_smtp.send_message.assert_called_once()


def test_smtp_swallows_exceptions():
    with patch(
        "core.notifications.smtplib.SMTP", side_effect=OSError("connection refused")
    ):
        # Não lança
        SMTPNotifier(
            host="x",
            port=25,
            user=None,
            password=None,
            sender="f@x",
            recipient="t@x",
        ).notify("t", "m")


# ── cache ─────────────────────────────────────────────────────────────────


def test_notifier_is_cached(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "stdout")
    n1 = notifications._get_notifier()
    n2 = notifications._get_notifier()
    assert n1 is n2


def test_reset_cache_picks_up_env_change(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "stdout")
    assert isinstance(notifications._get_notifier(), StdoutNotifier)
    monkeypatch.setenv("NOTIFICATION_BACKEND", "none")
    reset_notifier_cache()
    assert isinstance(notifications._get_notifier(), NullNotifier)
