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
    dias_operacionais_batch,
    refeicao_exists,
    refeicao_save,
    refeicoes_batch,
)
from core.notifications import notify

log = logging.getLogger(__name__)

_TIPOS_SEM_REFEICAO = frozenset({"feriado", "exercicio"})


def _dia_tem_refeicoes_from_map(d: date, tipos: dict[str, str]) -> bool:
    """Versão batch-aware de `dia_tem_refeicoes`: usa um dict pré-carregado."""
    tipo = tipos.get(d.isoformat())
    if tipo is None:
        tipo = "fim_semana" if d.weekday() >= 5 else "normal"
    return tipo not in _TIPOS_SEM_REFEICAO


_CARRY_FIELDS = (
    "pequeno_almoco",
    "lanche",
    "almoco",
    "jantar_tipo",
    "jantar_sai_unidade",
    "almoco_estufa",
    "jantar_estufa",
)


def _default_refeicao_para_dia(d: date) -> dict[str, Any]:
    """Default por dia: tudo marcado em dias com refeições; tudo a zero caso contrário."""
    if not dia_tem_refeicoes(d):
        return {
            "pequeno_almoco": 0,
            "lanche": 0,
            "almoco": None,
            "jantar_tipo": None,
            "jantar_sai_unidade": 0,
            "almoco_estufa": 0,
            "jantar_estufa": 0,
        }
    return {
        "pequeno_almoco": 1,
        "lanche": 1,
        "almoco": "Normal",
        "jantar_tipo": "Normal",
        "jantar_sai_unidade": 0,
        "almoco_estufa": 0,
        "jantar_estufa": 0,
    }


def _default_refeicao_para_dia_precomputado(
    d: date, tipos_dia: dict[str, str]
) -> dict[str, Any]:
    """Mesmo contrato que `_default_refeicao_para_dia` mas usa tipos já carregados."""
    if not _dia_tem_refeicoes_from_map(d, tipos_dia):
        return {
            "pequeno_almoco": 0,
            "lanche": 0,
            "almoco": None,
            "jantar_tipo": None,
            "jantar_sai_unidade": 0,
            "almoco_estufa": 0,
            "jantar_estufa": 0,
        }
    return {
        "pequeno_almoco": 1,
        "lanche": 1,
        "almoco": "Normal",
        "jantar_tipo": "Normal",
        "jantar_sai_unidade": 0,
        "almoco_estufa": 0,
        "jantar_estufa": 0,
    }


def _carry_forward(prev: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Aplica valores da semana anterior sobre o default.

    `jantar_sai_unidade` é sempre reposto a 0: licenças/saídas são pedidos pontuais
    e não devem propagar automaticamente para a semana seguinte.
    """
    out = dict(base)
    for k in _CARRY_FIELDS:
        v = prev.get(k)
        if v is not None:
            out[k] = v
    out["jantar_sai_unidade"] = 0
    return out


def autopreencher_refeicoes_semanais(dias_a_gerar: int = 14) -> None:
    """Preenche automaticamente refeições para os próximos `dias_a_gerar` dias.

    Pré-carrega a janela equivalente da semana anterior por utilizador para evitar
    N+1 queries; falhas de utilizadores individuais não interrompem o lote.
    """
    today = date.today()
    prev_de = today - timedelta(days=7)
    prev_ate = prev_de + timedelta(days=max(0, dias_a_gerar - 1))
    window_ate = today + timedelta(days=max(0, dias_a_gerar - 1))

    try:
        with db() as conn:
            users = [dict(r) for r in conn.execute("SELECT id FROM utilizadores")]
        tipos_dia = dias_operacionais_batch(today, window_ate)
    except Exception as e:
        log.exception("autopreencher_refeicoes_semanais: falha a obter contexto")
        notify(
            "Autopreenchimento falhou",
            f"Não foi possível carregar contexto (utilizadores/dias): {e}",
            severity="error",
        )
        return

    dias_com_refeicoes = [
        today + timedelta(days=i)
        for i in range(dias_a_gerar)
        if _dia_tem_refeicoes_from_map(today + timedelta(days=i), tipos_dia)
    ]

    falhas: list[int] = []
    for u in users:
        uid = u["id"]
        try:
            prev_meals, _ = refeicoes_batch(uid, prev_de, prev_ate)
            for d in dias_com_refeicoes:
                if utilizador_ausente(uid, d):
                    continue
                if refeicao_exists(uid, d):
                    continue
                base = _default_refeicao_para_dia_precomputado(d, tipos_dia)
                prev_row = prev_meals.get((d - timedelta(days=7)).isoformat(), {})
                final = _carry_forward(prev_row, base)
                refeicao_save(uid, d, final, alterado_por="sistema")
        except Exception:
            falhas.append(uid)
            log.exception("autopreencher: falha para uid=%s", uid)

    if falhas:
        log.error(
            "autopreencher_refeicoes_semanais: %d utilizador(es) falharam: %s",
            len(falhas),
            falhas,
        )
        notify(
            "Autopreenchimento: falhas parciais",
            f"{len(falhas)} utilizador(es) falharam: {falhas[:20]}"
            + (" …" if len(falhas) > 20 else ""),
            severity="warning",
        )


# Backward-compat alias (assinatura antiga, mas redireciona para o helper novo)
def _carry_forward_from_last_week(
    uid: int, d: date, base: dict[str, Any]
) -> dict[str, Any]:
    from core.meals import refeicao_get

    prev = refeicao_get(uid, d - timedelta(days=7))
    return _carry_forward(prev, base)


__all__ = [
    "_default_refeicao_para_dia",
    "_carry_forward_from_last_week",
    "autopreencher_refeicoes_semanais",
    "_is_friday",
    "_is_weekday_mon_to_fri",
]
