"""Queries batch para ausências, detenções e licenças."""

from __future__ import annotations

from datetime import date, timedelta

from core.database import db


def ausencias_batch(uid: int, d_de: date, d_ate: date) -> set:
    """Devolve conjunto de datas (ISO str) com ausência ativa no intervalo.

    Nota: ausências parciais (com hora_inicio/hora_fim) também aparecem.
    """
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


def ausencias_batch_detalhadas(uid: int, d_de: date, d_ate: date) -> dict:
    """Devolve dict {iso_date: info} com detalhes das ausências (horas, estufa).

    info = {"parcial": bool, "hora_inicio": str|None, "hora_fim": str|None,
            "estufa_almoco": bool, "estufa_jantar": bool}
    """
    with db() as conn:
        rows = conn.execute(
            """SELECT ausente_de, ausente_ate, hora_inicio, hora_fim,
                      estufa_almoco, estufa_jantar
               FROM ausencias
               WHERE utilizador_id=? AND ausente_ate>=? AND ausente_de<=?""",
            (uid, d_de.isoformat(), d_ate.isoformat()),
        ).fetchall()
    result: dict = {}
    for r in rows:
        a_de = date.fromisoformat(r["ausente_de"])
        a_ate = date.fromisoformat(r["ausente_ate"])
        d = max(a_de, d_de)
        parcial = bool(r["hora_inicio"] and r["hora_fim"])
        info = {
            "parcial": parcial,
            "hora_inicio": r["hora_inicio"],
            "hora_fim": r["hora_fim"],
            "estufa_almoco": bool(r["estufa_almoco"]),
            "estufa_jantar": bool(r["estufa_jantar"]),
        }
        while d <= min(a_ate, d_ate):
            d_iso = d.isoformat()
            # Se já existe uma ausência de dia inteiro, não substituir por parcial
            if d_iso not in result or not parcial:
                result[d_iso] = info
            d += timedelta(days=1)
    return result


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


def get_ausencias_cmd(ano_cmd: int | None = None) -> list[dict]:
    """Lista ausências para a vista CMD, filtradas por ano se fornecido."""
    with db() as conn:
        if ano_cmd:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                       a.ausente_de, a.ausente_ate, a.hora_inicio, a.hora_fim,
                       a.estufa_almoco, a.estufa_jantar, a.motivo
                    FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
                    WHERE u.perfil='aluno' AND u.ano=?
                    ORDER BY a.ausente_de DESC""",
                    (ano_cmd,),
                ).fetchall()
            ]
        return [
            dict(r)
            for r in conn.execute(
                """SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                   a.ausente_de, a.ausente_ate, a.hora_inicio, a.hora_fim,
                   a.estufa_almoco, a.estufa_jantar, a.motivo
                FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
                WHERE u.perfil='aluno'
                ORDER BY a.ausente_de DESC"""
            ).fetchall()
        ]


def remover_ausencia_autorizada(aid: int, ano_cmd: int, is_admin: bool) -> bool:
    """Remove ausência se autorizado (CMD do ano ou admin). Retorna True se removida."""
    with db() as conn:
        aus = conn.execute(
            """SELECT a.id FROM ausencias a
            JOIN utilizadores u ON u.id=a.utilizador_id
            WHERE a.id=? AND (u.ano=? OR ?=1)""",
            (aid, ano_cmd, 1 if is_admin else 0),
        ).fetchone()
    if not aus:
        return False
    from utils.business import _remover_ausencia

    _remover_ausencia(aid)
    return True


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
