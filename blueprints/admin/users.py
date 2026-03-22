"""Rotas admin — gestão de utilizadores e importação CSV."""

from __future__ import annotations

import csv
import io

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from blueprints.admin import admin_bp
from core.meals import get_totais_dia
from core.users import (
    count_users,
    csv_check_duplicates,
    list_users,
    update_contacts,
    update_user,
    update_user_password,
)
from utils.auth import current_user, role_required
from utils.constants import ANOS_OPCOES
from utils.helpers import _audit, _get_anos_disponiveis
from utils.passwords import (
    _criar_utilizador,
    _eliminar_utilizador,
    _reset_pw,
    _unblock_user,
    generate_password_hash,
)
from utils.validators import (
    _val_ano,
    _val_email,
    _val_ni,
    _val_nii,
    _val_nome,
    _val_perfil,
    _val_phone,
)

from datetime import date


@admin_bp.route("/admin")
@role_required("admin")
def admin_home():
    hoje = date.today()
    t = get_totais_dia(hoje.isoformat())
    n_users = count_users()

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
                    update_user(nii_e, nome_e, ni_e, ano_e, perfil_e, email_e, tel_e)
                    if pw_e:
                        update_user_password(nii_e, generate_password_hash(pw_e))
                    _audit(
                        current_user().get("nii", "admin"),
                        "editar_utilizador",
                        f"NII={nii_e}",
                    )
                    flash("Utilizador atualizado.", "ok")
                except Exception as ex:
                    current_app.logger.error("editar_utilizador: %s", ex)
                    flash("Erro interno. Tenta novamente.", "error")
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
                    update_contacts(nii_e, email_e, tel_e)
                    _audit(
                        current_user().get("nii", "admin"),
                        "editar_contactos",
                        f"NII={nii_e}",
                    )
                    flash("Contactos atualizados.", "ok")
                except Exception as ex:
                    current_app.logger.error("editar_contactos: %s", ex)
                    flash("Erro interno. Tenta novamente.", "error")
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
    page = max(1, int(request.args.get("page", "1") or "1"))
    per_page = 50

    rows, total = list_users(q if q else None, ano_f, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)

    edit_user_nii = request.args.get("edit_user", "")
    edit_user_row = next((r for r in rows if r["NII"] == edit_user_nii), None)
    edit_row = next((r for r in rows if r["NII"] == edit_nii), None)

    return render_template(
        "admin/utilizadores.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
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

            if (
                linhas
                and linhas[0]
                and linhas[0][0].strip().upper() in ("NII", "#", "ID", "NUM")
            ):
                linhas = linhas[1:]

            preview_rows = []
            erros = []
            existentes = csv_check_duplicates()

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

                # Validação rigorosa com os mesmos validators do formulário
                nii_v = _val_nii(nii)
                ni_v = _val_ni(ni)
                nome_v = _val_nome(nome)
                if not nii_v:
                    erros.append(
                        f"Linha {i}: NII '{nii}' inválido (só alfanuméricos, máx 32 chars)."
                    )
                    continue
                if ni_v is None:
                    erros.append(f"Linha {i} ({nii}): NI '{ni}' inválido.")
                    continue
                if not nome_v:
                    erros.append(
                        f"Linha {i} ({nii}): Nome inválido ou demasiado longo."
                    )
                    continue
                nii, ni, nome = nii_v, ni_v, nome_v

                perfil_v = _val_perfil(perfil)
                if not perfil_v:
                    erros.append(f"Linha {i} ({nii}): perfil '{perfil}' inválido.")
                    continue
                perfil = perfil_v

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
            existentes = csv_check_duplicates()

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
