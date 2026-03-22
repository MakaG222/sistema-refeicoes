"""CRUD e queries de refeições."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from core.constants import PRAZO_LIMITE_HORAS
from core.database import db


def refeicao_editavel(d: date) -> tuple[bool, str]:
    """Devolve (True, '') se a data d ainda pode ser editada, ou (False, motivo)."""
    agora_dt = datetime.now()
    hoje = agora_dt.date()

    if d < hoje:
        return (
            False,
            f"Não é possível alterar refeições de datas passadas ({d.strftime('%d/%m/%Y')}).",
        )

    if PRAZO_LIMITE_HORAS is not None:
        prazo_dt = datetime(d.year, d.month, d.day, 0, 0, 0) - timedelta(
            hours=PRAZO_LIMITE_HORAS
        )
        if agora_dt >= prazo_dt:
            prazo_str = prazo_dt.strftime("%d/%m/%Y às %H:%M")
            return False, (
                f"Prazo excedido para alterar a refeição de {d.strftime('%d/%m/%Y')}.\n"
                f"   O prazo terminou em {prazo_str} ({PRAZO_LIMITE_HORAS}h antes da refeição).\n"
                f"   Para efetuar alterações, fala com o Oficial de Dia."
            )

    return True, ""


def get_totais_dia(di: str, ano: int | None = None) -> dict[str, int]:
    """Devolve totais de todas as refeições para uma data ISO (di)."""
    _active = (
        "JOIN utilizadores u ON u.id=r.utilizador_id"
        " AND u.is_active=1"
        " AND NOT EXISTS ("
        "SELECT 1 FROM ausencias a"
        " WHERE a.utilizador_id=u.id AND a.ausente_de<=r.data AND a.ausente_ate>=r.data)"
    )
    _ano_cond = " AND u.ano=?" if ano is not None else ""

    with db() as conn:
        params_base = (di, ano) if ano is not None else (di,)

        pa = (
            conn.execute(
                f"SELECT COUNT(*) c FROM refeicoes r {_active}"  # nosec B608
                f" WHERE r.data=? {_ano_cond} AND r.pequeno_almoco=1",
                params_base,
            ).fetchone()["c"]
            or 0
        )
        ln = (
            conn.execute(
                f"SELECT COUNT(*) c FROM refeicoes r {_active}"  # nosec B608
                f" WHERE r.data=? {_ano_cond} AND r.lanche=1",
                params_base,
            ).fetchone()["c"]
            or 0
        )
        # _active and _ano_cond are hardcoded constants — safe to interpolate
        _sql_alm = (
            f"SELECT"  # nosec B608
            f" SUM(CASE WHEN r.almoco='Normal' THEN 1 ELSE 0 END) norm,"
            f" SUM(CASE WHEN r.almoco='Vegetariano' THEN 1 ELSE 0 END) veg,"
            f" SUM(CASE WHEN r.almoco='Dieta' THEN 1 ELSE 0 END) dieta,"
            f" SUM(COALESCE(r.almoco_estufa,0)) estufa"
            f" FROM refeicoes r {_active}"
            f" WHERE r.data=? {_ano_cond}"
        )
        alm = conn.execute(_sql_alm, params_base).fetchone()
        _sql_jan = (
            f"SELECT"  # nosec B608
            f" SUM(CASE WHEN r.jantar_tipo='Normal' THEN 1 ELSE 0 END) norm,"
            f" SUM(CASE WHEN r.jantar_tipo='Vegetariano' THEN 1 ELSE 0 END) veg,"
            f" SUM(CASE WHEN r.jantar_tipo='Dieta' THEN 1 ELSE 0 END) dieta,"
            f" SUM(COALESCE(r.jantar_sai_unidade,0)) sai,"
            f" SUM(COALESCE(r.jantar_estufa,0)) estufa"
            f" FROM refeicoes r {_active}"
            f" WHERE r.data=? {_ano_cond}"
        )
        jan = conn.execute(_sql_jan, params_base).fetchone()

    return {
        "pa": pa,
        "lan": ln,
        "alm_norm": alm["norm"] or 0,
        "alm_veg": alm["veg"] or 0,
        "alm_dieta": alm["dieta"] or 0,
        "alm_estufa": alm["estufa"] or 0,
        "jan_norm": jan["norm"] or 0,
        "jan_veg": jan["veg"] or 0,
        "jan_dieta": jan["dieta"] or 0,
        "jan_sai": jan["sai"] or 0,
        "jan_estufa": jan["estufa"] or 0,
    }


def get_totais_periodo(
    d_de: str, d_ate: str, ano: int | None = None
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Totais agrupados por dia para um intervalo. Devolve ({iso_date: dict}, empty_dict)."""
    _active = (
        "JOIN utilizadores u ON u.id=r.utilizador_id"
        " AND u.is_active=1"
        " AND NOT EXISTS ("
        "SELECT 1 FROM ausencias a"
        " WHERE a.utilizador_id=u.id AND a.ausente_de<=r.data AND a.ausente_ate>=r.data)"
    )
    _ano_cond = " AND u.ano=?" if ano is not None else ""
    params = (d_de, d_ate, ano) if ano is not None else (d_de, d_ate)

    with db() as conn:
        _sql_periodo = (
            f"SELECT r.data,"  # nosec B608
            f" SUM(CASE WHEN r.pequeno_almoco=1 THEN 1 ELSE 0 END) pa,"
            f" SUM(CASE WHEN r.lanche=1 THEN 1 ELSE 0 END) lan,"
            f" SUM(CASE WHEN r.almoco='Normal' THEN 1 ELSE 0 END) alm_norm,"
            f" SUM(CASE WHEN r.almoco='Vegetariano' THEN 1 ELSE 0 END) alm_veg,"
            f" SUM(CASE WHEN r.almoco='Dieta' THEN 1 ELSE 0 END) alm_dieta,"
            f" SUM(COALESCE(r.almoco_estufa,0)) alm_estufa,"
            f" SUM(CASE WHEN r.jantar_tipo='Normal' THEN 1 ELSE 0 END) jan_norm,"
            f" SUM(CASE WHEN r.jantar_tipo='Vegetariano' THEN 1 ELSE 0 END) jan_veg,"
            f" SUM(CASE WHEN r.jantar_tipo='Dieta' THEN 1 ELSE 0 END) jan_dieta,"
            f" SUM(COALESCE(r.jantar_sai_unidade,0)) jan_sai,"
            f" SUM(COALESCE(r.jantar_estufa,0)) jan_estufa"
            f" FROM refeicoes r {_active}"
            f" WHERE r.data>=? AND r.data<=? {_ano_cond}"
            f" GROUP BY r.data"
        )
        rows = conn.execute(_sql_periodo, params).fetchall()

    _empty = {
        "pa": 0,
        "lan": 0,
        "alm_norm": 0,
        "alm_veg": 0,
        "alm_dieta": 0,
        "alm_estufa": 0,
        "jan_norm": 0,
        "jan_veg": 0,
        "jan_dieta": 0,
        "jan_sai": 0,
        "jan_estufa": 0,
    }
    result = {}
    for r in rows:
        result[r["data"]] = {
            "pa": r["pa"] or 0,
            "lan": r["lan"] or 0,
            "alm_norm": r["alm_norm"] or 0,
            "alm_veg": r["alm_veg"] or 0,
            "alm_dieta": r["alm_dieta"] or 0,
            "alm_estufa": r["alm_estufa"] or 0,
            "jan_norm": r["jan_norm"] or 0,
            "jan_veg": r["jan_veg"] or 0,
            "jan_dieta": r["jan_dieta"] or 0,
            "jan_sai": r["jan_sai"] or 0,
            "jan_estufa": r["jan_estufa"] or 0,
        }
    return result, _empty


