"""Rotas admin — calendário operacional."""

from __future__ import annotations

import logging
from datetime import date, datetime

from flask import (
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from blueprints.admin import admin_bp
from core.calendar import add_entries, get_upcoming, remove_entry
from utils.auth import current_user, role_required
from utils.validators import _val_date_range, _val_text, _val_tipo_calendario

log = logging.getLogger(__name__)


@admin_bp.route("/admin/calendario", methods=["GET", "POST"])
@role_required("admin", "cmd")
def admin_calendario():
    u = current_user()
    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "adicionar":
            try:
                dia_de = request.form.get("dia_de", "").strip()
                dia_ate = request.form.get("dia_ate", "").strip() or dia_de
                tipo = _val_tipo_calendario(request.form.get("tipo", "normal"))
                nota = _val_text(request.form.get("nota", ""), 200) or None
                if not dia_de:
                    flash("Data de início obrigatória.", "error")
                else:
                    d_de = datetime.strptime(dia_de, "%Y-%m-%d").date()
                    d_ate = datetime.strptime(dia_ate, "%Y-%m-%d").date()
                    range_ok, range_msg = _val_date_range(d_de, d_ate)
                    if not range_ok:
                        flash(range_msg, "error")
                    else:
                        count = add_entries(d_de, d_ate, tipo, nota)
                        flash(
                            f"{count} dia(s) adicionado(s) ao calendário ({dia_de} → {dia_ate}).",
                            "ok",
                        )
            except ValueError as e:
                flash(f"Data inválida: {e}", "error")
            except Exception as e:
                log.exception("admin_calendario: erro ao adicionar entrada")
                flash(str(e), "error")
        elif acao == "remover":
            remove_entry(request.form.get("dia", ""))
            flash("Removido.", "ok")
        return redirect(url_for(".admin_calendario"))

    hoje = date.today()
    entradas = get_upcoming(hoje)

    TIPOS = ["normal", "fim_semana", "feriado", "exercicio", "outro"]
    ICONES = {
        "normal": "✅",
        "fim_semana": "🔵",
        "feriado": "🔴",
        "exercicio": "🟡",
        "outro": "⚪",
    }

    back_url = (
        url_for(".admin_home")
        if u.get("perfil") == "admin"
        else url_for("operations.painel_dia")
    )

    return render_template(
        "admin/calendario.html",
        hoje=hoje,
        entradas=entradas,
        TIPOS=TIPOS,
        ICONES=ICONES,
        back_url=back_url,
    )
