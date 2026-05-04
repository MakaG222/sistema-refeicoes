"""Rotas de API: health check e cron endpoints."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any

from flask import abort, current_app, request

import config as cfg
from core.backup import ensure_daily_backup, limpar_backups_antigos
from core.autofill import autopreencher_refeicoes_semanais
from core.database import db
from core.rate_limit import limiter

from blueprints.api import api_bp

log = logging.getLogger(__name__)


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
        # Sem token configurado: bloquear em produção, avisar e exigir token "dev" fora
        if cfg.is_production:
            return False
        current_app.logger.warning(
            "CRON_API_TOKEN não definido — a aceitar token 'dev' como fallback."
        )
        return secrets.compare_digest(token, "dev")
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
        log.exception("health: falha ao obter tamanho da BD")
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
        log.exception("health: falha ao verificar backup")
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
        log.exception("health: falha ao verificar espaço em disco")

    latency_ms = round((_time.monotonic() - t0) * 1000, 1)
    checks["latency_ms"] = latency_ms

    status_code = 200 if overall == "ok" else 503
    return {"status": overall, "ts": datetime.now().isoformat(), **checks}, status_code


@api_bp.route("/health/metrics")
def health_metrics():
    """Métricas básicas de request — contadores in-memory + per-route."""
    from core.middleware import get_metrics, get_route_metrics

    m = get_metrics()
    count = m["request_count"]
    route_m = get_route_metrics()
    # Top 10 rotas por contagem
    top_routes = dict(
        sorted(route_m.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
    )
    return _api_ok(
        {
            "request_count": count,
            "error_count": m["error_count"],
            "avg_latency_ms": round(m["total_latency_ms"] / max(count, 1), 1),
            "routes": top_routes,
        }
    )


@api_bp.route("/api/backup-cron", methods=["POST"])
@limiter.limit("30 per minute")
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
@limiter.limit("30 per minute")
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


@api_bp.route("/api/export-cron", methods=["POST"])
@limiter.limit("30 per minute")
def api_export_cron():
    """Endpoint para cron externo gerar relatório diário em PDF.

    Query params opcionais:
      - data=YYYY-MM-DD (default: hoje)
      - ano=1..8 (default: todos)

    Uso:
      curl -X POST -H "Authorization: Bearer <TOKEN>" \
           "http://host/api/export-cron?data=2026-04-19"
    """
    if not _verify_cron_token():
        abort(403)
    from datetime import date as _date

    from core.exports import exportacao_pdf_do_dia

    data_s = (request.args.get("data") or "").strip()
    ano_s = (request.args.get("ano") or "").strip()
    try:
        d = _date.fromisoformat(data_s) if data_s else _date.today()
    except ValueError:
        return _api_error("data inválida (esperado YYYY-MM-DD)", status=400)
    ano: int | None = None
    if ano_s:
        try:
            ano = int(ano_s)
        except ValueError:
            return _api_error("ano inválido", status=400)

    try:
        path = exportacao_pdf_do_dia(d, ano)
        return _api_ok({"path": path, "data": d.isoformat(), "ano": ano})
    except Exception as exc:
        current_app.logger.error(f"api_export_cron: {exc}")
        return _api_error(str(exc))


@api_bp.route("/api/unlock-expired", methods=["POST"])
@limiter.limit("30 per minute")
def api_unlock_expired():
    """Cron — limpeza de dados expirados de segurança.

    1. Remove eventos de login `fail` com mais de 24h (evita crescimento
       infinito da tabela login_eventos).
    2. Limpa reset_code expirados.
    3. Remove `locked_until` de utilizadores cujo bloqueio já passou
       (SQLite não expira automaticamente; mantem a lockout funcional
       via comparação em `blueprints/auth/routes.py:login` mas liberta
       a UI de mostrar contas como "bloqueadas" visualmente).

    Uso:
      curl -X POST -H "Authorization: Bearer <TOKEN>" http://host/api/unlock-expired
    """
    if not _verify_cron_token():
        abort(403)
    try:
        with db() as conn:
            # 1. Purga tentativas falhadas antigas (>24h) — retenção legal mínima.
            cur = conn.execute(
                "DELETE FROM login_eventos"
                " WHERE sucesso=0 AND criado_em < datetime('now','localtime','-24 hours')"
            )
            deleted_fails = cur.rowcount
            # 2. Limpa reset_codes expirados (evita accumulation e facilita audit).
            cur2 = conn.execute(
                "UPDATE utilizadores SET reset_code=NULL, reset_expires=NULL"
                " WHERE reset_expires IS NOT NULL"
                " AND reset_expires < datetime('now','localtime')"
            )
            expired_codes = cur2.rowcount
            # 3. Limpa locked_until já expirado.
            cur3 = conn.execute(
                "UPDATE utilizadores SET locked_until=NULL"
                " WHERE locked_until IS NOT NULL"
                " AND locked_until < datetime('now','localtime')"
            )
            unlocked = cur3.rowcount
            # 4. Limpa tokens de check-in expirados (QR rotativo).
            cur4 = conn.execute(
                "DELETE FROM checkin_tokens"
                " WHERE expires_at < datetime('now','localtime')"
            )
            expired_checkin_tokens = cur4.rowcount
            conn.commit()
        current_app.logger.info(
            "unlock-expired: failures=%d reset_codes=%d unlocked=%d checkin_tokens=%d",
            deleted_fails,
            expired_codes,
            unlocked,
            expired_checkin_tokens,
        )
        return _api_ok(
            {
                "deleted_login_failures": deleted_fails,
                "expired_reset_codes": expired_codes,
                "unlocked_users": unlocked,
                "expired_checkin_tokens": expired_checkin_tokens,
            }
        )
    except Exception as exc:
        current_app.logger.error(f"api_unlock_expired: {exc}")
        return _api_error(str(exc))
