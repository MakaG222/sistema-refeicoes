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

import config as cfg
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


class UserContextFilter(logging.Filter):
    """Injecta NII e perfil do utilizador autenticado nos log records.

    Em requests sem sessão autenticada ou fora de contexto Flask, os campos
    ficam como `"-"`. Útil em produção para correlacionar logs com acções
    concretas de um utilizador (ex: via grep por user_nii).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        user_nii = "-"
        user_role = "-"
        try:
            # `session` é um proxy — qualquer acesso fora do request context levanta.
            u = session.get("user") if session else None
            if u:
                user_nii = str(u.get("nii", "-") or "-")
                user_role = str(u.get("perfil", "-") or "-")
        except Exception:
            pass
        record.user_nii = user_nii  # type: ignore[attr-defined]
        record.user_role = user_role  # type: ignore[attr-defined]
        return True


def get_metrics() -> dict:
    """Retorna cópia snapshot das métricas."""
    with _metrics_lock:
        return dict(_metrics)


def get_route_metrics() -> dict[str, dict]:
    """Retorna cópia snapshot das métricas per-route."""
    with _metrics_lock:
        return {k: dict(v) for k, v in _route_metrics.items()}


# ── Prometheus exposition format (RFC textfile / openmetrics-lite) ──────────
# Suficiente para scrapes standard sem precisarmos do `prometheus_client` lib.
# Formato (https://prometheus.io/docs/instrumenting/exposition_formats/):
#   # HELP <name> <description>
#   # TYPE <name> counter|gauge|histogram
#   <name>{label="value",label2="v2"} <number>


def _prom_escape_label_value(v: str) -> str:
    """Escapa value de label conforme spec: backslash, aspa, newline."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_safe_route_name(endpoint: str) -> str:
    """Sanitiza um endpoint do Flask para usar como label value Prometheus.

    Flask endpoints têm formato `blueprint.view_func` — válidos como labels
    sem mais escape, mas filtramos None/empty defensivamente.
    """
    return endpoint or "unknown"


def to_prometheus_text(*, top_routes: int = 20) -> str:
    """Devolve as métricas in-memory no formato text/plain do Prometheus.

    Inclui:
      - http_requests_total (counter)
      - http_request_errors_total (counter)
      - http_request_duration_milliseconds_total (counter)
      - http_request_duration_milliseconds_avg (gauge derivado)
      - http_requests_per_route_total{route="..."} (counter, top N por count)
      - http_request_errors_per_route_total{route="..."} (counter)

    Args:
        top_routes: limita o nº de séries por-rota expostas (evita
            cardinality explosion em apps com muitos endpoints).

    Returns:
        String no exposition format Prometheus, terminada com newline.
    """
    snapshot = get_metrics()
    routes = get_route_metrics()
    count = snapshot.get("request_count", 0)
    err = snapshot.get("error_count", 0)
    total_ms = snapshot.get("total_latency_ms", 0.0)
    avg_ms = (total_ms / count) if count > 0 else 0.0

    lines: list[str] = []

    # Counter — total de requests
    lines.append("# HELP http_requests_total Total HTTP requests processed.")
    lines.append("# TYPE http_requests_total counter")
    lines.append(f"http_requests_total {count}")

    # Counter — errors (5xx)
    lines.append(
        "# HELP http_request_errors_total Total HTTP responses with status >=500."
    )
    lines.append("# TYPE http_request_errors_total counter")
    lines.append(f"http_request_errors_total {err}")

    # Counter — latency total (in ms; soma)
    lines.append(
        "# HELP http_request_duration_milliseconds_total"
        " Sum of request durations in milliseconds."
    )
    lines.append("# TYPE http_request_duration_milliseconds_total counter")
    lines.append(f"http_request_duration_milliseconds_total {total_ms:.3f}")

    # Gauge derivado — latency média
    lines.append(
        "# HELP http_request_duration_milliseconds_avg"
        " Average request duration in milliseconds since process start."
    )
    lines.append("# TYPE http_request_duration_milliseconds_avg gauge")
    lines.append(f"http_request_duration_milliseconds_avg {avg_ms:.3f}")

    # Per-route — limita cardinalidade
    sorted_routes = sorted(
        routes.items(),
        key=lambda kv: kv[1].get("count", 0),
        reverse=True,
    )[:top_routes]

    if sorted_routes:
        lines.append(
            "# HELP http_requests_per_route_total Total requests por endpoint Flask."
        )
        lines.append("# TYPE http_requests_per_route_total counter")
        for route, data in sorted_routes:
            label = _prom_escape_label_value(_prom_safe_route_name(route))
            lines.append(
                f'http_requests_per_route_total{{route="{label}"}} {data.get("count", 0)}'
            )

        lines.append(
            "# HELP http_request_errors_per_route_total Total responses 5xx por endpoint."
        )
        lines.append("# TYPE http_request_errors_per_route_total counter")
        for route, data in sorted_routes:
            label = _prom_escape_label_value(_prom_safe_route_name(route))
            lines.append(
                f'http_request_errors_per_route_total{{route="{label}"}} {data.get("error_count", 0)}'
            )

    # Gauge — tamanho da BD em bytes (útil para alertas de crescimento)
    try:
        from core.database import db_file_size_bytes

        db_size = db_file_size_bytes()
        lines.append("# HELP db_size_bytes SQLite database file size in bytes.")
        lines.append("# TYPE db_size_bytes gauge")
        lines.append(f"db_size_bytes {db_size}")
    except Exception:
        pass  # nunca falhar a métrica por causa do DB

    return "\n".join(lines) + "\n"


def register_middleware(app: Flask) -> None:
    """Regista before/after request e error handlers na app Flask."""

    # Registar RequestIdFilter + UserContextFilter nos loggers
    rid_filter = RequestIdFilter()
    uctx_filter = UserContextFilter()
    app.logger.addFilter(rid_filter)
    app.logger.addFilter(uctx_filter)
    for handler in app.logger.handlers:
        handler.addFilter(rid_filter)
        handler.addFilter(uctx_filter)

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
        # HSTS — só em produção (em dev sem HTTPS o header é inócuo mas pode
        # causar surpresas se o browser cachear e depois mudares de domínio).
        # max-age=1 ano + includeSubDomains. SEM `preload` — preload é uma
        # commitment permanente (browser-baked-in), só activar após validar
        # 6+ meses de HTTPS estável + opt-in explícito em hstspreload.org.
        if cfg.is_production:
            r.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
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

    @app.errorhandler(429)
    def err429(e):
        """Rate limited — JSON para /api/*, template para UI."""
        retry_after = getattr(e, "retry_after", None)
        if request and request.path.startswith("/api/"):
            payload = {
                "status": "error",
                "error": "rate limited",
                "retry_after": retry_after,
            }
            resp = (payload, 429)
            return resp
        return render_template(
            "errors/429.html", content="", retry_after=retry_after
        ), 429

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