def get_ocupacao_capacidade(d: date) -> dict[str, tuple[int, int]]:
    """Devolve ocupação e capacidade por refeição (capacidade -1 => sem limite)."""
    t = get_totais_dia(d.isoformat())
    with db() as conn:
        caps = {
            r["refeicao"]: r["max_total"]
            for r in conn.execute(
                "SELECT refeicao,max_total FROM capacidade_refeicao WHERE data=?",
                (d.isoformat(),),
            )
        }
    return {
        "Pequeno Almoço": (t["pa"], caps.get("Pequeno Almoço", -1)),
        "Lanche": (t["lan"], caps.get("Lanche", -1)),
        "Almoço": (
            t["alm_norm"] + t["alm_veg"] + t["alm_dieta"],
            caps.get("Almoço", -1),
        ),
        "Jantar": (
            t["jan_norm"] + t["jan_veg"] + t["jan_dieta"],
            caps.get("Jantar", -1),
        ),
    }


def get_menu_do_dia(d: date) -> dict[str, Any]:
    with db() as conn:
        r = conn.execute(
            "SELECT * FROM menus_diarios WHERE data=?", (d.isoformat(),)
        ).fetchone()
        return dict(r) if r else {}


def dias_operacionais_batch(d_de: date, d_ate: date) -> dict[str, str]:
    """Carrega tipos de dia do calendário operacional. Devolve {iso_date: tipo}."""
    with db() as conn:
        rows = conn.execute(
            "SELECT data, tipo FROM calendario_operacional WHERE data>=? AND data<=?",
            (d_de.isoformat(), d_ate.isoformat()),
        ).fetchall()
    return {r["data"]: r["tipo"] for r in rows}


