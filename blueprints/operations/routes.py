"""Rotas do blueprint operations."""

from datetime import date, datetime, timedelta

from flask import (
    render_template,
    Response,
    abort,
    flash,
    redirect,
    request,
    url_for,
)

from core.auth_db import user_by_nii
from core.backup import ensure_daily_backup
from core.meals import (
    dias_operacionais_batch,
    get_totais_dia,
    get_totais_periodo,
    refeicao_editavel,
    refeicao_get,
)
from core.operations import (
    get_alunos_ano_com_estado,
    get_alunos_para_impressao,
    get_anos_resumo,
    get_ausencias_lista,
    get_ausencias_recentes,
    get_detidos_dia,
    get_licencas_contadores,
    get_licencas_dia,
    get_presenca_consulta,
    marcar_presente,
    registar_entrada_presenca,
    registar_hora_licenca,
    registar_saida_presenca,
)
from blueprints.operations import ops_bp
from utils.auth import current_user, role_required
from utils.business import (
    _alertas_painel,
    _get_ocupacao_dia,
    _registar_ausencia,
    _remover_ausencia,
    _tem_ausencia_ativa,
)
from utils.constants import ABREV_DIAS, NOMES_DIAS
from utils.helpers import (
    _get_anos_disponiveis,
    _parse_date,
    _refeicao_set,
    esc,
)
from utils.validators import _val_refeicao


