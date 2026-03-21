"""Rotas do blueprint cmd."""

from datetime import date

from flask import (
    render_template,
    flash,
    redirect,
    request,
    url_for,
)

from core.auth_db import user_by_nii
from core.database import db
from blueprints.cmd import cmd_bp
from utils.auth import current_user, role_required
from utils.business import (
    _auto_marcar_refeicoes_detido,
    _registar_ausencia,
    _remover_ausencia,
)
from utils.helpers import (
    _audit,
    _parse_date,
)
from utils.passwords import _reset_pw
from utils.validators import (
    _val_email,
    _val_int_id,
    _val_ni,
    _val_nome,
    _val_phone,
    _val_text,
)


@cmd_bp.route("/cmd/editar-aluno/<nii>", methods=["GET", "POST"])
@role_required("cmd", "oficialdia", "admin")
def cmd_editar_aluno(nii):
    u = current_user()
    perfil = u.get("perfil")
    ano_cmd = int(u.get("ano", 0)) if u.get("ano") else 0
    ano_ret = request.args.get("ano", str(ano_cmd) if ano_cmd else "1")
    d_ret = request.args.get("d", date.today().isoformat())

    # Buscar o aluno
    with db() as conn:
        aluno = dict(
            conn.execute(
                "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NII=?",
                (nii,),
            ).fetchone()
            or {}
        )

    if not aluno:
        flash("Aluno não encontrado.", "error")
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
                with db() as conn:
                    conn.execute(
                        "UPDATE utilizadores SET Nome_completo=?,NI=?,email=?,telemovel=? WHERE NII=?",
                        (nome_n, ni_n or None, email_n, telef_n, nii),
                    )
                    conn.commit()
                flash(f"Dados de {nome_n} actualizados.", "ok")
                return redirect(
                    url_for(
                        "lista_alunos_ano", ano=ano_ret or aluno.get("ano", 1), d=d_ret
                    )
                )
            except Exception as ex:
                flash(f"Erro: {ex}", "error")

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

    with db() as conn:
        aluno = conn.execute(
            "SELECT NII, Nome_completo, ano, perfil FROM utilizadores WHERE NII=?",
            (nii,),
        ).fetchone()

    if not aluno:
        flash("Aluno não encontrado.", "error")
        return redirect(
            url_for("operations.lista_alunos_ano", ano=ano_cmd or 1, d=d_ret)
        )

    aluno = dict(aluno)

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

    with db() as conn:
        aluno = conn.execute(
            "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NII=?",
            (nii,),
        ).fetchone()

    if not aluno:
        flash("Aluno não encontrado.", "error")
        return redirect(url_for("operations.painel_dia"))
    aluno = dict(aluno)

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
    with db() as conn:
        total_ref = conn.execute(
            "SELECT COUNT(*) c FROM refeicoes WHERE utilizador_id=?", (uid,)
        ).fetchone()["c"]
        ausencias_ativas = conn.execute(
            """SELECT COUNT(*) c FROM ausencias WHERE utilizador_id=?
               AND ausente_de<=? AND ausente_ate>=?""",
            (uid, hoje.isoformat(), hoje.isoformat()),
        ).fetchone()["c"]
        aus_recentes = [
            dict(r)
            for r in conn.execute(
                """SELECT ausente_de, ausente_ate, motivo FROM ausencias
               WHERE utilizador_id=? ORDER BY ausente_de DESC LIMIT 5""",
                (uid,),
            ).fetchall()
        ]
        # Refeições de hoje
        ref_hoje = conn.execute(
            "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
            (uid, hoje.isoformat()),
        ).fetchone()

    ref_hoje = dict(ref_hoje) if ref_hoje else {}

    back_url = url_for(
        "operations.lista_alunos_ano", ano=ano_ret or aluno["ano"], d=d_ret
    )
    return render_template(
        "cmd/perfil_aluno.html",
        aluno=aluno,
        back_url=back_url,
        ano_ret=ano_ret,
        d_ret=d_ret,
        total_ref=total_ref,
        ausencias_ativas=ausencias_ativas,
        aus_recentes=aus_recentes,
        ref_hoje=ref_hoje,
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
                flash("ID inválido.", "error")
                return redirect(url_for(".ausencias_cmd"))
            # Validar que a ausência pertence ao ano do cmd
            with db() as conn:
                aus = conn.execute(
                    """SELECT a.id FROM ausencias a
                    JOIN utilizadores u ON u.id=a.utilizador_id
                    WHERE a.id=? AND (u.ano=? OR ?=0)""",
                    (aid, ano_cmd, perfil == "admin"),
                ).fetchone()
            if aus:
                _remover_ausencia(aid)
                flash("Ausência removida.", "ok")
            else:
                flash("Não autorizado.", "error")
            return redirect(url_for(".ausencias_cmd"))
        nii = request.form.get("nii", "").strip()
        db_u = user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
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

    with db() as conn:
        if perfil == "cmd":
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                       a.ausente_de, a.ausente_ate, a.motivo
                    FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
                    WHERE u.perfil='aluno' AND u.ano=?
                    ORDER BY a.ausente_de DESC""",
                    (ano_cmd,),
                ).fetchall()
            ]
        else:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                       a.ausente_de, a.ausente_ate, a.motivo
                    FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
                    WHERE u.perfil='aluno'
                    ORDER BY a.ausente_de DESC"""
                ).fetchall()
            ]

    # Alunos do ano para pesquisa rápida
    with db() as conn:
        alunos_ano = (
            [
                dict(r)
                for r in conn.execute(
                    "SELECT NII, NI, Nome_completo FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI",
                    (ano_cmd,),
                ).fetchall()
            ]
            if perfil == "cmd"
            else []
        )

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
                flash("ID inválido.", "error")
                return redirect(url_for(".detencoes_cmd"))
            with db() as conn:
                ok = conn.execute(
                    """SELECT d.id FROM detencoes d
                    JOIN utilizadores uu ON uu.id=d.utilizador_id
                    WHERE d.id=? AND (uu.ano=? OR ?=1)""",
                    (did, ano_cmd, 1 if perfil == "admin" else 0),
                ).fetchone()
                if ok:
                    conn.execute("DELETE FROM detencoes WHERE id=?", (did,))
                    conn.commit()
                    flash("Detenção removida.", "ok")
                else:
                    flash("Não autorizado.", "error")
            return redirect(url_for(".detencoes_cmd"))

        # criar
        nii = request.form.get("nii", "").strip()
        de = request.form.get("de", "").strip()
        ate = request.form.get("ate", "").strip()
        motivo = _val_text(request.form.get("motivo", ""))[:500]

        db_u = user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
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
            flash("Datas inválidas.", "error")
            return redirect(url_for(".detencoes_cmd"))

        with db() as conn:
            conn.execute(
                """INSERT INTO detencoes(utilizador_id, detido_de, detido_ate, motivo, criado_por)
                            VALUES(?,?,?,?,?)""",
                (db_u["id"], d1.isoformat(), d2.isoformat(), motivo or None, u["nii"]),
            )
            conn.commit()

        # Auto-marcar todas as refeições para os dias de detenção (se não estiverem marcadas)
        _auto_marcar_refeicoes_detido(db_u["id"], d1, d2, u["nii"])

        # Cancelar licenças existentes durante o período de detenção
        with db() as conn:
            conn.execute(
                "DELETE FROM licencas WHERE utilizador_id=? AND data>=? AND data<=?",
                (db_u["id"], d1.isoformat(), d2.isoformat()),
            )
            conn.commit()

        flash(
            f"Detenção registada para {db_u['Nome_completo']}. Refeições auto-marcadas.",
            "ok",
        )
        return redirect(url_for(".detencoes_cmd"))

    with db() as conn:
        if perfil == "cmd":
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT d.id, uu.NII, uu.Nome_completo, uu.NI, uu.ano,
                       d.detido_de, d.detido_ate, d.motivo
                    FROM detencoes d
                    JOIN utilizadores uu ON uu.id=d.utilizador_id
                    WHERE uu.perfil='aluno' AND uu.ano=?
                    ORDER BY d.detido_de DESC""",
                    (ano_cmd,),
                ).fetchall()
            ]
        else:
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT d.id, uu.NII, uu.Nome_completo, uu.NI, uu.ano,
                       d.detido_de, d.detido_ate, d.motivo
                    FROM detencoes d
                    JOIN utilizadores uu ON uu.id=d.utilizador_id
                    WHERE uu.perfil='aluno'
                    ORDER BY d.detido_de DESC"""
                ).fetchall()
            ]

    with db() as conn:
        if perfil == "cmd":
            alunos_ano = [
                dict(r)
                for r in conn.execute(
                    "SELECT NII, NI, Nome_completo FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI",
                    (ano_cmd,),
                ).fetchall()
            ]
        elif perfil == "admin":
            alunos_ano = [
                dict(r)
                for r in conn.execute(
                    "SELECT NII, NI, Nome_completo FROM utilizadores WHERE perfil='aluno' ORDER BY ano, NI"
                ).fetchall()
            ]
        else:
            alunos_ano = []

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