def refeicao_get(uid: int, d: date) -> dict[str, Any]:
    with db() as conn:
        r = conn.execute(
            "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
            (uid, d.isoformat()),
        ).fetchone()
        if r:
            return dict(r)
    return {
        "pequeno_almoco": 0,
        "lanche": 0,
        "almoco": None,
        "jantar_tipo": None,
        "jantar_sai_unidade": 0,
        "almoco_estufa": 0,
        "jantar_estufa": 0,
    }


def refeicoes_batch(
    uid: int, d_de: date, d_ate: date
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Carrega refeições de um aluno para um intervalo. Devolve ({iso_date: dict}, defaults)."""
    defaults = {
        "pequeno_almoco": 0,
        "lanche": 0,
        "almoco": None,
        "jantar_tipo": None,
        "jantar_sai_unidade": 0,
        "almoco_estufa": 0,
        "jantar_estufa": 0,
    }
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM refeicoes WHERE utilizador_id=? AND data>=? AND data<=?",
            (uid, d_de.isoformat(), d_ate.isoformat()),
        ).fetchall()
    result = {}
    for r in rows:
        result[r["data"]] = dict(r)
    return result, defaults


def refeicao_save(
    uid: int, d: date, r: dict[str, Any], alterado_por: str = "sistema"
) -> bool:
    """Guarda refeição e regista no log de auditoria os campos que mudaram."""
    try:
        with db() as conn:
            anterior = conn.execute(
                "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()
            anterior = dict(anterior) if anterior else {}

            dd = d.isoformat()
            det = conn.execute(
                """SELECT 1 FROM detencoes
                WHERE utilizador_id=? AND detido_de<=? AND detido_ate>=?
                LIMIT 1""",
                (uid, dd, dd),
            ).fetchone()
            if det:
                r["jantar_sai_unidade"] = 0

            conn.execute(
                """
                INSERT INTO refeicoes
                  (utilizador_id, data, pequeno_almoco, lanche, almoco, jantar_tipo, jantar_sai_unidade, almoco_estufa, jantar_estufa)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(utilizador_id, data) DO UPDATE SET
                    pequeno_almoco=excluded.pequeno_almoco,
                    lanche=excluded.lanche,
                    almoco=excluded.almoco,
                    jantar_tipo=excluded.jantar_tipo,
                    jantar_sai_unidade=excluded.jantar_sai_unidade,
                    almoco_estufa=excluded.almoco_estufa,
                    jantar_estufa=excluded.jantar_estufa
            """,
                (
                    uid,
                    d.isoformat(),
                    r.get("pequeno_almoco", 0),
                    r.get("lanche", 0),
                    r.get("almoco"),
                    r.get("jantar_tipo"),
                    r.get("jantar_sai_unidade", 0),
                    r.get("almoco_estufa", 0),
                    r.get("jantar_estufa", 0),
                ),
            )

            campos = [
                "pequeno_almoco",
                "lanche",
                "almoco",
                "jantar_tipo",
                "jantar_sai_unidade",
                "almoco_estufa",
                "jantar_estufa",
            ]
            for campo in campos:
                val_antes = (
                    str(anterior.get(campo))
                    if anterior.get(campo) is not None
                    else None
                )
                val_depois = str(r.get(campo)) if r.get(campo) is not None else None
                if val_antes != val_depois:
                    conn.execute(
                        """
                        INSERT INTO refeicoes_log
                          (utilizador_id, data_refeicao, campo, valor_antes, valor_depois, alterado_por)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            uid,
                            d.isoformat(),
                            campo,
                            val_antes,
                            val_depois,
                            alterado_por,
                        ),
                    )

            conn.commit()
            return True
    except sqlite3.IntegrityError as e:
        print(f"Rejeitado pela BD: {e}")
        return False
    except sqlite3.Error as e:
        print(f"Erro ao salvar: {e}")
        return False


