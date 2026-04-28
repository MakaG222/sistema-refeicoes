"""Rotas admin — gestão de companhias, turmas e promoções."""

from __future__ import annotations

import sqlite3

from flask import (
    current_app,
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
from utils.constants import ANOS_OPCOES, MSG_ERRO_INTERNO, MSG_ID_INVALIDO
from utils.helpers import _ano_label
from utils.validators import _val_ano, _val_int_id, _val_ni, _val_nii, _val_text


@admin_bp.route("/admin/companhias", methods=["GET", "POST"])
@role_required("admin")
def admin_companhias():
    if request.method == "POST":
        acao = request.form.get("acao", "")
        # Mapear cada acção à aba onde acontece, para que o redirect preserve
        # o contexto (e o flash apareça na aba certa).
        tab_por_acao = {
            "criar_turma": "turmas",
            "eliminar_turma": "turmas",
            "atribuir_turma": "atribuir",
            "mover_aluno": "mover",
            "promover_um": "promocao",
            "promover_todos": "promocao",
            "promover_todos_anos": "promocao",
        }
        anchor = "#" + tab_por_acao.get(acao, "turmas")

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
                except sqlite3.IntegrityError:
                    flash("Turma duplicada ou violação de integridade.", "error")
                except Exception as ex:
                    current_app.logger.error("criar_turma: %s", ex)
                    flash(MSG_ERRO_INTERNO, "error")

        elif acao == "eliminar_turma":
            tid = _val_int_id(request.form.get("tid", ""))
            if tid is None:
                flash(MSG_ID_INVALIDO, "error")
                return redirect(url_for(".admin_companhias"))
            try:
                delete_turma(tid)
                flash("Turma eliminada.", "ok")
            except Exception as ex:
                current_app.logger.error("eliminar_turma: %s", ex)
                flash(MSG_ERRO_INTERNO, "error")

        elif acao == "atribuir_turma":
            nii_at = request.form.get("nii_at", "").strip()
            tid_at = request.form.get("turma_id", "").strip()
            if not nii_at:
                flash("NII em falta — indica o aluno a atribuir.", "error")
            else:
                try:
                    turma_val = int(tid_at) if tid_at else None
                    if assign_turma(nii_at, turma_val):
                        flash(f"Turma do aluno {nii_at} atualizada.", "ok")
                    else:
                        flash(
                            f"NII {nii_at} não encontrado (ou não é aluno).",
                            "error",
                        )
                except ValueError:
                    flash(MSG_ID_INVALIDO, "error")
                except Exception as ex:
                    current_app.logger.error("atribuir_turma: %s", ex)
                    flash(MSG_ERRO_INTERNO, "error")

        elif acao == "mover_aluno":
            nii_m = _val_nii(request.form.get("nii_m", ""))
            novo_ano_v = _val_ano(request.form.get("novo_ano", ""))
            if not nii_m:
                flash("NII inválido.", "error")
            elif novo_ano_v is None:
                flash("Ano inválido (0-8).", "error")
            else:
                try:
                    if move_aluno_ano(nii_m, novo_ano_v):
                        flash(
                            f"Aluno {nii_m} movido para {_ano_label(novo_ano_v)}.",
                            "ok",
                        )
                    else:
                        flash(
                            f"NII {nii_m} não encontrado (ou não é aluno).",
                            "error",
                        )
                except Exception as ex:
                    current_app.logger.error("mover_aluno: %s", ex)
                    flash(MSG_ERRO_INTERNO, "error")

        elif acao == "promover_um":
            uid_p = _val_int_id(request.form.get("uid", ""))
            novo_ni = _val_ni(request.form.get("novo_ni", ""))
            if uid_p is None:
                flash("ID inválido.", "error")
                return redirect(url_for(".admin_companhias") + anchor)
            dest = promote_one(uid_p, novo_ni)
            if dest == "Não encontrado":
                flash(f"Aluno com ID {uid_p} não encontrado.", "error")
            else:
                flash(f"Aluno promovido para {dest}.", "ok")

        elif acao == "promover_todos":
            ano_origem = _val_ano(request.form.get("ano_origem", 0))
            if ano_origem is None:
                flash("Ano de origem inválido.", "error")
                return redirect(url_for(".admin_companhias") + anchor)
            dest = promote_all_in_year(ano_origem)
            flash(
                f"Todos os alunos do {_ano_label(ano_origem)} promovidos para {dest}.",
                "ok",
            )

        elif acao == "promover_todos_anos":
            counts = promote_all_years()
            parts = [f"{c} do {_ano_label(a)}" for a, c in counts.items() if c > 0]
            detail = ", ".join(parts) if parts else "nenhum aluno"
            flash(f"Promoção global concluída: {detail}.", "ok")

        return redirect(url_for(".admin_companhias") + anchor)

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
