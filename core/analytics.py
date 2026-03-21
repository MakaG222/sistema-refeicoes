"""Funções de analytics e séries temporais."""

from datetime import date, timedelta
from typing import Optional

from core.database import db


def period_days(base: date, days: int):
    return [(base - timedelta(days=i)) for i in range(days - 1, -1, -1)]


def series_consumo_por_dia(d0: date, d1: date, ano: Optional[int] = None):
    days = (d1 - d0).days + 1
    idx = {(d0 + timedelta(days=i)).isoformat(): i for i in range(days)}
    pa = [0] * days
    ln = [0] * days
    alm = [0] * days
    jan = [0] * days
    exc = [0] * days

    with db() as conn:
        if ano is None:
            q = """
                SELECT r.data d, SUM(r.pequeno_almoco) pa, SUM(r.lanche) lan,
                       SUM(CASE WHEN r.almoco IS NOT NULL THEN 1 ELSE 0 END) alm,
                       SUM(CASE WHEN r.jantar_tipo IS NOT NULL THEN 1 ELSE 0 END) jan
                FROM refeicoes r WHERE r.data BETWEEN ? AND ?
                GROUP BY r.data"""
            args = (d0.isoformat(), d1.isoformat())
        else:
            q = """
                SELECT r.data d, SUM(r.pequeno_almoco) pa, SUM(r.lanche) lan,
                       SUM(CASE WHEN r.almoco IS NOT NULL THEN 1 ELSE 0 END) alm,
                       SUM(CASE WHEN r.jantar_tipo IS NOT NULL THEN 1 ELSE 0 END) jan
                FROM refeicoes r JOIN utilizadores u ON u.id=r.utilizador_id
                WHERE r.data BETWEEN ? AND ? AND u.ano=?
                GROUP BY r.data"""
            args = (d0.isoformat(), d1.isoformat(), ano)

        for r in conn.execute(q, args):
            i = idx.get(r["d"])
            if i is None:
                continue
            pa[i] = r["pa"] or 0
            ln[i] = r["lan"] or 0
            alm[i] = r["alm"] or 0
            jan[i] = r["jan"] or 0

        for r in conn.execute(
            """
            SELECT data, COALESCE(SUM(ocupacao - capacidade),0) AS over
            FROM capacidade_excessos WHERE data BETWEEN ? AND ?
            GROUP BY data
        """,
            (d0.isoformat(), d1.isoformat()),
        ):
            i = idx.get(r["data"])
            if i is not None:
                exc[i] = r["over"] or 0

    days_list = [(d0 + timedelta(days=i)) for i in range(days)]
    return days_list, pa, ln, alm, jan, exc