@ops_bp.route("/painel", methods=["GET", "POST"])
@role_required("cozinha", "oficialdia", "cmd", "admin")
def painel_dia():
    u = current_user()
    perfil = u.get("perfil")
    d_str = request.args.get("d", date.today().isoformat())
    dt = _parse_date(d_str)

    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "backup":
            try:
                ensure_daily_backup()
                flash("Backup criado.", "ok")
            except Exception as e:
                flash(f"Falha: {e}", "error")
        return redirect(url_for(".painel_dia", d=dt.isoformat()))

    ano_int = int(u["ano"]) if perfil == "cmd" and u.get("ano") else None
    t = get_totais_dia(dt.isoformat(), ano_int)
    occ = _get_ocupacao_dia(dt)

    # Build occupancy items: (nome, icon, val, cap)
    occ_items = []
    for nome, icon in [
        ("Pequeno Almoço", "☕"),
        ("Lanche", "🥐"),
        ("Almoço", "🍽️"),
        ("Jantar", "🌙"),
    ]:
        val, cap = occ.get(nome, (0, -1))
        occ_items.append((nome, icon, val, cap))

    # ── Alertas operacionais ──────────────────────────────────────────────
    alertas = _alertas_painel(d_str, perfil)

    # ── Previsão de amanhã (cozinha / admin) ─────────────────
    previsao = None
    if perfil in ("cozinha", "admin"):
        amanha = dt + timedelta(days=1)
        t_am = get_totais_dia(amanha.isoformat(), ano_int)

        def _delta(h, a):
            d = a - h
            if d > 0:
                return f'<span style="color:#1e8449;font-size:.72rem"> ↑{d}</span>'
            if d < 0:
                return f'<span style="color:#c0392b;font-size:.72rem"> ↓{abs(d)}</span>'
            return '<span style="color:#6c757d;font-size:.72rem"> =</span>'

        alm_h = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
        jan_h = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]
        alm_a = t_am["alm_norm"] + t_am["alm_veg"] + t_am["alm_dieta"]
        jan_a = t_am["jan_norm"] + t_am["jan_veg"] + t_am["jan_dieta"]

        previsao = {
            "dia_semana": NOMES_DIAS[amanha.weekday()],
            "data_fmt": amanha.strftime("%d/%m"),
            "t_am": t_am,
            "alm_a": alm_a,
            "jan_a": jan_a,
            "deltas": {
                "pa": _delta(t["pa"], t_am["pa"]),
                "lan": _delta(t["lan"], t_am["lan"]),
                "alm": _delta(alm_h, alm_a),
                "jan": _delta(jan_h, jan_a),
            },
        }

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    # Ações rápidas por perfil
    acoes = []
    if perfil in ("cozinha", "oficialdia", "admin"):
        acoes.append(
            {
                "url": url_for("reporting.dashboard_semanal"),
                "icon": "📊",
                "label": "Dashboard",
                "cls": "btn-ghost",
            }
        )
        acoes.append(
            {
                "url": url_for("admin.admin_menus"),
                "icon": "🍽️",
                "label": "Menus & Capacidade",
                "cls": "btn-ghost",
            }
        )
        acoes.append(
            {
                "url": url_for("reporting.calendario_publico"),
                "icon": "📅",
                "label": "Calendário",
                "cls": "btn-ghost",
            }
        )
        acoes.append(
            {
                "url": url_for(".relatorio_semanal"),
                "icon": "📈",
                "label": "Relatório Semanal",
                "cls": "btn-ghost",
            }
        )

    if perfil in ("oficialdia", "admin"):
        for ano in _get_anos_disponiveis():
            acoes.append(
                {
                    "url": url_for(".lista_alunos_ano", ano=ano, d=d_str),
                    "icon": "👥",
                    "label": f"{ano}º Ano",
                    "cls": "btn-ghost",
                }
            )
        acoes.append(
            {
                "url": url_for(".controlo_presencas", d=dt.isoformat()),
                "icon": "🎯",
                "label": "Controlo Presenças",
                "cls": "btn-primary",
            }
        )
        acoes.append(
            {
                "url": url_for(".excecoes_dia", d=dt.isoformat()),
                "icon": "📝",
                "label": "Exceções",
                "cls": "btn-warn",
            }
        )
        acoes.append(
            {
                "url": url_for(".ausencias"),
                "icon": "🚫",
                "label": "Ausências",
                "cls": "btn-ghost",
            }
        )
        acoes.append(
            {
                "url": url_for(".licencas_entradas_saidas", d=d_str),
                "icon": "🚪",
                "label": "Licenças / Entradas",
                "cls": "btn-gold",
            }
        )

    if perfil == "cmd" and u.get("ano"):
        acoes.append(
            {
                "url": url_for(".lista_alunos_ano", ano=u["ano"], d=d_str),
                "icon": "👥",
                "label": f"Lista do {u['ano']}º Ano",
                "cls": "btn-ghost",
            }
        )
        acoes.append(
            {
                "url": url_for(".imprimir_ano", ano=u["ano"], d=d_str),
                "icon": "🖨",
                "label": "Imprimir mapa",
                "cls": "btn-ghost",
                "target": "_blank",
            }
        )
        acoes.append(
            {
                "url": url_for("cmd.ausencias_cmd"),
                "icon": "🚫",
                "label": f"Ausências do {u['ano']}º Ano",
                "cls": "btn-gold",
            }
        )
        acoes.append(
            {
                "url": url_for("cmd.detencoes_cmd"),
                "icon": "⛔",
                "label": "Detenções",
                "cls": "btn-warn",
            }
        )
        acoes.append(
            {
                "url": url_for("reporting.calendario_publico"),
                "icon": "📅",
                "label": "Calendário",
                "cls": "btn-ghost",
            }
        )

    # Painel de detidos (oficialdia / admin)
    detidos = []
    if perfil in ("oficialdia", "admin"):
        detidos = get_detidos_dia(d_str)

    # Licenças do dia (oficialdia / admin)
    lics = []
    if perfil in ("oficialdia", "admin"):
        lics = get_licencas_dia(d_str)

    back_url = url_for("admin.admin_home") if perfil == "admin" else ""
    label_ano = f" — {ano_int}º Ano" if ano_int else ""

    return render_template(
        "operations/painel.html",
        perfil=perfil,
        back_url=back_url,
        label_ano=label_ano,
        d_str=d_str,
        dt_iso=dt.isoformat(),
        dt_fmt=dt.strftime("%d/%m/%Y"),
        dia_semana=NOMES_DIAS[dt.weekday()],
        prev_d=prev_d,
        next_d=next_d,
        alertas=alertas,
        occ_items=occ_items,
        t=t,
        previsao=previsao,
        detidos=detidos,
        lics=lics,
        acoes=acoes,
    )


