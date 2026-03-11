"""Rotas do blueprint reporting."""

import io
import csv as _csv
from datetime import date, timedelta

from flask import (
    render_template,
    Response,
    abort,
    flash,
    request,
    url_for,
)
from markupsafe import Markup

import sistema_refeicoes_v8_4 as sr
from blueprints.reporting import report_bp
from utils.auth import (
    current_user,
    login_required,
    role_required,
)
from utils.constants import ABREV_DIAS, NOMES_DIAS
from utils.helpers import (
    _back_btn,
    _parse_date,
    _parse_date_strict,
    esc,
)


@report_bp.route("/exportar/mensal")
@role_required("cozinha", "oficialdia", "cmd", "admin")
def exportar_mensal():

    mes = request.args.get("mes", "")
    fmt = (request.args.get("fmt", "csv") or "csv").strip().lower()
    if fmt not in {"csv", "xlsx"}:
        abort(400)

    # Default: mês atual
    hoje = date.today()
    if mes:
        try:
            ano_m, mes_m = mes.split("-")
            d0 = date(int(ano_m), int(mes_m), 1)
        except (ValueError, IndexError):
            abort(400)
    else:
        d0 = date(hoje.year, hoje.month, 1)

    # Último dia do mês
    if d0.month == 12:
        d1 = date(d0.year + 1, 1, 1) - timedelta(days=1)
    else:
        d1 = date(d0.year, d0.month + 1, 1) - timedelta(days=1)

    MESES_PT = {
        1: "Janeiro",
        2: "Fevereiro",
        3: "Março",
        4: "Abril",
        5: "Maio",
        6: "Junho",
        7: "Julho",
        8: "Agosto",
        9: "Setembro",
        10: "Outubro",
        11: "Novembro",
        12: "Dezembro",
    }
    nome_mes = MESES_PT.get(d0.month, str(d0.month))

    dias_data = []
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
    _men_map, _men_empty = sr.get_totais_periodo(d0.isoformat(), d1.isoformat())
    _men_cal = sr.dias_operacionais_batch(d0, d1)
    di = d0
    while di <= d1:
        t = _men_map.get(di.isoformat(), _men_empty)
        tipo = _men_cal.get(
            di.isoformat(), "fim_semana" if di.weekday() >= 5 else "normal"
        )
        alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
        jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]
        dias_data.append((di, tipo, t, alm, jan))
        for k in totais:
            totais[k] += t[k]
        di += timedelta(days=1)

    HEADERS = [
        "Data",
        "Dia da Semana",
        "Tipo Dia",
        "PA",
        "Lanche",
        "Alm. Normal",
        "Alm. Veg.",
        "Alm. Dieta",
        "Total Almoços",
        "Jan. Normal",
        "Jan. Veg.",
        "Jan. Dieta",
        "Total Jantares",
        "Sai Unidade",
    ]

    def make_row(di, tipo, t, alm, jan):
        return [
            di.isoformat(),
            NOMES_DIAS[di.weekday()],
            tipo,
            t["pa"],
            t["lan"],
            t["alm_norm"],
            t["alm_veg"],
            t["alm_dieta"],
            alm,
            t["jan_norm"],
            t["jan_veg"],
            t["jan_dieta"],
            jan,
            t["jan_sai"],
        ]

    nome_ficheiro = f"relatorio_mensal_{d0.strftime('%Y-%m')}"

    if fmt == "xlsx":
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"{nome_mes} {d0.year}"

            header_fill = PatternFill("solid", fgColor="0A2D4E")
            header_font = Font(color="FFFFFF", bold=True)
            thin = Side(style="thin")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for col, h in enumerate(HEADERS, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.fill = header_fill
                c.font = header_font
                c.alignment = Alignment(horizontal="center")
                c.border = border

            tipo_fills = {
                "feriado": PatternFill("solid", fgColor="FFD6D6"),
                "exercicio": PatternFill("solid", fgColor="FFFACD"),
                "fim_semana": PatternFill("solid", fgColor="DDEEFF"),
            }

            for i, (di, tipo, t, alm, jan) in enumerate(dias_data, 2):
                row_data = make_row(di, tipo, t, alm, jan)
                fill = tipo_fills.get(tipo, PatternFill())
                for col, val in enumerate(row_data, 1):
                    c = ws.cell(row=i, column=col, value=val)
                    c.fill = fill
                    c.border = border
                    c.alignment = Alignment(horizontal="center")

            # Total row
            total_row_idx = len(dias_data) + 2
            total_alm = totais["alm_norm"] + totais["alm_veg"] + totais["alm_dieta"]
            total_jan = totais["jan_norm"] + totais["jan_veg"] + totais["jan_dieta"]
            total_data = [
                "TOTAL",
                "",
                "",
                totais["pa"],
                totais["lan"],
                totais["alm_norm"],
                totais["alm_veg"],
                totais["alm_dieta"],
                total_alm,
                totais["jan_norm"],
                totais["jan_veg"],
                totais["jan_dieta"],
                total_jan,
                totais["jan_sai"],
            ]
            total_fill = PatternFill("solid", fgColor="D5F5E3")
            total_font = Font(bold=True)
            for col, val in enumerate(total_data, 1):
                c = ws.cell(row=total_row_idx, column=col, value=val)
                c.fill = total_fill
                c.font = total_font
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
    writer.writerow(HEADERS)
    for di, tipo, t, alm, jan in dias_data:
        writer.writerow(make_row(di, tipo, t, alm, jan))
    total_alm = totais["alm_norm"] + totais["alm_veg"] + totais["alm_dieta"]
    total_jan = totais["jan_norm"] + totais["jan_veg"] + totais["jan_dieta"]
    writer.writerow(
        [
            "TOTAL",
            "",
            "",
            totais["pa"],
            totais["lan"],
            totais["alm_norm"],
            totais["alm_veg"],
            totais["alm_dieta"],
            total_alm,
            totais["jan_norm"],
            totais["jan_veg"],
            totais["jan_dieta"],
            total_jan,
            totais["jan_sai"],
        ]
    )
    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        csv_bytes,
        headers={
            "Content-Disposition": f"attachment; filename={nome_ficheiro}.csv",
            "Content-Type": "text/csv; charset=utf-8-sig",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# PAINEL OPERACIONAL
# ═══════════════════════════════════════════════════════════════════════════


# _alertas_painel — importado de utils/business.py


@report_bp.route("/calendario")
@login_required
def calendario_publico():
    import calendar as _cal

    u = current_user()
    hoje = date.today()
    mes_str = request.args.get("mes", hoje.strftime("%Y-%m"))
    try:
        ano_m, mes_m = int(mes_str[:4]), int(mes_str[5:7])
    except Exception:
        ano_m, mes_m = hoje.year, hoje.month

    ICONES = {
        "normal": "✅",
        "fim_semana": "🔵",
        "feriado": "🔴",
        "exercicio": "🟡",
        "outro": "⚪",
    }
    LABELS = {
        "normal": "Normal",
        "fim_semana": "Fim de semana",
        "feriado": "Feriado",
        "exercicio": "Exercício",
        "outro": "Outro",
    }
    CORES = {
        "normal": "#eafaf1",
        "fim_semana": "#ebf5fb",
        "feriado": "#fdecea",
        "exercicio": "#fef9e7",
        "outro": "#f8f9fa",
    }
    CORES_TEXT = {
        "normal": "#1e8449",
        "fim_semana": "#1a5276",
        "feriado": "#922b21",
        "exercicio": "#9a7d0a",
        "outro": "#6c757d",
    }

    ultimo_dia = _cal.monthrange(ano_m, mes_m)[1]
    d_inicio = date(ano_m, mes_m, 1)
    d_fim = date(ano_m, mes_m, ultimo_dia)
    with sr.db() as conn:
        entradas = {
            r["data"]: dict(r)
            for r in conn.execute(
                "SELECT data,tipo,nota FROM calendario_operacional WHERE data>=? AND data<=?",
                (d_inicio.isoformat(), d_fim.isoformat()),
            ).fetchall()
        }

    cal_grid = _cal.monthcalendar(ano_m, mes_m)
    DIAS_CAB = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

    grid_html = ""
    for semana in cal_grid:
        grid_html += "<tr>"
        for dia_n in semana:
            if dia_n == 0:
                grid_html += '<td style="background:#f9fafb;border:1px solid var(--border);border-radius:6px"></td>'
                continue
            d_obj = date(ano_m, mes_m, dia_n)
            entrada = entradas.get(d_obj.isoformat())
            tipo = (
                entrada["tipo"]
                if entrada
                else ("fim_semana" if d_obj.weekday() >= 5 else "normal")
            )
            nota = entrada["nota"] if entrada else ""
            is_hoje = d_obj == hoje
            bg = CORES.get(tipo, "#fff")
            tc = CORES_TEXT.get(tipo, "#1a2533")
            ic = ICONES.get(tipo, "✅")
            border_style = (
                "border:2.5px solid var(--primary)"
                if is_hoje
                else "border:1px solid var(--border)"
            )
            hoje_label = (
                '<div style="font-size:.58rem;color:var(--primary);font-weight:900;text-align:center">HOJE</div>'
                if is_hoje
                else ""
            )
            nota_html = (
                '<div style="font-size:.62rem;color:'
                + tc
                + ';margin-top:.12rem">'
                + esc(nota)
                + "</div>"
                if nota
                else ""
            )
            grid_html += (
                '<td style="background:'
                + bg
                + ";"
                + border_style
                + ';border-radius:7px;padding:.38rem;vertical-align:top">'
                + hoje_label
                + '<div style="font-weight:800;font-size:.82rem;color:'
                + tc
                + '">'
                + str(dia_n)
                + '</div><div style="font-size:.6rem">'
                + ic
                + "</div>"
                + nota_html
                + "</td>"
            )
        grid_html += "</tr>"

    if mes_m == 1:
        prev_mes = f"{ano_m - 1}-12"
    else:
        prev_mes = f"{ano_m}-{mes_m - 1:02d}"
    if mes_m == 12:
        next_mes = f"{ano_m + 1}-01"
    else:
        next_mes = f"{ano_m}-{mes_m + 1:02d}"

    MESES_PT = [
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    ]
    mes_titulo = f"{MESES_PT[mes_m - 1]} {ano_m}"
    perfil = u.get("perfil")
    back_url = (
        url_for("admin.admin_home")
        if perfil == "admin"
        else (
            url_for("aluno.aluno_home")
            if perfil == "aluno"
            else url_for("operations.painel_dia")
        )
    )

    legenda_html = "".join(
        '<span style="display:inline-flex;align-items:center;gap:.3rem;font-size:.78rem">'
        '<span style="width:.75rem;height:.75rem;background:'
        + CORES[t]
        + ";border:1px solid "
        + CORES_TEXT[t]
        + ';border-radius:3px;display:inline-block"></span>'
        + LABELS[t]
        + "</span>"
        for t in ["normal", "fim_semana", "feriado", "exercicio"]
    )

    header_cells = "".join(
        '<th style="text-align:center;padding:.3rem;font-size:.78rem;color:var(--primary);font-weight:700">'
        + d
        + "</th>"
        for d in DIAS_CAB
    )

    admin_link = (
        '<a class="btn btn-primary btn-sm" href="'
        + url_for("admin.admin_calendario")
        + '">⚙️ Gerir calendário</a>'
        if perfil in ("admin", "cmd")
        else '<div class="alert alert-info" style="margin-top:.6rem;font-size:.82rem">📌 O calendário é gerido pelo administrador.</div>'
    )

    c = (
        '<div class="container">'
        '<div class="page-header">'
        + _back_btn(back_url)
        + '<div class="page-title">📅 Calendário Operacional</div></div>'
        '<div class="card">'
        '<div class="flex-between" style="margin-bottom:.9rem">'
        '<a class="btn btn-ghost btn-sm" href="'
        + url_for(".calendario_publico", mes=prev_mes)
        + '">← Mês anterior</a>'
        '<strong style="font-size:1.05rem">' + mes_titulo + "</strong>"
        '<a class="btn btn-ghost btn-sm" href="'
        + url_for(".calendario_publico", mes=next_mes)
        + '">Mês seguinte →</a>'
        "</div>"
        '<div class="table-wrap"><table style="width:100%;border-collapse:separate;border-spacing:3px">'
        "<thead><tr>" + header_cells + "</tr></thead>"
        "<tbody>" + grid_html + "</tbody></table></div>"
        '<div style="margin-top:.8rem;display:flex;gap:.75rem;flex-wrap:wrap">'
        + legenda_html
        + "</div>"
        + admin_link
        + "</div></div>"
    )
    return render_template("reporting/calendario.html", content=Markup(c))


# ═══════════════════════════════════════════════════════════════════════════
# IMPRESSÃO — Mapa de refeições por ano
# ═══════════════════════════════════════════════════════════════════════════


@report_bp.route("/dashboard-semanal")
@role_required("cozinha", "oficialdia", "admin")
def dashboard_semanal():
    u = current_user()
    perfil = u.get("perfil")
    hoje = date.today()
    segunda = hoje - timedelta(days=hoje.weekday())
    d0_str = request.args.get("d0", segunda.isoformat())
    d0 = _parse_date(d0_str)
    d1 = d0 + timedelta(days=6)
    prev_w = (d0 - timedelta(days=7)).isoformat()
    next_w = (d0 + timedelta(days=7)).isoformat()

    # Batch: carregar totais e calendário para toda a semana numa query
    totais_map, _t_empty = sr.get_totais_periodo(d0.isoformat(), d1.isoformat())
    cal_map_wk = sr.dias_operacionais_batch(d0, d1)
    dias = []
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = totais_map.get(di.isoformat(), _t_empty)
        tipo = cal_map_wk.get(
            di.isoformat(), "fim_semana" if di.weekday() >= 5 else "normal"
        )
        dias.append({"data": di, "t": t, "tipo": tipo, "is_wknd": di.weekday() >= 5})

    max_alm = (
        max(
            (
                d["t"]["alm_norm"] + d["t"]["alm_veg"] + d["t"]["alm_dieta"]
                for d in dias
            ),
            default=1,
        )
        or 1
    )
    max_jan = (
        max(
            (
                d["t"]["jan_norm"] + d["t"]["jan_veg"] + d["t"]["jan_dieta"]
                for d in dias
            ),
            default=1,
        )
        or 1
    )
    max_pa = max((d["t"]["pa"] for d in dias), default=1) or 1

    def bar(val, maximo, cor, label):
        pct = int(round(100 * val / maximo)) if maximo else 0
        return (
            f'<div style="display:flex;align-items:flex-end;gap:.2rem;height:80px">'
            f'<div style="width:100%;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">'
            f'<span style="font-size:.7rem;font-weight:700;color:#1a2533;margin-bottom:.15rem">{val}</span>'
            f'<div style="width:100%;background:{cor};border-radius:5px 5px 0 0;height:{max(4, pct)}%"></div>'
            f"</div></div>"
        )

    # Chart almoços por dia
    alm_chart = ""
    jan_chart = ""
    pa_chart = ""
    table_rows = ""

    for d in dias:
        t = d["t"]
        di = d["data"]
        alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
        jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]
        tipo = d["tipo"]
        is_wk = d["is_wknd"]
        off = tipo in ("feriado", "exercicio")
        col_bg = "#f9fafb" if off else ("#fffdf5" if is_wk else "#fff")
        dow_col = "#c9a227" if is_wk else "#0a2d4e"

        # Stacked bar almoço
        alm_tot = alm or 0
        pn = int(round(80 * (t["alm_norm"] / max_alm))) if max_alm else 0
        pv = int(round(80 * (t["alm_veg"] / max_alm))) if max_alm else 0
        pd = int(round(80 * (t["alm_dieta"] / max_alm))) if max_alm else 0
        alm_chart += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;background:{col_bg};padding:.4rem .2rem;border-radius:6px">
          <div style="width:100%;height:80px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
            <span style="font-size:.68rem;font-weight:800;color:#1a2533;margin-bottom:.1rem">{alm_tot or "–"}</span>
            <div style="width:70%;display:flex;flex-direction:column;border-radius:4px 4px 0 0;overflow:hidden">
              {'<div style="height:' + str(pd) + 'px;background:#d68910"></div>' if pd else ""}
              {'<div style="height:' + str(pv) + 'px;background:#2471a3"></div>' if pv else ""}
              {'<div style="height:' + str(pn) + 'px;background:#1e8449"></div>' if pn else ""}
            </div>
          </div>
          <div style="font-size:.68rem;font-weight:800;color:{dow_col};margin-top:.2rem">{ABREV_DIAS[di.weekday()]}</div>
          <div style="font-size:.62rem;color:#6c757d">{di.strftime("%d/%m")}</div>
        </div>"""

        # Bar jantar
        pj = int(round(80 * (jan / max_jan))) if max_jan else 0
        jan_chart += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;background:{col_bg};padding:.4rem .2rem;border-radius:6px">
          <div style="width:100%;height:80px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
            <span style="font-size:.68rem;font-weight:800;color:#1a2533;margin-bottom:.1rem">{jan or "–"}</span>
            <div style="width:70%;height:{max(0, pj)}px;background:#1a5276;border-radius:4px 4px 0 0"></div>
          </div>
          <div style="font-size:.68rem;font-weight:800;color:{dow_col};margin-top:.2rem">{ABREV_DIAS[di.weekday()]}</div>
        </div>"""

        # Bar PA
        pp = int(round(80 * (t["pa"] / max_pa))) if max_pa else 0
        pa_chart += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;background:{col_bg};padding:.4rem .2rem;border-radius:6px">
          <div style="width:100%;height:80px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
            <span style="font-size:.68rem;font-weight:800;color:#1a2533;margin-bottom:.1rem">{t["pa"] or "–"}</span>
            <div style="width:70%;height:{max(0, pp)}px;background:#c9a227;border-radius:4px 4px 0 0"></div>
          </div>
          <div style="font-size:.68rem;font-weight:800;color:{dow_col};margin-top:.2rem">{ABREV_DIAS[di.weekday()]}</div>
        </div>"""

        sai_td = (
            "" if perfil == "cozinha" else f'<td class="center">{t["jan_sai"]}</td>'
        )
        table_rows += f"""<tr style="background:{col_bg}">
          <td><strong style="color:{dow_col}">{ABREV_DIAS[di.weekday()]}</strong> {di.strftime("%d/%m")}</td>
          <td class="center">{t["pa"]}</td><td class="center">{t["lan"]}</td>
          <td class="center">{t["alm_norm"]}</td><td class="center">{t["alm_veg"]}</td><td class="center">{t["alm_dieta"]}</td>
          <td class="center">{t["jan_norm"]}</td><td class="center">{t["jan_veg"]}</td><td class="center">{t["jan_dieta"]}</td>
          {sai_td}
        </tr>"""

    sai_th = "" if perfil == "cozinha" else '<th class="center">Sai</th>'
    _keys = [
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
    totais_semana = {k: sum(d["t"][k] for d in dias) for k in _keys}

    # Totais da semana anterior para comparação (1 query batch)
    prev_d0 = d0 - timedelta(days=7)
    prev_d1 = d0 - timedelta(days=1)
    prev_map, _ = sr.get_totais_periodo(prev_d0.isoformat(), prev_d1.isoformat())
    totais_prev = {k: 0 for k in _keys}
    for t_p in prev_map.values():
        for k in _keys:
            totais_prev[k] += t_p[k]

    def _wk_delta(curr, prev):
        d = curr - prev
        if d > 0:
            return f'<span style="color:#1e8449">↑{d}</span>'
        if d < 0:
            return f'<span style="color:#c0392b">↓{abs(d)}</span>'
        return '<span style="color:#6c757d">=</span>'

    _sai_total = (
        ""
        if perfil == "cozinha"
        else f'<td class="center"><strong>{totais_semana["jan_sai"]}</strong></td>'
    )
    _sai_prev = (
        ""
        if perfil == "cozinha"
        else f'<td class="center">{totais_prev["jan_sai"]}</td>'
    )
    _sai_var = (
        ""
        if perfil == "cozinha"
        else f'<td class="center">{_wk_delta(totais_semana["jan_sai"], totais_prev["jan_sai"])}</td>'
    )
    comparison_rows = f"""
        <tr style="background:#f0f4f8;font-weight:700;border-top:2px solid #0a2d4e">
          <td>Total semana</td>
          <td class="center">{totais_semana["pa"]}</td><td class="center">{totais_semana["lan"]}</td>
          <td class="center">{totais_semana["alm_norm"]}</td><td class="center">{totais_semana["alm_veg"]}</td><td class="center">{totais_semana["alm_dieta"]}</td>
          <td class="center">{totais_semana["jan_norm"]}</td><td class="center">{totais_semana["jan_veg"]}</td><td class="center">{totais_semana["jan_dieta"]}</td>
          {_sai_total}
        </tr>
        <tr style="background:#fef9e7;font-size:.82rem">
          <td>Semana anterior</td>
          <td class="center">{totais_prev["pa"]}</td><td class="center">{totais_prev["lan"]}</td>
          <td class="center">{totais_prev["alm_norm"]}</td><td class="center">{totais_prev["alm_veg"]}</td><td class="center">{totais_prev["alm_dieta"]}</td>
          <td class="center">{totais_prev["jan_norm"]}</td><td class="center">{totais_prev["jan_veg"]}</td><td class="center">{totais_prev["jan_dieta"]}</td>
          {_sai_prev}
        </tr>
        <tr style="background:#fff;font-size:.82rem">
          <td>Variação</td>
          <td class="center">{_wk_delta(totais_semana["pa"], totais_prev["pa"])}</td>
          <td class="center">{_wk_delta(totais_semana["lan"], totais_prev["lan"])}</td>
          <td class="center">{_wk_delta(totais_semana["alm_norm"], totais_prev["alm_norm"])}</td>
          <td class="center">{_wk_delta(totais_semana["alm_veg"], totais_prev["alm_veg"])}</td>
          <td class="center">{_wk_delta(totais_semana["alm_dieta"], totais_prev["alm_dieta"])}</td>
          <td class="center">{_wk_delta(totais_semana["jan_norm"], totais_prev["jan_norm"])}</td>
          <td class="center">{_wk_delta(totais_semana["jan_veg"], totais_prev["jan_veg"])}</td>
          <td class="center">{_wk_delta(totais_semana["jan_dieta"], totais_prev["jan_dieta"])}</td>
          {_sai_var}
        </tr>"""

    back_url = (
        url_for("admin.admin_home")
        if perfil == "admin"
        else url_for("operations.painel_dia")
    )

    legenda_alm = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:.3rem;font-size:.72rem">'
        f'<span style="width:.65rem;height:.65rem;background:{c};border-radius:2px;display:inline-block"></span>{lb}</span>'
        for lb, c in [("Normal", "#1e8449"), ("Veg.", "#2471a3"), ("Dieta", "#d68910")]
    )

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url)}
        <div class="page-title">📊 Dashboard Semanal</div>
      </div>
      <div class="card" style="padding:.85rem 1.1rem;margin-bottom:.75rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for(".dashboard_semanal", d0=prev_w)}">← Semana anterior</a>
            <strong>{d0.strftime("%d/%m/%Y")} — {d1.strftime("%d/%m/%Y")}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for(".dashboard_semanal", d0=next_w)}">Semana seguinte →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d0" value="{d0_str}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom:.85rem">
        <div class="stat-box"><div class="stat-num">{totais_semana["pa"]}</div><div class="stat-lbl">PA semana</div></div>
        <div class="stat-box"><div class="stat-num">{totais_semana["alm_norm"] + totais_semana["alm_veg"] + totais_semana["alm_dieta"]}</div><div class="stat-lbl">Almoços semana</div></div>
        <div class="stat-box"><div class="stat-num">{totais_semana["jan_norm"] + totais_semana["jan_veg"] + totais_semana["jan_dieta"]}</div><div class="stat-lbl">Jantares semana</div></div>
        <div class="stat-box"><div class="stat-num">{totais_semana["lan"]}</div><div class="stat-lbl">Lanches semana</div></div>
      </div>

      <div class="card">
        <div class="card-title">🍽️ Almoços por dia
          <span style="margin-left:.6rem;display:inline-flex;gap:.6rem">{legenda_alm}</span>
        </div>
        <div style="display:flex;gap:.3rem;align-items:flex-end;padding:.3rem 0">
          {alm_chart}
        </div>
        <div style="border-top:2px solid #e9ecef;margin-top:.3rem"></div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">🌙 Jantares por dia</div>
          <div style="display:flex;gap:.3rem;align-items:flex-end;padding:.3rem 0">{jan_chart}</div>
        </div>
        <div class="card">
          <div class="card-title">☕ Pequenos Almoços por dia</div>
          <div style="display:flex;gap:.3rem;align-items:flex-end;padding:.3rem 0">{pa_chart}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">📋 Tabela detalhada</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Dia</th><th>PA</th><th>Lan</th><th>Alm N</th><th>Alm V</th><th>Alm D</th><th>Jan N</th><th>Jan V</th><th>Jan D</th>{sai_th}</tr></thead>
            <tbody>{table_rows}{comparison_rows}</tbody>
          </table>
        </div>
        <div class="gap-btn" style="margin-top:.8rem">
          <a class="btn btn-primary" href="{url_for(".exportar_relatorio", d0=d0_str, fmt="csv")}">⬇ CSV</a>
          <a class="btn btn-primary" href="{url_for(".exportar_relatorio", d0=d0_str, fmt="xlsx")}">⬇ Excel</a>
        </div>
      </div>
      <div class="card">
        <div class="card-title">📅 Relatório Mensal</div>
        <div class="gap-btn">
          <a class="btn btn-gold" href="{url_for(".exportar_mensal", mes=d0.strftime("%Y-%m"), fmt="xlsx")}">📊 Excel mês {d0.strftime("%m/%Y")}</a>
          <a class="btn btn-ghost" href="{url_for(".exportar_mensal", mes=d0.strftime("%Y-%m"), fmt="csv")}">📄 CSV mês {d0.strftime("%m/%Y")}</a>
        </div>
      </div>
    </div>"""
    return render_template("reporting/dashboard_semanal.html", content=Markup(content))


# ═══════════════════════════════════════════════════════════════════════════
# EXPORTAÇÕES
# ═══════════════════════════════════════════════════════════════════════════


@report_bp.route("/exportar/dia")
@role_required("cozinha", "oficialdia", "cmd", "admin")
def exportar_dia():

    d_str = request.args.get("d", date.today().isoformat())
    fmt = (request.args.get("fmt", "csv") or "csv").strip().lower()
    if fmt not in {"csv", "xlsx"}:
        abort(400)
    dt = _parse_date_strict(d_str)
    if dt is None:
        abort(400)
    t = sr.get_totais_dia(dt.isoformat())

    # Tentar xlsx via openpyxl; cair para CSV se não disponível
    if fmt == "xlsx":
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Totais {dt.strftime('%d-%m-%Y')}"

            # Cabeçalho
            header_fill = PatternFill("solid", fgColor="0A2D4E")
            header_font = Font(color="FFFFFF", bold=True, size=11)
            border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin"),
            )

            headers = [
                "Data",
                "Dia",
                "PA",
                "Lanche",
                "Alm. Normal",
                "Alm. Veg.",
                "Alm. Dieta",
                "Jan. Normal",
                "Jan. Veg.",
                "Jan. Dieta",
                "Jan. Sai Unidade",
                "Total Almoços",
                "Total Jantares",
            ]
            for col, h in enumerate(headers, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.fill = header_fill
                c.font = header_font
                c.alignment = Alignment(horizontal="center")
                c.border = border

            total_alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
            total_jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]
            data_row = [
                dt.isoformat(),
                NOMES_DIAS[dt.weekday()],
                t["pa"],
                t["lan"],
                t["alm_norm"],
                t["alm_veg"],
                t["alm_dieta"],
                t["jan_norm"],
                t["jan_veg"],
                t["jan_dieta"],
                t["jan_sai"],
                total_alm,
                total_jan,
            ]
            alt_fill = PatternFill("solid", fgColor="EBF5FB")
            for col, val in enumerate(data_row, 1):
                c = ws.cell(row=2, column=col, value=val)
                c.fill = alt_fill
                c.border = border
                c.alignment = Alignment(horizontal="center")

            # Auto-largura
            for col in ws.columns:
                max_len = max(len(str(c.value or "")) for c in col) + 4
                ws.column_dimensions[col[0].column_letter].width = min(max_len, 22)

            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            return Response(
                buf.read(),
                headers={
                    "Content-Disposition": f"attachment; filename=totais_{dt.isoformat()}.xlsx",
                    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            )
        except ImportError:
            flash("openpyxl não instalado — a exportar CSV.", "warn")
            fmt = "csv"
        except Exception as ex:
            flash(f"Erro ao gerar Excel: {ex} — a exportar CSV.", "warn")
            fmt = "csv"

    # CSV
    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "Data",
            "Dia",
            "PA",
            "Lanche",
            "Alm. Normal",
            "Alm. Veg.",
            "Alm. Dieta",
            "Jan. Normal",
            "Jan. Veg.",
            "Jan. Dieta",
            "Jan. Sai Unidade",
            "Total Almoços",
            "Total Jantares",
        ]
    )
    total_alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
    total_jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]
    writer.writerow(
        [
            dt.isoformat(),
            NOMES_DIAS[dt.weekday()],
            t["pa"],
            t["lan"],
            t["alm_norm"],
            t["alm_veg"],
            t["alm_dieta"],
            t["jan_norm"],
            t["jan_veg"],
            t["jan_dieta"],
            t["jan_sai"],
            total_alm,
            total_jan,
        ]
    )
    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        csv_bytes,
        headers={
            "Content-Disposition": f"attachment; filename=totais_{dt.isoformat()}.csv",
            "Content-Type": "text/csv; charset=utf-8-sig",
        },
    )


