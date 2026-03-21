"""Rotas do blueprint admin."""

import io
from datetime import date, datetime, timedelta

from flask import (
    render_template,
    current_app,
    flash,
    redirect,
    request,
    send_file,
    url_for,
)
from markupsafe import Markup

from core.constants import BASE_DADOS
from core.database import db
from core.meals import get_totais_dia
from blueprints.admin import admin_bp
from utils.auth import (
    current_user,
    login_required,
    role_required,
)
from utils.constants import ANOS_OPCOES
from utils.helpers import (
    _ano_label,
    _audit,
    _back_btn,
    _get_anos_disponiveis,
    _parse_date,
    csrf_input,
    esc,
)
from utils.passwords import (
    _criar_utilizador,
    _eliminar_utilizador,
    _reset_pw,
    _unblock_user,
    generate_password_hash,
)
from utils.validators import (
    _val_ano,
    _val_cap,
    _val_date_range,
    _val_email,
    _val_int_id,
    _val_ni,
    _val_nii,
    _val_nome,
    _val_perfil,
    _val_phone,
    _val_text,
    _val_tipo_calendario,
)


@admin_bp.route("/admin")
@role_required("admin")
def admin_home():
    hoje = date.today()
    t = get_totais_dia(hoje.isoformat())
    with db() as conn:
        n_users = conn.execute("SELECT COUNT(*) c FROM utilizadores").fetchone()["c"]

    action_cards = [
        (url_for("operations.painel_dia"), "📋", "Painel do dia", "Ocupação e totais"),
        (
            url_for(".admin_utilizadores"),
            "👥",
            f"Utilizadores ({n_users})",
            "Gerir contas",
        ),
        (url_for(".admin_menus"), "🍽️", "Menus & Capacidade", "Ementas e limites"),
        (
            url_for("reporting.dashboard_semanal"),
            "📊",
            "Dashboard Semanal",
            "Gráficos e relatório",
        ),
        (
            url_for("operations.relatorio_semanal"),
            "📈",
            "Relatório Semanal",
            "Exportar dados",
        ),
        (url_for(".admin_log"), "📜", "Log de Refeições", "Alterações de refeições"),
        (
            url_for(".admin_audit"),
            "🔐",
            "Auditoria de Ações",
            "Logins e alterações admin",
        ),
        (url_for(".admin_calendario"), "⚙️", "Gerir Calendário", "Dias operacionais"),
        (url_for("operations.ausencias"), "🚫", "Ausências", "Gerir ausências"),
        (url_for("cmd.detencoes_cmd"), "⛔", "Detenções", "Registar detenções"),
        (
            url_for("operations.licencas_entradas_saidas"),
            "🚪",
            "Licenças / Entradas",
            "Controlo de saídas",
        ),
        (url_for("reporting.calendario_publico"), "📅", "Calendário", "Ver calendário"),
        (
            url_for(".admin_companhias"),
            "⚓",
            "Gestão de Companhias",
            "Turmas, promoções e cursos",
        ),
        (
            url_for("operations.controlo_presencas"),
            "🎯",
            "Controlo Presenças",
            "Pesquisa rápida por NI",
        ),
        (url_for(".admin_importar_csv"), "📥", "Importar CSV", "Criar alunos em massa"),
        (
            url_for(".admin_backup_download"),
            "💾",
            "Download BD",
            "Descarregar base de dados",
        ),
    ]

    anos = _get_anos_disponiveis()
    ano_cards = "".join(
        f'<a class="action-card" href="{url_for("operations.lista_alunos_ano", ano=a, d=hoje.isoformat())}">'
        f'<div class="icon">👥</div><div class="label">{_ano_label(a)}</div><div class="desc">Lista de presenças</div></a>'
        for a in anos
    )

    cards_html = "".join(
        f'<a class="action-card" href="{href}"><div class="icon">{icon}</div>'
        f'<div class="label">{label}</div><div class="desc">{desc}</div></a>'
        for href, icon, label, desc in action_cards
    )

    total_alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
    total_jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]

    content = f"""
    <div class="container">
      <div class="page-header"><div class="page-title">⚓ Administração — Escola Naval</div></div>
      <div class="card">
        <div class="card-title">📊 Hoje — {hoje.strftime("%d/%m/%Y")}</div>
        <div class="grid grid-4">
          <div class="stat-box"><div class="stat-num">{t["pa"]}</div><div class="stat-lbl">Pequenos Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{t["lan"]}</div><div class="stat-lbl">Lanches</div></div>
          <div class="stat-box"><div class="stat-num">{total_alm}</div><div class="stat-lbl">Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{total_jan}</div><div class="stat-lbl">Jantares</div></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">⚡ Módulos</div>
        <div class="grid grid-4">{cards_html}</div>
      </div>
      <div class="card">
        <div class="card-title">👥 Lista por ano</div>
        <div class="grid grid-4">{ano_cards}</div>
      </div>
    </div>"""
    return render_template("admin/home.html", content=Markup(content))


