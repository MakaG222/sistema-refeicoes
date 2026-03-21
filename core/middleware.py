"""core/middleware — before/after request hooks, error handlers, métricas."""

from __future__ import annotations

import secrets
import threading
import time

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

_WAL_CHECKPOINT_INTERVAL = 300  # checkpoint WAL a cada 5 min
_last_wal_checkpoint = 0.0


def get_metrics() -> dict:
    """Retorna cópia snapshot das métricas."""
    with _metrics_lock:
        return dict(_metrics)


def register_middleware(app: Flask) -> None:
    """Regista before/after request e error handlers na app Flask."""

    @app.before_request
    def before():
        global _last_wal_checkpoint
        g._t0 = time.perf_counter()

        session.permanent = True

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
        r.headers.setdefault("X-Content-Type-Options", "nosniff")
        r.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        r.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        r.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        )
        t0 = getattr(g, "_t0", None)
        if t0 is not None:
            dt_ms = (time.perf_counter() - t0) * 1000
            with _metrics_lock:
                _metrics["request_count"] += 1
                _metrics["total_latency_ms"] += dt_ms
                if r.status_code >= 500:
                    _metrics["error_count"] += 1
            if dt_ms > 500:
                app.logger.warning(
                    "Slow request: %s %s %.0fms",
                    request.method,
                    request.path,
                    dt_ms,
                )
        return r

    @app.errorhandler(400)
    def err400(e):
        return render_template("errors/400.html", content=""), 400

    @app.errorhandler(404)
    def err404(e):
        return render_template("errors/404.html", content=""), 404

    @app.errorhandler(500)
    def err500(e):
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
