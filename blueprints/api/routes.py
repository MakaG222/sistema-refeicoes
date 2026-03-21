"""Rotas de API: health check e cron endpoints."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from flask import abort, current_app, request

import config as cfg
from core.backup import ensure_daily_backup, limpar_backups_antigos
from core.autofill import autopreencher_refeicoes_semanais
from core.database import db

from blueprints.api import api_bp


# ── Helpers de resposta padronizada ──────────────────────────────────────────


def _api_ok(data: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """Resposta JSON de sucesso."""
    resp: dict[str, Any] = {"status": "ok", "ts": datetime.now().isoformat()}
    if data:
        resp.update(data)
    return resp, 200


def _api_error(msg: str, status: int = 500) -> tuple[dict[str, Any], int]:
    """Resposta JSON de erro."""
    return {"status": "error", "error": msg, "ts": datetime.now().isoformat()}, status


def _verify_cron_token() -> bool:
    """Verifica o token Bearer no header Authorization para endpoints de cron."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[len("Bearer ") :]
    if not cfg.CRON_API_TOKEN:
        # Sem token configurado: bloquear em produção, avisar fora
        if cfg.is_production:
            return False
        current_app.logger.warning(
            "CRON_API_TOKEN não definido — endpoint de cron desprotegido!"
        )
        return True  # permite apenas fora de produção sem token
    return secrets.compare_digest(token, cfg.CRON_API_TOKEN)


@api_bp.route("/health")
def health():
    """Health check — verifica BD, backup, disco e devolve JSON."""
    import os
    import time as _time
    from pathlib import Path

    from core.constants import BACKUP_DIR

    t0 = _time.monotonic()
    checks: dict[str, Any] = {}
    overall = "ok"

    # DB check
    try:
        with db() as conn:
            conn.execute("SELECT 1 FROM utilizadores LIMIT 1").fetchone()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = "error"
        overall = "error"
        current_app.logger.error(f"health: BD falhou — {exc}")

    # DB size
    try:
        import core.constants

        checks["db_size_mb"] = round(
            os.path.getsize(core.constants.BASE_DADOS) / (1024 * 1024), 1
        )
    except Exception:
        checks["db_size_mb"] = None

    # Backup age
    try:
        backup_dir = Path(BACKUP_DIR)
        backups = sorted(
            backup_dir.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        if backups:
            age_hours = round((_time.time() - backups[0].stat().st_mtime) / 3600, 1)
            checks["last_backup_hours"] = age_hours
            if age_hours > 48:
                checks["backup"] = "warn"
        else:
            checks["backup"] = "no_backups"
    except Exception:
        checks["backup"] = "unknown"

    # Disk free space
    try:
        import core.constants

        stat = os.statvfs(os.path.dirname(core.constants.BASE_DADOS) or ".")
        free_mb = round((stat.f_bavail * stat.f_frsize) / (1024 * 1024))
        checks["disk_free_mb"] = free_mb
        if free_mb < 100:
            checks["disk"] = "warn"
    except Exception:
        pass

    latency_ms = round((_time.monotonic() - t0) * 1000, 1)
    checks["latency_ms"] = latency_ms

    status_code = 200 if overall == "ok" else 503
    return {"status": overall, "ts": datetime.now().isoformat(), **checks}, status_code


@api_bp.route("/health/metrics")
def health_metrics():
    """Métricas básicas de request — contadores in-memory."""
    from core.middleware import get_metrics

    m = get_metrics()
    count = m["request_count"]
    return _api_ok(
        {
            "request_count": count,
            "error_count": m["error_count"],
            "avg_latency_ms": round(m["total_latency_ms"] / max(count, 1), 1),
        }
    )


@api_bp.route("/api/backup-cron", methods=["POST"])
def api_backup_cron():
    """Endpoint para cron job externo invocar backup diário.
    Uso: curl -X POST -H "Authorization: Bearer <CRON_API_TOKEN>" http://host/api/backup-cron
    """
    if not _verify_cron_token():
        abort(403)
    try:
        ensure_daily_backup()
        limpar_backups_antigos()
        return _api_ok()
    except Exception as exc:
        current_app.logger.error(f"api_backup_cron: {exc}")
        return _api_error(str(exc))


@api_bp.route("/api/autopreencher-cron", methods=["POST"])
def api_autopreencher_cron():
    """Endpoint para cron externo pré-preencher refeições semanalmente.
    Uso: curl -X POST -H "Authorization: Bearer <CRON_API_TOKEN>" http://host/api/autopreencher-cron
    """
    if not _verify_cron_token():
        abort(403)
    try:
        autopreencher_refeicoes_semanais(cfg.DIAS_ANTECEDENCIA)
        return _api_ok()
    except Exception as exc:
        current_app.logger.error(f"api_autopreencher_cron: {exc}")
        return _api_error(str(exc))
