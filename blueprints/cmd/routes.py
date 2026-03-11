"""Rotas do blueprint cmd."""

from datetime import date

from flask import (
    render_template,
    flash,
    redirect,
    request,
    url_for,
)
from markupsafe import Markup

import sistema_refeicoes_v8_4 as sr
from blueprints.cmd import cmd_bp
from utils.auth import current_user, role_required
from utils.business import (
    _auto_marcar_refeicoes_detido,
    _registar_ausencia,
    _remover_ausencia,
)
from utils.helpers import (
    _audit,
    _back_btn,
    _parse_date,
    csrf_input,
    esc,
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
    with sr.db() as conn:
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
                with sr.db() as conn:
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
    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url, f"{ano_ret}º Ano")}
        <div class="page-title">👤 Editar aluno — {esc(aluno.get("Nome_completo", ""))}</div>
      </div>
      <div class="card" style="max-width:560px">
        <div class="card-title">ℹ️ Dados do aluno
          <span class="badge badge-info" style="margin-left:.4rem">{aluno["ano"]}º Ano</span>
        </div>
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group">
              <label>Nome completo</label>
              <input type="text" name="nome" value="{esc(aluno.get("Nome_completo", ""))}" required>
            </div>
            <div class="form-group">
              <label>NI <span class="text-muted small">(número interno)</span></label>
              <input type="text" name="ni" value="{esc(aluno.get("NI") or "")}">
            </div>
            <div class="form-group">
              <label>📧 Email</label>
              <input type="email" name="email" value="{esc(aluno.get("email") or "")}" placeholder="email@exemplo.pt">
            </div>
            <div class="form-group">
              <label>📱 Telemóvel</label>
              <input type="tel" name="telemovel" value="{esc(aluno.get("telemovel") or "")}" placeholder="+351XXXXXXXXX">
            </div>
          </div>
          <div class="alert alert-info" style="font-size:.81rem;margin-bottom:.8rem">
            📌 NII: <strong>{esc(aluno["NII"])}</strong> — Este campo não pode ser alterado aqui.
            Para alterar o NII contacta o administrador.
          </div>
          <div class="gap-btn">
            <button class="btn btn-ok">💾 Guardar alterações</button>
            <a class="btn btn-ghost" href="{back_url}">Cancelar</a>
          </div>
        </form>
        <hr style="margin:1rem 0">
        <form method="post" action="{url_for(".cmd_reset_password", nii=nii)}"
              onsubmit="return confirm('Tens a certeza que queres resetar a password de {esc(aluno.get("Nome_completo", ""))}?')">
          {csrf_input()}
          <input type="hidden" name="ano" value="{ano_ret}">
          <input type="hidden" name="d" value="{d_ret}">
          <button class="btn btn-danger btn-sm">🔑 Resetar password</button>
          <span class="text-muted small" style="margin-left:.5rem">Gera uma password temporária (o aluno terá de mudar no próximo login)</span>
        </form>
      </div>
    </div>"""
    return render_template("cmd/editar_aluno.html", content=Markup(content))


@cmd_bp.route("/cmd/reset-password/<nii>", methods=["POST"])
@role_required("cmd", "oficialdia", "admin")
def cmd_reset_password(nii):
    u = current_user()
    perfil = u.get("perfil")
    ano_cmd = int(u.get("ano", 0)) if u.get("ano") else 0
    ano_ret = request.form.get("ano", str(ano_cmd) if ano_cmd else "1")
    d_ret = request.form.get("d", date.today().isoformat())

    with sr.db() as conn:
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

    ok, result = _reset_pw(nii)
    if ok:
        _audit(
            u["nii"],
            "cmd_reset_password",
            f"NII={nii} por {u['nome']} ({perfil})",
        )
        flash(
            f"Password de {aluno['Nome_completo']} resetada. Temporária: {result}",
            "ok",
        )
    else:
        flash(f"Erro: {result}", "error")

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

    with sr.db() as conn:
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
    with sr.db() as conn:
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

    def yn(v, t=None):
        return (
            f'<span class="badge badge-ok">{t or "✅"}</span>'
            if v
            else '<span class="badge badge-muted">—</span>'
        )

    aus_html = ""
    for a in aus_recentes:
        aus_html += f'<div style="font-size:.82rem;padding:.25rem 0;border-bottom:1px solid var(--border)">{a["ausente_de"]} → {a["ausente_ate"]} <span class="text-muted small">{esc(a["motivo"] or "—")}</span></div>'

    back_url = url_for(
        "operations.lista_alunos_ano", ano=ano_ret or aluno["ano"], d=d_ret
    )
    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url, f"{ano_ret or aluno['ano']}º Ano")}
        <div class="page-title">👁 Perfil — {esc(aluno.get("Nome_completo", ""))}</div>
        <span class="badge badge-info">Só leitura</span>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">ℹ️ Informação pessoal</div>
          <div style="display:flex;flex-direction:column;gap:.7rem;font-size:.9rem">
            <div><span class="text-muted">Nome completo:</span><br><strong>{esc(aluno["Nome_completo"])}</strong></div>
            <div><span class="text-muted">NII:</span><br><strong>{esc(aluno["NII"])}</strong></div>
            <div><span class="text-muted">NI:</span><br><strong>{esc(aluno.get("NI") or "—")}</strong></div>
            <div><span class="text-muted">Ano:</span><br><strong>{aluno["ano"]}º Ano</strong></div>
            <div><span class="text-muted">📧 Email:</span><br><strong>{esc(aluno.get("email") or "—")}</strong></div>
            <div><span class="text-muted">📱 Telemóvel:</span><br><strong>{esc(aluno.get("telemovel") or "—")}</strong></div>
          </div>
          <hr style="margin:1rem 0">
          <div class="grid grid-2">
            <div class="stat-box"><div class="stat-num">{total_ref}</div><div class="stat-lbl">Refeições registadas</div></div>
            <div class="stat-box"><div class="stat-num" style="color:{"var(--warn)" if ausencias_ativas else "var(--ok)"}">{ausencias_ativas}</div><div class="stat-lbl">Ausências ativas</div></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">🍽️ Refeições de hoje — {hoje.strftime("%d/%m/%Y")}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.8rem">
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">☕ Pequeno Almoço</div>
              <div style="margin-top:.3rem">{yn(ref_hoje.get("pequeno_almoco"))}</div>
            </div>
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">🥐 Lanche</div>
              <div style="margin-top:.3rem">{yn(ref_hoje.get("lanche"))}</div>
            </div>
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">🍽️ Almoço</div>
              <div style="margin-top:.3rem"><strong>{ref_hoje.get("almoco") or "—"}</strong></div>
            </div>
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">🌙 Jantar</div>
              <div style="margin-top:.3rem"><strong>{ref_hoje.get("jantar_tipo") or "—"}</strong></div>
            </div>
          </div>
          {'<div class="alert alert-warn" style="font-size:.82rem">⚠️ Aluno com ausência ativa hoje</div>' if ausencias_ativas else ""}
          <div class="card-title" style="margin-top:.8rem">📋 Ausências recentes</div>
          {aus_html or '<div class="text-muted small">Sem ausências registadas.</div>'}
        </div>
      </div>
      <div class="alert alert-info" style="font-size:.82rem">
        🔒 Estás no modo de visualização. Para editar dados do aluno, contacta o Comandante de Companhia ou o Administrador.
      </div>
    </div>"""
    return render_template("cmd/perfil_aluno.html", content=Markup(content))


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
            with sr.db() as conn:
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
        db_u = sr.user_by_nii(nii)
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

    filtro_ano = f"AND u.ano={ano_cmd}" if perfil == "cmd" else ""
    with sr.db() as conn:
        rows = [
            dict(r)
            for r in conn.execute(f"""
            SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                   a.ausente_de, a.ausente_ate, a.motivo
            FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
            WHERE u.perfil='aluno' {filtro_ano}
            ORDER BY a.ausente_de DESC""").fetchall()
        ]

    # Alunos do ano para pesquisa rápida
    with sr.db() as conn:
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
    rows_html = "".join(
        f"""
      <tr>
        <td><strong>{esc(r["Nome_completo"])}</strong><br><span class="text-muted small">{esc(r["NII"])} · {r["ano"]}º ano</span></td>
        <td>{r["ausente_de"]}</td><td>{r["ausente_ate"]}</td>
        <td>{esc(r["motivo"] or "—")}</td>
        <td>{'<span class="badge badge-warn">Atual</span>' if r["ausente_de"] <= hoje <= r["ausente_ate"] else '<span class="badge badge-muted">Inativa</span>'}</td>
        <td><form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="remover"><input type="hidden" name="id" value="{r["id"]}"><button class="btn btn-danger btn-sm">🗑</button></form></td>
      </tr>"""
        for r in rows
    )

    alunos_options = "".join(
        f'<option value="{esc(a["NII"])}">{esc(a["NI"])} — {esc(a["Nome_completo"])}</option>'
        for a in alunos_ano
    )
    alunos_datalist = (
        f'<datalist id="alunos_list">{alunos_options}</datalist>' if alunos_ano else ""
    )

    titulo = (
        f"🚫 Ausências — {ano_cmd}º Ano"
        if perfil == "cmd"
        else "🚫 Ausências (todos os anos)"
    )
    back_url = (
        url_for("operations.painel_dia")
        if perfil == "cmd"
        else url_for("operations.ausencias")
    )

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">{titulo}</div></div>
      <div class="card">
        <div class="card-title">Registar ausência</div>
        {alunos_datalist}
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group">
              <label>NII do aluno</label>
              <input type="text" name="nii" maxlength="32" required placeholder="NII" list="alunos_list">
              {'<div class="text-muted small" style="margin-top:.25rem">💡 Escreve para ver sugestões de alunos do teu ano</div>' if alunos_ano else ""}
            </div>
            <div class="form-group"><label>Motivo (opcional)</label><input type="text" name="motivo" maxlength="500" placeholder="Ex: deslocação, exercício..."></div>
            <div class="form-group"><label>De</label><input type="date" name="de" required value="{hoje}"></div>
            <div class="form-group"><label>Até</label><input type="date" name="ate" required value="{hoje}"></div>
          </div>
          <button class="btn btn-ok">✅ Registar ausência</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Ausências registadas</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Aluno</th><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem ausências.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("cmd/ausencias.html", content=Markup(content))


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
            with sr.db() as conn:
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

        db_u = sr.user_by_nii(nii)
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

        with sr.db() as conn:
            conn.execute(
                """INSERT INTO detencoes(utilizador_id, detido_de, detido_ate, motivo, criado_por)
                            VALUES(?,?,?,?,?)""",
                (db_u["id"], d1.isoformat(), d2.isoformat(), motivo or None, u["nii"]),
            )
            conn.commit()

        # Auto-marcar todas as refeições para os dias de detenção (se não estiverem marcadas)
        _auto_marcar_refeicoes_detido(db_u["id"], d1, d2, u["nii"])

        # Cancelar licenças existentes durante o período de detenção
        with sr.db() as conn:
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

    filtro_ano = f"AND uu.ano={int(ano_cmd)}" if perfil == "cmd" else ""
    with sr.db() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
            SELECT d.id, uu.NII, uu.Nome_completo, uu.NI, uu.ano,
                   d.detido_de, d.detido_ate, d.motivo
            FROM detencoes d
            JOIN utilizadores uu ON uu.id=d.utilizador_id
            WHERE uu.perfil='aluno' {filtro_ano}
            ORDER BY d.detido_de DESC
        """
            ).fetchall()
        ]

    with sr.db() as conn:
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
    rows_html = "".join(
        f"""
      <tr>
        <td><strong>{esc(r["Nome_completo"])}</strong><br><span class="text-muted small">{esc(r["NII"])} · {r["ano"]}º ano</span></td>
        <td>{r["detido_de"]}</td><td>{r["detido_ate"]}</td>
        <td>{esc(r["motivo"] or "—")}</td>
        <td>{'<span class="badge badge-warn">Atual</span>' if r["detido_de"] <= hoje <= r["detido_ate"] else '<span class="badge badge-muted">Inativa</span>'}</td>
        <td><form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="remover"><input type="hidden" name="id" value="{r["id"]}"><button class="btn btn-danger btn-sm">🗑</button></form></td>
      </tr>"""
        for r in rows
    )

    alunos_options = "".join(
        f'<option value="{esc(a["NII"])}">{esc(a["NI"])} — {esc(a["Nome_completo"])}</option>'
        for a in alunos_ano
    )
    alunos_datalist = (
        f'<datalist id="alunos_list">{alunos_options}</datalist>' if alunos_ano else ""
    )

    titulo = (
        f"⛔ Detenções — {ano_cmd}º Ano"
        if perfil == "cmd"
        else "⛔ Detenções (todos os anos)"
    )
    back_url = url_for("operations.painel_dia")

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">{titulo}</div></div>
      <div class="card">
        <div class="card-title">Registar detenção</div>
        {alunos_datalist}
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group">
              <label>NII do aluno</label>
              <input type="text" name="nii" maxlength="32" required placeholder="NII" list="alunos_list">
            </div>
            <div class="form-group"><label>Motivo (opcional)</label><input type="text" name="motivo" maxlength="500" placeholder="Ex: detido por..."></div>
            <div class="form-group"><label>De</label><input type="date" name="de" required value="{hoje}"></div>
            <div class="form-group"><label>Até</label><input type="date" name="ate" required value="{hoje}"></div>
          </div>
          <button class="btn btn-ok">⛔ Registar detenção</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Detenções registadas</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Aluno</th><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem detenções.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("cmd/detencoes.html", content=Markup(content))


# ═══════════════════════════════════════════════════════════════════════════
# LICENÇAS — ENTRADAS / SAÍDAS (Oficial de Dia)
# ═══════════════════════════════════════════════════════════════════════════
