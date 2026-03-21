"""Auto-preenchimento semanal de refeições."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from core.absences import utilizador_ausente
from core.database import db
from core.meals import (
    _is_friday,
    _is_weekday_mon_to_fri,
    dia_tem_refeicoes,
    refeicao_exists,
    refeicao_get,
    refeicao_save,
)


# Re-export para backward-compat
def _default_refeicao_para_dia(d: date) -> dict[str, Any]:
    """Marks all meals Mon-Fri, except dinner on Fridays."""
    if not _is_weekday_mon_to_fri(d):
        return {
            "pequeno_almoco": 0,
            "lanche": 0,
            "almoco": None,
            "jantar_tipo": None,
            "jantar_sai_unidade": 0,
        }
    base = {
        "pequeno_almoco": 1,
        "lanche": 1,
        "almoco": "Normal",
        "jantar_tipo": "Normal",
        "jantar_sai_unidade": 0,
    }
    if _is_friday(d):
        base["jantar_tipo"] = None
        base["jantar_sai_unidade"] = 0
    return base


def _carry_forward_from_last_week(
    uid: int, d: date, base: dict[str, Any]
) -> dict[str, Any]:
    prev = refeicao_get(uid, d - timedelta(days=7))
    out = dict(base)
    for k in [
        "pequeno_almoco",
        "lanche",
        "almoco",
        "jantar_tipo",
        "jantar_sai_unidade",
    ]:
        if k in prev and prev[k] is not None:
            out[k] = prev[k]
    if _is_friday(d):
        out["jantar_tipo"] = None
        out["jantar_sai_unidade"] = 0
    return out


def autopreencher_refeicoes_semanais(dias_a_gerar: int = 14) -> None:
    """Preenche automaticamente refeições para os próximos dias."""
    try:
        today = date.today()
        with db() as conn:
            users = [dict(r) for r in conn.execute("SELECT id FROM utilizadores")]
        for u in users:
            uid = u["id"]
            for i in range(dias_a_gerar):
                d = today + timedelta(days=i)
                if not dia_tem_refeicoes(d):
                    continue
                if utilizador_ausente(uid, d):
                    continue
                if refeicao_exists(uid, d):
                    continue
                base = _default_refeicao_para_dia(d)
                final = _carry_forward_from_last_week(uid, d, base)
                refeicao_save(uid, d, final, alterado_por="sistema")
    except Exception as e:
        logging.warning(f"autopreencher_refeicoes_semanais falhou: {e}")