# ═══════════════════════════════════════════════════════════════════════════
# LISTA DE ALUNOS POR ANO (Oficial de Dia / CMD / Admin)
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/alunos/<int:ano>", methods=["GET", "POST"])
@role_required("oficialdia", "cmd", "admin")
def lista_alunos_ano(ano):
    u = current_user()
    perfil = u.get("perfil")

    # CMD só pode ver o seu ano
    if perfil == "cmd" and str(ano) != str(u.get("ano", "")):
        flash("Acesso restrito ao teu ano.", "error")
        return redirect(url_for(".painel_dia"))

    d_str = request.args.get("d", date.today().isoformat())
    dt = _parse_date(d_str)

    # POST: marcar/desmarcar ausência via lista
    if request.method == "POST":
        acao = request.form.get("acao", "")
        uid_t = request.form.get("uid", "")
        if acao == "marcar_ausente" and uid_t:
            _registar_ausencia(
                int(uid_t),
                dt.isoformat(),
                dt.isoformat(),
                f"Marcado por {u['nome']} ({perfil})",
                u["nii"],
            )
        elif acao == "marcar_presente" and uid_t:
            marcar_presente(int(uid_t), dt.isoformat())
        return redirect(url_for(".lista_alunos_ano", ano=ano, d=d_str))

    alunos = get_alunos_ano_com_estado(ano, dt)

    t = get_totais_dia(dt.isoformat(), ano)
    total_alunos = len(alunos)
    com_ref = sum(
        1
        for a in alunos
        if any([a["almoco"], a["jantar_tipo"], a["pequeno_almoco"], a["lanche"]])
    )
    ausentes = sum(1 for a in alunos if a["ausente"])

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    # Tabs de ano
    tabs_anos = _get_anos_disponiveis() if perfil in ("oficialdia", "admin") else []

    # Validação de prazo para esta data
    ok_prazo, _ = refeicao_editavel(dt)

    return render_template(
        "operations/lista_alunos.html",
        perfil=perfil,
        back_url=url_for(".painel_dia", d=d_str),
        ano=ano,
        dia_semana=NOMES_DIAS[dt.weekday()],
        dt_fmt=dt.strftime("%d/%m/%Y"),
        d_str=d_str,
        prev_d=prev_d,
        next_d=next_d,
        tabs_anos=tabs_anos,
        ok_prazo=ok_prazo,
        total_alunos=total_alunos,
        com_ref=com_ref,
        ausentes=ausentes,
        alunos=alunos,
        t=t,
    )


