"""Rotas admin — gestão de companhias, turmas e promoções."""

from __future__ import annotations

from flask import (
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from blueprints.admin import admin_bp
from core.companhias import (
    assign_turma,
    create_turma,
    delete_turma,
    get_companhias_data,
    move_aluno_ano,
    promote_all_in_year,
    promote_all_years,
    promote_one,
)
from utils.auth import role_required
from utils.constants import ANOS_OPCOES
from utils.helpers import _ano_label
from utils.validators import _val_ano, _val_int_id, _val_ni, _val_nii, _val_text


@admin_bp.route("/admin/companhias", methods=["GET", "POST"])
@role_required("admin")
def admin_companhias():
    if request.method == "POST":
        acao = request.form.get("acao", "")

        if acao == "criar_turma":
            nome_turma = _val_text(request.form.get("nome_turma", ""), 100)
            ano_turma = request.form.get("ano_turma", "").strip()
            descricao = _val_text(request.form.get("descricao", ""), 200)
            if not nome_turma or not ano_turma:
                flash("Nome e ano são obrigatórios.", "error")
            else:
                try:
                    ano_int = _val_ano(ano_turma)
                    if ano_int is None:
                        flash("Ano inválido (0-8).", "error")
                        return redirect(url_for(".admin_companhias"))
                    create_turma(nome_turma, ano_int, descricao or None)
                    flash(
                        f'Turma "{nome_turma}" ({_ano_label(ano_int)}) criada com sucesso!',
                        "ok",
                    )
                except Exception as ex:
                    flash(f"Erro ao criar turma: {ex}", "error")

        elif acao == "eliminar_turma":
            tid = _val_int_id(request.form.get("tid", ""))
            if tid is None:
                flash("ID de turma inválido.", "error")
                return redirect(url_for(".admin_companhias"))
            try:
                delete_turma(tid)
                flash("Turma eliminada.", "ok")
            except Exception as ex:
                flash(f"Erro: {ex}", "error")

        elif acao == "atribuir_turma":
            nii_at = request.form.get("nii_at", "").strip()
            tid_at = request.form.get("turma_id", "").strip()
            if nii_at:
                try:
                    turma_val = int(tid_at) if tid_at else None
                    assign_turma(nii_at, turma_val)
                    flash(f"Turma do aluno {nii_at} atualizada.", "ok")
                except Exception as ex:
                    flash(f"Erro: {ex}", "error")

        elif acao == "mover_aluno":
            nii_m = _val_nii(request.form.get("nii_m", ""))
            novo_ano_v = _val_ano(request.form.get("novo_ano", ""))
            if not nii_m:
                flash("NII inválido.", "error")
            elif novo_ano_v is None:
                flash("Ano inválido (0-8).", "error")
            else:
                try:
                    move_aluno_ano(nii_m, novo_ano_v)
                    flash(f"Aluno {nii_m} movido para {_ano_label(novo_ano_v)}.", "ok")
                except Exception as ex:
                    flash(f"Erro: {ex}", "error")

        elif acao == "promover_um":
            uid_p = _val_int_id(request.form.get("uid", ""))
            novo_ni = _val_ni(request.form.get("novo_ni", ""))
            if uid_p is None:
                flash("ID inválido.", "error")
                return redirect(url_for(".admin_companhias"))
            dest = promote_one(uid_p, novo_ni)
            flash(f"Aluno promovido para {dest}.", "ok")

        elif acao == "promover_todos":
            ano_origem = _val_ano(request.form.get("ano_origem", 0))
            if ano_origem is None:
                flash("Ano de origem inválido.", "error")
                return redirect(url_for(".admin_companhias"))
            dest = promote_all_in_year(ano_origem)
            flash(
                f"Todos os alunos do {_ano_label(ano_origem)} promovidos para {dest}.",
                "ok",
            )

        elif acao == "promover_todos_anos":
            promote_all_years()
            flash("Promoção global concluída.", "ok")

        return redirect(url_for(".admin_companhias"))

    data = get_companhias_data()

    return render_template(
        "admin/companhias.html",
        anos_data=data["anos_data"],
        all_anos=data["all_anos"],
        turmas=data["turmas"],
        alunos_all=data["alunos_all"],
        promocao_data=data["promocao_data"],
        ANOS_OPCOES=ANOS_OPCOES,
    )


@admin_bp.route("/admin/turmas")
@role_required("admin")
def admin_turmas():
    return redirect(url_for(".admin_companhias"))


@admin_bp.route("/admin/promover", methods=["GET", "POST"])
@role_required("admin")
def admin_promover():
    return redirect(url_for(".admin_companhias") + "#promocao")
