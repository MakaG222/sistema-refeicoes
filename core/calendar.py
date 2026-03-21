"""core/calendar — Calendário operacional CRUD."""

from __future__ import annotations

from datetime import date, timedelta

from core.database import db


def add_entries(d_from: date, d_to: date, tipo: str, nota: str | None) -> int:
    """Adiciona entradas ao calendário operacional. Retorna o número de dias."""
    count = 0
    with db() as conn:
        cur = d_from
        while cur <= d_to:
            conn.execute(
                "INSERT OR REPLACE INTO calendario_operacional(data,tipo,nota) VALUES (?,?,?)",
                (cur.isoformat(), tipo, nota),
            )
            cur += timedelta(days=1)
            count += 1
        conn.commit()
    return count


def remove_entry(data: str) -> None:
    """Remove uma entrada do calendário operacional."""
    with db() as conn:
        conn.execute(
            "DELETE FROM calendario_operacional WHERE data=?",
            (data,),
        )
        conn.commit()


def get_upcoming(from_date: date, limit: int = 90) -> list[dict]:
    """Retorna as próximas entradas do calendário operacional."""
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT data,tipo,nota FROM calendario_operacional WHERE data >= ? ORDER BY data LIMIT ?",
                (from_date.isoformat(), limit),
            ).fetchall()
        ]
