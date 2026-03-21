"""core/audit — Consultas de logs de refeições e auditoria admin."""

from __future__ import annotations

from core.database import db


def query_meal_log(
    q_nome: str = "",
    q_por: str = "",
    q_campo: str = "",
    q_d0: str = "",
    q_d1: str = "",
    limit: int = 500,
) -> tuple[list[dict], int, list[str]]:
    """Consulta o log de alterações de refeições.

    Retorna (rows, total_logs, campos_disponiveis).
    """
    sql = """SELECT l.id, l.alterado_em, u.NII, u.Nome_completo, u.ano,
                    l.data_refeicao, l.campo, l.valor_antes, l.valor_depois, l.alterado_por
             FROM refeicoes_log l LEFT JOIN utilizadores u ON u.id=l.utilizador_id
             WHERE 1=1"""
    args: list = []

    if q_nome:
        sql += " AND u.Nome_completo LIKE ?"
        args.append(f"%{q_nome}%")
    if q_por:
        sql += " AND l.alterado_por LIKE ?"
        args.append(f"%{q_por}%")
    if q_campo:
        sql += " AND l.campo=?"
        args.append(q_campo)
    if q_d0:
        sql += " AND l.data_refeicao >= ?"
        args.append(q_d0)
    if q_d1:
        sql += " AND l.data_refeicao <= ?"
        args.append(q_d1)

    sql += " ORDER BY l.alterado_em DESC LIMIT ?"
    args.append(limit)

    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        total_logs = conn.execute("SELECT COUNT(*) c FROM refeicoes_log").fetchone()[
            "c"
        ]
        campos_disponiveis = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT campo FROM refeicoes_log ORDER BY campo"
            ).fetchall()
        ]

    return rows, total_logs, campos_disponiveis


def query_admin_audit(
    actor: str = "",
    action: str = "",
    limit: int = 500,
) -> tuple[list[dict], int]:
    """Consulta o log de auditoria admin.

    Retorna (rows, total).
    """
    sql = "SELECT id,ts,actor,action,detail FROM admin_audit_log WHERE 1=1"
    args: list = []
    if actor:
        sql += " AND actor LIKE ?"
        args.append(f"%{actor}%")
    if action:
        sql += " AND action LIKE ?"
        args.append(f"%{action}%")
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        total = conn.execute("SELECT COUNT(*) c FROM admin_audit_log").fetchone()["c"]

    return rows, total
