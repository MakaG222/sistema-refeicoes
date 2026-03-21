"""Queries batch para ausências, detenções e licenças."""

from datetime import date, timedelta

from core.database import db


def ausencias_batch(uid: int, d_de: date, d_ate: date) -> set:
    """Devolve conjunto de datas (ISO str) com ausência ativa no intervalo."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ausente_de, ausente_ate FROM ausencias
               WHERE utilizador_id=? AND ausente_ate>=? AND ausente_de<=?""",
            (uid, d_de.isoformat(), d_ate.isoformat()),
        ).fetchall()
    dates = set()
    for r in rows:
        a_de = date.fromisoformat(r["ausente_de"])
        a_ate = date.fromisoformat(r["ausente_ate"])
        d = max(a_de, d_de)
        while d <= min(a_ate, d_ate):
            dates.add(d.isoformat())
            d += timedelta(days=1)
    return dates


def detencoes_batch(uid: int, d_de: date, d_ate: date) -> set:
    """Devolve conjunto de datas (ISO str) com detenção ativa no intervalo."""
    with db() as conn:
        rows = conn.execute(
            """SELECT detido_de, detido_ate FROM detencoes
               WHERE utilizador_id=? AND detido_ate>=? AND detido_de<=?""",
            (uid, d_de.isoformat(), d_ate.isoformat()),
        ).fetchall()
    dates = set()
    for r in rows:
        d_de_r = date.fromisoformat(r["detido_de"])
        d_ate_r = date.fromisoformat(r["detido_ate"])
        d = max(d_de_r, d_de)
        while d <= min(d_ate_r, d_ate):
            dates.add(d.isoformat())
            d += timedelta(days=1)
    return dates


def licencas_batch(uid: int, d_de: date, d_ate: date) -> dict:
    """Carrega licenças de um aluno para um intervalo. Devolve {iso_date: tipo}."""
    with db() as conn:
        rows = conn.execute(
            "SELECT data, tipo FROM licencas WHERE utilizador_id=? AND data>=? AND data<=?",
            (uid, d_de.isoformat(), d_ate.isoformat()),
        ).fetchall()
    return {r["data"]: r["tipo"] for r in rows}


def utilizador_ausente(uid: int, d: date) -> bool:
    """Devolve True se o utilizador tem ausência registada para a data d."""
    with db() as conn:
        r = conn.execute(
            """
            SELECT 1 FROM ausencias
            WHERE utilizador_id=? AND ausente_de <= ? AND ausente_ate >= ?
            LIMIT 1
        """,
            (uid, d.isoformat(), d.isoformat()),
        ).fetchone()
        return r is not None
