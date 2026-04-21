"""core/audit — Consultas de logs de refeições e auditoria admin."""

from __future__ import annotations

from core.database import db


def query_meal_log(
    q_nome: str = "",
    q_por: str = "",
    q_campo: str = "",
    q_d0: str = "",
    q_d1: str = "",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, int, list[str]]:
    """Consulta o log de alterações de refeições.

    Retorna (rows, filtered_total, total_logs, campos_disponiveis).
    """
    where = "WHERE 1=1"
    args: list = []

    if q_nome:
        where += " AND u.Nome_completo LIKE ?"
        args.append(f"%{q_nome}%")
    if q_por:
        where += " AND l.alterado_por LIKE ?"
        args.append(f"%{q_por}%")
    if q_campo:
        where += " AND l.campo=?"
        args.append(q_campo)
    if q_d0:
        where += " AND l.data_refeicao >= ?"
        args.append(q_d0)
    if q_d1:
        where += " AND l.data_refeicao <= ?"
        args.append(q_d1)

    base_sql = (
        "FROM refeicoes_log l LEFT JOIN utilizadores u ON u.id=l.utilizador_id " + where
    )

    offset = (page - 1) * per_page

    with db() as conn:
        filtered_total = conn.execute(
            f"SELECT COUNT(*) c {base_sql}",  # nosec B608
            args,
        ).fetchone()["c"]

        rows = conn.execute(
            f"SELECT l.id, l.alterado_em, u.NII, u.Nome_completo, u.ano,"  # nosec B608
            f" l.data_refeicao, l.campo, l.valor_antes, l.valor_depois, l.alterado_por"
            f" {base_sql} ORDER BY l.alterado_em DESC LIMIT ? OFFSET ?",
            [*args, per_page, offset],
        ).fetchall()

        total_logs = conn.execute("SELECT COUNT(*) c FROM refeicoes_log").fetchone()[
            "c"
        ]
        campos_disponiveis = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT campo FROM refeicoes_log ORDER BY campo"
            ).fetchall()
        ]

    return rows, filtered_total, total_logs, campos_disponiveis


def query_admin_audit(
    actor: str = "",
    action: str = "",
    limit: int = 500,
) -> tuple[list[dict], int]:
    """Consulta o log de auditoria admin (modo legacy — limit fixo, sem paginação).

    Retorna (rows, total_absoluto). Para paginação, usa `query_admin_audit_paged`.
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


def query_admin_audit_paged(
    actor: str = "",
    action: str = "",
    *,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, int]:
    """Versão paginada — retorna (rows, filtered_total, total_absoluto).

    `filtered_total` considera filtros `actor` e `action`; `total_absoluto`
    conta TODOS os registos (útil para footer do template).
    """
    page = max(1, int(page or 1))
    per_page = max(1, min(500, int(per_page or 50)))
    base = "FROM admin_audit_log WHERE 1=1"
    args: list = []
    if actor:
        base += " AND actor LIKE ?"
        args.append(f"%{actor}%")
    if action:
        base += " AND action LIKE ?"
        args.append(f"%{action}%")

    with db() as conn:
        total_abs = conn.execute("SELECT COUNT(*) c FROM admin_audit_log").fetchone()[
            "c"
        ]
        filtered_total = conn.execute(
            "SELECT COUNT(*) c " + base,  # nosec B608 — args parametrizados
            args,
        ).fetchone()["c"]
        offset = (page - 1) * per_page
        sql = (
            "SELECT id,ts,actor,action,detail "
            + base
            + " ORDER BY id DESC LIMIT ? OFFSET ?"
        )
        rows = [
            dict(r) for r in conn.execute(sql, [*args, per_page, offset]).fetchall()
        ]
    return rows, filtered_total, total_abs