def refeicao_exists(uid: int, d: date) -> bool:
    try:
        with db() as conn:
            r = conn.execute(
                "SELECT 1 FROM refeicoes WHERE utilizador_id=? AND data=? LIMIT 1",
                (uid, d.isoformat()),
            ).fetchone()
            return r is not None
    except Exception:
        return False


def _is_weekday_mon_to_fri(d: date) -> bool:
    return 0 <= d.weekday() <= 4


def _is_friday(d: date) -> bool:
    return d.weekday() == 4


def dia_operacional(d: date) -> str:
    """Devolve o tipo do dia segundo o calendário operacional."""
    with db() as conn:
        r = conn.execute(
            "SELECT tipo FROM calendario_operacional WHERE data=?", (d.isoformat(),)
        ).fetchone()
    if r:
        return r["tipo"]
    return "fim_semana" if d.weekday() >= 5 else "normal"


def dia_tem_refeicoes(d: date) -> bool:
    """Dias normais têm refeições; feriados e exercícios não."""
    return dia_operacional(d) not in ("feriado", "exercicio", "fim_semana")


# Helpers para CSV export
_HEADERS_TOTAIS = [
    "data",
    "PA_total",
    "Lanche_total",
    "Almoco_Normal",
    "Almoco_Vegetariano",
    "Almoco_Dieta",
    "Almoco_Estufa",
    "Jantar_Normal",
    "Jantar_Vegetariano",
    "Jantar_Dieta",
    "Jantar_Saem_Unidade",
    "Jantar_Estufa",
]
_HEADERS_DISTRIBUICAO = [
    "ano",
    "NI",
    "Nome_completo",
    "data",
    "pequeno_almoco",
    "lanche",
    "almoco",
    "almoco_estufa",
    "jantar_tipo",
    "jantar_sai_unidade",
    "jantar_estufa",
]


def _totais_para_csv_row(
    di: str, t: dict[str, int], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    row = {
        "data": di,
        "PA_total": t["pa"],
        "Lanche_total": t["lan"],
        "Almoco_Normal": t["alm_norm"],
        "Almoco_Vegetariano": t["alm_veg"],
        "Almoco_Dieta": t["alm_dieta"],
        "Almoco_Estufa": t.get("alm_estufa", 0),
        "Jantar_Normal": t["jan_norm"],
        "Jantar_Vegetariano": t["jan_veg"],
        "Jantar_Dieta": t["jan_dieta"],
        "Jantar_Saem_Unidade": t["jan_sai"],
        "Jantar_Estufa": t.get("jan_estufa", 0),
    }
    if extra:
        row.update(extra)
    return row
