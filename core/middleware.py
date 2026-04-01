"""core/middleware — before/after request hooks, error handlers, métricas."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from uuid import uuid4

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from core.database import wal_checkpoint

# ── Métricas básicas (in-memory, thread-safe) ────────────────────────────
_metrics_lock = threading.Lock()
_metrics = {"request_count": 0, "error_count": 0, "total_latency_ms": 0.0}
_route_metrics: dict[str, dict] = {}

_WAL_CHECKPOINT_INTERVAL = 300  # checkpoint WAL a cada 5 min
_last_wal_checkpoint = 0.0


class RequestIdFilter(logging.Filter):
    """Injecta request_id nos log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(g, "request_id", "-")  # type: ignore[attr-defined]
        return True


def get_metrics() -> dict:
    """Retorna cópia snapshot das métricas."""
    with _metrics_lock:
        return dict(_metrics)


def get_route_metrics() -> dict[str, dict]:
    """Retorna cópia snapshot das métricas per-route."""
    with _metrics_lock:
        return {k: dict(v) for k, v in _route_metrics.items()}


def register_middleware(app: Flask) -> None:
    """Regista before/after request e error handlers na app Flask."""

    # Registar RequestIdFilter nos loggers
    rid_filter = RequestIdFilter()
    app.logger.addFilter(rid_filter)
    for handler in app.logger.handlers:
        handler.addFilter(rid_filter)

    @app.before_request
    def before():
        global _last_wal_checkpoint
        g._t0 = time.perf_counter()
        g.request_id = request.headers.get("X-Request-ID") or uuid4().hex[:12]

        session.permanent = True

        # HTTPS redirect em produção (via X-Forwarded-Proto do proxy)
        if (
            app.config.get("SESSION_COOKIE_SECURE")
            and request.headers.get("X-Forwarded-Proto", "https") == "http"
            and request.endpoint != "api.health"
        ):
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)

        # WAL checkpoint periódico
        now = time.time()
        if now - _last_wal_checkpoint > _WAL_CHECKPOINT_INTERVAL:
            _last_wal_checkpoint = now
            wal_checkpoint()

        if request.method == "POST":
            if request.blueprint == "api":
                return
            t = session.get("_csrf_token", "")
            ft = request.form.get("csrf_token", "")
            if not t or not ft or not secrets.compare_digest(t, ft):
                if "user" not in session and request.endpoint not in {None}:
                    flash(
                        "A sessão expirou. Inicia sessão novamente e repete a operação.",
                        "warn",
                    )
                    return redirect(url_for("auth.login"))
                abort(400)

    @app.after_request
    def after(r):
        # Request ID no response
        r.headers["X-Request-ID"] = getattr(g, "request_id", "")

        r.headers.setdefault("X-Content-Type-Options", "nosniff")
        r.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        r.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        r.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self';"
            " script-src 'self';"
            " style-src 'self';"
            " img-src 'self' data:;"
            " font-src 'self';"
            " form-action 'self';"
            " frame-ancestors 'none';"
            " base-uri 'self'",
        )
        t0 = getattr(g, "_t0", None)
        if t0 is not None:
            dt_ms = (time.perf_counter() - t0) * 1000
            ep = request.endpoint or "unknown"
            with _metrics_lock:
                _metrics["request_count"] += 1
                _metrics["total_latency_ms"] += dt_ms
                if r.status_code >= 500:
                    _metrics["error_count"] += 1
                rm = _route_metrics.setdefault(
                    ep, {"count": 0, "total_ms": 0.0, "error_count": 0}
                )
                rm["count"] += 1
                rm["total_ms"] += dt_ms
                if r.status_code >= 500:
                    rm["error_count"] += 1
            if dt_ms > 500:
                app.logger.warning(
                    "Slow request: %s %s %.0fms rid=%s",
                    request.method,
                    request.path,
                    dt_ms,
                    getattr(g, "request_id", "-"),
                )
        return r

    @app.errorhandler(400)
    def err400(e):
        return render_template("errors/400.html", content=""), 400

    @app.errorhandler(403)
    def err403(e):
        return render_template("errors/403.html", content=""), 403

    @app.errorhandler(404)
    def err404(e):
        return render_template("errors/404.html", content=""), 404

    @app.errorhandler(500)
    def err500(e):
        import sqlite3

        # BD bloqueada → 503 com mensagem clara
        orig = getattr(e, "original_exception", e)
        if isinstance(orig, sqlite3.OperationalError) and "locked" in str(orig).lower():
            app.logger.warning(
                "DB locked: %s | path=%s user=%s",
                orig,
                request.path if request else "unknown",
                session.get("user", {}).get("nii", "anonymous")
                if session
                else "no-session",
            )
            return render_template("errors/503.html", content=""), 503

        app.logger.critical(
            "CRITICAL ERROR: %s | path=%s method=%s user=%s",
            e,
            request.path if request else "unknown",
            request.method if request else "unknown",
            session.get("user", {}).get("nii", "anonymous")
            if session
            else "no-session",
        )
        return render_template("errors/500.html", content=""), 500
