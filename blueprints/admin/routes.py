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
    _get_anos_disponiveis,
    _parse_date,
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

    total_alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
    total_jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]

    return render_template(
        "admin/home.html",
        hoje=hoje,
        t=t,
        total_alm=total_alm,
        total_jan=total_jan,
        action_cards=action_cards,
        anos=anos,
    )


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
            ok, msg = _reset_pw(nii)
            if ok:
                _audit(
                    current_user().get("nii", "admin"),
                    "reset_pw_admin",
                    f"NII={nii}",
                )
            flash(
                "Password resetada. O utilizador deve usar o NII como password temporária."
                if ok
                else msg,
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

    return render_template(
        "admin/utilizadores.html",
        rows=rows,
        q=q,
        ano_f=ano_f,
        edit_user_nii=edit_user_nii,
        edit_nii=edit_nii,
        edit_user_row=edit_user_row,
        edit_row=edit_row,
        ANOS_OPCOES=ANOS_OPCOES,
    )


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
    novos = []
    dupls = []
    if resultado:
        novos = [r for r in resultado["preview"] if not r["duplicado"]]
        dupls = [r for r in resultado["preview"] if r["duplicado"]]

    return render_template(
        "admin/importar_csv.html",
        resultado=resultado,
        novos=novos,
        dupls=dupls,
    )


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

    back_url = (
        url_for("operations.painel_dia")
        if current_user().get("perfil") in ("cozinha", "oficialdia")
        else url_for(".admin_home")
    )

    return render_template(
        "admin/menus.html",
        dt=dt,
        menu=menu,
        caps=caps,
        back_url=back_url,
    )


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

    return render_template(
        "admin/auditoria.html",
        rows=rows,
        total=total,
        limite=limite,
        q_actor=q_actor,
        q_action=q_action,
        ACTION_ICONS=ACTION_ICONS,
    )


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

    # ── Dados de promoção por ano ─────────────────────────────────────────
    promocao_data = []
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
        if a >= 6:
            destino = "Concluído"
            cor = "#922b21"
        else:
            destino = _ano_label(a + 1)
            cor = "#1e8449"
        promocao_data.append(
            {
                "ano": a,
                "alunos": alunos_a,
                "destino": destino,
                "cor": cor,
            }
        )

    # ── Alunos para mover / atribuir turma ──────────────────────────────
    with db() as conn:
        alunos_all = [
            dict(r)
            for r in conn.execute(
                "SELECT NII, NI, Nome_completo, ano, turma_id FROM utilizadores WHERE perfil='aluno' ORDER BY ano, NI"
            ).fetchall()
        ]

    return render_template(
        "admin/companhias.html",
        anos_data=anos_data,
        all_anos=all_anos,
        turmas=turmas,
        alunos_all=alunos_all,
        promocao_data=promocao_data,
        ANOS_OPCOES=ANOS_OPCOES,
    )


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
