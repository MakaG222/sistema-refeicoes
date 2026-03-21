"""Rotas de API: health check e cron endpoints."""

import secrets
from datetime import datetime

from flask import abort, current_app, request

import config as cfg
from core.database import db
from core.backup import ensure_daily_backup, limpar_backups_antigos
from core.autofill import autopreencher_refeicoes_semanais

from blueprints.api import api_bp


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
    """Health check — verifica BD e devolve JSON + 200 (ou 503 se falhar)."""
    import time as _time

    t0 = _time.monotonic()
    try:
        with db() as conn:
            conn.execute("SELECT 1 FROM utilizadores LIMIT 1").fetchone()
        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        resp = {
            "status": "ok",
            "db": "ok",
            "latency_ms": latency_ms,
            "ts": datetime.now().isoformat(),
        }
        return resp, 200
    except Exception as exc:
        current_app.logger.error(f"health: BD falhou — {exc}")
        return {
            "status": "error",
            "db": "error",
            "ts": datetime.now().isoformat(),
        }, 503


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
        return {"status": "ok", "ts": datetime.now().isoformat()}
    except Exception as exc:
        current_app.logger.error(f"api_backup_cron: {exc}")
        return {"status": "error", "msg": str(exc)}, 500


@api_bp.route("/api/autopreencher-cron", methods=["POST"])
def api_autopreencher_cron():
    """Endpoint para cron externo pré-preencher refeições semanalmente.
    Uso: curl -X POST -H "Authorization: Bearer <CRON_API_TOKEN>" http://host/api/autopreencher-cron
    """
    if not _verify_cron_token():
        abort(403)
    try:
        autopreencher_refeicoes_semanais(cfg.DIAS_ANTECEDENCIA)
        return {"status": "ok", "ts": datetime.now().isoformat()}
    except Exception as exc:
        current_app.logger.error(f"api_autopreencher_cron: {exc}")
        return {"status": "error", "msg": str(exc)}, 500
