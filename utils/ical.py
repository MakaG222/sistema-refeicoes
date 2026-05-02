"""utils/ical.py — Geração de ficheiros iCalendar (RFC 5545) para refeições.

Hand-rolled (sem dependência externa). RFC 5545 é simples para o nosso caso:
- VCALENDAR + N x VEVENT
- Linhas terminadas em CRLF (\r\n)
- Tempos sem timezone (floating local time) — o calendar app interpreta na
  timezone local do utilizador, o que para uma escola em Portugal corresponde
  ao tempo real do evento.

Não fazemos line-folding (>75 octetos) porque os summaries são curtos
("Almoço Normal", "Lanche", etc.) — todas as linhas geradas ficam <60 chars.

Uso:
    from utils.ical import build_meals_ics
    body = build_meals_ics(uid=42, nome="João Silva", refeicoes=...)
    return Response(body, mimetype="text/calendar; charset=utf-8")
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import config as cfg


# RFC 5545 §3.7.3 — PRODID identifica o produtor do calendar.
# Formato recomendado: "-//<Org>//<Product>//<Lang>"
_PRODID = "-//Escola Naval//Sistema de Refeicoes//PT"

# Refeições configuradas em config.REFEICAO_HORARIOS — ordenadas por hora
# de início para os events aparecerem ordenados no calendar.
_TIPOS_ORDENADOS = ("pequeno_almoco", "almoco", "lanche", "jantar")

# Labels human-friendly para o SUMMARY (o que o user vê no calendar).
_LABELS = {
    "pequeno_almoco": "Pequeno-Almoço",
    "lanche": "Lanche",
    "almoco": "Almoço",
    "jantar": "Jantar",
}


def _ics_escape(text: str) -> str:
    """Escapa caracteres especiais conforme RFC 5545 §3.3.11.

    Os 4 caracteres a escapar em TEXT values: backslash, vírgula, ponto e
    vírgula, newline. Sem esta escape, calendar parsers podem partir o
    ficheiro silenciosamente (campos ficam truncados ou misturados).
    """
    return (
        text.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fmt_local(d: date, hhmm: str) -> str:
    """Devolve `YYYYMMDDTHHMMSS` (formato DTSTART local floating)."""
    h, m = hhmm.split(":")
    dt = datetime(d.year, d.month, d.day, int(h), int(m), 0)
    return dt.strftime("%Y%m%dT%H%M%S")


def _meal_summary(tipo: str, refeicao_row: dict[str, Any]) -> str | None:
    """Devolve summary humano para a refeição, ou None se não está marcada.

    Lógica por tipo:
      - pequeno_almoco / lanche: presença booleana → "Pequeno-Almoço" / "Lanche"
      - almoco: tem variante (Normal/Vegetariano/Dieta) + flag estufa
      - jantar: variante (jantar_tipo) + flag estufa + sai unidade
    """
    if tipo == "pequeno_almoco":
        return _LABELS[tipo] if refeicao_row.get("pequeno_almoco") else None
    if tipo == "lanche":
        return _LABELS[tipo] if refeicao_row.get("lanche") else None
    if tipo == "almoco":
        variante = refeicao_row.get("almoco")
        if not variante:
            return None
        suffix = " ♨" if refeicao_row.get("almoco_estufa") else ""
        return f"{_LABELS[tipo]} {variante}{suffix}"
    if tipo == "jantar":
        variante = refeicao_row.get("jantar_tipo")
        if not variante:
            return None
        if refeicao_row.get("jantar_sai_unidade"):
            return f"{_LABELS[tipo]} (sai da unidade)"
        suffix = " ♨" if refeicao_row.get("jantar_estufa") else ""
        return f"{_LABELS[tipo]} {variante}{suffix}"
    return None


def _build_event(
    *,
    uid_aluno: int,
    d: date,
    tipo: str,
    summary: str,
    horario: tuple[str, str],
    dtstamp: str,
) -> list[str]:
    """Constrói um VEVENT como lista de linhas (sem terminator)."""
    h_inicio, h_fim = horario
    # UID estável: re-import do mesmo evento ATUALIZA em vez de duplicar.
    # Formato: <aluno>-<data>-<tipo>@refeicoes.escolanaval
    uid = f"{uid_aluno}-{d.isoformat()}-{tipo}@refeicoes.escolanaval"
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{_fmt_local(d, h_inicio)}",
        f"DTEND:{_fmt_local(d, h_fim)}",
        f"SUMMARY:{_ics_escape(summary)}",
        "LOCATION:Refeitório - Escola Naval",
        "STATUS:CONFIRMED",
        "TRANSP:TRANSPARENT",  # não bloqueia a hora no calendar (é informativo)
        "END:VEVENT",
    ]


def build_meals_ics(
    *,
    uid_aluno: int,
    nome: str,
    refeicoes_por_data: dict[str, dict[str, Any]],
    horarios: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Gera o body iCalendar (string) para as refeições marcadas de um aluno.

    Args:
        uid_aluno: ID do aluno (usado nos UIDs dos events).
        nome: nome do aluno (vai para X-WR-CALNAME, visível no calendar app).
        refeicoes_por_data: `{iso_date: row_dict}` — formato devolvido por
            `core.meals.refeicoes_batch`. Datas sem entrada são ignoradas.
        horarios: opcional, override de `cfg.REFEICAO_HORARIOS` (útil em testes).

    Returns:
        String com o conteúdo do .ics (CRLF terminado, RFC 5545).
    """
    h = horarios or cfg.REFEICAO_HORARIOS
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(f'Refeições - {nome}')}",
        "X-WR-TIMEZONE:Europe/Lisbon",
    ]

    # Iterar datas em ordem (estável p/ caches e diffs)
    for data_iso in sorted(refeicoes_por_data):
        try:
            d = date.fromisoformat(data_iso)
        except ValueError:
            continue  # data inválida (não devia acontecer mas defensivo)
        row = refeicoes_por_data[data_iso] or {}
        for tipo in _TIPOS_ORDENADOS:
            if tipo not in h:
                continue
            summary = _meal_summary(tipo, row)
            if summary is None:
                continue
            lines.extend(
                _build_event(
                    uid_aluno=uid_aluno,
                    d=d,
                    tipo=tipo,
                    summary=summary,
                    horario=h[tipo],
                    dtstamp=dtstamp,
                )
            )

    lines.append("END:VCALENDAR")
    # RFC 5545 §3.1: linhas DEVEM ser CRLF terminadas.
    return "\r\n".join(lines) + "\r\n"
