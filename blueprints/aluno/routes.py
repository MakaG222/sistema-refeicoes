"""Rotas do aluno: home, editar refeições, ausências, histórico, password, perfil."""

import io
import csv as _csv
from datetime import date, timedelta

from flask import (
    render_template,
    Response,
    abort,
    flash,
    redirect,
    request,
    session,
    url_for,
)
from markupsafe import Markup

import config as cfg
from core.auth_db import user_id_by_nii
from core.database import db
from core.meals import (
    dias_operacionais_batch,
    get_menu_do_dia,
    refeicao_get,
    refeicoes_batch,
)
from core.absences import ausencias_batch, detencoes_batch, licencas_batch
from blueprints.aluno import aluno_bp
from utils.auth import current_user, login_required
from utils.business import (
    _cancelar_licenca_fds,
    _dia_editavel_aluno,
    _editar_ausencia,
    _get_ocupacao_dia,
    _licencas_semana_usadas,
    _marcar_licenca_fds,
    _pode_marcar_licenca,
    _registar_ausencia,
    _regras_licenca,
    _tem_ausencia_ativa,
    _tem_detencao_ativa,
)
from utils.constants import ABREV_DIAS, NOMES_DIAS
from utils.helpers import (
    _audit,
    _back_btn,
    _bar_html,
    _parse_date,
    _parse_date_strict,
    _prazo_label,
    _refeicao_set,
    csrf_input,
    esc,
)
from utils.passwords import _alterar_password
from utils.validators import (
    _val_email,
    _val_int_id,
    _val_phone,
    _val_refeicao,
    _val_text,
)


