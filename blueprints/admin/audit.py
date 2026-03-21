"""Rotas admin — log de refeições e auditoria de ações."""

from __future__ import annotations

from flask import (
    current_app,
    render_template,
    request,
)

from blueprints.admin import admin_bp
from core.audit import query_admin_audit, query_meal_log
from utils.auth import role_required
from utils.validators import _val_int_id


@admin_bp.route("/admin/log")
@role_required("admin")
def admin_log():
    q_nome = request.args.get("q_nome", "").strip()
    q_por = request.args.get("q_por", "").strip()
    q_campo = request.args.get("q_campo", "").strip()
    q_d0 = request.args.get("d0", "").strip()
    q_d1 = request.args.get("d1", "").strip()
    q_limit_str = request.args.get("limite", "500")
    try:
        q_limit = min(int(q_limit_str), 5000)
    except Exception:
        q_limit = 500

    rows, total_logs, campos_disponiveis = query_meal_log(
        q_nome=q_nome,
        q_por=q_por,
        q_campo=q_campo,
        q_d0=q_d0,
        q_d1=q_d1,
        limit=q_limit,
    )

    mostrando = len(rows)
    filtros_ativos = any([q_nome, q_por, q_campo, q_d0, q_d1])

    return render_template(
        "admin/log.html",
        rows=rows,
        total_logs=total_logs,
        campos_disponiveis=campos_disponiveis,
        mostrando=mostrando,
        filtros_ativos=filtros_ativos,
        q_nome=q_nome,
        q_por=q_por,
        q_campo=q_campo,
        q_d0=q_d0,
        q_d1=q_d1,
        q_limit=q_limit,
    )


@admin_bp.route("/admin/auditoria")
@role_required("admin")
def admin_audit():
    """Registo de ações administrativas (logins, criação/edição de utilizadores, etc.)."""
    limite = min(_val_int_id(request.args.get("limite", "500")) or 500, 5000)
    q_actor = request.args.get("actor", "").strip()
    q_action = request.args.get("action", "").strip()

    try:
        rows, total = query_admin_audit(
            actor=q_actor,
            action=q_action,
            limit=limite,
        )
    except Exception as exc:
        current_app.logger.error(f"admin_audit: {exc}")
        rows, total = [], 0

    ACTION_ICONS = {
        "login": "🔑",
        "criar_utilizador": "➕",
        "editar_utilizador": "✏️",
        "reset_password": "🔄",
        "eliminar_utilizador": "🗑️",
    }

    return render_template(
        "admin/auditoria.html",
        rows=rows,
        total=total,
        limite=limite,
        q_actor=q_actor,
        q_action=q_action,
        ACTION_ICONS=ACTION_ICONS,
    )
