"""Rotas do blueprint cmd."""

import logging
from datetime import date

from flask import (
    render_template,
    flash,
    redirect,
    request,
    url_for,
)

from core.absences import get_ausencias_cmd, remover_ausencia_autorizada
from core.auth_db import user_by_nii
from core.detencoes import (
    cancelar_licencas_periodo,
    criar_detencao,
    get_alunos_para_selecao,
    get_detencoes_lista,
    remover_detencao,
)
from core.users import (
    get_aluno_profile_data,
    get_user_by_nii_fields,
    update_aluno_data,
)
from blueprints.cmd import cmd_bp
from utils.auth import current_user, role_required
from utils.business import (
    _auto_marcar_refeicoes_detido,
    _registar_ausencia,
)
from utils.helpers import (
    _audit,
    _parse_date,
)
from utils.passwords import _reset_pw
from utils.constants import MSG_ERRO_INTERNO, MSG_ID_INVALIDO, MSG_NAO_ENCONTRADO
from utils.validators import (
    _val_email,
    _val_int_id,
    _val_ni,
    _val_nome,
    _val_phone,
    _val_text,
)

log = logging.getLogger(__name__)


@cmd_bp.route("/cmd/editar-aluno/<nii>", methods=["GET", "POST"])
@role_required("cmd", "oficialdia", "admin")
def cmd_editar_aluno(nii):
    u = current_user()
    perfil = u.get("perfil")
    ano_cmd = int(u.get("ano", 0)) if u.get("ano") else 0
    ano_ret = request.args.get("ano", str(ano_cmd) if ano_cmd else "1")
    d_ret = request.args.get("d", date.today().isoformat())

    # Buscar o aluno
    aluno = get_user_by_nii_fields(nii) or {}

    if not aluno:
        flash(MSG_NAO_ENCONTRADO, "error")
        back_ano = aluno.get("ano", ano_cmd or 1) if aluno else (ano_cmd or 1)
        return redirect(url_for("operations.lista_alunos_ano", ano=back_ano, d=d_ret))

    # CMD só pode editar alunos do seu ano
    if perfil == "cmd" and int(aluno.get("ano", 0)) != ano_cmd:
        flash("Só podes editar alunos do teu ano.", "error")
        return redirect(url_for("operations.lista_alunos_ano", ano=ano_cmd, d=d_ret))

    if request.method == "POST":
        nome_n = _val_nome(request.form.get("nome", ""))
        ni_n = _val_ni(request.form.get("ni", ""))
        email_n = _val_email(request.form.get("email", ""))
        telef_n = _val_phone(request.form.get("telemovel", ""))
        if not nome_n:
            flash("O nome não pode estar vazio.", "error")
        elif ni_n is None:
            flash("NI inválido (alfanumérico, máx. 20 caracteres).", "error")
        elif email_n is False:
            flash("Email inválido.", "error")
        elif telef_n is False:
            flash("Telemóvel inválido.", "error")
        else:
            try:
                update_aluno_data(nii, nome_n, ni_n, email_n, telef_n)
                flash(f"Dados de {nome_n} actualizados.", "ok")
                return redirect(
                    url_for(
                        "lista_alunos_ano", ano=ano_ret or aluno.get("ano", 1), d=d_ret
                    )
                )
            except Exception:
                log.exception("cmd_editar_aluno: erro ao atualizar dados")
                flash(MSG_ERRO_INTERNO, "error")

    back_url = url_for("operations.lista_alunos_ano", ano=ano_ret, d=d_ret)
    return render_template(
        "cmd/editar_aluno.html",
        aluno=aluno,
        back_url=back_url,
        ano_ret=ano_ret,
        d_ret=d_ret,
        nii=nii,
    )