@report_bp.route("/exportar/relatorio")
@role_required("cozinha", "oficialdia", "admin")
def exportar_relatorio():

    d0_str = request.args.get("d0", date.today().isoformat())
    fmt = (request.args.get("fmt", "csv") or "csv").strip().lower()
    if fmt not in {"csv", "xlsx"}:
        abort(400)
    d0 = _parse_date_strict(d0_str)
    if d0 is None:
        abort(400)
    d1 = d0 + timedelta(days=6)

    dias_data = []
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
    _exp_map, _exp_empty = sr.get_totais_periodo(d0.isoformat(), d1.isoformat())
    _exp_cal = sr.dias_operacionais_batch(d0, d1)
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = _exp_map.get(di.isoformat(), _exp_empty)
        tipo = _exp_cal.get(
            di.isoformat(), "fim_semana" if di.weekday() >= 5 else "normal"
        )
        alm = t["alm_norm"] + t["alm_veg"] + t["alm_dieta"]
        jan = t["jan_norm"] + t["jan_veg"] + t["jan_dieta"]
        dias_data.append((di, tipo, t, alm, jan))
        for k in totais:
            totais[k] += t[k]

    HEADERS = [
        "Data",
        "Dia da Semana",
        "Tipo Dia",
        "PA",
        "Lanche",
        "Alm. Normal",
        "Alm. Veg.",
        "Alm. Dieta",
        "Total Almoços",
        "Jan. Normal",
        "Jan. Veg.",
        "Jan. Dieta",
        "Total Jantares",
        "Sai Unidade",
    ]

    def make_row(di, tipo, t, alm, jan):
        return [
            di.isoformat(),
            NOMES_DIAS[di.weekday()],
            tipo,
            t["pa"],
            t["lan"],
            t["alm_norm"],
            t["alm_veg"],
            t["alm_dieta"],
            alm,
            t["jan_norm"],
            t["jan_veg"],
            t["jan_dieta"],
            jan,
            t["jan_sai"],
        ]

    nome = f"relatorio_{d0_str}_a_{d1.isoformat()}"

    if fmt == "xlsx":
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Relatório {d0.strftime('%d-%m')} a {d1.strftime('%d-%m-%Y')}"

            header_fill = PatternFill("solid", fgColor="0A2D4E")
            header_font = Font(color="FFFFFF", bold=True)
            thin = Side(style="thin")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for col, h in enumerate(HEADERS, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.fill = header_fill
                c.font = header_font
                c.alignment = Alignment(horizontal="center")
                c.border = border

            TIPO_CORES = {
                "feriado": "FFD6D6",
                "exercicio": "FFFACD",
                "fim_semana": "DDEEFF",
                "normal": "FFFFFF",
                "outro": "F0F0F0",
            }
            for ri, (di, tipo, t, alm, jan) in enumerate(dias_data, 2):
                row_fill = PatternFill("solid", fgColor=TIPO_CORES.get(tipo, "FFFFFF"))
                for col, val in enumerate(make_row(di, tipo, t, alm, jan), 1):
                    c = ws.cell(row=ri, column=col, value=val)
                    c.fill = row_fill
                    c.border = border
                    c.alignment = Alignment(horizontal="center" if col > 2 else "left")

            # Linha de totais
            total_alm = totais["alm_norm"] + totais["alm_veg"] + totais["alm_dieta"]
            total_jan = totais["jan_norm"] + totais["jan_veg"] + totais["jan_dieta"]
            total_row = [
                "TOTAL",
                "—",
                "—",
                totais["pa"],
                totais["lan"],
                totais["alm_norm"],
                totais["alm_veg"],
                totais["alm_dieta"],
                total_alm,
                totais["jan_norm"],
                totais["jan_veg"],
                totais["jan_dieta"],
                total_jan,
                totais["jan_sai"],
            ]
            total_fill = PatternFill("solid", fgColor="D5E8F0")
            total_font = Font(bold=True)
            for col, val in enumerate(total_row, 1):
                c = ws.cell(row=9, column=col, value=val)
                c.fill = total_fill
                c.font = total_font
                c.border = border
                c.alignment = Alignment(horizontal="center" if col > 2 else "left")

            for col in ws.columns:
                max_len = max(len(str(c.value or "")) for c in col) + 3
                ws.column_dimensions[col[0].column_letter].width = min(max_len, 20)

            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            return Response(
                buf.read(),
                headers={
                    "Content-Disposition": f"attachment; filename={nome}.xlsx",
                    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            )
        except ImportError:
            flash("openpyxl não instalado — a exportar CSV.", "warn")
        except Exception as ex:
            flash(f"Erro ao gerar Excel: {ex} — a exportar CSV.", "warn")

    # CSV (com BOM para Excel abrir correctamente)
    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=";")
    writer.writerow(HEADERS)
    for di, tipo, t, alm, jan in dias_data:
        writer.writerow(make_row(di, tipo, t, alm, jan))
    total_alm = totais["alm_norm"] + totais["alm_veg"] + totais["alm_dieta"]
    total_jan = totais["jan_norm"] + totais["jan_veg"] + totais["jan_dieta"]
    writer.writerow(
        [
            "TOTAL",
            "—",
            "—",
            totais["pa"],
            totais["lan"],
            totais["alm_norm"],
            totais["alm_veg"],
            totais["alm_dieta"],
            total_alm,
            totais["jan_norm"],
            totais["jan_veg"],
            totais["jan_dieta"],
            total_jan,
            totais["jan_sai"],
        ]
    )
    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        csv_bytes,
        headers={
            "Content-Disposition": f"attachment; filename={nome}.csv",
            "Content-Type": "text/csv; charset=utf-8-sig",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONTROLO DE PRESENÇAS — Módulo rápido via NI (Oficial de Dia)
# ═══════════════════════════════════════════════════════════════════════════
