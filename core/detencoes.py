"""core/detencoes — CRUD de detenções."""

from __future__ import annotations

from datetime import date

from core.database import db


def get_detencoes_lista(ano_cmd: int | None = None) -> list[dict]:
    """Lista detenções, opcionalmente filtradas por ano."""
    with db() as conn:
        if ano_cmd:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT d.id, uu.NII, uu.Nome_completo, uu.NI, uu.ano,
                       d.detido_de, d.detido_ate, d.motivo
                    FROM detencoes d
                    JOIN utilizadores uu ON uu.id=d.utilizador_id
                    WHERE uu.perfil='aluno' AND uu.ano=?
                    ORDER BY d.detido_de DESC""",
                    (ano_cmd,),
                ).fetchall()
            ]
        return [
            dict(r)
            for r in conn.execute(
                """SELECT d.id, uu.NII, uu.Nome_completo, uu.NI, uu.ano,
                   d.detido_de, d.detido_ate, d.motivo
                FROM detencoes d
                JOIN utilizadores uu ON uu.id=d.utilizador_id
                WHERE uu.perfil='aluno'
                ORDER BY d.detido_de DESC"""
            ).fetchall()
        ]


def criar_detencao(
    uid: int, d1: date, d2: date, motivo: str | None, criado_por: str
) -> None:
    """Cria uma detenção para um aluno."""
    with db() as conn:
        conn.execute(
            """INSERT INTO detencoes(utilizador_id, detido_de, detido_ate, motivo, criado_por)
            VALUES(?,?,?,?,?)""",
            (uid, d1.isoformat(), d2.isoformat(), motivo or None, criado_por),
        )
        conn.commit()


def remover_detencao(did: int, ano_cmd: int, is_admin: bool) -> bool:
    """Remove uma detenção se autorizado. Retorna True se removida."""
    with db() as conn:
        ok = conn.execute(
            """SELECT d.id FROM detencoes d
            JOIN utilizadores uu ON uu.id=d.utilizador_id
            WHERE d.id=? AND (uu.ano=? OR ?=1)""",
            (did, ano_cmd, 1 if is_admin else 0),
        ).fetchone()
        if ok:
            conn.execute("DELETE FROM detencoes WHERE id=?", (did,))
            conn.commit()
            return True
    return False


def cancelar_licencas_periodo(uid: int, d1: date, d2: date) -> None:
    """Cancela licenças existentes durante um período."""
    with db() as conn:
        conn.execute(
            "DELETE FROM licencas WHERE utilizador_id=? AND data>=? AND data<=?",
            (uid, d1.isoformat(), d2.isoformat()),
        )
        conn.commit()


def get_alunos_para_selecao(ano_cmd: int | None, perfil: str) -> list[dict]:
    """Retorna alunos para selecção em formulários (detenções/ausências)."""
    with db() as conn:
        if perfil == "cmd" and ano_cmd:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT NII, NI, Nome_completo FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI",
                    (ano_cmd,),
                ).fetchall()
            ]
        if perfil == "admin":
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT NII, NI, Nome_completo FROM utilizadores WHERE perfil='aluno' ORDER BY ano, NI"
                ).fetchall()
            ]
    return []