@cmd_bp.route("/cmd/reset-password/<nii>", methods=["POST"])
@role_required("cmd", "oficialdia", "admin")
def cmd_reset_password(nii):
    u = current_user()
    perfil = u.get("perfil")
    ano_cmd = int(u.get("ano", 0)) if u.get("ano") else 0
    ano_ret = request.form.get("ano", str(ano_cmd) if ano_cmd else "1")
    d_ret = request.form.get("d", date.today().isoformat())

    aluno = get_user_by_nii_fields(nii, "NII,Nome_completo,ano,perfil")

    if not aluno:
        flash(MSG_NAO_ENCONTRADO, "error")
        return redirect(
            url_for("operations.lista_alunos_ano", ano=ano_cmd or 1, d=d_ret)
        )

    # Só pode resetar alunos (não admins/cmd/cozinha/oficialdia)
    if aluno.get("perfil") != "aluno":
        flash("Só é possível resetar passwords de alunos.", "error")
        return redirect(url_for("operations.lista_alunos_ano", ano=ano_ret, d=d_ret))

    # CMD só pode resetar alunos do seu ano
    if perfil == "cmd" and int(aluno.get("ano", 0)) != ano_cmd:
        flash("Só podes resetar passwords de alunos do teu ano.", "error")
        return redirect(url_for("operations.lista_alunos_ano", ano=ano_cmd, d=d_ret))

    ok, msg = _reset_pw(nii)
    if ok:
        _audit(
            u["nii"],
            "cmd_reset_password",
            f"NII={nii} por {u['nome']} ({perfil})",
        )
        flash(
            f"Password de {aluno['Nome_completo']} resetada. Deve usar o NII como password temporária.",
            "ok",
        )
    else:
        flash(f"Erro: {msg}", "error")

    return redirect(url_for(".cmd_editar_aluno", nii=nii, ano=ano_ret, d=d_ret))


# ═══════════════════════════════════════════════════════════════════════════
# VER PERFIL DE ALUNO — Oficial de Dia (apenas leitura)
# ═══════════════════════════════════════════════════════════════════════════


@cmd_bp.route("/alunos/perfil/<nii>")
@role_required("oficialdia", "admin", "cmd")
def ver_perfil_aluno(nii):
    u = current_user()
    perfil = u.get("perfil")
    ano_ret = request.args.get("ano", "")
    d_ret = request.args.get("d", date.today().isoformat())

    aluno = get_user_by_nii_fields(nii)

    if not aluno:
        flash(MSG_NAO_ENCONTRADO, "error")
        return redirect(url_for("operations.painel_dia"))

    # CMD só pode ver alunos do seu ano
    if perfil == "cmd" and str(aluno.get("ano", 0)) != str(u.get("ano", "")):
        flash("Acesso restrito ao teu ano.", "error")
        return redirect(url_for("operations.painel_dia"))

    # Admin é redirecionado para edição
    if perfil == "admin":
        return redirect(
            url_for(".cmd_editar_aluno", nii=nii, ano=ano_ret or aluno["ano"], d=d_ret)
        )

    hoje = date.today()
    uid = aluno["id"]
    profile = get_aluno_profile_data(uid, hoje.isoformat())

    back_url = url_for(
        "operations.lista_alunos_ano", ano=ano_ret or aluno["ano"], d=d_ret
    )
    return render_template(
        "cmd/perfil_aluno.html",
        aluno=aluno,
        back_url=back_url,
        ano_ret=ano_ret,
        d_ret=d_ret,
        total_ref=profile["total_ref"],
        ausencias_ativas=profile["ausencias_ativas"],
        aus_recentes=profile["aus_recentes"],
        ref_hoje=profile["ref_hoje"],
        det_recentes=profile["det_recentes"],
        detencoes_ativas=profile["detencoes_ativas"],
        hoje=hoje,
    )


# ═══════════════════════════════════════════════════════════════════════════
# AUSÊNCIAS — CMD (acesso restrito ao seu ano)
# ═══════════════════════════════════════════════════════════════════════════