@admin_bp.route("/admin/utilizadores", methods=["GET", "POST"])
@role_required("admin")
def admin_utilizadores():
    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "criar":
            ok, err = _criar_utilizador(
                request.form.get("nii", "").strip(),
                request.form.get("ni", "").strip(),
                request.form.get("nome", "").strip(),
                request.form.get("ano", "").strip(),
                request.form.get("perfil", "aluno"),
                request.form.get("pw", "").strip(),
            )
            flash(
                "Utilizador criado." if ok else (err or "Erro."),
                "ok" if ok else "error",
            )
        elif acao == "editar_user":
            nii_e = _val_nii(request.form.get("nii", ""))
            nome_e = _val_nome(request.form.get("nome", ""))
            ni_e = _val_ni(request.form.get("ni", ""))
            ano_e = _val_ano(request.form.get("ano", ""))
            perfil_e = _val_perfil(request.form.get("perfil", "aluno"))
            email_e = _val_email(request.form.get("email", ""))
            tel_e = _val_phone(request.form.get("telemovel", ""))
            pw_e = request.form.get("pw", "").strip()[:256]
            if not nii_e:
                flash("NII inválido.", "error")
            elif not nome_e:
                flash("Nome inválido ou vazio.", "error")
            elif ni_e is None:
                flash("NI inválido.", "error")
            elif ano_e is None:
                flash("Ano inválido (0-8).", "error")
            elif not perfil_e:
                flash("Perfil inválido.", "error")
            elif email_e is False:
                flash("Email inválido.", "error")
            elif tel_e is False:
                flash("Telemóvel inválido.", "error")
            else:
                try:
                    with db() as conn:
                        conn.execute(
                            "UPDATE utilizadores SET Nome_completo=?,NI=?,ano=?,perfil=?,email=?,telemovel=? WHERE NII=?",
                            (nome_e, ni_e, ano_e, perfil_e, email_e, tel_e, nii_e),
                        )
                        conn.commit()
                    if pw_e:
                        with db() as conn:
                            conn.execute(
                                "UPDATE utilizadores SET Palavra_chave=?,must_change_password=1 WHERE NII=?",
                                (generate_password_hash(pw_e), nii_e),
                            )
                            conn.commit()
                    _audit(
                        current_user().get("nii", "admin"),
                        "editar_utilizador",
                        f"NII={nii_e}",
                    )
                    flash("Utilizador atualizado.", "ok")
                except Exception as ex:
                    flash(f"Erro: {ex}", "error")
        elif acao == "editar_contactos":
            nii_e = _val_nii(request.form.get("nii", ""))
            email_e = _val_email(request.form.get("email", ""))
            tel_e = _val_phone(request.form.get("telemovel", ""))
            if not nii_e:
                flash("NII inválido.", "error")
            elif email_e is False:
                flash("Email inválido.", "error")
            elif tel_e is False:
                flash("Telemóvel inválido.", "error")
            else:
                try:
                    with db() as conn:
                        conn.execute(
                            "UPDATE utilizadores SET email=?, telemovel=? WHERE NII=?",
                            (email_e, tel_e, nii_e),
                        )
                        conn.commit()
                    _audit(
                        current_user().get("nii", "admin"),
                        "editar_contactos",
                        f"NII={nii_e}",
                    )
                    flash("Contactos atualizados.", "ok")
                except Exception as ex:
                    flash(f"Erro: {ex}", "error")
        elif acao == "reset_pw":
            nii = request.form.get("nii", "")
            ok, nova_pw = _reset_pw(nii)
            if ok:
                _audit(
                    current_user().get("nii", "admin"),
                    "reset_pw_admin",
                    f"NII={nii}",
                )
            flash(
                f"Password resetada. Temporária: {nova_pw}" if ok else nova_pw,
                "ok" if ok else "error",
            )
        elif acao == "desbloquear":
            nii_d = request.form.get("nii", "")
            _unblock_user(nii_d)
            _audit(
                current_user().get("nii", "admin"),
                "desbloquear",
                f"NII={nii_d}",
            )
            flash("Desbloqueado.", "ok")
        elif acao == "eliminar":
            nii = request.form.get("nii", "")
            eliminado = _eliminar_utilizador(nii)
            if eliminado:
                _audit(
                    current_user().get("nii", "admin"),
                    "eliminar_utilizador",
                    f"NII={nii}",
                )
            flash(f"'{nii}' eliminado." if eliminado else "NII não encontrado.", "ok")
        return redirect(url_for(".admin_utilizadores"))

    q = request.args.get("q", "").strip()
    ano_f = request.args.get("ano", "all")
    edit_nii = request.args.get("edit_contactos", "")
    with db() as conn:
        sql = "SELECT id,NII,NI,Nome_completo,ano,perfil,locked_until,email,telemovel FROM utilizadores WHERE 1=1"
        args = []
        if q:
            sql += " AND Nome_completo LIKE ?"
            args.append(f"%{q}%")
        if ano_f != "all":
            sql += " AND ano=?"
            args.append(ano_f)
        sql += " ORDER BY ano, NI"
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]

    edit_user_nii = request.args.get("edit_user", "")
    edit_user_row = next((r for r in rows if r["NII"] == edit_user_nii), None)
    edit_row = next((r for r in rows if r["NII"] == edit_nii), None)

    def action_btns(r):
        ne = esc(r["NII"])
        b = f'<a class="btn btn-gold btn-sm" href="?edit_user={ne}" title="Editar utilizador">✏️ Editar</a>'
        b += f'<a class="btn btn-ghost btn-sm" href="?edit_contactos={ne}" title="Editar email/telemóvel">✉️</a>'
        if r.get("locked_until"):
            b += f'<form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="desbloquear"><input type="hidden" name="nii" maxlength="32" value="{ne}"><button class="btn btn-ghost btn-sm">🔓</button></form>'
        b += f'<form method="post" style="display:inline" onsubmit="return confirm(\'Eliminar {ne}?\');">{csrf_input()}<input type="hidden" name="acao" value="eliminar"><input type="hidden" name="nii" maxlength="32" value="{ne}"><button class="btn btn-danger btn-sm">🗑</button></form>'
        return b

    rows_html = "".join(
        f"""
      <tr{'style="background:#f0f7ff"' if r["NII"] == edit_user_nii or r["NII"] == edit_nii else ""}>
        <td class="small text-muted">{esc(r["NII"])}</td><td>{esc(r["NI"])}</td>
        <td><strong>{esc(r["Nome_completo"])}</strong></td>
        <td class="center">{esc(r["ano"])}</td>
        <td><span class="badge badge-info">{esc(r["perfil"])}</span></td>
        <td class="small text-muted">{esc(r.get("email") or "—")}</td>
        <td class="small text-muted">{esc(r.get("telemovel") or "—")}</td>
        <td>{'<span class="badge badge-warn">Bloqueado</span>' if r.get("locked_until") else '<span class="badge badge-ok">Ativo</span>'}</td>
        <td>{action_btns(r)}</td>
      </tr>"""
        for r in rows
    )

    edit_user_form = ""
    if edit_user_row:
        er = edit_user_row
        perfil_opts = "".join(
            f'<option value="{p}" {"selected" if er["perfil"] == p else ""}>{p}</option>'
            for p in ["aluno", "oficialdia", "cozinha", "cmd", "admin"]
        )
        edit_user_form = f'''
        <div class="card" style="border:1.5px solid var(--primary);max-width:640px">
          <div class="card-title">✏️ Editar Utilizador — {esc(er["Nome_completo"])}</div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="editar_user">
            <input type="hidden" name="nii" maxlength="32" value="{esc(er["NII"])}">
            <div class="grid grid-3">
              <div class="form-group"><label>Nome completo</label><input type="text" name="nome" value="{esc(er["Nome_completo"])}" required></div>
              <div class="form-group"><label>NI</label><input type="text" name="ni" value="{esc(er["NI"] or "")}"></div>
              <div class="form-group"><label>Ano</label>
                <select name="ano">
                  <option value="0">0 — Concluído/Inativo</option>
                  {"".join(f'<option value="{a}" {"selected" if str(er["ano"]) == str(a) else ""}>{_ano_label(a)}</option>' for a, _ in ANOS_OPCOES)}
                </select>
              </div>
              <div class="form-group"><label>Perfil</label><select name="perfil">{perfil_opts}</select></div>
              <div class="form-group"><label>Email</label><input type="email" name="email" value="{esc(er.get("email") or "")}"></div>
              <div class="form-group"><label>Telemóvel</label><input type="tel" name="telemovel" value="{esc(er.get("telemovel") or "")}"></div>
            </div>
            <div class="form-group"><label>Nova password (deixa em branco para não alterar)</label><input type="text" name="pw" placeholder="Nova password opcional..."></div>
            <div class="gap-btn">
              <button class="btn btn-ok">💾 Guardar alterações</button>
              <a class="btn btn-ghost" href="{url_for(".admin_utilizadores")}">Cancelar</a>
            </div>
          </form>
        </div>'''

    edit_contactos_form = ""
    if edit_row:
        edit_contactos_form = f"""
        <div class="card" style="border:1.5px solid var(--gold);max-width:520px">
          <div class="card-title">✉️ Contactos — {esc(edit_row["Nome_completo"])}</div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="editar_contactos">
            <input type="hidden" name="nii" maxlength="32" value="{esc(edit_row["NII"])}">
            <div class="grid grid-2">
              <div class="form-group"><label>Email</label>
                <input type="email" name="email" value="{esc(edit_row.get("email") or "")}" placeholder="nome@exemplo.pt">
              </div>
              <div class="form-group"><label>Telemóvel</label>
                <input type="tel" name="telemovel" value="{esc(edit_row.get("telemovel") or "")}" placeholder="+351XXXXXXXXX">
              </div>
            </div>
            <div class="gap-btn">
              <button class="btn btn-ok">💾 Guardar contactos</button>
              <a class="btn btn-ghost" href="{url_for(".admin_utilizadores")}">Cancelar</a>
            </div>
          </form>
        </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for(".admin_home"))}<div class="page-title">👥 Utilizadores ({len(rows)})</div>
        <a class="btn btn-primary btn-sm" href="{url_for(".admin_importar_csv")}">📥 Importar CSV</a>
      </div>
      {edit_user_form}
      {edit_contactos_form}
      <div class="card">
        <form method="get" style="display:flex;gap:.5rem;flex-wrap:wrap">
          <input type="text" name="q" placeholder="Pesquisar por nome..." value="{esc(q)}" style="flex:1;min-width:200px">
          <select name="ano" style="width:auto">
            <option value="all" {"selected" if ano_f == "all" else ""}>Todos os anos</option>
            {"".join(f"<option value='{a}' {'selected' if ano_f == str(a) else ''}>{_ano_label(a)}</option>" for a, _ in ANOS_OPCOES)}
          </select>
          <button class="btn btn-primary">Filtrar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">🆕 Criar utilizador</div>
        <form method="post">
          {csrf_input()}
          <input type="hidden" name="acao" value="criar">
          <div class="grid grid-3">
            <div class="form-group"><label>NII</label><input type="text" name="nii" maxlength="32" required></div>
            <div class="form-group"><label>NI</label><input type="text" name="ni" required></div>
            <div class="form-group"><label>Nome completo</label><input type="text" name="nome" required></div>
            <div class="form-group"><label>Ano</label>
              <select name="ano" required>
                {"".join(f"<option value='{a}'>{_ano_label(a)}</option>" for a, _ in ANOS_OPCOES)}
              </select>
            </div>
            <div class="form-group"><label>Perfil</label>
              <select name="perfil">{"".join(f"<option value='{p}'>{p}</option>" for p in ["aluno", "oficialdia", "cozinha", "cmd", "admin"])}</select>
            </div>
            <div class="form-group"><label>Password inicial</label><input type="text" name="pw" required></div>
          </div>
          <button class="btn btn-ok">Criar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Lista
<span style="font-size:.74rem;font-weight:400;color:var(--muted);margin-left:.5rem">Clica em ✉️ para editar email/telemóvel</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>NII</th><th>NI</th><th>Nome</th><th>Ano</th><th>Perfil</th><th>Email</th><th>Telemóvel</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="9" class="text-muted center" style="padding:1.5rem">Sem utilizadores.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("admin/utilizadores.html", content=Markup(content))


@admin_bp.route("/admin/importar-csv", methods=["GET", "POST"])
@role_required("admin")
def admin_importar_csv():
    """Importação de alunos em massa via CSV.

    Formato esperado (com ou sem cabeçalho):
        NII, NI, Nome_completo, ano
    Colunas opcionais na mesma linha: perfil, password
    Se perfil omitido → 'aluno'
    Se password omitida → NII do aluno (deve alterar no 1.º login)
    """
    import csv

    resultado = None

    if request.method == "POST":
        acao = request.form.get("acao", "")

        if acao == "preview":
            f = request.files.get("csvfile")
            if not f or not f.filename:
                flash("Nenhum ficheiro selecionado.", "error")
                return redirect(url_for(".admin_importar_csv"))

            raw = f.read().decode("utf-8-sig", errors="replace")
            linhas = list(csv.reader(io.StringIO(raw)))

            # Detectar cabeçalho (primeira célula da 1.ª linha)
            if (
                linhas
                and linhas[0]
                and linhas[0][0].strip().upper() in ("NII", "#", "ID", "NUM")
            ):
                linhas = linhas[1:]

            preview_rows = []
            erros = []
            with db() as conn:
                existentes = {
                    r["NII"]
                    for r in conn.execute("SELECT NII FROM utilizadores").fetchall()
                }

            for i, row in enumerate(linhas, 1):
                row = [c.strip() for c in row]
                if not any(row):
                    continue
                if len(row) < 4:
                    erros.append(
                        f"Linha {i}: colunas insuficientes ({len(row)} — esperadas: NII, NI, Nome, Ano)."
                    )
                    continue
                nii, ni, nome, ano_raw = row[0], row[1], row[2], row[3]
                perfil = row[4] if len(row) > 4 and row[4] else "aluno"
                pw = row[5] if len(row) > 5 and row[5] else nii

                if not nii or not ni or not nome:
                    erros.append(f"Linha {i}: NII, NI e Nome são obrigatórios.")
                    continue
                try:
                    ano = int(ano_raw)
                    if ano not in [a for a, _ in ANOS_OPCOES]:
                        erros.append(
                            f"Linha {i} ({nii}): ano inválido '{ano_raw}'. Usa 1–8."
                        )
                        continue
                except ValueError:
                    erros.append(f"Linha {i} ({nii}): ano não é número ('{ano_raw}').")
                    continue

                duplicado = nii in existentes
                preview_rows.append(
                    {
                        "linha": i,
                        "nii": nii,
                        "ni": ni,
                        "nome": nome,
                        "ano": ano,
                        "perfil": perfil,
                        "pw": pw,
                        "duplicado": duplicado,
                    }
                )

            resultado = {"preview": preview_rows, "erros": erros, "raw": raw}

        elif acao == "confirmar":
            raw = request.form.get("raw_csv", "")
            linhas = list(csv.reader(io.StringIO(raw)))
            if (
                linhas
                and linhas[0]
                and linhas[0][0].strip().upper() in ("NII", "#", "ID", "NUM")
            ):
                linhas = linhas[1:]

            criados = 0
            ignorados = 0
            erros_conf = []
            with db() as conn:
                existentes = {
                    r["NII"]
                    for r in conn.execute("SELECT NII FROM utilizadores").fetchall()
                }

            for i, row in enumerate(linhas, 1):
                row = [c.strip() for c in row]
                if not any(row) or len(row) < 4:
                    continue
                nii, ni, nome, ano_raw = row[0], row[1], row[2], row[3]
                perfil = row[4] if len(row) > 4 and row[4] else "aluno"
                pw = row[5] if len(row) > 5 and row[5] else nii

                if nii in existentes:
                    ignorados += 1
                    continue

                try:
                    ano = int(ano_raw)
                except ValueError:
                    erros_conf.append(f"Linha {i} ({nii}): ano inválido.")
                    continue

                ok, err = _criar_utilizador(nii, ni, nome, str(ano), perfil, pw)
                if ok:
                    criados += 1
                    existentes.add(nii)
                else:
                    erros_conf.append(f"Linha {i} ({nii}): {err}")

            _audit(
                current_user().get("nii", "admin"),
                "importar_csv",
                f"criados={criados} ignorados={ignorados} erros={len(erros_conf)}",
            )
            msgs = [f"✅ {criados} aluno(s) criado(s)."]
            if ignorados:
                msgs.append(f"⚠️ {ignorados} ignorado(s) (NII já existe).")
            if erros_conf:
                msgs.append(
                    f"❌ {len(erros_conf)} erro(s): " + "; ".join(erros_conf[:5])
                )
            flash(" ".join(msgs), "ok" if not erros_conf else "warn")
            return redirect(url_for(".admin_utilizadores"))

    # ── Render ───────────────────────────────────────────────────────────────
    preview_html = ""
    erros_html = ""
    hidden_raw = ""

    if resultado:
        rows_prev = resultado["preview"]
        erros_list = resultado["erros"]

        if erros_list:
            erros_html = (
                '<div class="alert alert-warn">⚠️ <strong>Avisos de parsing:</strong><ul style="margin:.4rem 0 0 1.2rem">'
                + "".join(f"<li>{esc(e)}</li>" for e in erros_list)
                + "</ul></div>"
            )

        novos = [r for r in rows_prev if not r["duplicado"]]
        dupls = [r for r in rows_prev if r["duplicado"]]
        raw_csv_escaped = esc(resultado["raw"])
        hidden_raw = f'<input type="hidden" name="raw_csv" value="{raw_csv_escaped}">'

        def _ano_badge(a):
            return f'<span class="badge badge-info">{_ano_label(a)}</span>'

        trs = "".join(
            f"""
          <tr style="{"background:#f0fff4" if not r["duplicado"] else "background:#fff9e6;opacity:.7"}">
            <td class="small text-muted">{r["linha"]}</td>
            <td><strong>{esc(r["nii"])}</strong></td>
            <td>{esc(r["ni"])}</td>
            <td>{esc(r["nome"])}</td>
            <td>{_ano_badge(r["ano"])}</td>
            <td><span class="badge badge-{"info" if r["perfil"] == "aluno" else "warn"}">{esc(r["perfil"])}</span></td>
            <td class="small text-muted">{esc(r["pw"]) if not r["duplicado"] else "—"}</td>
            <td>{'<span class="badge badge-warn">⚠️ Já existe</span>' if r["duplicado"] else '<span class="badge badge-ok">✅ Novo</span>'}</td>
          </tr>"""
            for r in rows_prev
        )

        sumario = (
            f'<div class="alert alert-info" style="margin-bottom:.5rem">'
            f"📊 <strong>{len(novos)} a criar</strong>"
            f"{f', {len(dupls)} ignorados (já existem)' if dupls else ''}"
            f", {len(erros_list)} avisos de formato.</div>"
        )

        confirmar_btn = (
            f'''
        <form method="post" style="margin-top:.9rem">
          {csrf_input()}
          <input type="hidden" name="acao" value="confirmar">
          {hidden_raw}
          <button class="btn btn-ok" {"disabled" if not novos else ""}>
            ✅ Confirmar e importar {len(novos)} aluno(s)
          </button>
          <a class="btn btn-ghost" href="{url_for(".admin_importar_csv")}" style="margin-left:.5rem">↩️ Cancelar</a>
        </form>'''
            if novos
            else '<div class="alert alert-warn">Nenhum aluno novo para importar.</div>'
        )

        preview_html = f"""
        <div class="card">
          <div class="card-title">👁️ Pré-visualização ({len(rows_prev)} linha(s))</div>
          {sumario}
          {erros_html}
          <div class="table-wrap">
            <table>
              <thead><tr><th>#</th><th>NII</th><th>NI</th><th>Nome</th><th>Ano</th><th>Perfil</th><th>Password inicial</th><th>Estado</th></tr></thead>
              <tbody>{trs}</tbody>
            </table>
          </div>
          {confirmar_btn}
        </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".admin_utilizadores"))}
        <div class="page-title">📥 Importar Alunos via CSV</div>
      </div>

      <div class="card" style="max-width:680px">
        <div class="card-title">📋 Instruções</div>
        <p style="font-size:.85rem;color:var(--muted);line-height:1.6">
          Carrega um ficheiro <strong>.csv</strong> com uma linha por aluno. Colunas aceites:<br>
          <code style="background:#f0f4f8;padding:.1rem .4rem;border-radius:4px;font-size:.83rem">NII, NI, Nome_completo, Ano [, Perfil] [, Password]</code><br><br>
          • <strong>Perfil</strong> omitido → <code>aluno</code><br>
          • <strong>Password</strong> omitida → igual ao NII (deve alterar no 1.º login)<br>
          • <strong>Ano</strong>: 1–6 para anos curriculares, 7 para CFBO, 8 para CFCO<br>
          • Linhas com NII já existente são ignoradas (sem sobrescrever)<br>
          • A 1.ª linha é ignorada se começar por <code>NII</code>, <code>#</code>, <code>ID</code> ou <code>NUM</code>
        </p>
        <div class="alert alert-info" style="margin-top:.8rem;font-size:.82rem">
          💡 <strong>Exemplo de CSV:</strong><br>
          <pre style="margin:.4rem 0 0;font-size:.78rem;background:#f0f4f8;padding:.5rem;border-radius:6px;overflow-x:auto">NII,NI,Nome_completo,Ano,Perfil,Password