@aluno_bp.route("/aluno/licenca-fds", methods=["POST"])
@login_required
def aluno_licenca_fds():
    """Marcar ou cancelar licença de fim de semana."""
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    if not uid:
        flash("Conta de sistema — funcionalidade não disponível.", "error")
        return redirect(url_for(".aluno_home"))

    sexta_str = request.form.get("sexta", "")
    acao = request.form.get("acao_fds", "marcar")  # "marcar" ou "cancelar"
    sexta = _parse_date_strict(sexta_str)
    if not sexta or sexta.weekday() != 4:  # 4 = sexta-feira
        flash("Data inválida — apenas sextas-feiras.", "error")
        return redirect(url_for(".aluno_home"))

    # Verificar se a sexta ainda é editável
    ok_edit, msg = _dia_editavel_aluno(sexta)
    if not ok_edit:
        flash(f"Não é possível editar: {msg}", "warn")
        return redirect(url_for(".aluno_home"))

    # Verificar ausência
    if _tem_ausencia_ativa(uid, sexta):
        flash("Tens uma ausência registada para este período.", "warn")
        return redirect(url_for(".aluno_home"))

    # Verificar detenção
    if _tem_detencao_ativa(uid, sexta):
        flash("Estás detido — não podes marcar licença de fim de semana.", "error")
        return redirect(url_for(".aluno_home"))

    if acao == "cancelar":
        ok, err = _cancelar_licenca_fds(uid, sexta, u["nii"])
        flash(
            "Licença de fim de semana cancelada." if ok else (err or "Erro."),
            "ok" if ok else "error",
        )
    else:
        # Verificar regras de licença para a sexta
        with db() as conn:
            aluno_row = conn.execute(
                "SELECT ano, NI FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
        ano_aluno = int(aluno_row["ano"]) if aluno_row else 1
        ni_aluno = aluno_row["NI"] if aluno_row else ""

        pode, motivo = _pode_marcar_licenca(uid, sexta, ano_aluno, ni_aluno)
        if not pode:
            flash(motivo, "warn")
            return redirect(url_for(".aluno_home"))

        ok, err = _marcar_licenca_fds(uid, sexta, u["nii"])
        flash(
            "✅ Licença de fim de semana marcada! Jantar de sexta e refeições de sábado/domingo cancelados."
            if ok
            else (err or "Erro."),
            "ok" if ok else "error",
        )

    return redirect(url_for(".aluno_home"))


@aluno_bp.route("/aluno")
@login_required
def aluno_home():
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    hoje = date.today()
    menu = get_menu_do_dia(hoje)

    # Banner ausência ativa hoje
    ausente_hoje = uid and _tem_ausencia_ativa(uid, hoje)
    ausente_html = ""
    if ausente_hoje:
        ausente_html = '<div class="ausente-banner">⚓ Tens uma <strong>ausência registada</strong> para hoje. As tuas refeições não serão contabilizadas.</div>'

    menu_html = ""
    if menu:

        def mv(k):
            return esc(menu.get(k) or "—")

        menu_html = f"""
        <div class="card">
          <div class="card-title">🍽️ Ementa de hoje — {hoje.strftime("%d/%m/%Y")}</div>
          <div class="grid grid-4">
            <div><strong>Peq. Almoço</strong><br><span class="text-muted">{mv("pequeno_almoco")}</span></div>
            <div><strong>Lanche</strong><br><span class="text-muted">{mv("lanche")}</span></div>
            <div><strong>Almoço</strong><br>N: {mv("almoco_normal")}<br>V: {mv("almoco_veg")}<br>D: {mv("almoco_dieta")}</div>
            <div><strong>Jantar</strong><br>N: {mv("jantar_normal")}<br>V: {mv("jantar_veg")}<br>D: {mv("jantar_dieta")}</div>
          </div>
        </div>"""

    def chip(val, label, tp=None):
        if val:
            return f'<span class="meal-chip chip-{"type" if tp else "ok"}">{tp or label} ✓</span>'
        return f'<span class="meal-chip chip-no">{label} ✗</span>'

    # Batch-load: carregar todos os dados de uma vez (elimina N+1)
    d_ate = hoje + timedelta(days=cfg.DIAS_ANTECEDENCIA)
    cal_map = dias_operacionais_batch(hoje, d_ate)
    if uid:
        ref_map, ref_defaults = refeicoes_batch(uid, hoje, d_ate)
        aus_set = ausencias_batch(uid, hoje, d_ate)
        det_set = detencoes_batch(uid, hoje, d_ate)
        lic_map = licencas_batch(uid, hoje, d_ate)
    else:
        ref_map, ref_defaults = {}, {}
        aus_set = set()
        det_set = set()
        lic_map = {}

    dias_html = ""
    for i in range(cfg.DIAS_ANTECEDENCIA + 1):
        d = hoje + timedelta(days=i)
        d_iso = d.isoformat()
        tipo = cal_map.get(d_iso, "fim_semana" if d.weekday() >= 5 else "normal")
        r = ref_map.get(d_iso, ref_defaults) if uid else {}
        ok_edit, _ = _dia_editavel_aluno(d)
        prazo = _prazo_label(d)
        ausente_d = d_iso in aus_set
        detido_d = d_iso in det_set
        is_weekend = d.weekday() >= 5
        is_off = tipo in ("feriado", "exercicio")

        if is_off:
            ic = {"feriado": "🔴", "exercicio": "🟡"}.get(tipo, "⚪")
            lb = {"feriado": "Feriado", "exercicio": "Exercício"}.get(tipo, tipo)
            dias_html += f"""
            <div class="week-card day-off">
              <div class="week-dow">{ABREV_DIAS[d.weekday()]}</div>
              <div class="week-date">{d.strftime("%d/%m")}</div>
              <span class="text-muted small">{ic} {lb}</span>
            </div>"""
            continue

        aus_chip = (
            '<span class="meal-chip chip-type" style="background:#fef3cd;color:#856404;margin-bottom:.3rem;display:block">⚓ Ausente</span>'
            if ausente_d
            else ""
        )
        det_chip = (
            '<span class="meal-chip chip-type" style="background:#fdecea;color:#7a1c1c;margin-bottom:.3rem;display:block">🚫 Detido</span>'
            if detido_d
            else ""
        )
        # Licença do batch
        lic_chip = ""
        lic_tipo = lic_map.get(d_iso)
        if uid and not detido_d and lic_tipo:
            lic_lbl = "Antes jantar" if lic_tipo == "antes_jantar" else "Após jantar"
            lic_chip = f'<span class="meal-chip chip-type" style="background:#d4efdf;color:#1e8449;margin-bottom:.3rem;display:block">🚪 {lic_lbl}</span>'
        alm_t = r.get("almoco")
        jan_t = r.get("jantar_tipo")
        meals = f"""<div class="week-meals">
            {chip(r.get("pequeno_almoco"), "PA")}
            {chip(r.get("lanche"), "Lan")}
            {chip(alm_t, "Alm", alm_t[:3] if alm_t else None)}
            {chip(jan_t, "Jan", jan_t[:3] if jan_t else None)}
          </div>{prazo}"""
        btn = (
            f'<a class="btn btn-primary btn-sm" style="margin-top:.38rem" href="{url_for(".aluno_editar", d=d.isoformat())}">✏️ Editar</a>'
            if ok_edit and not ausente_d
            else ""
        )

        # ── Botão licença FDS (sextas-feiras) ────────────────────────
        fds_btn_html = ""
        is_friday = d.weekday() == 4
        if is_friday and uid and not is_off:
            tem_licenca_fds = lic_map.get(d_iso) == "antes_jantar"
            nao_detido = not detido_d
            nao_ausente = not ausente_d

            if ok_edit and nao_detido and nao_ausente:
                if tem_licenca_fds:
                    fds_btn_html = f"""
                    <form method="post" action="{url_for(".aluno_licenca_fds")}" style="margin-top:.4rem">
                      {csrf_input()}
                      <input type="hidden" name="sexta" value="{d_iso}">
                      <input type="hidden" name="acao_fds" value="cancelar">
                      <button class="btn btn-danger btn-sm" style="width:100%;font-size:.7rem"
                        onclick="return confirm('Cancelar licença de fim de semana?')">
                        🔄 Cancelar licença FDS
                      </button>
                    </form>"""
                else:
                    fds_btn_html = f"""
                    <form method="post" action="{url_for(".aluno_licenca_fds")}" style="margin-top:.4rem">
                      {csrf_input()}
                      <input type="hidden" name="sexta" value="{d_iso}">
                      <input type="hidden" name="acao_fds" value="marcar">
                      <button class="btn btn-gold btn-sm" style="width:100%;font-size:.7rem"
                        onclick="return confirm('Marcar licença de fim de semana?\\nIsto vai:\\n• Retirar o jantar de sexta\\n• Apagar refeições de sábado e domingo')">
                        🏖️ Licença FDS
                      </button>
                    </form>"""

        card_cls = "weekend-active" if is_weekend else ""
        dow_cls = "weekend" if is_weekend else ""

        dias_html += f"""
        <div class="week-card {card_cls}">
          <div class="week-dow {dow_cls}">{ABREV_DIAS[d.weekday()]}</div>
          <div class="week-date">{d.strftime("%d/%m/%Y")}</div>
          {aus_chip}{det_chip}{lic_chip}{meals}{fds_btn_html}{btn}
        </div>"""

    stats_html = ""
    if uid:
        d0 = (hoje - timedelta(days=30)).isoformat()
        with db() as conn:
            rows = conn.execute(
                "SELECT pequeno_almoco,lanche,almoco,jantar_tipo FROM refeicoes WHERE utilizador_id=? AND data>=?",
                (uid, d0),
            ).fetchall()
        if rows:
            stats_html = f"""
            <div class="card">
              <div class="card-title">📊 Últimos 30 dias</div>
              <div class="grid grid-4">
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r["pequeno_almoco"])}</div><div class="stat-lbl">Pequenos Almoços</div></div>
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r["lanche"])}</div><div class="stat-lbl">Lanches</div></div>
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r["almoco"])}</div><div class="stat-lbl">Almoços</div></div>
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r["jantar_tipo"])}</div><div class="stat-lbl">Jantares</div></div>
              </div>
            </div>"""

    content = f"""
    <div class="container">
      <div class="page-header"><div class="page-title">Olá, {esc(u["nome"])} 👋</div></div>
      {ausente_html}{menu_html}
      <div class="card">
        <div class="card-title">📆 Próximos {cfg.DIAS_ANTECEDENCIA} dias

        </div>
        <div class="week-grid">{dias_html}</div>
      </div>
      {stats_html}
      <div class="gap-btn">
        <a class="btn btn-ghost" href="{url_for(".aluno_historico")}">🕘 Histórico (30 dias)</a>
        <a class="btn btn-gold" href="{url_for(".aluno_ausencias")}">🚫 Gerir ausências</a>
        <a class="btn btn-ghost" href="{url_for(".aluno_password")}">🔑 Alterar password</a>
        <a class="btn btn-ghost" href="{url_for("reporting.calendario_publico")}">📅 Calendário</a>
        <a class="btn btn-primary" href="{url_for(".aluno_perfil")}">👤 O meu perfil</a>
      </div>
    </div>"""
    return render_template("aluno/home.html", content=Markup(content))


@aluno_bp.route("/aluno/editar/<d>", methods=["GET", "POST"])
@login_required
def aluno_editar(d):
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    dt = _parse_date(d)

    if not uid:
        flash("Conta de sistema — não é possível editar refeições.", "error")
        return redirect(url_for(".aluno_home"))

    # Bloquear edição se tem ausência ativa
    if _tem_ausencia_ativa(uid, dt):
        flash(
            "Tens uma ausência registada para este dia. Remove a ausência primeiro.",
            "warn",
        )
        return redirect(url_for(".aluno_home"))

    ok_edit, msg = _dia_editavel_aluno(dt)
    if not ok_edit:
        flash(f"Não é possível editar: {msg}", "warn")
        return redirect(url_for(".aluno_home"))

    r = refeicao_get(uid, dt)
    occ = _get_ocupacao_dia(dt)
    is_weekend = dt.weekday() >= 5
    detido = _tem_detencao_ativa(uid, dt)

    # Dados do aluno para regras de licença
    with db() as conn:
        aluno_row = conn.execute(
            "SELECT ano, NI FROM utilizadores WHERE id=?", (uid,)
        ).fetchone()
    ano_aluno = int(aluno_row["ano"]) if aluno_row else 1
    ni_aluno = aluno_row["NI"] if aluno_row else ""

    # Licença existente para este dia
    with db() as conn:
        lic_row = conn.execute(
            "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
            (uid, dt.isoformat()),
        ).fetchone()
    licenca_atual = lic_row["tipo"] if lic_row else ""

    pode_lic, motivo_lic = _pode_marcar_licenca(uid, dt, ano_aluno, ni_aluno)

    if request.method == "POST":
        pa = 1 if request.form.get("pa") in ("1", "on") else 0
        lanche = 1 if request.form.get("lanche") in ("1", "on") else 0
        alm = _val_refeicao(request.form.get("almoco"))
        jan = _val_refeicao(request.form.get("jantar"))
        sai = 0 if detido else (1 if request.form.get("sai") else 0)

        # Processar licença (antes_jantar / apos_jantar / vazio)
        licenca_tipo = request.form.get("licenca", "")
        with db() as conn:
            if licenca_tipo in ("antes_jantar", "apos_jantar"):
                pode, motivo = _pode_marcar_licenca(uid, dt, ano_aluno, ni_aluno)
                if pode:
                    conn.execute(
                        """INSERT INTO licencas(utilizador_id, data, tipo)
                        VALUES(?,?,?)
                        ON CONFLICT(utilizador_id, data) DO UPDATE SET tipo=excluded.tipo""",
                        (uid, dt.isoformat(), licenca_tipo),
                    )
                    if licenca_tipo == "antes_jantar":
                        jan = ""
                        sai = 1
                    else:
                        sai = 1
                else:
                    flash(motivo, "warn")
            else:
                conn.execute(
                    "DELETE FROM licencas WHERE utilizador_id=? AND data=?",
                    (uid, dt.isoformat()),
                )
            conn.commit()

        if _refeicao_set(uid, dt, pa, lanche, alm, jan, sai, alterado_por=u["nii"]):
            flash("Refeições atualizadas!", "ok")
        else:
            flash("Erro ao guardar.", "error")
        return redirect(url_for(".aluno_home"))

    def occ_row(nome):
        val, cap = occ.get(nome, (0, -1))
        return f'<div style="margin-bottom:.65rem"><strong style="font-size:.84rem">{nome}</strong>{_bar_html(val, cap)}</div>'

    wknd_note = (
        '<div class="alert alert-info" style="margin-bottom:.8rem">Fim de semana — refeições opcionais.</div>'
        if is_weekend
        else ""
    )

    detido_note = (
        '<div class="alert alert-warn" style="margin-bottom:.8rem">'
        "🚫 Estás detido neste dia. Não podes sair da unidade."
        "</div>"
        if detido
        else ""
    )

    # Valores atuais
    pa_on = 1 if r.get("pequeno_almoco") else 0
    lan_on = 1 if r.get("lanche") else 0
    alm_val = r.get("almoco") or ""
    jan_val = r.get("jantar_tipo") or ""
    jan_blocked = licenca_atual == "antes_jantar"

    # Secção de licença — oculta se detido
    licenca_html = ""
    if not detido:
        regras = _regras_licenca(ano_aluno, ni_aluno)
        usadas_semana = _licencas_semana_usadas(uid, dt)
        max_uteis = regras["max_dias_uteis"]
        lic_disabled = "" if pode_lic else " disabled"
        lic_warn = (
            f'<div class="alert alert-warn" style="margin-top:.5rem">{esc(motivo_lic)}</div>'
            if not pode_lic and motivo_lic
            else ""
        )

        sel_antes = " checked" if licenca_atual == "antes_jantar" else ""
        sel_apos = " checked" if licenca_atual == "apos_jantar" else ""
        sel_nenhuma = " checked" if not licenca_atual else ""

        if dt.weekday() < 4:
            quota_info = f'<span class="text-muted small">Seg-Qui usadas: <strong>{usadas_semana}/{max_uteis}</strong></span>'
        else:
            quota_info = (
                '<span class="text-muted small">Fim de semana — sem limite.</span>'
            )

        licenca_html = f"""
      <div class="card" style="border-top:3px solid #2e86c1">
        <div class="card-title">🚪 Licença de saída</div>
        {quota_info}
        <div class="sw-group" style="margin-top:.6rem">
          <label class="sw-row{"  sw-on" if not licenca_atual else ""}" data-lic>
            <input type="radio" name="licenca" value=""{sel_nenhuma}{lic_disabled}>
            <span class="sw-label">Sem licença</span>
          </label>
          <label class="sw-row{"  sw-on" if licenca_atual == "antes_jantar" else ""}" data-lic>
            <input type="radio" name="licenca" value="antes_jantar"{sel_antes}{lic_disabled}>
            <span class="sw-icon">🌅</span>
            <span class="sw-label">Antes do jantar</span>
            <span class="sw-hint">(não janta)</span>
          </label>
          <label class="sw-row{"  sw-on" if licenca_atual == "apos_jantar" else ""}" data-lic>
            <input type="radio" name="licenca" value="apos_jantar"{sel_apos}{lic_disabled}>
            <span class="sw-icon">🌙</span>
            <span class="sw-label">Após o jantar</span>
            <span class="sw-hint">(janta na unidade)</span>
          </label>
        </div>
        {lic_warn}
      </div>"""

    # Ementa do dia
    menu = get_menu_do_dia(dt)
    ementa_html = ""
    if menu:

        def _mv(k):
            return esc(menu.get(k) or "—")

        ementa_html = f"""
      <div class="card" style="border-top:3px solid #f39c12">
        <div class="card-title">📋 Ementa — {dt.strftime("%d/%m/%Y")}</div>
        <div class="grid grid-4" style="font-size:.85rem">
          <div><strong>Peq. Almoço</strong><br><span class="text-muted">{_mv("pequeno_almoco")}</span></div>
          <div><strong>Lanche</strong><br><span class="text-muted">{_mv("lanche")}</span></div>
          <div><strong>Almoço</strong><br>N: {_mv("almoco_normal")}<br>V: {_mv("almoco_veg")}<br>D: {_mv("almoco_dieta")}</div>
          <div><strong>Jantar</strong><br>N: {_mv("jantar_normal")}<br>V: {_mv("jantar_veg")}<br>D: {_mv("jantar_dieta")}</div>
        </div>
      </div>"""

    content = f"""
    <style>
      .sw-group{{display:flex;flex-direction:column;gap:.45rem}}
      .sw-row{{display:flex;align-items:center;gap:.55rem;cursor:pointer;padding:.7rem .85rem;
        border:2px solid var(--border);border-radius:12px;transition:all .2s;
        user-select:none;-webkit-tap-highlight-color:transparent;background:#fff}}
      .sw-row:active{{transform:scale(.97)}}
      .sw-row.sw-on{{background:#eafaf1;border-color:#27ae60}}
      .sw-row input[type=hidden],.sw-row input[type=radio]{{display:none}}
      .sw-icon{{font-size:1.25rem;flex-shrink:0}}
      .sw-label{{flex:1;font-weight:600;font-size:.9rem}}
      .sw-hint{{font-size:.75rem;color:var(--muted);font-weight:400}}
      .sw-mark{{width:28px;height:28px;border-radius:8px;border:2px solid var(--border);
        display:flex;align-items:center;justify-content:center;font-size:.85rem;font-weight:800;
        color:transparent;transition:all .2s;background:#fff;flex-shrink:0}}
      .sw-row.sw-on .sw-mark{{background:#27ae60;border-color:#27ae60;color:#fff}}
      .sw-pills{{display:flex;gap:.35rem;flex-wrap:wrap}}
      .sw-pill{{padding:.5rem .75rem;border:2px solid var(--border);border-radius:10px;cursor:pointer;
        font-weight:600;font-size:.82rem;transition:all .2s;user-select:none;
        -webkit-tap-highlight-color:transparent;text-align:center;min-width:60px;background:#fff}}
      .sw-pill:active{{transform:scale(.95)}}
      .sw-pill.sw-sel{{background:#eafaf1;border-color:#27ae60;color:#1a5c38}}
      .sw-pill-group{{display:flex;flex-direction:column;gap:.35rem}}
      .sw-pill-row{{display:flex;align-items:center;gap:.55rem;padding:.5rem .85rem;
        border:2px solid var(--border);border-radius:12px;background:#fff}}
      .sw-pill-row.sw-on{{border-color:#27ae60;background:#f0faf4}}
      .sw-pill-label{{font-weight:600;font-size:.9rem;min-width:auto;white-space:nowrap}}
      .sw-pill-icon{{font-size:1.25rem;flex-shrink:0}}
      .sw-pill-opts{{display:flex;gap:.3rem;flex:1;justify-content:flex-end;flex-wrap:wrap}}
    </style>
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".aluno_home"))}
        <div class="page-title">🍽️ {NOMES_DIAS[dt.weekday()]}, {dt.strftime("%d/%m/%Y")}</div>
      </div>
      {wknd_note}{detido_note}{ementa_html}
      <div class="card">
        <div class="card-title">📊 Ocupação</div>
        {occ_row("Pequeno Almoço")}{occ_row("Lanche")}{occ_row("Almoço")}{occ_row("Jantar")}
      </div>
      <div class="card">
        <div class="card-title">✏️ Marcar refeições</div>
        <form method="post" id="mealForm">
          {csrf_input()}
          <input type="hidden" name="pa" id="h_pa" value="{pa_on}">
          <input type="hidden" name="lanche" id="h_lanche" value="{lan_on}">
          <input type="hidden" name="almoco" id="h_almoco" value="{esc(alm_val)}">
          <input type="hidden" name="jantar" id="h_jantar" value="{esc(jan_val)}">
          <div class="sw-group">
            <!-- PA toggle -->
            <div class="sw-row{"  sw-on" if pa_on else ""}" data-meal="pa" onclick="toggleMeal(this)">
              <span class="sw-icon">☕</span>
              <span class="sw-label">Pequeno Almoço</span>
              <span class="sw-mark">{"✓" if pa_on else ""}</span>
            </div>
            <!-- Lanche toggle -->
            <div class="sw-row{"  sw-on" if lan_on else ""}" data-meal="lanche" onclick="toggleMeal(this)">
              <span class="sw-icon">🥐</span>
              <span class="sw-label">Lanche</span>
              <span class="sw-mark">{"✓" if lan_on else ""}</span>
            </div>
            <!-- Almoço pill selector -->
            <div class="sw-pill-row{"  sw-on" if alm_val else ""}" id="alm_row">
              <span class="sw-pill-icon">🍽️</span>
              <span class="sw-pill-label">Almoço</span>
              <div class="sw-pill-opts">
                <div class="sw-pill{" sw-sel" if alm_val == "Normal" else ""}" onclick="setPill('almoco','Normal',this)">Normal</div>
                <div class="sw-pill{" sw-sel" if alm_val == "Vegetariano" else ""}" onclick="setPill('almoco','Vegetariano',this)">Veg</div>
                <div class="sw-pill{" sw-sel" if alm_val == "Dieta" else ""}" onclick="setPill('almoco','Dieta',this)">Dieta</div>
              </div>
            </div>
            <!-- Jantar pill selector -->
            <div class="sw-pill-row{"  sw-on" if jan_val and not jan_blocked else ""}" id="jan_row"{"  style=opacity:.4;pointer-events:none" if jan_blocked else ""}>
              <span class="sw-pill-icon">🌙</span>
              <span class="sw-pill-label">Jantar</span>
              <div class="sw-pill-opts">
                <div class="sw-pill{" sw-sel" if jan_val == "Normal" else ""}" onclick="setPill('jantar','Normal',this)">Normal</div>
                <div class="sw-pill{" sw-sel" if jan_val == "Vegetariano" else ""}" onclick="setPill('jantar','Vegetariano',this)">Veg</div>
                <div class="sw-pill{" sw-sel" if jan_val == "Dieta" else ""}" onclick="setPill('jantar','Dieta',this)">Dieta</div>
              </div>
            </div>
          </div>
          {licenca_html}
          <hr>
          <div class="gap-btn">
            <button class="btn btn-ok" style="flex:1;justify-content:center;padding:.7rem">💾 Guardar</button>
            <a class="btn btn-ghost" href="{url_for(".aluno_home")}">Cancelar</a>
          </div>
        </form>
      </div>
    </div>
    <script>
    function toggleMeal(el){{
      var key=el.getAttribute('data-meal');
      var h=document.getElementById('h_'+key);
      var on=h.value==='1'||h.value==='on';
      h.value=on?'0':'1';
      el.classList.toggle('sw-on',!on);
      el.querySelector('.sw-mark').textContent=on?'':'✓';
    }}
    function setPill(meal,val,pill){{
      var h=document.getElementById('h_'+meal);
      var row=pill.closest('.sw-pill-row');
      var pills=row.querySelectorAll('.sw-pill');
      if(h.value===val){{
        h.value='';
        pills.forEach(function(p){{p.classList.remove('sw-sel')}});
        row.classList.remove('sw-on');
      }} else {{
        h.value=val;
        pills.forEach(function(p){{p.classList.remove('sw-sel')}});
        pill.classList.add('sw-sel');
        row.classList.add('sw-on');
      }}
    }}
    // Licença radio: highlight active + block jantar se antes_jantar
    document.querySelectorAll('[data-lic] input[type=radio]').forEach(function(r){{
      r.addEventListener('change',function(){{
        document.querySelectorAll('[data-lic]').forEach(function(l){{l.classList.remove('sw-on')}});
        r.closest('[data-lic]').classList.add('sw-on');
        syncJantar();
      }});
    }});
    function syncJantar(){{
      var antes=document.querySelector('input[name=licenca][value=antes_jantar]');
      var jr=document.getElementById('jan_row');
      if(!jr)return;
      if(antes && antes.checked){{
        jr.style.opacity='.4';jr.style.pointerEvents='none';
        document.getElementById('h_jantar').value='';
        jr.querySelectorAll('.sw-pill').forEach(function(p){{p.classList.remove('sw-sel')}});
        jr.classList.remove('sw-on');
      }} else {{
        jr.style.opacity='1';jr.style.pointerEvents='auto';
      }}
    }}
    </script>"""
    return render_template("aluno/editar.html", content=Markup(content))


# ─── Aluno: Gerir ausências próprias ─────────────────────────────────────


@aluno_bp.route("/aluno/ausencias", methods=["GET", "POST"])
@login_required
def aluno_ausencias():
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    if not uid:
        flash("Conta de sistema — funcionalidade não disponível.", "error")
        return redirect(url_for(".aluno_home"))

    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "criar":
            de = request.form.get("de", "")
            ate = request.form.get("ate", "")
            motivo = _val_text(request.form.get("motivo", ""))[:500]
            ok, err = _registar_ausencia(uid, de, ate, motivo, u["nii"])
            flash(
                "Ausência registada com sucesso!" if ok else (err or "Erro."),
                "ok" if ok else "error",
            )
        elif acao == "editar":
            aid = _val_int_id(request.form.get("id", ""))
            de = request.form.get("de", "")
            ate = request.form.get("ate", "")
            motivo = _val_text(request.form.get("motivo", ""))[:500]
            if aid is None:
                flash("ID inválido.", "error")
            else:
                ok, err = _editar_ausencia(aid, uid, de, ate, motivo)
                flash(
                    "Ausência atualizada!" if ok else (err or "Erro."),
                    "ok" if ok else "error",
                )
        elif acao == "remover":
            aid = _val_int_id(request.form.get("id", ""))
            if aid is not None:
                with db() as conn:
                    conn.execute(
                        "DELETE FROM ausencias WHERE id=? AND utilizador_id=?",
                        (aid, uid),
                    )
                    conn.commit()
                flash("Ausência removida.", "ok")
        return redirect(url_for(".aluno_ausencias"))

    with db() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id,ausente_de,ausente_ate,motivo FROM ausencias WHERE utilizador_id=? ORDER BY ausente_de DESC",
                (uid,),
            ).fetchall()
        ]

    hoje = date.today().isoformat()
    edit_id = request.args.get("edit", "")
    edit_row = next((r for r in rows if str(r["id"]) == edit_id), None)

    if edit_row:
        form_title = "✏️ Editar ausência"
        form_action = "editar"
        form_de = edit_row["ausente_de"]
        form_ate = edit_row["ausente_ate"]
        form_motivo = edit_row["motivo"] or ""
        form_id_inp = f'<input type="hidden" name="id" value="{edit_row["id"]}">'
        cancel_btn = f'<a class="btn btn-ghost" href="{url_for(".aluno_ausencias")}">Cancelar</a>'
    else:
        form_title = "➕ Nova ausência"
        form_action = "criar"
        form_de = form_ate = form_motivo = ""
        form_id_inp = ""
        cancel_btn = ""

    rows_html = ""
    for r in rows:
        is_atual = r["ausente_de"] <= hoje <= r["ausente_ate"]
        is_futura = r["ausente_de"] > hoje
        estado = (
            '<span class="badge badge-warn">Atual</span>'
            if is_atual
            else (
                '<span class="badge badge-info">Futura</span>'
                if is_futura
                else '<span class="badge badge-muted">Passada</span>'
            )
        )
        pode = is_atual or is_futura
        edit_btn = (
            f'<a class="btn btn-ghost btn-sm" href="{url_for(".aluno_ausencias")}?edit={r["id"]}">✏️</a>'
            if pode
            else ""
        )
        rem_form = (
            (
                f'<form method="post" style="display:inline">{csrf_input()}'
                f'<input type="hidden" name="acao" value="remover">'
                f'<input type="hidden" name="id" value="{r["id"]}">'
                f'<button class="btn btn-danger btn-sm" onclick="return confirm(\'Remover ausência?\')">🗑</button></form>'
            )
            if pode
            else ""
        )
        rows_html += f"""<tr>
          <td>{r["ausente_de"]}</td><td>{r["ausente_ate"]}</td>
          <td>{esc(r["motivo"] or "—")}</td><td>{estado}</td>
          <td><div class="gap-btn">{edit_btn}{rem_form}</div></td>
        </tr>"""

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".aluno_home"))}
        <div class="page-title">🚫 As minhas ausências</div>
      </div>
      <div class="alert alert-info">
        📌 Com uma ausência ativa as tuas refeições não são contabilizadas e não podes editar refeições para esse período.
      </div>
      <div class="card" style="max-width:520px">
        <div class="card-title">{form_title}</div>
        <form method="post">
          {csrf_input()}
          <input type="hidden" name="acao" value="{form_action}">
          {form_id_inp}
          <div class="grid grid-2">
            <div class="form-group">
              <label>De</label>
              <input type="date" name="de" value="{form_de}" required min="{date.today().isoformat()}">
            </div>
            <div class="form-group">
              <label>Até</label>
              <input type="date" name="ate" value="{form_ate}" required min="{date.today().isoformat()}">
            </div>
          </div>
          <div class="form-group">
            <label>Motivo (opcional)</label>
            <input type="text" name="motivo" maxlength="500" value="{esc(form_motivo)}" placeholder="Ex: deslocação, exercício, visita...">
          </div>
          <div class="gap-btn">
            <button class="btn btn-ok">{"Atualizar" if edit_row else "Registar ausência"}</button>
            {cancel_btn}
          </div>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Histórico de ausências</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="5" class="text-muted" style="padding:1.5rem;text-align:center">Sem ausências registadas.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render_template("aluno/ausencias.html", content=Markup(content))


