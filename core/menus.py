"""core/menus — CRUD de menus diários e capacidades de refeição."""

from __future__ import annotations

from core.database import db


def save_menu(data: str, vals: list) -> None:
    """Guarda o menu diário (INSERT OR REPLACE)."""
    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO menus_diarios
            (data,pequeno_almoco,lanche,almoco_normal,almoco_veg,almoco_dieta,jantar_normal,jantar_veg,jantar_dieta)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (data, *vals),
        )
        conn.commit()


def save_capacity(data: str, refeicao: str, cap_int: int) -> None:
    """Guarda ou remove a capacidade de uma refeição num dia.

    cap_int < 0 → remove o limite.
    """
    with db() as conn:
        if cap_int < 0:
            conn.execute(
                "DELETE FROM capacidade_refeicao WHERE data=? AND refeicao=?",
                (data, refeicao),
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO capacidade_refeicao(data,refeicao,max_total) VALUES (?,?,?)",
                (data, refeicao, cap_int),
            )
        conn.commit()


def get_menu(data: str) -> dict | None:
    """Retorna o menu de um dia ou None."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM menus_diarios WHERE data=?", (data,)
        ).fetchone()
        return dict(row) if row else None


def get_capacities(data: str) -> dict:
    """Retorna {refeicao: max_total} para um dia."""
    with db() as conn:
        return {
            r["refeicao"]: r["max_total"]
            for r in conn.execute(
                "SELECT refeicao,max_total FROM capacidade_refeicao WHERE data=?",
                (data,),
            )
        }