@cmd_bp.route("/cmd/ausencias", methods=["GET", "POST"])
@role_required("cmd", "admin")
def ausencias_cmd():
    u = current_user()
    perfil = u.get("perfil")
    ano_cmd = int(u.get("ano", 0)) if perfil == "cmd" else 0

    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "remover":
            aid = _val_int_id(request.form.get("id"))
            if aid is None:
                flash(MSG_ID_INVALIDO, "error")
                return redirect(url_for(".ausencias_cmd"))
            if remover_ausencia_autorizada(aid, ano_cmd, perfil == "admin"):
                flash("Ausência removida.", "ok")
            else:
                flash(MSG_NAO_ENCONTRADO, "error")
            return redirect(url_for(".ausencias_cmd"))
        if acao == "bulk_ausencia":
            niis = request.form.getlist("niis")
            if not niis:
                flash("Seleciona pelo menos um aluno.", "error")
                return redirect(url_for(".ausencias_cmd"))
            de = request.form.get("de", "")
            ate = request.form.get("ate", "")
            motivo = _val_text(request.form.get("motivo", ""))[:500]
            count_ok = 0
            erros = []
            for nii_b in niis:
                db_b = user_by_nii(nii_b.strip())
                if not db_b:
                    erros.append(f"{nii_b}: não encontrado")
                    continue
                if perfil == "cmd" and int(db_b.get("ano", 0)) != ano_cmd:
                    erros.append(f"{db_b['Nome_completo']}: outro ano")
                    continue
                ok, err = _registar_ausencia(db_b["id"], de, ate, motivo, u["nii"])
                if ok:
                    count_ok += 1
                else:
                    erros.append(f"{db_b['Nome_completo']}: {err}")
            flash(f"{count_ok}/{len(niis)} ausência(s) registada(s).", "ok" if count_ok else "error")
            if erros:
                flash("Falhas: " + "; ".join(erros[:5]), "warn")
            return redirect(url_for(".ausencias_cmd"))

        nii = request.form.get("nii", "").strip()
        db_u = user_by_nii(nii)
        if not db_u:
            flash(MSG_NAO_ENCONTRADO, "error")
        elif perfil == "cmd" and int(db_u.get("ano", 0)) != ano_cmd:
            flash(
                f"Só podes registar ausências para alunos do {ano_cmd}º ano.", "error"
            )
        else:
            ok, err = _registar_ausencia(
                db_u["id"],
                request.form.get("de", ""),
                request.form.get("ate", ""),
                _val_text(request.form.get("motivo", ""))[:500],
                u["nii"],
            )
            flash(
                f"Ausência registada para {db_u['Nome_completo']}."
                if ok
                else (err or "Falha."),
                "ok" if ok else "error",
            )
        return redirect(url_for(".ausencias_cmd"))

    rows = get_ausencias_cmd(ano_cmd if perfil == "cmd" else None)

    # Alunos do ano para pesquisa rápida
    alunos_ano = get_alunos_para_selecao(ano_cmd, perfil) if perfil == "cmd" else []

    hoje = date.today().isoformat()
    titulo = (
        f"Ausências — {ano_cmd}º Ano"
        if perfil == "cmd"
        else "Ausências (todos os anos)"
    )
    back_url = (
        url_for("operations.painel_dia")
        if perfil == "cmd"
        else url_for("operations.ausencias")
    )

    return render_template(
        "cmd/ausencias.html",
        rows=rows,
        alunos_ano=alunos_ano,
        titulo=titulo,
        back_url=back_url,
        hoje=hoje,
    )


# ═══════════════════════════════════════════════════════════════════════════
# DETENÇÕES
# ═══════════════════════════════════════════════════════════════════════════