@aluno_bp.route("/aluno/historico")
@login_required
def aluno_historico():
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    hoje = date.today()
    rows = []
    if uid:
        with db() as conn:
            rows = conn.execute(
                """SELECT data,pequeno_almoco,lanche,almoco,jantar_tipo,jantar_sai_unidade
              FROM refeicoes WHERE utilizador_id=? AND data>=? ORDER BY data DESC""",
                (uid, (hoje - timedelta(days=30)).isoformat()),
            ).fetchall()

    def yn(v):
        return "✅" if v else "❌"

    rows_html = "".join(
        f"<tr><td>{r['data']}</td><td>{yn(r['pequeno_almoco'])}</td><td>{yn(r['lanche'])}</td>"
        f"<td>{r['almoco'] or '—'}</td><td>{r['jantar_tipo'] or '—'}</td>"
        f"<td>{'✅' if r['jantar_sai_unidade'] else '—'}</td></tr>"
        for r in rows
    )

    export_btns = ""
    if rows:
        export_btns = f"""
      <div class="gap-btn" style="margin-top:.8rem">
        <a class="btn btn-ghost btn-sm" href="{url_for(".exportar_historico_aluno", fmt="csv")}">📄 Exportar CSV</a>
        <a class="btn btn-ghost btn-sm" href="{url_for(".exportar_historico_aluno", fmt="xlsx")}">📊 Exportar Excel</a>
      </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for(".aluno_home"))}<div class="page-title">🕘 Histórico — 30 dias</div></div>
      <div class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Data</th><th>PA</th><th>Lanche</th><th>Almoço</th><th>Jantar</th><th>Sai</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem registos.</td></tr>'}</tbody>
          </table>
        </div>
        {export_btns}
      </div>
    </div>"""
    return render_template("aluno/historico.html", content=Markup(content))