# ═══════════════════════════════════════════════════════════════════════════
# RELATÓRIO SEMANAL (cozinha + oficialdia + admin)
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/relatorio")
@role_required("cozinha", "oficialdia", "admin")
def relatorio_semanal():
    u = current_user()
    perfil = u.get("perfil")
    hoje = date.today()
    segunda = hoje - timedelta(days=hoje.weekday())
    d0_str = request.args.get("d0", segunda.isoformat())
    d0 = _parse_date(d0_str)
    d1 = d0 + timedelta(days=6)
    ICONE = {
        "normal": "",
        "fim_semana": "🌊",
        "feriado": "🔴",
        "exercicio": "🟡",
        "outro": "⚪",
    }

    # Batch: totais e calendário para a semana toda
    _rel_map, _rel_empty = get_totais_periodo(d0.isoformat(), d1.isoformat())
    _rel_cal = dias_operacionais_batch(d0, d1)

    totais = {
        k: 0
        for k in [
            "pa",
            "lan",
            "alm_norm",
            "alm_veg",
            "alm_dieta",
            "alm_estufa",
            "jan_norm",
            "jan_veg",
            "jan_dieta",
            "jan_sai",
            "jan_estufa",
        ]
    }
    dias = []
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = _rel_map.get(di.isoformat(), _rel_empty)
        tipo = _rel_cal.get(
            di.isoformat(), "fim_semana" if di.weekday() >= 5 else "normal"
        )
        dias.append(
            {
                "abrev": ABREV_DIAS[di.weekday()],
                "data_fmt": di.strftime("%d/%m"),
                "weekday": di.weekday(),
                "tipo": tipo,
                "icone": ICONE.get(tipo, ""),
                "t": t,
            }
        )
        for k in totais:
            totais[k] += t[k]

    prev_w = (d0 - timedelta(days=7)).isoformat()
    next_w = (d0 + timedelta(days=7)).isoformat()
    back_url = (
        url_for("admin.admin_home") if perfil == "admin" else url_for(".painel_dia")
    )

    return render_template(
        "operations/relatorio.html",
        back_url=back_url,
        d0_str=d0_str,
        d0_fmt=d0.strftime("%d/%m/%Y"),
        d1_fmt=d1.strftime("%d/%m/%Y"),
        prev_w=prev_w,
        next_w=next_w,
        show_sai=perfil != "cozinha",
        dias=dias,
        totais=totais,
    )


# ═══════════════════════════════════════════════════════════════════════════
# EXCEÇÕES
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/excecoes/<d>", methods=["GET", "POST"])
@role_required("oficialdia", "admin")
def excecoes_dia(d):
    u = current_user()
    dt = _parse_date(d)

    if request.method == "POST":
        nii = request.form.get("nii", "").strip()
        db_u = user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
            return redirect(url_for(".excecoes_dia", d=dt.isoformat()))
        pa = 1 if request.form.get("pa") else 0
        lanche = 1 if request.form.get("lanche") else 0
        alm = _val_refeicao(request.form.get("almoco"))
        jan = _val_refeicao(request.form.get("jantar"))
        sai = 1 if request.form.get("sai") else 0
        if _refeicao_set(
            db_u["id"], dt, pa, lanche, alm, jan, sai, alterado_por=u["nii"]
        ):
            flash(f"Exceção guardada para {db_u['Nome_completo']}.", "ok")
        else:
            flash("Erro ao guardar.", "error")
        return redirect(
            url_for(".excecoes_dia", d=dt.isoformat(), nii=request.form.get("nii", ""))
        )

    nii_q = request.args.get("nii", "").strip()
    u_info = user_by_nii(nii_q) if nii_q else None
    r = refeicao_get(u_info["id"], dt) if u_info and u_info.get("id") else {}

    ausente_hoje = False
    ok_prazo = False
    aus_hist = []
    chk_items = []
    ref_almoco = ""
    ref_jantar = ""
    ref_sai = False

    if u_info:
        uid_info = u_info.get("id")
        ausente_hoje = uid_info and _tem_ausencia_ativa(uid_info, dt)
        ok_prazo, _ = refeicao_editavel(dt)
        if uid_info:
            aus_hist = get_ausencias_recentes(uid_info)
        chk_items = [
            ("pa", r.get("pequeno_almoco"), "☕", "Pequeno Almoço"),
            ("lanche", r.get("lanche"), "🥐", "Lanche"),
        ]
        ref_almoco = r.get("almoco", "") or ""
        ref_jantar = r.get("jantar_tipo", "") or ""
        ref_sai = r.get("jantar_sai_unidade")

    return render_template(
        "operations/excecoes.html",
        d=d,
        dia_semana=NOMES_DIAS[dt.weekday()],
        dt_fmt=dt.strftime("%d/%m/%Y"),
        nii_q=nii_q,
        u_info=u_info,
        ausente_hoje=ausente_hoje,
        ok_prazo=ok_prazo,
        aus_hist=aus_hist,
        chk_items=chk_items,
        tipos_refeicao=["Normal", "Vegetariano", "Dieta"],
        ref_almoco=ref_almoco,
        ref_jantar=ref_jantar,
        ref_sai=ref_sai,
    )