@cmd_bp.route("/cmd/detencoes", methods=["GET", "POST"])
@role_required("cmd", "admin")
def detencoes_cmd():
    u = current_user()
    perfil = u.get("perfil")
    ano_cmd = int(u.get("ano", 0)) if perfil == "cmd" else 0

    if request.method == "POST":
        acao = request.form.get("acao", "")

        if acao == "remover":
            did = _val_int_id(request.form.get("id", ""))
            if did is None:
                flash(MSG_ID_INVALIDO, "error")
                return redirect(url_for(".detencoes_cmd"))
            if remover_detencao(did, ano_cmd, perfil == "admin"):
                flash("Detenção removida.", "ok")
            else:
                flash("Não autorizado.", "error")
            return redirect(url_for(".detencoes_cmd"))

        if acao == "bulk_detencao":
            niis = request.form.getlist("niis")
            if not niis:
                flash("Seleciona pelo menos um aluno.", "error")
                return redirect(url_for(".detencoes_cmd"))
            de = request.form.get("de", "").strip()
            ate = request.form.get("ate", "").strip()
            motivo = _val_text(request.form.get("motivo", ""))[:500]
            try:
                d1 = _parse_date(de)
                d2 = _parse_date(ate)
                if d2 < d1:
                    flash("A data 'Até' tem de ser igual ou posterior à data 'De'.", "error")
                    return redirect(url_for(".detencoes_cmd"))
            except Exception:
                flash("Datas inválidas.", "error")
                return redirect(url_for(".detencoes_cmd"))
            count_ok = 0
            erros = []
            for nii_b in niis:
                db_b = user_by_nii(nii_b.strip())
                if not db_b:
                    erros.append(f"{nii_b}: não encontrado")
                    continue
                if perfil == "cmd" and int(db_b.get("ano", 0)) != ano_cmd:
                    erros.append(f"{db_b['Nome_completo']}: outro ano")
                    continue
                ok, msg = criar_detencao(db_b["id"], d1, d2, motivo, u["nii"])
                if ok:
                    _auto_marcar_refeicoes_detido(db_b["id"], d1, d2, u["nii"])
                    cancelar_licencas_periodo(db_b["id"], d1, d2)
                    count_ok += 1
                else:
                    erros.append(f"{db_b['Nome_completo']}: {msg}")
            flash(f"{count_ok}/{len(niis)} detenção(ões) registada(s).", "ok" if count_ok else "error")
            if erros:
                flash("Falhas: " + "; ".join(erros[:5]), "warn")
            return redirect(url_for(".detencoes_cmd"))

        # criar
        nii = request.form.get("nii", "").strip()
        de = request.form.get("de", "").strip()
        ate = request.form.get("ate", "").strip()
        motivo = _val_text(request.form.get("motivo", ""))[:500]

        db_u = user_by_nii(nii)
        if not db_u:
            flash(MSG_NAO_ENCONTRADO, "error")
            return redirect(url_for(".detencoes_cmd"))

        if perfil == "cmd" and int(db_u.get("ano", 0)) != ano_cmd:
            flash(
                f"Só podes registar detenções para alunos do {ano_cmd}º ano.", "error"
            )
            return redirect(url_for(".detencoes_cmd"))

        try:
            d1 = _parse_date(de)
            d2 = _parse_date(ate)
            if d2 < d1:
                flash(
                    "A data 'Até' tem de ser igual ou posterior à data 'De'.", "error"
                )
                return redirect(url_for(".detencoes_cmd"))
        except Exception:
            log.exception("detencoes_cmd: erro ao processar datas")
            flash("Datas inválidas.", "error")
            return redirect(url_for(".detencoes_cmd"))

        ok, msg = criar_detencao(db_u["id"], d1, d2, motivo, u["nii"])
        if not ok:
            flash(msg, "error")
            return redirect(url_for(".detencoes_cmd"))

        # Auto-marcar todas as refeições para os dias de detenção (se não estiverem marcadas)
        _auto_marcar_refeicoes_detido(db_u["id"], d1, d2, u["nii"])

        # Cancelar licenças existentes durante o período de detenção
        cancelar_licencas_periodo(db_u["id"], d1, d2)

        flash(
            f"Detenção registada para {db_u['Nome_completo']}. Refeições auto-marcadas.",
            "ok",
        )
        return redirect(url_for(".detencoes_cmd"))

    rows = get_detencoes_lista(ano_cmd if perfil == "cmd" else None)
    alunos_ano = get_alunos_para_selecao(ano_cmd, perfil)

    hoje = date.today().isoformat()
    titulo = (
        f"Detenções — {ano_cmd}º Ano"
        if perfil == "cmd"
        else "Detenções (todos os anos)"
    )
    back_url = url_for("operations.painel_dia")

    return render_template(
        "cmd/detencoes.html",
        rows=rows,
        alunos_ano=alunos_ano,
        titulo=titulo,
        back_url=back_url,
        hoje=hoje,
    )


# ═══════════════════════════════════════════════════════════════════════════
# LICENÇAS — ENTRADAS / SAÍDAS (Oficial de Dia)
# ═══════════════════════════════════════════════════════════════════════════