@aluno_bp.route("/aluno/exportar-historico")
@login_required
def exportar_historico_aluno():
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    fmt = (request.args.get("fmt", "csv") or "csv").strip().lower()
    if fmt not in {"csv", "xlsx"}:
        abort(400)

    hoje = date.today()
    rows = []
    if uid:
        with db() as conn:
            rows = conn.execute(
                """SELECT data,pequeno_almoco,lanche,almoco,jantar_tipo,jantar_sai_unidade
              FROM refeicoes WHERE utilizador_id=? AND data>=? ORDER BY data DESC""",
                (uid, (hoje - timedelta(days=30)).isoformat()),
            ).fetchall()

    headers = ["Data", "PA", "Lanche", "Almoço", "Jantar", "Sai Unidade"]

    def make_row(r):
        return [
            r["data"],
            "Sim" if r["pequeno_almoco"] else "Não",
            "Sim" if r["lanche"] else "Não",
            r["almoco"] or "—",
            r["jantar_tipo"] or "—",
            "Sim" if r["jantar_sai_unidade"] else "Não",
        ]

    nome_ficheiro = f"historico_{u['nii']}_{hoje.isoformat()}"

    if fmt == "xlsx":
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Histórico {u['nome'][:20]}"

            header_fill = PatternFill("solid", fgColor="0A2D4E")
            header_font = Font(color="FFFFFF", bold=True, size=11)
            thin = Side(style="thin")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for col, h in enumerate(headers, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.fill = header_fill
                c.font = header_font
                c.alignment = Alignment(horizontal="center")
                c.border = border

            alt_fill = PatternFill("solid", fgColor="EBF5FB")
            for i, r in enumerate(rows, 2):
                row_data = make_row(r)
                fill = alt_fill if i % 2 == 0 else PatternFill()
                for col, val in enumerate(row_data, 1):
                    c = ws.cell(row=i, column=col, value=val)
                    c.fill = fill
                    c.border = border
                    c.alignment = Alignment(horizontal="center")

            for col in ws.columns:
                max_len = max(len(str(c.value or "")) for c in col) + 4
                ws.column_dimensions[col[0].column_letter].width = min(max_len, 22)

            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            return Response(
                buf.read(),
                headers={
                    "Content-Disposition": f"attachment; filename={nome_ficheiro}.xlsx",
                    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            )
        except ImportError:
            fmt = "csv"

    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=";")
    writer.writerow(headers)
    for r in rows:
        writer.writerow(make_row(r))
    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        csv_bytes,
        headers={
            "Content-Disposition": f"attachment; filename={nome_ficheiro}.csv",
            "Content-Type": "text/csv; charset=utf-8-sig",
        },
    )