20240001,A001,João Silva,1,aluno,senha123
20240002,A002,Maria Costa,1
20240003,A003,Pedro Santos,2</pre>
        </div>
      </div>

      <div class="card" style="max-width:680px">
        <div class="card-title">📤 Carregar ficheiro</div>
        <form method="post" enctype="multipart/form-data">
          {csrf_input()}
          <input type="hidden" name="acao" value="preview">
          <div class="form-group">
            <label>Ficheiro CSV</label>
            <input type="file" name="csvfile" accept=".csv,.txt" required style="padding:.42rem .6rem">
          </div>
          <button class="btn btn-primary">🔍 Pré-visualizar</button>
        </form>
      </div>

      {preview_html}
    </div>"""
    return render_template("admin/importar_csv.html", content=Markup(content))


@admin_bp.route("/admin/menus", methods=["GET", "POST"])
@role_required("cozinha", "admin", "oficialdia")
def admin_menus():
    d_str = request.args.get("d", date.today().isoformat())
    dt = _parse_date(d_str)

    if request.method == "POST":
        d_save = request.form.get("data", dt.isoformat())
        campos = [
            "pequeno_almoco",
            "lanche",
            "almoco_normal",
            "almoco_veg",
            "almoco_dieta",
            "jantar_normal",
            "jantar_veg",
            "jantar_dieta",
        ]
        vals = [request.form.get(c, "").strip() or None for c in campos]
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO menus_diarios
                (data,pequeno_almoco,lanche,almoco_normal,almoco_veg,almoco_dieta,jantar_normal,jantar_veg,jantar_dieta)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (d_save, *vals),
            )
            for ref in ["Pequeno Almoço", "Lanche", "Almoço", "Jantar"]:
                cap_key = "cap_" + ref.lower().replace(" ", "_").replace(
                    "ç", "c"
                ).replace("ã", "a")
                cap_val = request.form.get(cap_key, "").strip()
                if cap_val:
                    try:
                        cap_int = _val_cap(cap_val)
                        if cap_int is None:
                            continue
                        if cap_int < 0:
                            conn.execute(
                                "DELETE FROM capacidade_refeicao WHERE data=? AND refeicao=?",
                                (d_save, ref),
                            )
                        else:
                            conn.execute(
                                "INSERT OR REPLACE INTO capacidade_refeicao(data,refeicao,max_total) VALUES (?,?,?)",
                                (d_save, ref, cap_int),
                            )
                    except ValueError:
                        pass
            conn.commit()
        flash("Menu e capacidades guardados.", "ok")
        return redirect(url_for(".admin_menus", d=d_save))

    with db() as conn:
        menu = conn.execute(
            "SELECT * FROM menus_diarios WHERE data=?", (dt.isoformat(),)
        ).fetchone()
        caps = {
            r["refeicao"]: r["max_total"]
            for r in conn.execute(
                "SELECT refeicao,max_total FROM capacidade_refeicao WHERE data=?",
                (dt.isoformat(),),
            )
        }

    def mv(k):
        return esc(menu[k] if menu and menu[k] else "")

    def cv(ref):
        return caps.get(ref, "")

    back_url = (
        url_for("operations.painel_dia")
        if current_user().get("perfil") in ("cozinha", "oficialdia")
        else url_for(".admin_home")
    )

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">🍽️ Menus & Capacidade</div></div>
      <div class="card" style="max-width:640px">
        <form method="post">
          {csrf_input()}
          <div class="form-group"><label>Data</label><input type="date" name="data" value="{dt.isoformat()}" required></div>
          <div class="card-title" style="margin:.7rem 0 .55rem">Ementa</div>
          <div class="grid grid-2">
            <div class="form-group"><label>☕ Pequeno Almoço</label><input type="text" name="pequeno_almoco" value="{mv("pequeno_almoco")}"></div>
            <div class="form-group"><label>🥐 Lanche</label><input type="text" name="lanche" value="{mv("lanche")}"></div>
            <div class="form-group"><label>🍽️ Almoço Normal</label><input type="text" name="almoco_normal" value="{mv("almoco_normal")}"></div>
            <div class="form-group"><label>🥗 Almoço Vegetariano</label><input type="text" name="almoco_veg" value="{mv("almoco_veg")}"></div>
            <div class="form-group"><label>🥙 Almoço Dieta</label><input type="text" name="almoco_dieta" value="{mv("almoco_dieta")}"></div>
            <div class="form-group"><label>🌙 Jantar Normal</label><input type="text" name="jantar_normal" value="{mv("jantar_normal")}"></div>
            <div class="form-group"><label>🌿 Jantar Vegetariano</label><input type="text" name="jantar_veg" value="{mv("jantar_veg")}"></div>
            <div class="form-group"><label>🥗 Jantar Dieta</label><input type="text" name="jantar_dieta" value="{mv("jantar_dieta")}"></div>
          </div>
          <div class="card-title" style="margin:.7rem 0 .55rem">Capacidades <span class="text-muted small">(-1 ou vazio = sem limite)</span></div>
          <div class="grid grid-2">
            <div class="form-group"><label>PA</label><input type="number" name="cap_pequeno_almoco" value="{cv("Pequeno Almoço")}"></div>
            <div class="form-group"><label>Lanche</label><input type="number" name="cap_lanche" value="{cv("Lanche")}"></div>
            <div class="form-group"><label>Almoço</label><input type="number" name="cap_almoco" value="{cv("Almoço")}"></div>
            <div class="form-group"><label>Jantar</label><input type="number" name="cap_jantar" value="{cv("Jantar")}"></div>
          </div>
          <hr>
          <div class="gap-btn"><button class="btn btn-ok">💾 Guardar</button><a class="btn btn-ghost" href="{back_url}">Cancelar</a></div>
        </form>
      </div>
    </div>"""
    return render_template("admin/menus.html", content=Markup(content))


