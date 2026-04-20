"""Previsão de consumo — média móvel por dia-da-semana.

Sem dependências externas (nada de numpy/prophet). A heurística:

    Para cada (refeição, dia-da-semana), média dos últimos N mesmos dias-da-semana
    históricos (default N=4 → 4 segundas-feiras para prever a próxima segunda).

É propositadamente simples — serve como baseline auditável; se o erro médio
for muito alto, substituir por um modelo mais sofisticado é trivial (a interface
pública mantém-se).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import NamedTuple

from core.analytics import series_consumo_por_dia

log = logging.getLogger(__name__)


class ForecastPoint(NamedTuple):
    dia: date
    weekday: int  # 0=Monday … 6=Sunday
    pa: int
    lanche: int
    almoco: int
    jantar: int
    amostras: int  # quantos dias históricos contribuíram


_MEAL_KEYS = ("pa", "lanche", "almoco", "jantar")


def _rolling_mean_by_weekday(
    series: list[tuple[date, int, int, int, int]],
    weekday: int,
    samples: int,
) -> tuple[dict[str, int], int]:
    """Média dos últimos `samples` dias com o mesmo weekday, da mais recente
    para a mais antiga. Retorna `(totais, n_amostras_usadas)`.
    """
    vals: dict[str, list[int]] = {k: [] for k in _MEAL_KEYS}
    n = 0
    for d, pa, lan, alm, jan in reversed(series):
        if d.weekday() != weekday:
            continue
        vals["pa"].append(pa)
        vals["lanche"].append(lan)
        vals["almoco"].append(alm)
        vals["jantar"].append(jan)
        n += 1
        if n >= samples:
            break
    if n == 0:
        return ({k: 0 for k in _MEAL_KEYS}, 0)
    return ({k: round(sum(v) / len(v)) for k, v in vals.items()}, n)


def forecast_proximos_dias(
    dias: int = 7,
    ano: int | None = None,
    semanas_historico: int = 4,
    today: date | None = None,
) -> list[ForecastPoint]:
    """Devolve previsão para os próximos `dias` dias a partir de amanhã.

    Args:
        dias: nº de dias a prever (default 7).
        ano: filtrar por ano de curso (default: todos).
        semanas_historico: janela de histórico por weekday (default 4).
        today: override para testes.

    Returns:
        Lista de `ForecastPoint` por ordem cronológica. `amostras` indica
        confiança — 0 significa que não havia histórico e o valor é 0.
    """
    today = today or date.today()
    # Precisamos de pelo menos N semanas para trás para cada weekday
    lookback_days = max(7 * semanas_historico + 6, 14)
    d0 = today - timedelta(days=lookback_days)
    d1 = today - timedelta(days=1)

    try:
        days, pa, ln, alm, jan, _ = series_consumo_por_dia(d0, d1, ano)
    except Exception:
        log.exception("forecast: falha a obter série histórica")
        return []

    series = list(zip(days, pa, ln, alm, jan, strict=True))
    out: list[ForecastPoint] = []
    for i in range(1, dias + 1):
        d = today + timedelta(days=i)
        totais, n_amostras = _rolling_mean_by_weekday(
            series, d.weekday(), semanas_historico
        )
        out.append(
            ForecastPoint(
                dia=d,
                weekday=d.weekday(),
                pa=totais["pa"],
                lanche=totais["lanche"],
                almoco=totais["almoco"],
                jantar=totais["jantar"],
                amostras=n_amostras,
            )
        )
    return out


__all__ = ["ForecastPoint", "forecast_proximos_dias"]