@aluno_bp.route("/aluno/password", methods=["GET", "POST"])
@login_required
def aluno_password():
    u = current_user()
    if request.method == "POST":
        # Rate limiting: máx 10 tentativas por 5 minutos
        import time as _t

        pw_attempts = session.get("_pw_attempts", [])
        now = _t.time()
        pw_attempts = [t for t in pw_attempts if now - t < 300]
        if len(pw_attempts) >= 10:
            flash("Demasiadas tentativas. Aguarda uns minutos.", "error")
            return redirect(url_for(".aluno_password"))
        pw_attempts.append(now)
        session["_pw_attempts"] = pw_attempts

        old = request.form.get("old", "")
        new = request.form.get("new", "")
        conf = request.form.get("conf", "")
        if new != conf:
            flash("As passwords não coincidem.", "error")
        else:
            ok, err = _alterar_password(u["nii"], old, new)
            flash(
                "Password alterada!" if ok else (err or "Erro."),
                "ok" if ok else "error",
            )
            if ok:
                session.pop("must_change_password", None)
                session.pop("_pw_attempts", None)
                return redirect(url_for("auth.dashboard"))

    is_forced = session.get("must_change_password")
    title = "🔐 Definir nova password" if is_forced else "🔑 Alterar password"
    old_hint = "A tua password atual (NII se é o primeiro login)" if is_forced else ""
    forced_note = (
        '<div class="alert alert-warn" style="margin-bottom:1rem">⚠️ É o teu primeiro login. Define uma password pessoal para continuar.</div>'
        if is_forced
        else ""
    )
    cancel_btn = (
        ""
        if is_forced
        else f'<a class="btn btn-ghost" href="{url_for(".aluno_home")}">Cancelar</a>'
    )
    back_btn = "" if is_forced else _back_btn(url_for(".aluno_home"))

    content = f"""
    <div class="container">
      <div class="page-header">{back_btn}<div class="page-title">{title}</div></div>
      {forced_note}
      <div class="card" style="max-width:440px">
        <form method="post">
          {csrf_input()}
          <div class="form-group"><label>Password atual</label><input type="password" name="old" maxlength="256" required placeholder="{old_hint}"></div>
          <div class="form-group"><label>Nova password (mín. 8 caracteres, letras e números)</label><input type="password" name="new" maxlength="256" required minlength="8"></div>
          <div class="form-group"><label>Confirmar nova password</label><input type="password" name="conf" maxlength="256" required></div>
          <div class="gap-btn"><button class="btn btn-ok">💾 Guardar</button>{cancel_btn}</div>
        </form>
      </div>
    </div>"""
    return render_template("aluno/password.html", content=Markup(content))


