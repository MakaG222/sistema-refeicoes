"""Funções auxiliares partilhadas (rendering, auditoria, datas, etc.)."""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from flask import Response, current_app, render_template, request, session
from markupsafe import Markup, escape

from core.constants import PRAZO_LIMITE_HORAS
from core.database import db
from core.meals import refeicao_editavel, refeicao_save

from utils.constants import ANOS_LABELS

# ═══════════════════════════════════════════════════════════════════════════
# RENDERING
# ═══════════════════════════════════════════════════════════════════════════


def render(content: str, status: int = 200) -> Response:
    """Renderiza conteúdo HTML dentro do layout base (templates/base.html)."""
    html = render_template("base.html", content=content)
    return Response(html, status=status, mimetype="text/html")


def esc(v: object) -> str:
    """Escapa HTML de forma segura."""
    return str(escape(str(v))) if v is not None else ""


def csrf_input() -> Markup:
    """Gera input hidden com token CSRF."""
    t = session.get("_csrf_token") or secrets.token_urlsafe(32)
    session["_csrf_token"] = t
    return Markup(f'<input type="hidden" name="csrf_token" value="{t}">')  # nosec B704


# ═══════════════════════════════════════════════════════════════════════════
# DATAS
# ═══════════════════════════════════════════════════════════════════════════


def _parse_date(s: str | None, default: date | None = None) -> date:
    """Parse de data YYYY-MM-DD com fallback."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default or date.today()


def _parse_date_strict(s: str | None) -> date | None:
    """Parse de data YYYY-MM-DD estrito (devolve None se inválido)."""
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS DE NEGÓCIO
# ═══════════════════════════════════════════════════════════════════════════


def _ano_label(ano: int | str | None) -> str:
    """Label legível para um ano escolar."""
    return ANOS_LABELS.get(int(ano) if ano else 0, f"{ano}\u00ba Ano")


def _get_anos_disponiveis() -> list[int]:
    """Anos com alunos na BD."""
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT CAST(ano AS INTEGER) AS ano FROM utilizadores"
            " WHERE ano IS NOT NULL AND ano != '' AND CAST(ano AS INTEGER) > 0"
            " ORDER BY CAST(ano AS INTEGER)"
        ).fetchall()
    return [r["ano"] for r in rows]


def _refeicao_set(
    uid: int,
    dt: date,
    pa: int,
    lanche: int,
    alm: str | None,
    jan: str | None,
    sai: int,
    alterado_por: str = "sistema",
    alm_estufa: int = 0,
    jan_estufa: int = 0,
) -> bool:
    """Guarda uma refeição completa."""
    r = {
        "pequeno_almoco": pa,
        "lanche": lanche,
        "almoco": alm or None,
        "jantar_tipo": jan or None,
        "jantar_sai_unidade": sai,
        "almoco_estufa": alm_estufa,
        "jantar_estufa": jan_estufa,
    }
    return refeicao_save(uid, dt, r, alterado_por=alterado_por)


def _back_btn(href: str, label: str = "Voltar") -> Markup:
    """Botão de voltar HTML."""
    return Markup(f'<a class="back-btn" href="{href}">\u2190 {label}</a>')  # nosec B704


def _bar_html(val: int, cap: int | None) -> Markup:
    """Barra de ocupação HTML."""
    if cap is None or cap <= 0:
        return Markup(f'<div class="occ-label">{val} (sem limite)</div>')  # nosec B704
    pct = min(100, int(round(100 * val / cap)))
    color = "#1e8449" if pct < 80 else ("#d68910" if pct < 95 else "#c0392b")
    return Markup(  # nosec B704 — val/cap/pct are integers, color is hardcoded
        f'<div class="occ-bar"><span style="width:{pct}%;background:{color}"></span></div>'
        f'<div class="occ-label">{val} / {cap} ({pct}%)</div>'
    )


def _prazo_label(d: date) -> Markup:
    """Label de prazo de edição para uma data."""
    ok, _ = refeicao_editavel(d)
    if ok:
        return Markup("")
    if PRAZO_LIMITE_HORAS is not None:
        prazo_dt = datetime(d.year, d.month, d.day) - timedelta(
            hours=PRAZO_LIMITE_HORAS
        )
        h = (prazo_dt - datetime.now()).total_seconds() / 3600
        if h <= 0:
            return Markup('<span class="prazo-lock">\U0001f512 Prazo expirado</span>')
        if h <= 24:
            return Markup(  # nosec B704
                f'<span class="prazo-warn">\u26a0\ufe0f Prazo em {int(h)}h</span>'
            )
    return Markup('<span class="prazo-lock">\U0001f512 Prazo expirado</span>')


# ═══════════════════════════════════════════════════════════════════════════
# AUDITORIA / IP
# ═══════════════════════════════════════════════════════════════════════════


def _audit(actor: str, action: str, detail: str = "") -> None:
    """Regista uma entrada de auditoria na tabela admin_audit_log + log estruturado."""
    from flask import g

    rid = getattr(g, "request_id", "-") if g else "-"
    current_app.logger.info(
        "action=%s actor=%s detail=%s rid=%s", action, actor, detail, rid
    )
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO admin_audit_log(actor,action,detail) VALUES(?,?,?)",
                (actor, action, detail),
            )
            conn.commit()
    except Exception as exc:
        current_app.logger.warning(f"_audit falhou [{action}]: {exc}")


def _client_ip() -> str:
    """IP do cliente (com ProxyFix activo atrás de proxy)."""
    try:
        if request.access_route:
            return str(request.access_route[0])[:64]
    except Exception:
        pass
    return str(request.remote_addr or "")[:64]