@admin_bp.route("/admin/log")
@role_required("admin")
def admin_log():
    # ── Filtros ──────────────────────────────────────────────────────────────
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

    sql = """SELECT l.id, l.alterado_em, u.NII, u.Nome_completo, u.ano,
                    l.data_refeicao, l.campo, l.valor_antes, l.valor_depois, l.alterado_por
             FROM refeicoes_log l LEFT JOIN utilizadores u ON u.id=l.utilizador_id
             WHERE 1=1"""
    args = []

    if q_nome:
        sql += " AND u.Nome_completo LIKE ?"
        args.append(f"%{q_nome}%")
    if q_por:
        sql += " AND l.alterado_por LIKE ?"
        args.append(f"%{q_por}%")
    if q_campo:
        sql += " AND l.campo=?"
        args.append(q_campo)
    if q_d0:
        sql += " AND l.data_refeicao >= ?"
        args.append(q_d0)
    if q_d1:
        sql += " AND l.data_refeicao <= ?"
        args.append(q_d1)

    sql += " ORDER BY l.alterado_em DESC LIMIT ?"
    args.append(q_limit)

    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        total_logs = conn.execute("SELECT COUNT(*) c FROM refeicoes_log").fetchone()[
            "c"
        ]
        campos_disponiveis = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT campo FROM refeicoes_log ORDER BY campo"
            ).fetchall()
        ]

    # Paginação info
    mostrando = len(rows)

    campos_opts = '<option value="">Todos os campos</option>' + "".join(
        f'<option value="{c}" {"selected" if q_campo == c else ""}>{c}</option>'
        for c in campos_disponiveis
    )

    limites_opts = "".join(
        f'<option value="{n}" {"selected" if str(q_limit) == str(n) else ""}>{n} linhas</option>'
        for n in [100, 200, 500, 1000, 2000, 5000]
    )

    rows_html = "".join(
        f"""
      <tr>
        <td class="small" style="white-space:nowrap">{(r["alterado_em"] or "")[:16]}</td>
        <td>
          <span style="font-weight:600">{esc(r["Nome_completo"] or r["NII"] or "—")}</span>
          {'<br><span class="text-muted small">' + esc(r["NII"]) + (f" · {r['ano']}º ano" if r["ano"] else "") + "</span>" if r["Nome_completo"] else ""}
        </td>
        <td style="white-space:nowrap">{r["data_refeicao"]}</td>
        <td><span class="badge badge-info">{esc(r["campo"])}</span></td>
        <td class="small text-muted">{esc(r["valor_antes"] or "—")}</td>
        <td class="small" style="color:var(--ok);font-weight:600">{esc(r["valor_depois"] or "—")}</td>
        <td class="small text-muted">{esc(r["alterado_por"] or "—")}</td>
      </tr>"""
        for r in rows
    )

    filtros_ativos = any([q_nome, q_por, q_campo, q_d0, q_d1])
    limpar_btn = (
        f'<a class="btn btn-ghost btn-sm" href="{url_for(".admin_log")}">✕ Limpar filtros</a>'
        if filtros_ativos
        else ""
    )

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for(".admin_home"))}<div class="page-title">📜 Log de Alterações</div></div>
      <div class="card">
        <div class="card-title">🔍 Filtros
          <span class="badge badge-muted" style="margin-left:.5rem;font-size:.72rem">{total_logs} registos totais</span>
          {f'<span class="badge badge-warn" style="margin-left:.3rem;font-size:.72rem">A mostrar {mostrando}</span>' if filtros_ativos else ""}
        </div>
        <form method="get" style="display:flex;flex-wrap:wrap;gap:.5rem;align-items:flex-end">
          <div class="form-group" style="margin:0;min-width:180px;flex:1">
            <label style="font-size:.77rem">👤 Utilizador (nome)</label>
            <input type="text" name="q_nome" value="{esc(q_nome)}" placeholder="Nome do aluno..." style="font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0;min-width:140px">
            <label style="font-size:.77rem">✏️ Alterado por (NII)</label>
            <input type="text" name="q_por" value="{esc(q_por)}" placeholder="NII..." style="font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0;min-width:140px">
            <label style="font-size:.77rem">🏷 Campo</label>
            <select name="q_campo" style="font-size:.82rem">{campos_opts}</select>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">📅 Data ref. de</label>
            <input type="date" name="d0" value="{esc(q_d0)}" style="width:auto;font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">📅 até</label>
            <input type="date" name="d1" value="{esc(q_d1)}" style="width:auto;font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">📊 Máx. linhas</label>
            <select name="limite" style="width:auto;font-size:.82rem">{limites_opts}</select>
          </div>
          <button class="btn btn-primary btn-sm" style="align-self:flex-end">🔍 Filtrar</button>
          {limpar_btn}
        </form>
      </div>
      <div class="card">
        <div class="card-title">Resultados
          <span class="badge badge-info" style="margin-left:.5rem;font-size:.72rem;font-weight:400">
            {mostrando} {"(filtrado)" if filtros_ativos else "mais recentes"}
          </span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Quando</th>
                <th>Utilizador</th>
                <th>Data Ref.</th>
                <th>Campo</th>
                <th>Antes</th>
                <th>Depois</th>
                <th>Por (NII)</th>
              </tr>
            </thead>
            <tbody>{rows_html or '<tr><td colspan="7" class="text-muted center" style="padding:2rem">Sem registos com estes filtros.</td></tr>'}</tbody>
          </table>
        </div>
        {f'<div style="margin-top:.6rem;font-size:.8rem;color:var(--muted)">💡 A mostrar os primeiros {q_limit} resultados. Usa os filtros para refinar.</div>' if mostrando == q_limit else ""}
      </div>
    </div>"""
    return render_template("admin/log.html", content=Markup(content))


@admin_bp.route("/admin/auditoria")
@role_required("admin")
def admin_audit():
    """Registo de ações administrativas (logins, criação/edição de utilizadores, etc.)."""
    limite = min(_val_int_id(request.args.get("limite", "500")) or 500, 5000)
    q_actor = request.args.get("actor", "").strip()
    q_action = request.args.get("action", "").strip()

    try:
        with db() as conn:
            sql = "SELECT id,ts,actor,action,detail FROM admin_audit_log WHERE 1=1"
            args: list = []
            if q_actor:
                sql += " AND actor LIKE ?"
                args.append(f"%{q_actor}%")
            if q_action:
                sql += " AND action LIKE ?"
                args.append(f"%{q_action}%")
            sql += " ORDER BY id DESC LIMIT ?"
            args.append(limite)
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
            total = conn.execute("SELECT COUNT(*) c FROM admin_audit_log").fetchone()[
                "c"
            ]
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

    rows_html = "".join(
        f"""
      <tr>
        <td class="small text-muted" style="white-space:nowrap">{esc(r["ts"] or "")[:16]}</td>
        <td><strong>{esc(r["actor"])}</strong></td>
        <td>{ACTION_ICONS.get(r["action"], "📌")} {esc(r["action"])}</td>
        <td class="small text-muted">{esc(r.get("detail") or "—")}</td>
      </tr>"""
        for r in rows
    )

    limites_opts = "".join(
        f'<option value="{n}" {"selected" if str(limite) == str(n) else ""}>{n}</option>'
        for n in [100, 200, 500, 1000, 2000, 5000]
    )

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for(".admin_home"))}<div class="page-title">🔐 Auditoria de Ações</div></div>
      <div class="card">
        <div class="card-title">🔍 Filtros
          <span class="badge badge-muted" style="margin-left:.5rem;font-size:.72rem">{total} entradas</span>
        </div>
        <form method="get" style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:flex-end">
          <div class="form-group" style="margin:0;min-width:160px;flex:1">
            <label style="font-size:.77rem">👤 Actor (NII)</label>
            <input type="text" name="actor" value="{esc(q_actor)}" placeholder="NII...">
          </div>
          <div class="form-group" style="margin:0;min-width:160px;flex:1">
            <label style="font-size:.77rem">📌 Ação</label>
            <input type="text" name="action" value="{esc(q_action)}" placeholder="ex: login, criar_utilizador...">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">Máx.</label>
            <select name="limite" style="width:auto">{limites_opts}</select>
          </div>
          <button class="btn btn-primary btn-sm">🔍 Filtrar</button>
          <a class="btn btn-ghost btn-sm" href="{url_for(".admin_audit")}">✕ Limpar</a>
        </form>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Quando</th><th>Actor</th><th>Ação</th><th>Detalhe</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="4" class="text-muted center" style="padding:2rem">Sem registos.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("admin/auditoria.html", content=Markup(content))


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
                        count = 0
                        with db() as conn:
                            cur = d_de
                            while cur <= d_ate:
                                conn.execute(
                                    "INSERT OR REPLACE INTO calendario_operacional(data,tipo,nota) VALUES (?,?,?)",
                                    (cur.isoformat(), tipo, nota),
                                )
                                cur += timedelta(days=1)
                                count += 1
                            conn.commit()
                        flash(
                            f"{count} dia(s) adicionado(s) ao calendário ({dia_de} → {dia_ate}).",
                            "ok",
                        )
            except ValueError as e:
                flash(f"Data inválida: {e}", "error")
            except Exception as e:
                flash(str(e), "error")
        elif acao == "remover":
            with db() as conn:
                conn.execute(
                    "DELETE FROM calendario_operacional WHERE data=?",
                    (request.form.get("dia", ""),),
                )
                conn.commit()
            flash("Removido.", "ok")
        return redirect(url_for(".admin_calendario"))

    hoje = date.today()
    with db() as conn:
        entradas = conn.execute(
            "SELECT data,tipo,nota FROM calendario_operacional WHERE data >= ? ORDER BY data LIMIT 90",
            (hoje.isoformat(),),
        ).fetchall()

    TIPOS = ["normal", "fim_semana", "feriado", "exercicio", "outro"]
    ICONES = {
        "normal": "✅",
        "fim_semana": "🔵",
        "feriado": "🔴",
        "exercicio": "🟡",
        "outro": "⚪",
    }

    rows_html = "".join(
        f"""
      <tr><td>{r["data"]}</td><td>{ICONES.get(r["tipo"], "⚪")} {esc(r["tipo"])}</td><td>{esc(r["nota"] or "—")}</td>
      <td><form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="remover"><input type="hidden" name="dia" value="{r["data"]}"><button class="btn btn-danger btn-sm">🗑</button></form></td></tr>"""
        for r in entradas
    )

    back_url = (
        url_for(".admin_home")
        if u.get("perfil") == "admin"
        else url_for("operations.painel_dia")
    )
    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">📅 Calendário Operacional</div></div>
      <div class="card">
        <div class="card-title">Adicionar / atualizar período</div>
        <div class="alert alert-info" style="margin-bottom:.8rem">
          💡 Para um único dia, preenche apenas a <strong>Data de início</strong> (ou coloca a mesma data nos dois campos).
          Para um período, preenche ambas as datas — todos os dias do intervalo serão atualizados.
        </div>
        <form method="post">
          {csrf_input()}
          <input type="hidden" name="acao" value="adicionar">
          <div class="grid grid-2" style="max-width:520px">
            <div class="form-group"><label>📅 Data de início</label><input type="date" name="dia_de" required value="{hoje.isoformat()}"></div>
            <div class="form-group"><label>📅 Data de fim <span class="text-muted small">(inclusive)</span></label><input type="date" name="dia_ate" value="{hoje.isoformat()}"></div>
          </div>
          <div class="grid grid-2" style="max-width:520px">
            <div class="form-group"><label>Tipo</label>
              <select name="tipo">{"".join(f"<option value='{t}'>{ICONES.get(t, '')} {t}</option>" for t in TIPOS)}</select>
            </div>
            <div class="form-group"><label>Nota</label><input type="text" name="nota" placeholder="ex: Natal, Exercício..."></div>
          </div>
          <button class="btn btn-ok">💾 Guardar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Próximas entradas (até 90 dias)</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Data</th><th>Tipo</th><th>Nota</th><th></th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="4" class="text-muted center" style="padding:1.5rem">Sem entradas.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("admin/calendario.html", content=Markup(content))


# ═══════════════════════════════════════════════════════════════════════════
# CALENDÁRIO PÚBLICO — Visível por todos os utilizadores
# ═══════════════════════════════════════════════════════════════════════════


@admin_bp.route("/admin/companhias", methods=["GET", "POST"])
@role_required("admin")
def admin_companhias():
    if request.method == "POST":
        acao = request.form.get("acao", "")

        # ── Criar turma ──────────────────────────────────────────────────
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
                    with db() as conn:
                        conn.execute(
                            "INSERT INTO turmas (nome, ano, descricao) VALUES (?,?,?)",
                            (nome_turma, ano_int, descricao or None),
                        )
                        conn.commit()
                    flash(
                        f'Turma "{nome_turma}" ({_ano_label(ano_int)}) criada com sucesso!',
                        "ok",
                    )
                except Exception as ex:
                    flash(f"Erro ao criar turma: {ex}", "error")

        # ── Eliminar turma ───────────────────────────────────────────────
        elif acao == "eliminar_turma":
            tid = _val_int_id(request.form.get("tid", ""))
            if tid is None:
                flash("ID de turma inválido.", "error")
                return redirect(url_for(".admin_companhias"))
            try:
                with db() as conn:
                    # Desassociar alunos antes de eliminar
                    conn.execute(
                        "UPDATE utilizadores SET turma_id=NULL WHERE turma_id=?",
                        (tid,),
                    )
                    conn.execute("DELETE FROM turmas WHERE id=?", (tid,))
                    conn.commit()
                flash("Turma eliminada.", "ok")
            except Exception as ex:
                flash(f"Erro: {ex}", "error")

        # ── Atribuir aluno a turma ──────────────────────────────────────
        elif acao == "atribuir_turma":
            nii_at = request.form.get("nii_at", "").strip()
            tid_at = request.form.get("turma_id", "").strip()
            if nii_at:
                try:
                    turma_val = int(tid_at) if tid_at else None
                    with db() as conn:
                        conn.execute(
                            "UPDATE utilizadores SET turma_id=? WHERE NII=? AND perfil='aluno'",
                            (turma_val, nii_at),
                        )
                        conn.commit()
                    flash(
                        f"Turma do aluno {nii_at} atualizada.",
                        "ok",
                    )
                except Exception as ex:
                    flash(f"Erro: {ex}", "error")

        # ── Mover aluno de ano ───────────────────────────────────────────
        elif acao == "mover_aluno":
            nii_m = _val_nii(request.form.get("nii_m", ""))
            novo_ano_v = _val_ano(request.form.get("novo_ano", ""))
            if not nii_m:
                flash("NII inválido.", "error")
            elif novo_ano_v is None:
                flash("Ano inválido (0-8).", "error")
            else:
                try:
                    with db() as conn:
                        conn.execute(
                            "UPDATE utilizadores SET ano=? WHERE NII=? AND perfil='aluno'",
                            (novo_ano_v, nii_m),
                        )
                        conn.commit()
                    flash(f"Aluno {nii_m} movido para {_ano_label(novo_ano_v)}.", "ok")
                except Exception as ex:
                    flash(f"Erro: {ex}", "error")

        # ── Promover aluno individual ─────────────────────────────────────
        elif acao == "promover_um":
            uid_p = _val_int_id(request.form.get("uid", ""))
            novo_ni = _val_ni(request.form.get("novo_ni", ""))
            if uid_p is None:
                flash("ID inválido.", "error")
                return redirect(url_for(".admin_companhias"))
            with db() as conn:
                al = conn.execute(
                    "SELECT ano,NI FROM utilizadores WHERE id=?", (uid_p,)
                ).fetchone()
            if al:
                ano_a = al["ano"]
                # CFBO(7) e CFCO(8) não têm progressão automática para acima
                if ano_a >= 6:
                    novo_ano_p = 0
                else:
                    novo_ano_p = ano_a + 1
                with db() as conn:
                    conn.execute(
                        "UPDATE utilizadores SET ano=?,NI=? WHERE id=?",
                        (novo_ano_p, novo_ni or al["NI"], uid_p),
                    )
                    conn.commit()
                dest = _ano_label(novo_ano_p) if novo_ano_p else "Concluído"
                flash(f"Aluno promovido para {dest}.", "ok")

        # ── Promoção global de um ano ─────────────────────────────────────
        elif acao == "promover_todos":
            ano_origem = _val_ano(request.form.get("ano_origem", 0))
            if ano_origem is None:
                flash("Ano de origem inválido.", "error")
                return redirect(url_for(".admin_companhias"))
            if ano_origem >= 6:
                novo_ano_p = 0
            else:
                novo_ano_p = ano_origem + 1
            with db() as conn:
                conn.execute(
                    "UPDATE utilizadores SET ano=? WHERE perfil='aluno' AND ano=?",
                    (novo_ano_p, ano_origem),
                )
                conn.commit()
            dest = _ano_label(novo_ano_p) if novo_ano_p else "Concluído"
            flash(
                f"Todos os alunos do {_ano_label(ano_origem)} promovidos para {dest}.",
                "ok",
            )

        # ── Promoção global todos os anos ────────────────────────────────
        elif acao == "promover_todos_anos":
            with db() as conn:
                # Promover do maior para o menor para evitar conflitos
                for ano_a in range(6, 0, -1):
                    novo_ano_p = 0 if ano_a >= 6 else ano_a + 1
                    conn.execute(
                        "UPDATE utilizadores SET ano=? WHERE perfil='aluno' AND ano=?",
                        (novo_ano_p, ano_a),
                    )
                conn.commit()
            flash("Promoção global concluída.", "ok")

        return redirect(url_for(".admin_companhias"))

    # ── Carregar dados ───────────────────────────────────────────────────
    try:
        with db() as conn:
            turmas = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM turmas ORDER BY ano, nome"
                ).fetchall()
            ]
    except Exception:
        turmas = []

    # Contagens por ano (inclui CFBO e CFCO)
    anos_data = {}
    all_anos = list(range(1, 7)) + [7, 8]
    for a in all_anos:
        with db() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE ano=? AND perfil='aluno'",
                (a,),
            ).fetchone()["c"]
        anos_data[a] = cnt

    # ── HTML alunos por ano ───────────────────────────────────────────────
    anos_grid = ""
    for a in all_anos:
        n = anos_data.get(a, 0)
        anos_grid += f'<div class="stat-box"><div class="stat-num">{n}</div><div class="stat-lbl">{_ano_label(a)}</div></div>'

    # ── HTML promoção ─────────────────────────────────────────────────────
    def _build_promover_html():
        cards = ""
        promovable = list(range(1, 7)) + [7, 8]
        for a in promovable:
            with db() as conn:
                alunos_a = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id,NI,Nome_completo,ano FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI",
                        (a,),
                    ).fetchall()
                ]
            n = len(alunos_a)
            if a >= 6:
                destino = "Concluído"
                cor = "#922b21"
            else:
                destino = _ano_label(a + 1)
                cor = "#1e8449"
            alunos_list = "".join(
                '<div style="display:flex;justify-content:space-between;align-items:center;padding:.3rem 0;border-bottom:1px solid var(--border);font-size:.82rem;gap:.4rem">'
                "<span><strong>"
                + esc(al["NI"])
                + "</strong> — "
                + esc(al["Nome_completo"])
                + "</span>"
                '<form method="post" style="display:inline;display:flex;gap:.3rem;align-items:center">'
                + str(csrf_input())
                + '<input type="hidden" name="acao" value="promover_um">'
                '<input type="hidden" name="uid" value="' + str(al["id"]) + '">'
                '<input type="text" name="novo_ni" placeholder="Novo NI" style="width:110px;padding:.25rem .45rem;font-size:.78rem;border-radius:7px;border:1.5px solid var(--border)">'
                '<button class="btn btn-ghost btn-sm" title="Promover este aluno">↑ Promover</button>'
                "</form></div>"
                for al in alunos_a
            )
            disabled = " disabled" if not alunos_a else ""
            cards += (
                '<div class="card" style="border-top:3px solid ' + cor + '">'
                '<div class="card-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.4rem">'
                "<span>"
                + _ano_label(a)
                + ' <span class="badge badge-info" style="margin-left:.4rem">'
                + str(n)
                + " alunos</span></span>"
                '<form method="post" style="display:inline" onsubmit="return confirm(\'Promover todos os alunos deste ano?\')">'
                + str(csrf_input())
                + '<input type="hidden" name="acao" value="promover_todos"><input type="hidden" name="ano_origem" value="'
                + str(a)
                + '">'
                '<button class="btn btn-sm" style="background:'
                + cor
                + ';color:#fff"'
                + disabled
                + ">🎖️ Promover todos → "
                + destino
                + "</button></form></div>"
                '<div style="max-height:180px;overflow-y:auto;border-top:1px solid var(--border);padding-top:.4rem">'
                + (
                    alunos_list
                    or '<div class="text-muted small" style="padding:.3rem">Sem alunos.</div>'
                )
                + "</div></div>"
            )
        return cards

    anos_cards_prom = _build_promover_html()

    # ── HTML turmas criadas ───────────────────────────────────────────────
    turmas_html = ""
    for t in turmas:
        turmas_html += f"""
        <tr>
          <td><strong>{esc(t["nome"])}</strong></td>
          <td>{_ano_label(t["ano"])}</td>
          <td>{esc(t.get("descricao") or "—")}</td>
          <td class="small text-muted">{(t.get("criado_em") or "")[:16]}</td>
          <td>
            <form method="post" style="display:inline" onsubmit="return confirm('Eliminar turma?')">
              {csrf_input()}
              <input type="hidden" name="acao" value="eliminar_turma">
              <input type="hidden" name="tid" value="{t["id"]}">
              <button class="btn btn-danger btn-sm">🗑</button>
            </form>
          </td>
        </tr>"""

    # ── Alunos para mover / atribuir turma ──────────────────────────────
    with db() as conn:
        alunos_all = conn.execute(
            "SELECT NII, NI, Nome_completo, ano, turma_id FROM utilizadores WHERE perfil='aluno' ORDER BY ano, NI"
        ).fetchall()
    alunos_opts = "".join(
        f'<option value="{esc(a["NII"])}">[{_ano_label(a["ano"])}] {esc(a["NI"])} — {esc(a["Nome_completo"])}</option>'
        for a in alunos_all
    )
    turma_select_opts = '<option value="">— Sem turma —</option>' + "".join(
        f'<option value="{t["id"]}">{esc(t["nome"])} ({_ano_label(t["ano"])})</option>'
        for t in turmas
    )

    ano_select_opts = "".join(
        f'<option value="{a}">{_ano_label(a)}</option>' for a, _ in ANOS_OPCOES
    )
    ano_select_criar = "".join(
        f'<option value="{a}">{lbl}</option>' for a, lbl in ANOS_OPCOES
    )
    ano_select_mover = (
        ano_select_opts + '<option value="0">Concluído / Inativo</option>'
    )

    # ── Tabs de secção (via hash) ─────────────────────────────────────────
    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".admin_home"))}
        <div class="page-title">⚓ Gestão de Companhias</div>
      </div>

      <!-- Tabs -->
      <div class="year-tabs" style="margin-bottom:1rem">
        <a class="year-tab" href="#turmas" onclick="showTab('turmas')">📚 Turmas</a>
        <a class="year-tab" href="#atribuir" onclick="showTab('atribuir')">👥 Atribuir Turma</a>
        <a class="year-tab" href="#promocao" onclick="showTab('promocao')">🎖️ Promoção</a>
        <a class="year-tab" href="#mover" onclick="showTab('mover')">🔄 Mover Aluno</a>
      </div>
      <script>
        function showTab(id) {{
          ['turmas','atribuir','promocao','mover'].forEach(function(t) {{
            document.getElementById('tab-'+t).style.display = (t===id) ? '' : 'none';
          }});
          document.querySelectorAll('.year-tab').forEach(function(el) {{
            el.classList.toggle('active', el.getAttribute('href')==='#'+id);
          }});
        }}
        // Mostrar tab por hash ou primeiro
        var hash = window.location.hash.replace('#','') || 'turmas';
        showTab(hash);
      </script>

      <!-- Tab: Turmas -->
      <div id="tab-turmas">
        <div class="card">
          <div class="card-title">📊 Alunos por ano/curso</div>
          <div class="grid grid-4">{anos_grid}</div>
        </div>
        <div class="grid grid-2">
          <div class="card">
            <div class="card-title">➕ Criar nova turma / companhia</div>
            <form method="post">
              {csrf_input()}
              <input type="hidden" name="acao" value="criar_turma">
              <div class="form-group">
                <label>Nome da turma <span class="text-muted small">(ex: Alpha, Bravo...)</span></label>
                <input type="text" name="nome_turma" required placeholder="Ex: Alpha">
              </div>
              <div class="form-group">
                <label>Ano curricular / Curso</label>
                <select name="ano_turma" required>
                  {ano_select_criar}
                </select>
              </div>
              <div class="form-group">
                <label>Descrição <span class="text-muted small">(opcional)</span></label>
                <input type="text" name="descricao" placeholder="Ex: Turma de engenharia naval">
              </div>
              <button class="btn btn-ok">💾 Criar turma</button>
            </form>
          </div>
          <div class="card">
            <div class="card-title">📋 Turmas criadas ({len(turmas)})</div>
            {'<div class="table-wrap"><table><thead><tr><th>Nome</th><th>Ano/Curso</th><th>Descrição</th><th>Criada em</th><th></th></tr></thead><tbody>' + turmas_html + "</tbody></table></div>" if turmas else '<div class="text-muted" style="padding:.8rem">Nenhuma turma criada ainda.</div>'}
          </div>
        </div>
      </div>

      <!-- Tab: Atribuir Turma -->
      <div id="tab-atribuir" style="display:none">
        <div class="card" style="max-width:520px">
          <div class="card-title">👥 Atribuir aluno a uma turma</div>
          <div class="alert alert-info" style="font-size:.81rem;margin-bottom:.8rem">
            💡 Liga um aluno a uma turma/companhia criada no separador Turmas.
          </div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="atribuir_turma">
            <div class="form-group">
              <label>Aluno (NII)</label>
              <select name="nii_at" required>
                <option value="">— Selecionar aluno —</option>
                {alunos_opts}
              </select>
            </div>
            <div class="form-group">
              <label>Turma</label>
              <select name="turma_id">
                {turma_select_opts}
              </select>
            </div>
            <button class="btn btn-ok">💾 Atribuir turma</button>
          </form>
        </div>
      </div>

      <!-- Tab: Promoção -->
      <div id="tab-promocao" style="display:none">
        <div class="alert alert-warn">⚠️ <strong>Atenção:</strong> A promoção é permanente. Recomenda-se fazer backup antes.</div>
        <div class="card">
          <div class="card-title">🚀 Promoção global — todos os anos em simultâneo</div>
          <p style="font-size:.85rem;color:var(--muted);margin-bottom:.8rem">Promove todos: 1º→2º, 2º→3º, ..., 5º→6º, 6º→Concluído. CFBO e CFCO não são afetados pela promoção global.</p>
          <form method="post" onsubmit="return confirm('Promover TODOS os alunos de todos os anos?')">
            {csrf_input()}<input type="hidden" name="acao" value="promover_todos_anos">
            <button class="btn btn-danger">🎖️ Promoção Global</button>
          </form>
        </div>
        <div class="grid grid-2">{anos_cards_prom}</div>
      </div>

      <!-- Tab: Mover Aluno -->
      <div id="tab-mover" style="display:none">
        <div class="card" style="max-width:520px">
          <div class="card-title">🔄 Mover aluno de ano</div>
          <div class="alert alert-info" style="font-size:.81rem;margin-bottom:.8rem">
            💡 Usa esta função para mover um aluno individualmente para outro ano sem usar a promoção global, incluindo para os cursos CFBO e CFCO.
          </div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="mover_aluno">
            <div class="form-group">
              <label>Aluno (NII)</label>
              <select name="nii_m" required>
                <option value="">— Selecionar aluno —</option>
                {alunos_opts}
              </select>
            </div>
            <div class="form-group">
              <label>Mover para</label>
              <select name="novo_ano" required>
                {ano_select_mover}
              </select>
            </div>
            <button class="btn btn-warn">🔄 Mover aluno</button>
          </form>
        </div>
      </div>
    </div>"""
    return render_template("admin/companhias.html", content=Markup(content))


# Rota de compatibilidade — redireciona para o novo módulo


@admin_bp.route("/admin/turmas")
@role_required("admin")
def admin_turmas():
    return redirect(url_for(".admin_companhias"))


@admin_bp.route("/admin/promover", methods=["GET", "POST"])
@role_required("admin")
def admin_promover():
    return redirect(url_for(".admin_companhias") + "#promocao")


# health, api_backup_cron, api_autopreencher_cron — movidos para blueprints/api/routes.py


@admin_bp.route("/admin/backup-download")
@login_required
@role_required("admin")
def admin_backup_download():
    """Permite ao admin descarregar o ficheiro da base de dados."""
    from pathlib import Path

    db_path = Path(BASE_DADOS)
    if not db_path.exists():
        flash("Ficheiro da base de dados não encontrado.", "error")
        return redirect(url_for(".admin_home"))
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    nome = f"{db_path.stem}_{ts}.db"
    return send_file(
        db_path,
        as_attachment=True,
        download_name=nome,
        mimetype="application/x-sqlite3",
    )