# ─── Aluno: Perfil próprio ────────────────────────────────────────────────


@aluno_bp.route("/aluno/perfil", methods=["GET", "POST"])
@login_required
def aluno_perfil():
    u = current_user()
    uid = user_id_by_nii(u["nii"])
    if not uid:
        flash("Conta de sistema — funcionalidade não disponível.", "error")
        return redirect(url_for(".aluno_home"))

    with db() as conn:
        row = dict(
            conn.execute(
                "SELECT NII, NI, Nome_completo, ano, email, telemovel FROM utilizadores WHERE id=?",
                (uid,),
            ).fetchone()
        )

    if request.method == "POST":
        email_n = _val_email(request.form.get("email", ""))
        if email_n is False:
            flash("Email inválido.", "error")
            return redirect(url_for(".aluno_perfil"))
        telef_n = _val_phone(request.form.get("telemovel", ""))
        if telef_n is False:
            flash("Telemóvel inválido.", "error")
            return redirect(url_for(".aluno_perfil"))
        try:
            with db() as conn:
                conn.execute(
                    "UPDATE utilizadores SET email=?, telemovel=? WHERE id=?",
                    (email_n, telef_n, uid),
                )
                conn.commit()
            _audit(
                current_user().get("nii", "?"),
                "aluno_perfil_update",
                f"uid={uid}",
            )
            flash("Perfil atualizado com sucesso!", "ok")
            return redirect(url_for(".aluno_perfil"))
        except Exception as ex:
            flash(f"Erro: {ex}", "error")

    hoje = date.today()
    with db() as conn:
        total_ref = conn.execute(
            "SELECT COUNT(*) c FROM refeicoes WHERE utilizador_id=?", (uid,)
        ).fetchone()["c"]
        ausencias_ativas = conn.execute(
            """SELECT COUNT(*) c FROM ausencias WHERE utilizador_id=?
               AND ausente_de<=? AND ausente_ate>=?""",
            (uid, hoje.isoformat(), hoje.isoformat()),
        ).fetchone()["c"]

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for(".aluno_home"))}
        <div class="page-title">👤 O meu perfil</div>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">ℹ️ Informação pessoal</div>
          <div style="display:flex;flex-direction:column;gap:.6rem;font-size:.9rem">
            <div><span class="text-muted">Nome completo:</span><br><strong>{esc(row["Nome_completo"])}</strong></div>
            <div><span class="text-muted">NII:</span><br><strong>{esc(row["NII"])}</strong></div>
            <div><span class="text-muted">NI:</span><br><strong>{esc(row["NI"] or "—")}</strong></div>
            <div><span class="text-muted">Ano:</span><br><strong>{row["ano"]}º Ano</strong></div>
          </div>
          <hr style="margin:1rem 0">
          <div class="grid grid-2">
            <div class="stat-box"><div class="stat-num">{total_ref}</div><div class="stat-lbl">Refeições registadas</div></div>
            <div class="stat-box"><div class="stat-num" style="color:{"var(--warn)" if ausencias_ativas else "var(--ok)"}">{ausencias_ativas}</div><div class="stat-lbl">Ausências ativas</div></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">✉️ Contactos</div>
          <form method="post">
            {csrf_input()}
            <div class="form-group">
              <label>📧 Email</label>
              <input type="email" name="email" value="{esc(row.get("email") or "")}" placeholder="o-teu-email@exemplo.pt">
            </div>
            <div class="form-group">
              <label>📱 Telemóvel</label>
              <input type="tel" name="telemovel" value="{esc(row.get("telemovel") or "")}" placeholder="+351XXXXXXXXX">
            </div>
            <div class="gap-btn">
              <button class="btn btn-ok">💾 Guardar contactos</button>
              <a class="btn btn-ghost" href="{url_for(".aluno_home")}">Cancelar</a>
            </div>
          </form>
        </div>
      </div>
      <div class="card">
        <div class="card-title">⚡ Ações rápidas</div>
        <div class="gap-btn">
          <a class="btn btn-ghost" href="{url_for(".aluno_ausencias")}">🚫 Gerir ausências</a>
          <a class="btn btn-ghost" href="{url_for(".aluno_historico")}">🕘 Histórico de refeições</a>
          <a class="btn btn-ghost" href="{url_for(".aluno_password")}">🔑 Alterar password</a>
        </div>
      </div>
    </div>"""
    return render_template("aluno/perfil.html", content=Markup(content))
