"""Notificações de eventos operacionais.

Backend selecionado por env var `NOTIFICATION_BACKEND` (default: `none`):

  - `none` / vazio: silencioso (NullNotifier).
  - `stdout`: imprime em stdout (útil em dev/Docker logs).
  - `webhook`: POST JSON para `NOTIFICATION_WEBHOOK_URL`.
  - `smtp`: e-mail via `NOTIFICATION_SMTP_HOST`/`_PORT`/`_USER`/`_PASS`/
           `_FROM`/`_TO` (STARTTLS por defeito).

Design:
  - Falhas do notifier **nunca** levantam excepção para o chamador; apenas loggam.
  - `notify()` é thread-safe na medida em que o cache é imutável após a 1ª
    chamada; os backends usam clientes efémeros por chamada.
  - `reset_notifier_cache()` existe para testes.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol

log = logging.getLogger(__name__)

Severity = str  # "info" | "warning" | "error"


class Notifier(Protocol):
    def notify(self, title: str, message: str, severity: Severity = "info") -> None: ...


class NullNotifier:
    """Notifier silencioso — default quando nenhum backend está configurado."""

    def notify(self, title: str, message: str, severity: Severity = "info") -> None:
        return None


class StdoutNotifier:
    """Escreve uma linha em stdout — útil em Docker/dev."""

    def notify(self, title: str, message: str, severity: Severity = "info") -> None:
        print(f"[{severity.upper()}] {title}: {message}", flush=True)


class WebhookNotifier:
    """POST JSON para um endpoint HTTP(S) (Slack/Discord/Teams/custom).

    Apenas aceita schemes ``http``/``https``; qualquer outro (``file://``,
    ``ftp://``, ...) é rejeitado no __init__.
    """

    _ALLOWED_SCHEMES = ("http", "https")

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in self._ALLOWED_SCHEMES:
            raise ValueError(
                f"WebhookNotifier: scheme '{parsed.scheme}' não permitido "
                f"(apenas {self._ALLOWED_SCHEMES})"
            )
        if not parsed.netloc:
            raise ValueError("WebhookNotifier: URL sem host")
        self.url = url
        self.timeout = timeout

    def notify(self, title: str, message: str, severity: Severity = "info") -> None:
        payload = json.dumps(
            {"title": title, "message": message, "severity": severity}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            # nosec B310 — scheme validado no __init__ (apenas http/https).
            with urllib.request.urlopen(  # noqa: S310  # nosec B310
                req, timeout=self.timeout
            ) as resp:
                if resp.status >= 300:
                    log.warning("Webhook notifier: HTTP %s", resp.status)
        except Exception:
            log.exception("Webhook notifier falhou")


class SMTPNotifier:
    """Envia e-mail via SMTP (STARTTLS por defeito)."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        sender: str,
        recipient: str,
        timeout: float = 10.0,
        use_starttls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.recipient = recipient
        self.timeout = timeout
        self.use_starttls = use_starttls

    def notify(self, title: str, message: str, severity: Severity = "info") -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[{severity.upper()}] {title}"
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg.set_content(message)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.use_starttls:
                    smtp.starttls()
                if self.user and self.password:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except Exception:
            log.exception("SMTP notifier falhou")


def _env_truthy(val: str | None, default: bool = True) -> bool:
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off", "")


def _build_notifier() -> Notifier:
    backend = os.getenv("NOTIFICATION_BACKEND", "none").strip().lower()
    if backend in ("", "none", "off", "disabled"):
        return NullNotifier()
    if backend == "stdout":
        return StdoutNotifier()
    if backend == "webhook":
        url = os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip()
        if not url:
            log.warning(
                "NOTIFICATION_BACKEND=webhook mas NOTIFICATION_WEBHOOK_URL vazio — "
                "a usar NullNotifier."
            )
            return NullNotifier()
        try:
            return WebhookNotifier(url)
        except ValueError as e:
            log.warning(
                "NOTIFICATION_WEBHOOK_URL inválido (%s) — a usar NullNotifier.", e
            )
            return NullNotifier()
    if backend == "smtp":
        host = os.getenv("NOTIFICATION_SMTP_HOST", "").strip()
        sender = os.getenv("NOTIFICATION_SMTP_FROM", "").strip()
        recipient = os.getenv("NOTIFICATION_SMTP_TO", "").strip()
        if not host or not sender or not recipient:
            log.warning(
                "NOTIFICATION_BACKEND=smtp com config incompleta "
                "(HOST/FROM/TO obrigatórios) — a usar NullNotifier."
            )
            return NullNotifier()
        try:
            port = int(os.getenv("NOTIFICATION_SMTP_PORT", "587"))
        except ValueError:
            log.warning("NOTIFICATION_SMTP_PORT inválido — a usar 587.")
            port = 587
        return SMTPNotifier(
            host=host,
            port=port,
            user=os.getenv("NOTIFICATION_SMTP_USER") or None,
            password=os.getenv("NOTIFICATION_SMTP_PASS") or None,
            sender=sender,
            recipient=recipient,
            use_starttls=_env_truthy(os.getenv("NOTIFICATION_SMTP_STARTTLS"), True),
        )
    log.warning("NOTIFICATION_BACKEND=%s desconhecido — a usar NullNotifier.", backend)
    return NullNotifier()


@lru_cache(maxsize=1)
def _get_notifier() -> Notifier:
    return _build_notifier()


def notify(title: str, message: str, severity: Severity = "info") -> None:
    """Envia notificação operacional. Nunca lança excepção."""
    try:
        _get_notifier().notify(title, message, severity)
    except Exception:
        log.exception("notify() falhou inesperadamente")


def reset_notifier_cache() -> None:
    """Limpa o cache do notifier — usado por testes ao mexer em env vars."""
    clear = getattr(_get_notifier, "cache_clear", None)
    if callable(clear):
        clear()


__all__ = [
    "Notifier",
    "NullNotifier",
    "StdoutNotifier",
    "WebhookNotifier",
    "SMTPNotifier",
    "notify",
    "reset_notifier_cache",
]