# ═══════════════════════════════════════════════════════════════════════════
# AUSÊNCIAS
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/ausencias", methods=["GET", "POST"])
@role_required("oficialdia", "admin")
def ausencias():
    u = current_user()
    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "remover":
            _remover_ausencia(request.form.get("id"))
            flash("Ausência removida.", "ok")
            return redirect(url_for(".ausencias"))
        nii = request.form.get("nii", "").strip()
        db_u = user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
        else:
            ok, err = _registar_ausencia(
                db_u["id"],
                request.form.get("de", ""),
                request.form.get("ate", ""),
                request.form.get("motivo", "")[:500],
                u["nii"],
            )
            flash(
                f"Ausência registada para {db_u['Nome_completo']}."
                if ok
                else (err or "Falha."),
                "ok" if ok else "error",
            )
        return redirect(url_for(".ausencias"))

    rows = get_ausencias_lista()
    hoje = date.today().isoformat()

    return render_template(
        "operations/ausencias.html",
        rows=rows,
        hoje=hoje,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CMD — Editar dados de aluno do seu ano
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/oficialdia/licencas-es", methods=["GET", "POST"])
@role_required("oficialdia", "admin")
def licencas_entradas_saidas():
    hoje = date.today()
    d_str = request.args.get("d", hoje.isoformat())
    dt = _parse_date(d_str)

    if request.method == "POST":
        acao = request.form.get("acao", "")
        lic_id = request.form.get("lic_id", "")

        if acao in ("saida", "entrada", "limpar_saida", "limpar_entrada") and lic_id:
            registar_hora_licenca(lic_id, acao)
            if acao == "saida":
                flash("✅ Saída registada.", "ok")
            elif acao == "entrada":
                flash("✅ Entrada registada.", "ok")

        return redirect(url_for(".licencas_entradas_saidas", d=d_str))

    # ── Contadores e listas ───────────────────────────────────────────────
    lic_data = get_licencas_contadores(d_str)
    total = lic_data["total"]
    saidas = lic_data["saidas"]
    entradas = lic_data["entradas"]
    fora = lic_data["fora"]
    rows_hoje = lic_data["rows_hoje"]
    rows_fora = lic_data["rows_fora"]

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    return render_template(
        "operations/licencas_es.html",
        d_str=d_str,
        dia_semana=NOMES_DIAS[dt.weekday()],
        dt_fmt=dt.strftime("%d/%m/%Y"),
        prev_d=prev_d,
        next_d=next_d,
        total=total,
        saidas=saidas,
        entradas=entradas,
        fora=fora,
        rows_hoje=rows_hoje,
        rows_fora=rows_fora,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/presencas", methods=["GET", "POST"])
@role_required("oficialdia", "admin", "cmd")
def controlo_presencas():
    u = current_user()
    hoje = date.today()
    d_str = request.args.get("d", hoje.isoformat())
    dt = _parse_date(d_str)

    resultado = None
    ni_q = ""

    if request.method == "POST":
        acao = request.form.get("acao", "")
        ni_q = request.form.get("ni", "").strip()

        if acao == "consultar" and ni_q:
            resultado = get_presenca_consulta(ni_q, dt)
            if not resultado:
                flash(f'NI "{ni_q}" não encontrado.', "error")

        elif acao == "dar_saida" and ni_q:
            from core.users import get_aluno_by_ni

            aluno = get_aluno_by_ni(ni_q)
            if aluno:
                registar_saida_presenca(
                    aluno["id"], dt, u["nii"], u["nome"], u["perfil"]
                )
                flash(
                    f"✅ Saída registada para {aluno['Nome_completo']} (NI {ni_q}).",
                    "ok",
                )
            else:
                flash(f'NI "{ni_q}" não encontrado.', "error")

        elif acao == "dar_entrada" and ni_q:
            from core.users import get_aluno_by_ni

            aluno = get_aluno_by_ni(ni_q)
            if aluno:
                registar_entrada_presenca(aluno["id"], dt)
                flash(
                    f"✅ Entrada registada para {aluno['Nome_completo']} (NI {ni_q}).",
                    "ok",
                )
            else:
                flash(f'NI "{ni_q}" não encontrado.', "error")

        # Após POST sem resultado, redirecionar limpo
        if resultado is None:
            return redirect(url_for(".controlo_presencas", d=dt.isoformat()))

    # Resumo de todos os anos para a data
    anos_resumo = get_anos_resumo(dt, _get_anos_disponiveis())

    # Build ref_chips for resultado
    ref_chips = []
    if resultado:
        ref = resultado["ref"]
        ref_chips = [
            (ref.get("pequeno_almoco"), "☕ PA", None),
            (ref.get("lanche"), "🥐 Lanche", None),
            (
                ref.get("almoco"),
                "🍽️ Almoço",
                ref.get("almoco", "")[:3] if ref.get("almoco") else None,
            ),
            (
                ref.get("jantar_tipo"),
                "🌙 Jantar",
                ref.get("jantar_tipo", "")[:3] if ref.get("jantar_tipo") else None,
            ),
        ]

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    return render_template(
        "operations/presencas.html",
        dt_iso=dt.isoformat(),
        dt_fmt=dt.strftime("%d/%m/%Y"),
        dia_semana=NOMES_DIAS[dt.weekday()],
        prev_d=prev_d,
        next_d=next_d,
        ni_q=ni_q,
        resultado=resultado,
        ref_chips=ref_chips,
        anos_resumo=anos_resumo,
    )


# ═══════════════════════════════════════════════════════════════════════════
# GESTÃO DE COMPANHIAS — Unificação de Turmas + Promoção de Alunos
# ═══════════════════════════════════════════════════════════════════════════


@ops_bp.route("/imprimir/<int:ano>")
@role_required("oficialdia", "cozinha", "cmd", "admin")
def imprimir_ano(ano):
    u = current_user()
    perfil = u.get("perfil")
    if perfil == "cmd" and str(ano) != str(u.get("ano", "")):
        abort(403)

    d_str = request.args.get("d", date.today().isoformat())
    dt = _parse_date(d_str)

    alunos = get_alunos_para_impressao(ano, dt)

    def sim_nao(v):
        return "✓" if v else "–"

    rows = "".join(
        f"""
        <tr{'style="background:#fff9ec"' if a["ausente"] else ""}>
          <td>{esc(a["NI"])}</td>
          <td style="text-align:left">{esc(a["Nome_completo"])}{"  🏖" if a["ausente"] else ""}</td>
          <td>{sim_nao(a["pequeno_almoco"])}</td>
          <td>{sim_nao(a["lanche"])}</td>
          <td>{(a["almoco"] or "–")[:3]}</td>
          <td>{(a["jantar_tipo"] or "–")[:3]}</td>
          <td>{"✓" if a["jantar_sai_unidade"] else "–"}</td>
        </tr>"""
        for a in alunos
    )

    t = get_totais_dia(dt.isoformat(), ano)
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")

    html = f"""<!doctype html>
<html lang="pt"><head><meta charset="utf-8">
<title>Mapa {ano}º Ano — {dt.strftime("%d/%m/%Y")}</title>
<style>
  @page{{size:A4;margin:1.5cm}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:Arial,sans-serif;font-size:10pt;color:#111}}
  .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.8cm;border-bottom:2px solid #0a2d4e;padding-bottom:.4cm}}
  .header-left h1{{font-size:14pt;color:#0a2d4e;font-weight:900}}
  .header-left p{{font-size:9pt;color:#555;margin-top:.15cm}}
  .header-right{{text-align:right;font-size:8pt;color:#555}}
  table{{width:100%;border-collapse:collapse;font-size:9pt}}
  th{{background:#0a2d4e;color:#fff;padding:.25cm .3cm;text-align:center;font-weight:700}}
  td{{padding:.22cm .3cm;border:1px solid #ccc;text-align:center;vertical-align:middle}}
  tr:nth-child(even){{background:#f5f8fc}}
  tr[style]{{background:#fff9ec!important}}
  .totais{{margin-top:.6cm;font-size:9pt;border:1px solid #ccc;border-radius:6px;padding:.4cm}}
  .totais-title{{font-weight:800;font-size:10pt;color:#0a2d4e;margin-bottom:.25cm}}
  .totais-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:.3cm}}
  .totais-item{{text-align:center;background:#f0f4f8;border-radius:4px;padding:.2cm}}
  .totais-num{{font-size:14pt;font-weight:900;color:#0a2d4e}}
  .totais-lbl{{font-size:8pt;color:#555}}
  .footer{{margin-top:.6cm;font-size:7.5pt;color:#888;text-align:right}}
  .legenda{{margin-top:.4cm;font-size:8pt;color:#555}}
  @media print{{button{{display:none!important}}}}
</style>
</head><body>
<div class="header">
  <div class="header-left">
    <h1>⚓ Escola Naval — Mapa de Refeições</h1>
    <p><strong>{ano}º Ano</strong> &nbsp;|&nbsp; {NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}</p>
  </div>
  <div class="header-right">
    Gerado em: {gerado_em}<br>
    Por: {esc(u["nome"])}
    <br><br>
    <button onclick="window.print()" style="background:#0a2d4e;color:#fff;border:none;padding:.3cm .6cm;border-radius:5px;cursor:pointer;font-size:9pt">🖨 Imprimir</button>
  </div>
</div>

<table>
  <thead><tr>
    <th style="width:1.2cm">NI</th>
    <th style="width:6cm;text-align:left">Nome</th>
    <th>PA</th><th>Lanche</th><th>Almoço</th><th>Jantar</th><th>Sai</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>

<div class="totais">
  <div class="totais-title">📊 Totais — {ano}º Ano</div>
  <div class="totais-grid">
    <div class="totais-item"><div class="totais-num">{t["pa"]}</div><div class="totais-lbl">Peq. Almoços</div></div>
    <div class="totais-item"><div class="totais-num">{t["lan"]}</div><div class="totais-lbl">Lanches</div></div>
    <div class="totais-item"><div class="totais-num">{t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]}</div><div class="totais-lbl">Almoços</div></div>
    <div class="totais-item"><div class="totais-num">{t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]}</div><div class="totais-lbl">Jantares</div></div>
    <div class="totais-item"><div class="totais-num">{t["alm_norm"]}</div><div class="totais-lbl">Alm. Normal</div></div>
    <div class="totais-item"><div class="totais-num">{t["alm_veg"]}</div><div class="totais-lbl">Alm. Veg.</div></div>
    <div class="totais-item"><div class="totais-num">{t["alm_dieta"]}</div><div class="totais-lbl">Alm. Dieta</div></div>
    <div class="totais-item"><div class="totais-num">{t["jan_sai"]}</div><div class="totais-lbl">Saem após jantar</div></div>
  </div>
</div>
<div class="legenda">PA=Pequeno Almoço &nbsp;|&nbsp; Nor=Normal &nbsp;|&nbsp; Veg=Vegetariano &nbsp;|&nbsp; Die=Dieta &nbsp;|&nbsp; 🏖=Ausente</div>
<div class="footer">Escola Naval &nbsp;|&nbsp; Documento de uso interno</div>
</body></html>"""

    return Response(html, mimetype="text/html")


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD VISUAL SEMANAL
# ═══════════════════════════════════════════════════════════════════════════
