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
from markupsafe import Markup

from core.auth_db import user_by_nii
from core.backup import ensure_daily_backup
from core.database import db
from core.meals import (
    dias_operacionais_batch,
    get_totais_dia,
    get_totais_periodo,
    refeicao_editavel,
    refeicao_get,
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
    _ano_label,
    _back_btn,
    _bar_html,
    _get_anos_disponiveis,
    _parse_date,
    _refeicao_set,
    csrf_input,
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

    def occ_card(nome, icon):
        val, cap = occ.get(nome, (0, -1))
        bar = _bar_html(val, cap) if cap > 0 else ""
        return f'<div class="stat-box"><div class="stat-num">{val}</div><div class="stat-lbl">{icon} {nome}</div>{bar}</div>'

    detail = f"""
    <div class="grid grid-3" style="margin-top:.9rem">
      <div class="stat-box"><div class="stat-num">{t["alm_norm"]}</div><div class="stat-lbl">Almoço Normal</div></div>
      <div class="stat-box"><div class="stat-num">{t["alm_veg"]}</div><div class="stat-lbl">Almoço Vegetariano</div></div>
      <div class="stat-box"><div class="stat-num">{t["alm_dieta"]}</div><div class="stat-lbl">Almoço Dieta</div></div>
      <div class="stat-box"><div class="stat-num">{t["jan_norm"]}</div><div class="stat-lbl">Jantar Normal</div></div>
      <div class="stat-box"><div class="stat-num">{t["jan_veg"]}</div><div class="stat-lbl">Jantar Vegetariano</div></div>
      <div class="stat-box"><div class="stat-num">{t["jan_dieta"]}</div><div class="stat-lbl">Jantar Dieta</div></div>
    </div>"""

    # ── Alertas operacionais ──────────────────────────────────────────────
    alertas = _alertas_painel(d_str, perfil)
    alertas_html = ""
    if alertas:
        items = "".join(
            f'<div class="alert alert-{a["cat"]}" style="margin-bottom:.4rem">'
            f"{a['icon']} {esc(a['msg'])}</div>"
            for a in alertas
        )
        alertas_html = f'<div style="margin-bottom:1rem">{items}</div>'

    # ── Previsão de amanhã (cozinha / admin) ─────────────────
    previsao_html = ""
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

        previsao_html = f"""
      <div class="card" style="border-top:3px solid #2e86c1">
        <div class="card-title">🔮 Previsão Amanhã — {NOMES_DIAS[amanha.weekday()]} {amanha.strftime("%d/%m")}</div>
        <div class="grid grid-4">
          <div class="stat-box"><div class="stat-num">{t_am["pa"]}{_delta(t["pa"], t_am["pa"])}</div><div class="stat-lbl">☕ PA</div></div>
          <div class="stat-box"><div class="stat-num">{t_am["lan"]}{_delta(t["lan"], t_am["lan"])}</div><div class="stat-lbl">🥐 Lanche</div></div>
          <div class="stat-box"><div class="stat-num">{alm_a}{_delta(alm_h, alm_a)}</div><div class="stat-lbl">🍽️ Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{jan_a}{_delta(jan_h, jan_a)}</div><div class="stat-lbl">🌙 Jantares</div></div>
        </div>
        <div class="grid grid-3" style="margin-top:.5rem">
          <div class="stat-box"><div class="stat-num">{t_am["alm_norm"]}</div><div class="stat-lbl">Alm Normal</div></div>
          <div class="stat-box"><div class="stat-num">{t_am["alm_veg"]}</div><div class="stat-lbl">Alm Veg</div></div>
          <div class="stat-box"><div class="stat-num">{t_am["alm_dieta"]}</div><div class="stat-lbl">Alm Dieta</div></div>
        </div>
      </div>"""

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()
    nav_data = f"""
    <div class="flex-between" style="margin-bottom:1.1rem">
      <div class="flex">
        <a class="btn btn-ghost btn-sm" href="{url_for(".painel_dia", d=prev_d)}">← Anterior</a>
        <strong>{NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}</strong>
        <a class="btn btn-ghost btn-sm" href="{url_for(".painel_dia", d=next_d)}">Próximo →</a>
      </div>
      <form method="get" style="display:flex;gap:.3rem">
        <input type="date" name="d" value="{d_str}" style="width:auto">
        <button class="btn btn-primary btn-sm">Ir</button>
      </form>
    </div>"""

    # Ações rápidas por perfil
    acoes = []
    if perfil in ("cozinha", "oficialdia", "admin"):
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for("reporting.dashboard_semanal")}">📊 Dashboard</a>'
        )
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for("admin.admin_menus")}">🍽️ Menus &amp; Capacidade</a>'
        )
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for("reporting.calendario_publico")}">📅 Calendário</a>'
        )
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for(".relatorio_semanal")}">📈 Relatório Semanal</a>'
        )

    if perfil in ("oficialdia", "admin"):
        anos = _get_anos_disponiveis()
        for ano in anos:
            acoes.append(
                f'<a class="btn btn-ghost" href="{url_for(".lista_alunos_ano", ano=ano, d=d_str)}">👥 {ano}º Ano</a>'
            )
        acoes.append(
            f'<a class="btn btn-primary" href="{url_for(".controlo_presencas", d=dt.isoformat())}">🎯 Controlo Presenças</a>'
        )
        acoes.append(
            f'<a class="btn btn-warn" href="{url_for(".excecoes_dia", d=dt.isoformat())}">📝 Exceções</a>'
        )
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for(".ausencias")}">🚫 Ausências</a>'
        )
        acoes.append(
            f'<a class="btn btn-gold" href="{url_for(".licencas_entradas_saidas", d=d_str)}">🚪 Licenças / Entradas</a>'
        )

    if perfil == "cmd" and u.get("ano"):
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for(".lista_alunos_ano", ano=u["ano"], d=d_str)}">👥 Lista do {u["ano"]}º Ano</a>'
        )
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for(".imprimir_ano", ano=u["ano"], d=d_str)}" target="_blank">🖨 Imprimir mapa</a>'
        )
        acoes.append(
            f'<a class="btn btn-gold" href="{url_for("cmd.ausencias_cmd")}">🚫 Ausências do {u["ano"]}º Ano</a>'
        )
        acoes.append(
            f'<a class="btn btn-warn" href="{url_for("cmd.detencoes_cmd")}">⛔ Detenções</a>'
        )
        acoes.append(
            f'<a class="btn btn-ghost" href="{url_for("reporting.calendario_publico")}">📅 Calendário</a>'
        )

    backup_btn = ""
    if perfil in ("oficialdia", "admin"):
        backup_btn = f'<form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="backup"><button class="btn btn-ghost">💾 Backup BD</button></form>'
    if perfil == "admin":
        backup_btn += f' <a class="btn btn-ghost" href="{url_for("admin.admin_backup_download")}">📥 Download BD</a>'

    # Painel de detidos (oficialdia / admin)
    detidos_html = ""
    if perfil in ("oficialdia", "admin"):
        with db() as conn:
            detidos = [
                dict(r)
                for r in conn.execute(
                    """SELECT uu.NI, uu.Nome_completo, uu.ano, d.detido_de, d.detido_ate, d.motivo
                    FROM detencoes d JOIN utilizadores uu ON uu.id=d.utilizador_id
                    WHERE uu.perfil='aluno' AND d.detido_de<=? AND d.detido_ate>=?
                    ORDER BY uu.ano, uu.NI""",
                    (d_str, d_str),
                ).fetchall()
            ]
        if detidos:
            det_rows = "".join(
                f"<tr><td>{esc(r['NI'])}</td><td>{esc(r['Nome_completo'])}</td>"
                f"<td>{r['ano']}º</td><td>{r['detido_de']} a {r['detido_ate']}</td>"
                f"<td>{esc(r['motivo'] or '—')}</td></tr>"
                for r in detidos
            )
            detidos_html = f"""
      <div class="card" style="border-top:3px solid #e74c3c">
        <div class="card-title">⛔ Detidos hoje ({len(detidos)})</div>
        <div class="table-wrap"><table>
          <thead><tr><th>NI</th><th>Nome</th><th>Ano</th><th>Período</th><th>Motivo</th></tr></thead>
          <tbody>{det_rows}</tbody>
        </table></div>
      </div>"""

    # Licenças do dia (oficialdia / admin)
    licencas_html = ""
    if perfil in ("oficialdia", "admin"):
        with db() as conn:
            lics = [
                dict(r)
                for r in conn.execute(
                    """SELECT uu.NI, uu.Nome_completo, uu.ano, l.tipo, l.hora_saida, l.hora_entrada
                    FROM licencas l JOIN utilizadores uu ON uu.id=l.utilizador_id
                    WHERE l.data=? ORDER BY uu.ano, uu.NI""",
                    (d_str,),
                ).fetchall()
            ]
        if lics:

            def _lic_tipo_badge(tp):
                if tp == "antes_jantar":
                    return '<span class="badge badge-info" style="font-size:.6rem">🌅 Antes</span>'
                return '<span class="badge badge-muted" style="font-size:.6rem">🌙 Após</span>'

            lic_rows = "".join(
                f"<tr><td>{esc(r['NI'])}</td><td>{esc(r['Nome_completo'])}</td>"
                f"<td>{r['ano']}º</td><td>{_lic_tipo_badge(r['tipo'])}</td>"
                f"<td>{'✅ ' + r['hora_saida'] if r['hora_saida'] else '—'}</td>"
                f"<td>{'✅ ' + r['hora_entrada'] if r['hora_entrada'] else '—'}</td></tr>"
                for r in lics
            )
            licencas_html = f"""
      <div class="card" style="border-top:3px solid #2e86c1">
        <div class="card-title">🚪 Licenças hoje ({len(lics)})</div>
        <div class="table-wrap"><table>
          <thead><tr><th>NI</th><th>Nome</th><th>Ano</th><th>Tipo</th><th>Saída</th><th>Entrada</th></tr></thead>
          <tbody>{lic_rows}</tbody>
        </table></div>
        <a class="btn btn-gold btn-sm" href="{url_for(".licencas_entradas_saidas", d=d_str)}" style="margin-top:.5rem">Gerir entradas/saídas</a>
      </div>"""

    back = _back_btn(url_for("admin.admin_home")) if perfil == "admin" else ""
    label_ano = f" — {ano_int}º Ano" if ano_int else ""

    content = f"""
    <div class="container">
      <div class="page-header">
        {back}
        <div class="page-title">📋 Painel Operacional{label_ano}</div>
        {backup_btn}
      </div>
      {nav_data}
      {alertas_html}
      <div class="card">
        <div class="card-title">Ocupação geral</div>
        <div class="grid grid-4">
          {occ_card("Pequeno Almoço", "☕")}
          {occ_card("Lanche", "🥐")}
          {occ_card("Almoço", "🍽️")}
          {occ_card("Jantar", "🌙")}
        </div>
        {detail}
        {'<div style="margin-top:.65rem;font-size:.81rem;color:var(--muted)">🚪 Saem após jantar: <strong>' + str(t["jan_sai"]) + "</strong></div>" if perfil != "cozinha" else ""}
      </div>
      {previsao_html}
      {detidos_html}
      {licencas_html}
      <div class="card">
        <div class="card-title">⬇ Exportar</div>
        <div class="gap-btn">
          <a class="btn btn-primary" href="{url_for("reporting.exportar_dia", d=dt.isoformat(), fmt="csv")}">CSV</a>
          <a class="btn btn-primary" href="{url_for("reporting.exportar_dia", d=dt.isoformat(), fmt="xlsx")}">Excel</a>
        </div>
      </div>
      {'<div class="card"><div class="card-title">⚡ Ações rápidas</div><div class="gap-btn">' + chr(10).join(acoes) + "</div></div>" if acoes else ""}
    </div>"""
    return render_template("operations/painel.html", content=Markup(content))


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
            with db() as conn:
                conn.execute(
                    """DELETE FROM ausencias WHERE utilizador_id=?
                                AND ausente_de=? AND ausente_ate=?""",
                    (uid_t, dt.isoformat(), dt.isoformat()),
                )
                conn.commit()
        return redirect(url_for(".lista_alunos_ano", ano=ano, d=d_str))

    with db() as conn:
        alunos = [
            dict(r)
            for r in conn.execute(
                """
            SELECT u.id, u.NII, u.NI, u.Nome_completo,
                   r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade,
                   EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                          AND a.ausente_de <= ? AND a.ausente_ate >= ?) AS ausente,
                   (SELECT l.tipo FROM licencas l WHERE l.utilizador_id=u.id AND l.data=?) AS licenca_tipo
            FROM utilizadores u
            LEFT JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
            WHERE u.ano=?
            ORDER BY u.NI
        """,
                (
                    dt.isoformat(),
                    dt.isoformat(),
                    dt.isoformat(),
                    dt.isoformat(),
                    ano,
                ),
            ).fetchall()
        ]

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
    anos = _get_anos_disponiveis()
    tabs = ""
    if perfil in ("oficialdia", "admin"):
        tabs = (
            '<div class="year-tabs">'
            + "".join(
                f'<a class="year-tab {"active" if a == ano else ""}" href="{url_for(".lista_alunos_ano", ano=a, d=d_str)}">{_ano_label(a)}</a>'
                for a in anos
            )
            + "</div>"
        )

    def chip_ref(val, label, tp=None):
        if val:
            return f'<span class="meal-chip chip-{"type" if tp else "ok"}">{tp or label} ✓</span>'
        return f'<span class="meal-chip chip-no">{label} ✗</span>'

    # Validação de prazo para esta data
    ok_prazo, _ = refeicao_editavel(dt)

    rows_html = ""
    for a in alunos:
        sem = not any([a["pequeno_almoco"], a["lanche"], a["almoco"], a["jantar_tipo"]])
        row_bg = (
            "background:#fdecea"
            if a["ausente"]
            else ("background:#fff3cd" if sem else "background:#d5f5e3")
        )
        ausente_b = (
            '<span class="badge badge-warn" style="font-size:.65rem">Ausente</span>'
            if a["ausente"]
            else ""
        )
        sai_b = (
            '<span class="badge badge-muted" style="font-size:.65rem">🚪</span>'
            if a["jantar_sai_unidade"]
            else ""
        )
        lic_b = ""
        if a.get("licenca_tipo") == "antes_jantar":
            lic_b = '<span class="badge badge-info" style="font-size:.6rem">🌅 Lic. antes</span>'
        elif a.get("licenca_tipo") == "apos_jantar":
            lic_b = '<span class="badge badge-muted" style="font-size:.6rem">🌙 Lic. após</span>'
        exc_btn = ""  # Edição de refeições disponível no módulo de Exceções/Controlo de Presenças

        # Botão perfil do aluno — OD só pode VER, cmd e admin podem EDITAR
        edit_aluno_btn = ""
        if perfil == "oficialdia":
            edit_aluno_btn = f'<a class="btn btn-ghost btn-sm" href="{url_for("cmd.ver_perfil_aluno", nii=a["NII"], ano=ano, d=d_str)}" title="Ver perfil do aluno">👁</a>'
        elif perfil in ("admin", "cmd"):
            edit_aluno_btn = f'<a class="btn btn-ghost btn-sm" href="{url_for("cmd.cmd_editar_aluno", nii=a["NII"], ano=ano, d=d_str)}" title="Editar dados do aluno">👤</a>'

        # Botão de presença/ausência — removido da lista (usar módulo Controlo de Presenças)
        presenca_btn = ""

        rows_html += f"""
        <tr style="{row_bg}">
          <td class="small text-muted">{esc(a["NI"])}</td>
          <td><strong>{esc(a["Nome_completo"])}</strong> {ausente_b} {lic_b}</td>
          <td>{chip_ref(a["pequeno_almoco"], "PA")}</td>
          <td>{chip_ref(a["lanche"], "Lan")}</td>
          <td>{chip_ref(a["almoco"], "Almoço", a["almoco"][:3] if a["almoco"] else None)}</td>
          <td>{chip_ref(a["jantar_tipo"], "Jantar", a["jantar_tipo"][:3] if a["jantar_tipo"] else None)} {sai_b}</td>
          <td><div class="gap-btn">{presenca_btn}{exc_btn}{edit_aluno_btn}</div></td>
        </tr>"""

    wknd_badge = ""
    prazo_info_banner = ""
    if ok_prazo:
        prazo_info_banner = '<div class="alert alert-ok" style="margin-bottom:.7rem">✅ Os alunos ainda podem editar as próprias refeições (prazo não expirou).</div>'
    else:
        prazo_info_banner = '<div class="alert alert-info" style="margin-bottom:.7rem">🔒 Prazo expirado — os alunos já não podem alterar. Usa o botão <strong>✏️</strong> para fazer exceções.</div>'

    imprimir_btn = f'<a class="btn btn-ghost" href="{url_for(".imprimir_ano", ano=ano, d=d_str)}" target="_blank">🖨 Imprimir mapa</a>'

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".painel_dia", d=d_str), "Painel")}
        <div class="page-title">👥 {_ano_label(ano)} — {NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}{wknd_badge}</div>
        {imprimir_btn}
      </div>
      {tabs}
      {prazo_info_banner}
      <div class="grid grid-4" style="margin-bottom:1.1rem">
        <div class="stat-box"><div class="stat-num">{total_alunos}</div><div class="stat-lbl">Total alunos</div></div>
        <div class="stat-box"><div class="stat-num" style="color:var(--ok)">{com_ref}</div><div class="stat-lbl">Com refeições</div></div>
        <div class="stat-box"><div class="stat-num" style="color:var(--danger)">{total_alunos - com_ref}</div><div class="stat-lbl">Sem refeições</div></div>
        <div class="stat-box"><div class="stat-num" style="color:var(--warn)">{ausentes}</div><div class="stat-lbl">Ausentes</div></div>
      </div>

      <div class="card" style="padding:.9rem 1.2rem;margin-bottom:.8rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for(".lista_alunos_ano", ano=ano, d=prev_d)}">← Anterior</a>
            <strong>{dt.strftime("%d/%m/%Y")}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for(".lista_alunos_ano", ano=ano, d=next_d)}">Próximo →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d" value="{d_str}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Lista de presenças
          {'<span class="badge badge-info" style="margin-left:.5rem;font-weight:400;font-size:.7rem">Usa o módulo <a href="' + url_for(".controlo_presencas", d=d_str) + '">Controlo de Presenças</a> para marcar entradas/saídas</span>' if perfil in ("oficialdia", "admin") else ""}
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>NI</th><th>Nome</th><th>PA</th><th>Lanche</th><th>Almoço</th><th>Jantar</th><th>Presença / Exc.</th></tr>
            </thead>
            <tbody>{rows_html or '<tr><td colspan="7" class="text-muted center" style="padding:1.5rem">Sem dados.</td></tr>'}</tbody>
          </table>
        </div>
        <div style="margin-top:.7rem;font-size:.78rem;color:var(--muted);display:flex;gap:.8rem;flex-wrap:wrap">
          <span style="display:inline-flex;align-items:center;gap:.3rem"><span style="width:.75rem;height:.75rem;background:#d5f5e3;border:1px solid #a9dfbf;border-radius:3px;display:inline-block"></span>Presente com refeições</span>
          <span style="display:inline-flex;align-items:center;gap:.3rem"><span style="width:.75rem;height:.75rem;background:#fff3cd;border:1px solid #ffc107;border-radius:3px;display:inline-block"></span>Sem refeições marcadas</span>
          <span style="display:inline-flex;align-items:center;gap:.3rem"><span style="width:.75rem;height:.75rem;background:#fdecea;border:1px solid #f1948a;border-radius:3px;display:inline-block"></span>Ausente</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">📊 Totais do {ano}º Ano</div>
        <div class="grid grid-4">
          <div class="stat-box"><div class="stat-num">{t["pa"]}</div><div class="stat-lbl">Pequenos Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{t["lan"]}</div><div class="stat-lbl">Lanches</div></div>
          <div class="stat-box"><div class="stat-num">{t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]}</div><div class="stat-lbl">Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]}</div><div class="stat-lbl">Jantares</div></div>
        </div>
        <div class="gap-btn" style="margin-top:.8rem">
          <a class="btn btn-primary" href="{url_for("reporting.exportar_dia", d=d_str, fmt="csv")}">⬇ CSV</a>
          <a class="btn btn-primary" href="{url_for("reporting.exportar_dia", d=d_str, fmt="xlsx")}">⬇ Excel</a>
          <a class="btn btn-ghost" href="{url_for(".imprimir_ano", ano=ano, d=d_str)}" target="_blank">🖨 Imprimir</a>
        </div>
      </div>
    </div>"""
    return render_template("operations/lista_alunos.html", content=Markup(content))


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
    res = []
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = _rel_map.get(di.isoformat(), _rel_empty)
        tipo = _rel_cal.get(
            di.isoformat(), "fim_semana" if di.weekday() >= 5 else "normal"
        )
        res.append({"data": di, "t": t, "tipo": tipo})

    totais = {
        k: 0
        for k in [
            "pa",
            "lan",
            "alm_norm",
            "alm_veg",
            "alm_dieta",
            "jan_norm",
            "jan_veg",
            "jan_dieta",
            "jan_sai",
        ]
    }
    rows_html = ""
    for r in res:
        is_off = r["tipo"] in ("feriado", "exercicio")
        is_wknd = r["data"].weekday() >= 5
        st = (
            "color:var(--muted);background:#f9fafb"
            if is_off
            else ("background:#fffdf5" if is_wknd else "")
        )
        ic = ICONE.get(r["tipo"], "")
        t = r["t"]
        sai_td = (
            "" if perfil == "cozinha" else f'<td class="center">{t["jan_sai"]}</td>'
        )
        rows_html += f"""
        <tr style="{st}">
          <td><strong>{ABREV_DIAS[r["data"].weekday()]}</strong> {r["data"].strftime("%d/%m")} {ic}</td>
          <td class="center">{t["pa"]}</td><td class="center">{t["lan"]}</td>
          <td class="center">{t["alm_norm"]}</td><td class="center">{t["alm_veg"]}</td><td class="center">{t["alm_dieta"]}</td>
          <td class="center">{t["jan_norm"]}</td><td class="center">{t["jan_veg"]}</td><td class="center">{t["jan_dieta"]}</td>
          {sai_td}
        </tr>"""
        for k in totais:
            totais[k] += t[k]

    sai_th = "" if perfil == "cozinha" else "<th>Sai</th>"
    sai_total = (
        "" if perfil == "cozinha" else f'<td class="center">{totais["jan_sai"]}</td>'
    )
    rows_html += f"""
    <tr style="font-weight:800;background:#f0f4f8;border-top:2px solid var(--border)">
      <td>TOTAL</td>
      <td class="center">{totais["pa"]}</td><td class="center">{totais["lan"]}</td>
      <td class="center">{totais["alm_norm"]}</td><td class="center">{totais["alm_veg"]}</td><td class="center">{totais["alm_dieta"]}</td>
      <td class="center">{totais["jan_norm"]}</td><td class="center">{totais["jan_veg"]}</td><td class="center">{totais["jan_dieta"]}</td>
      {sai_total}
    </tr>"""

    prev_w = (d0 - timedelta(days=7)).isoformat()
    next_w = (d0 + timedelta(days=7)).isoformat()
    back_url = (
        url_for("admin.admin_home") if perfil == "admin" else url_for(".painel_dia")
    )

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url)}
        <div class="page-title">📊 Relatório Semanal</div>
      </div>
      <div class="card" style="padding:.9rem 1.2rem;margin-bottom:.8rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for(".relatorio_semanal", d0=prev_w)}">← Semana anterior</a>
            <strong>{d0.strftime("%d/%m/%Y")} — {d1.strftime("%d/%m/%Y")}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for(".relatorio_semanal", d0=next_w)}">Semana seguinte →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d0" value="{d0_str}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Dia</th><th>PA</th><th>Lanche</th><th>Alm N</th><th>Alm V</th><th>Alm D</th><th>Jan N</th><th>Jan V</th><th>Jan D</th>{sai_th}</tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        <div class="gap-btn" style="margin-top:.9rem">
          <a class="btn btn-primary" href="{url_for("reporting.exportar_relatorio", d0=d0_str, fmt="csv")}">⬇ CSV</a>
          <a class="btn btn-primary" href="{url_for("reporting.exportar_relatorio", d0=d0_str, fmt="xlsx")}">⬇ Excel</a>
        </div>
      </div>
      <div class="grid grid-4">
        <div class="stat-box"><div class="stat-num">{totais["pa"]}</div><div class="stat-lbl">Total PA</div></div>
        <div class="stat-box"><div class="stat-num">{totais["lan"]}</div><div class="stat-lbl">Total Lanches</div></div>
        <div class="stat-box"><div class="stat-num">{totais["alm_norm"] + totais["alm_veg"] + totais["alm_dieta"]}</div><div class="stat-lbl">Total Almoços</div></div>
        <div class="stat-box"><div class="stat-num">{totais["jan_norm"] + totais["jan_veg"] + totais["jan_dieta"]}</div><div class="stat-lbl">Total Jantares</div></div>
      </div>
    </div>"""
    return render_template("operations/relatorio.html", content=Markup(content))


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

    def tipos_opt(sel):
        return "".join(
            f'<option value="{t}" {"selected" if sel == t else ""}>{t}</option>'
            for t in ["Normal", "Vegetariano", "Dieta"]
        )

    def chk_label(name, checked, icon, label):
        s = "background:#eafaf1;border-color:#a9dfbf" if checked else ""
        return f'<label style="display:flex;align-items:center;gap:.6rem;cursor:pointer;padding:.6rem;border:1.5px solid var(--border);border-radius:9px;{s}"><input type="checkbox" name="{name}" {"checked" if checked else ""}> {icon} {label}</label>'

    form_html = ""
    if u_info:
        uid_info = u_info.get("id")
        # Ausência ativa
        ausente_hoje = uid_info and _tem_ausencia_ativa(uid_info, dt)
        # Prazo — pode o aluno ainda alterar por si?
        ok_prazo, _ = refeicao_editavel(dt)
        # Histórico recente de ausências
        aus_hist = []
        if uid_info:
            with db() as conn:
                aus_hist = [
                    dict(r)
                    for r in conn.execute(
                        """
                    SELECT ausente_de, ausente_ate, motivo FROM ausencias
                    WHERE utilizador_id=? ORDER BY ausente_de DESC LIMIT 5
                """,
                        (uid_info,),
                    ).fetchall()
                ]

        ausente_alert = ""
        if ausente_hoje:
            ausente_alert = '<div class="alert alert-warn">⚠️ <strong>Utilizador com ausência activa hoje</strong> — esta exceção pode não ter efeito prático.</div>'

        prazo_info = ""
        if ok_prazo:
            prazo_info = '<div class="alert alert-ok" style="margin-bottom:.6rem">✅ O aluno ainda pode alterar refeições por si próprio (prazo não expirou). Esta exceção só é necessária se o aluno não conseguir aceder ao sistema.</div>'
        else:
            prazo_info = '<div class="alert alert-info" style="margin-bottom:.6rem">🔒 Prazo expirado — o aluno já não pode alterar. Esta exceção é necessária para efetuar qualquer alteração.</div>'

        aus_hist_html = ""
        if aus_hist:
            aus_hist_html = '<div style="margin-top:.75rem"><div class="card-title" style="font-size:.8rem;margin-bottom:.4rem">📋 Ausências recentes</div>'
            for ah in aus_hist:
                aus_hist_html += f'<div style="font-size:.78rem;padding:.22rem 0;border-bottom:1px solid var(--border);color:var(--text)">{ah["ausente_de"]} → {ah["ausente_ate"]} <span class="text-muted">{esc(ah["motivo"] or "—")}</span></div>'
            aus_hist_html += "</div>"

        form_html = f"""
        <div class="card">
          <div class="card-title">✏️ {esc(u_info.get("Nome_completo", ""))} — NI {esc(u_info.get("NI", ""))} | {esc(u_info.get("ano", ""))}º Ano</div>
          {ausente_alert}{prazo_info}
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="nii" maxlength="32" value="{esc(nii_q)}">
            <div class="grid grid-2">
              {chk_label("pa", r.get("pequeno_almoco"), "☕", "Pequeno Almoço")}
              {chk_label("lanche", r.get("lanche"), "🥐", "Lanche")}
              <div class="form-group" style="margin:0">
                <label>🍽️ Almoço</label>
                <select name="almoco"><option value="">— Sem almoço —</option>{tipos_opt(r.get("almoco"))}</select>
              </div>
              <div class="form-group" style="margin:0">
                <label>🌙 Jantar</label>
                <select name="jantar"><option value="">— Sem jantar —</option>{tipos_opt(r.get("jantar_tipo"))}</select>
              </div>
            </div>
            <div style="margin-top:.8rem">
              {chk_label("sai", r.get("jantar_sai_unidade"), "🚪", "Sai da unidade após jantar")}
            </div>
            <hr>
            <button class="btn btn-ok">💾 Guardar exceção</button>
          </form>
          {aus_hist_html}
        </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".painel_dia", d=dt.isoformat()), "Painel")}
        <div class="page-title">📝 Exceções — {NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}</div>
      </div>
      <div class="card">
        <div class="card-title">Pesquisar utilizador</div>
        <form method="get" style="display:flex;gap:.5rem">
          <input type="hidden" name="d" value="{d}">
          <input type="text" name="nii" maxlength="32" placeholder="NII do utilizador" value="{esc(nii_q)}" style="flex:1">
          <button class="btn btn-primary">Pesquisar</button>
        </form>
      </div>
      {form_html or '<div class="card"><div class="text-muted">Introduz um NII para editar exceções.</div></div>'}
    </div>"""
    return render_template("operations/excecoes.html", content=Markup(content))


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

    with db() as conn:
        rows = [
            dict(r)
            for r in conn.execute("""
            SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                   a.ausente_de, a.ausente_ate, a.motivo
            FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
            ORDER BY a.ausente_de DESC""").fetchall()
        ]

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

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for(".painel_dia"))}<div class="page-title">🚫 Ausências</div></div>
      <div class="card">
        <div class="card-title">Registar ausência</div>
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group"><label>NII do utilizador</label><input type="text" name="nii" maxlength="32" required placeholder="NII"></div>
            <div class="form-group"><label>Motivo (opcional)</label><input type="text" name="motivo" maxlength="500" placeholder="Ex: deslocação, prova..."></div>
            <div class="form-group"><label>De</label><input type="date" name="de" required></div>
            <div class="form-group"><label>Até</label><input type="date" name="ate" required></div>
          </div>
          <button class="btn btn-ok">Registar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Lista de ausências</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Utilizador</th><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th></th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem ausências.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("operations/ausencias.html", content=Markup(content))


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
        agora = datetime.now().strftime("%H:%M")

        with db() as conn:
            if acao == "saida" and lic_id:
                conn.execute(
                    "UPDATE licencas SET hora_saida=? WHERE id=? AND hora_saida IS NULL",
                    (agora, lic_id),
                )
                conn.commit()
                flash("✅ Saída registada.", "ok")

            elif acao == "entrada" and lic_id:
                conn.execute(
                    "UPDATE licencas SET hora_entrada=? WHERE id=? AND hora_entrada IS NULL",
                    (agora, lic_id),
                )
                conn.commit()
                flash("✅ Entrada registada.", "ok")

            elif acao == "limpar_saida" and lic_id:
                conn.execute(
                    "UPDATE licencas SET hora_saida=NULL WHERE id=?", (lic_id,)
                )
                conn.commit()

            elif acao == "limpar_entrada" and lic_id:
                conn.execute(
                    "UPDATE licencas SET hora_entrada=NULL WHERE id=?", (lic_id,)
                )
                conn.commit()

        return redirect(url_for(".licencas_entradas_saidas", d=d_str))

    # ── Contadores ────────────────────────────────────────────────────────
    with db() as conn:
        # Total de licenças marcadas para hoje
        total = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE l.data=? AND uu.perfil='aluno'""",
            (d_str,),
        ).fetchone()["c"]

        # Saíram hoje (têm hora_saida)
        saidas = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE l.data=? AND uu.perfil='aluno' AND l.hora_saida IS NOT NULL""",
            (d_str,),
        ).fetchone()["c"]

        # Regressaram (têm hora_entrada)
        entradas = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE l.data=? AND uu.perfil='aluno' AND l.hora_entrada IS NOT NULL""",
            (d_str,),
        ).fetchone()["c"]

        # Fora da unidade = saíram (em qualquer data) mas ainda não regressaram
        fora = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE uu.perfil='aluno'
                 AND l.hora_saida IS NOT NULL
                 AND l.hora_entrada IS NULL""",
        ).fetchone()["c"]

        # ── Lista principal: licenças do dia selecionado ──────────────────
        rows_hoje = [
            dict(r)
            for r in conn.execute(
                """SELECT l.id, uu.NI, uu.Nome_completo, uu.ano,
                          l.data, l.tipo, l.hora_saida, l.hora_entrada
                   FROM licencas l
                   JOIN utilizadores uu ON uu.id=l.utilizador_id
                   WHERE l.data=? AND uu.perfil='aluno'
                   ORDER BY uu.ano, uu.NI""",
                (d_str,),
            ).fetchall()
        ]

        # ── Alunos ainda fora de dias anteriores ─────────────────────────
        rows_fora = [
            dict(r)
            for r in conn.execute(
                """SELECT l.id, uu.NI, uu.Nome_completo, uu.ano,
                          l.data, l.tipo, l.hora_saida, l.hora_entrada
                   FROM licencas l
                   JOIN utilizadores uu ON uu.id=l.utilizador_id
                   WHERE uu.perfil='aluno'
                     AND l.hora_saida IS NOT NULL
                     AND l.hora_entrada IS NULL
                     AND l.data != ?
                   ORDER BY l.data ASC, uu.ano, uu.NI""",
                (d_str,),
            ).fetchall()
        ]

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    def _tipo_badge(tp):
        if tp == "antes_jantar":
            return '<span class="badge badge-info" style="font-size:.65rem">🌅 Antes jantar</span>'
        return '<span class="badge badge-muted" style="font-size:.65rem">🌙 Após jantar</span>'

    def _build_row(r, mostrar_data=False):
        saiu = r["hora_saida"]
        entrou = r["hora_entrada"]

        if saiu and not entrou:
            estado = '<span class="badge badge-warn">Fora</span>'
        elif saiu and entrou:
            estado = '<span class="badge badge-ok">Regressou</span>'
        else:
            estado = '<span class="badge badge-muted">Pendente</span>'

        # Saída: botão se ainda não saiu, hora se já saiu
        if not saiu:
            col_saida = (
                f'<form method="post" style="display:inline">{csrf_input()}'
                f'<input type="hidden" name="acao" value="saida">'
                f'<input type="hidden" name="lic_id" value="{r["id"]}">'
                f'<button class="btn btn-warn btn-sm">🚶 Registar saída</button></form>'
            )
        else:
            col_saida = f'<span class="text-muted small">{saiu}</span>'

        # Entrada: botão só se saiu mas ainda não entrou
        if saiu and not entrou:
            col_entrada = (
                f'<form method="post" style="display:inline">{csrf_input()}'
                f'<input type="hidden" name="acao" value="entrada">'
                f'<input type="hidden" name="lic_id" value="{r["id"]}">'
                f'<button class="btn btn-ok btn-sm">✅ Registar entrada</button></form>'
            )
        elif entrou:
            col_entrada = f'<span class="text-muted small">{entrou}</span>'
        else:
            col_entrada = "—"

        data_td = (
            f'<td class="small text-muted">{r["data"]}</td>' if mostrar_data else ""
        )

        return (
            f"<tr>"
            f"<td>{esc(r['NI'])}</td>"
            f"<td><strong>{esc(r['Nome_completo'])}</strong></td>"
            f"<td>{r['ano']}º</td>"
            f"{data_td}"
            f"<td>{_tipo_badge(r['tipo'])}</td>"
            f"<td>{estado}</td>"
            f"<td>{col_saida}</td>"
            f"<td>{col_entrada}</td>"
            f"</tr>"
        )

    rows_html = "".join(_build_row(r) for r in rows_hoje)

    # Secção de alunos ainda fora de dias anteriores
    fora_html = ""
    if rows_fora:
        fora_rows_html = "".join(_build_row(r, mostrar_data=True) for r in rows_fora)
        fora_html = f"""
        <div class="card" style="border-left:4px solid var(--danger)">
          <div class="card-title" style="color:var(--danger)">
            ⚠️ Ainda fora da unidade — dias anteriores ({len(rows_fora)})
          </div>
          <div class="alert alert-warn" style="font-size:.82rem">
            Estes alunos saíram em dias anteriores e ainda não regressaram.
            Regista a entrada aqui quando voltarem.
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>NI</th><th>Nome</th><th>Ano</th><th>Data saída</th><th>Tipo</th><th>Estado</th><th>Saída</th><th>Entrada</th></tr>
              </thead>
              <tbody>{fora_rows_html}</tbody>
            </table>
          </div>
        </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".painel_dia", d=d_str))}
        <div class="page-title">🚪 Licenças / Entradas &amp; Saídas</div>
      </div>
      <div class="flex-between" style="margin-bottom:1rem">
        <div class="flex">
          <a class="btn btn-ghost btn-sm" href="{url_for(".licencas_entradas_saidas", d=prev_d)}">← Anterior</a>
          <strong>{NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}</strong>
          <a class="btn btn-ghost btn-sm" href="{url_for(".licencas_entradas_saidas", d=next_d)}">Próximo →</a>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom:1rem">
        <div class="stat-box">
          <div class="stat-num">{total}</div>
          <div class="stat-lbl">Licenças hoje</div>
        </div>
        <div class="stat-box">
          <div class="stat-num">{saidas}</div>
          <div class="stat-lbl">Saíram hoje</div>
        </div>
        <div class="stat-box">
          <div class="stat-num">{entradas}</div>
          <div class="stat-lbl">Regressaram hoje</div>
        </div>
        <div class="stat-box" style="{"background:#fef3cd" if fora > 0 else ""}">
          <div class="stat-num" style="{"color:var(--danger)" if fora > 0 else ""}">{fora}</div>
          <div class="stat-lbl">Fora da unidade</div>
        </div>
      </div>

      {fora_html}

      <div class="card">
        <div class="card-title">Licenças de {dt.strftime("%d/%m/%Y")}</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>NI</th><th>Nome</th><th>Ano</th><th>Tipo</th><th>Estado</th><th>Saída</th><th>Entrada</th></tr>
            </thead>
            <tbody>
              {rows_html or '<tr><td colspan="7" class="text-muted center" style="padding:1.5rem">Sem licenças para este dia.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("operations/licencas_es.html", content=Markup(content))


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
            with db() as conn:
                aluno = conn.execute(
                    "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NI=? AND perfil='aluno'",
                    (ni_q,),
                ).fetchone()
            if aluno:
                aluno = dict(aluno)
                uid = aluno["id"]
                ausente = _tem_ausencia_ativa(uid, dt)
                with db() as conn:
                    ref = conn.execute(
                        "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
                        (uid, dt.isoformat()),
                    ).fetchone()
                    ref = dict(ref) if ref else {}
                    lic = conn.execute(
                        "SELECT tipo, hora_saida, hora_entrada FROM licencas WHERE utilizador_id=? AND data=?",
                        (uid, dt.isoformat()),
                    ).fetchone()
                    lic = dict(lic) if lic else {}
                resultado = {
                    "aluno": aluno,
                    "ausente": ausente,
                    "ref": ref,
                    "ni": ni_q,
                    "licenca": lic,
                }
            else:
                flash(f'NI "{ni_q}" não encontrado.', "error")

        elif acao == "dar_saida" and ni_q:
            with db() as conn:
                aluno = conn.execute(
                    "SELECT id,NII,Nome_completo FROM utilizadores WHERE NI=? AND perfil='aluno'",
                    (ni_q,),
                ).fetchone()
            if aluno:
                aluno = dict(aluno)
                _registar_ausencia(
                    aluno["id"],
                    dt.isoformat(),
                    dt.isoformat(),
                    f"Saída registada por {u['nome']} ({u['perfil']})",
                    u["nii"],
                )
                # Sincronizar hora_saida na licença (se existir)
                agora = datetime.now().strftime("%H:%M")
                with db() as conn:
                    conn.execute(
                        "UPDATE licencas SET hora_saida=? WHERE utilizador_id=? AND data=? AND hora_saida IS NULL",
                        (agora, aluno["id"], dt.isoformat()),
                    )
                    conn.commit()
                flash(
                    f"✅ Saída registada para {aluno['Nome_completo']} (NI {ni_q}).",
                    "ok",
                )
            else:
                flash(f'NI "{ni_q}" não encontrado.', "error")

        elif acao == "dar_entrada" and ni_q:
            with db() as conn:
                aluno = conn.execute(
                    "SELECT id,NII,Nome_completo FROM utilizadores WHERE NI=? AND perfil='aluno'",
                    (ni_q,),
                ).fetchone()
            if aluno:
                aluno = dict(aluno)
                with db() as conn:
                    conn.execute(
                        """DELETE FROM ausencias WHERE utilizador_id=?
                                    AND ausente_de=? AND ausente_ate=?""",
                        (aluno["id"], dt.isoformat(), dt.isoformat()),
                    )
                    conn.commit()
                # Sincronizar hora_entrada na licença (se existir)
                agora = datetime.now().strftime("%H:%M")
                with db() as conn:
                    conn.execute(
                        "UPDATE licencas SET hora_entrada=? WHERE utilizador_id=? AND data=? AND hora_entrada IS NULL",
                        (agora, aluno["id"], dt.isoformat()),
                    )
                    conn.commit()
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
    anos_resumo = []
    for ano in _get_anos_disponiveis():
        with db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE ano=? AND perfil='aluno'",
                (ano,),
            ).fetchone()["c"]
            ausentes_a = conn.execute(
                """
                SELECT COUNT(*) c FROM utilizadores u
                WHERE u.ano=? AND u.perfil='aluno'
                AND EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                           AND a.ausente_de<=? AND a.ausente_ate>=?)""",
                (ano, dt.isoformat(), dt.isoformat()),
            ).fetchone()["c"]
            com_ref = conn.execute(
                """
                SELECT COUNT(*) c FROM utilizadores u
                WHERE u.ano=? AND u.perfil='aluno'
                AND EXISTS(SELECT 1 FROM refeicoes r WHERE r.utilizador_id=u.id
                           AND r.data=? AND (r.almoco IS NOT NULL OR r.jantar_tipo IS NOT NULL))""",
                (ano, dt.isoformat()),
            ).fetchone()["c"]
        anos_resumo.append(
            {
                "ano": ano,
                "total": total,
                "ausentes": ausentes_a,
                "presentes": total - ausentes_a,
                "com_ref": com_ref,
            }
        )

    resumo_html = ""
    for r in anos_resumo:
        resumo_html += f"""
        <div class="stat-box" style="cursor:pointer" onclick="window.location='{url_for(".lista_alunos_ano", ano=r["ano"], d=dt.isoformat())}'">
          <div class="stat-num">{r["presentes"]} <small style="font-size:.6em;color:var(--muted)">/ {r["total"]}</small></div>
          <div class="stat-lbl">{_ano_label(r["ano"])} — Presentes</div>
          <div style="margin-top:.35rem;font-size:.75rem">
            <span style="color:var(--warn)">✖ {r["ausentes"]} ausentes</span> &nbsp;
            <span style="color:var(--ok)">🍽 {r["com_ref"]} c/ refeições</span>
          </div>
        </div>"""

    # Resultado da pesquisa
    resultado_html = ""
    if resultado:
        al = resultado["aluno"]
        ref = resultado["ref"]
        ausente = resultado["ausente"]
        ni_val = resultado["ni"]
        lic_info = resultado.get("licenca", {})

        estado_cor = "#fdecea" if ausente else "#d5f5e3"
        estado_txt = "🔴 AUSENTE" if ausente else "🟢 PRESENTE"

        # Licença badge
        lic_badge = ""
        if lic_info.get("tipo") == "antes_jantar":
            lic_badge = '<div style="margin-top:.4rem"><span class="badge badge-info">🌅 Licença antes do jantar</span>'
            if lic_info.get("hora_saida"):
                lic_badge += f' <span class="text-muted small">Saiu: {lic_info["hora_saida"]}</span>'
            if lic_info.get("hora_entrada"):
                lic_badge += f' <span class="text-muted small">Entrou: {lic_info["hora_entrada"]}</span>'
            lic_badge += "</div>"
        elif lic_info.get("tipo") == "apos_jantar":
            lic_badge = '<div style="margin-top:.4rem"><span class="badge badge-muted">🌙 Licença após o jantar</span>'
            if lic_info.get("hora_saida"):
                lic_badge += f' <span class="text-muted small">Saiu: {lic_info["hora_saida"]}</span>'
            if lic_info.get("hora_entrada"):
                lic_badge += f' <span class="text-muted small">Entrou: {lic_info["hora_entrada"]}</span>'
            lic_badge += "</div>"

        def ref_chip(val, label, tipo=None):
            if val:
                txt = tipo if tipo else "✓"
                return f'<span style="background:#eafaf1;border:1.5px solid #a9dfbf;border-radius:7px;padding:.25rem .55rem;font-size:.8rem;font-weight:700">{label} {txt}</span>'
            return f'<span style="background:#fdecea;border:1.5px solid #f1948a;border-radius:7px;padding:.25rem .55rem;font-size:.8rem;color:var(--muted)">{label} ✗</span>'

        acao_presenca = f"""
        <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.8rem">
          {
            ""
            if not ausente
            else f'''
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="dar_entrada">
            <input type="hidden" name="ni" value="{esc(ni_val)}">
            <button class="btn btn-ok">✅ Dar Entrada (marcar presente)</button>
          </form>'''
        }
          {
            ""
            if ausente
            else f'''
          <form method="post" onsubmit="return confirm('Confirmar saída de {esc(al["Nome_completo"])}?')">
            {csrf_input()}
            <input type="hidden" name="acao" value="dar_saida">
            <input type="hidden" name="ni" value="{esc(ni_val)}">
            <button class="btn btn-danger">🚪 Dar Saída (marcar ausente)</button>
          </form>'''
        }
          <a class="btn btn-ghost" href="{
            url_for(
                "cmd.ver_perfil_aluno", nii=al["NII"], ano=al["ano"], d=dt.isoformat()
            )
        }">👁 Ver perfil completo</a>
        </div>"""

        resultado_html = f"""
        <div class="card" style="border-left:4px solid {"var(--danger)" if ausente else "var(--ok)"}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:.5rem">
            <div>
              <div style="font-size:1.15rem;font-weight:800">{esc(al["Nome_completo"])}</div>
              <div class="text-muted small">NI: <strong>{esc(al["NI"])}</strong> &nbsp;|&nbsp; {al["ano"]}º Ano &nbsp;|&nbsp; NII: {esc(al["NII"])}</div>
              {lic_badge}
            </div>
            <div style="background:{estado_cor};padding:.4rem .9rem;border-radius:20px;font-weight:800;font-size:1rem">{estado_txt}</div>
          </div>
          <hr style="margin:.7rem 0">
          <div class="card-title" style="font-size:.82rem;margin-bottom:.5rem">🍽️ Refeições em {dt.strftime("%d/%m/%Y")}</div>
          <div style="display:flex;gap:.4rem;flex-wrap:wrap">
            {ref_chip(ref.get("pequeno_almoco"), "☕ PA")}
            {ref_chip(ref.get("lanche"), "🥐 Lanche")}
            {ref_chip(ref.get("almoco"), "🍽️ Almoço", ref.get("almoco", "")[:3] if ref.get("almoco") else None)}
            {ref_chip(ref.get("jantar_tipo"), "🌙 Jantar", ref.get("jantar_tipo", "")[:3] if ref.get("jantar_tipo") else None)}
            {'<span style="background:#fef9e7;border:1.5px solid #f9e79f;border-radius:7px;padding:.25rem .55rem;font-size:.8rem">🚪 Sai</span>' if ref.get("jantar_sai_unidade") else ""}
          </div>
          {acao_presenca}
        </div>"""

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".painel_dia", d=dt.isoformat()), "Painel")}
        <div class="page-title">🎯 Controlo de Presenças</div>
      </div>

      <!-- Navegação de datas -->
      <div class="card" style="padding:.75rem 1.1rem;margin-bottom:.8rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for(".controlo_presencas", d=prev_d)}">← Anterior</a>
            <strong>{NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for(".controlo_presencas", d=next_d)}">Próximo →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d" value="{dt.isoformat()}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>

      <!-- Pesquisa rápida por NI -->
      <div class="card" style="border-top:3px solid var(--primary)">
        <div class="card-title">🔍 Pesquisa rápida por NI</div>
        <div class="alert alert-info" style="margin-bottom:.8rem;font-size:.82rem">
          💡 Introduz o NI do aluno (ex: <strong>222</strong>) para consultar o estado de presença e refeições. Podes depois dar entrada ou saída diretamente.
        </div>
        <form method="post" style="display:flex;gap:.5rem;flex-wrap:wrap">
          {csrf_input()}
          <input type="hidden" name="acao" value="consultar">
          <input type="text" name="ni" value="{esc(ni_q)}" placeholder="NI do aluno (ex: 222)"
            style="flex:1;min-width:140px;font-size:1.05rem;font-weight:700;letter-spacing:.05em"
            autofocus autocomplete="off">
          <button class="btn btn-primary" style="font-size:1rem">🔍 Consultar</button>
        </form>
      </div>

      {resultado_html}

      <!-- Botão Licenças / Entradas-Saídas -->
      <div class="card" style="border-top:3px solid var(--gold)">
        <div class="flex-between">
          <div>
            <div class="card-title" style="margin-bottom:.2rem">🚪 Licenças e Entradas/Saídas</div>
            <span class="text-muted small">Ver quem tem licença para hoje e registar entradas/saídas</span>
          </div>
          <a class="btn btn-gold" href="{url_for(".licencas_entradas_saidas", d=dt.isoformat())}">Abrir painel</a>
        </div>
      </div>

      <!-- Resumo por ano -->
      <div class="card">
        <div class="card-title">📊 Resumo geral — {dt.strftime("%d/%m/%Y")}</div>
        <div class="grid grid-3">{resumo_html or '<div class="text-muted">Sem dados.</div>'}</div>
        <div style="margin-top:.6rem;font-size:.8rem;color:var(--muted)">Clica num ano para ver a lista completa.</div>
      </div>
    </div>"""
    return render_template("operations/presencas.html", content=Markup(content))


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

    with db() as conn:
        alunos = [
            dict(r)
            for r in conn.execute(
                """
            SELECT u.NI, u.Nome_completo,
                   r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade,
                   EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                          AND a.ausente_de<=? AND a.ausente_ate>=?) AS ausente
            FROM utilizadores u
            LEFT JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
            WHERE u.ano=? ORDER BY u.NI
        """,
                (dt.isoformat(), dt.isoformat(), dt.isoformat(), ano),
            ).fetchall()
        ]

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
