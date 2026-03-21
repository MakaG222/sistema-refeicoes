"""core/users — CRUD e consultas de utilizadores."""

from __future__ import annotations

from core.database import db


def count_users() -> int:
    """Conta o número total de utilizadores."""
    with db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM utilizadores").fetchone()["c"]


def list_users(q: str | None = None, ano: str | None = None) -> list[dict]:
    """Lista utilizadores com filtros opcionais de nome e ano."""
    sql = "SELECT id,NII,NI,Nome_completo,ano,perfil,locked_until,email,telemovel FROM utilizadores WHERE 1=1"
    args: list = []
    if q:
        sql += " AND Nome_completo LIKE ?"
        args.append(f"%{q}%")
    if ano and ano != "all":
        sql += " AND ano=?"
        args.append(ano)
    sql += " ORDER BY ano, NI"
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def update_user(
    nii: str,
    nome: str,
    ni: str,
    ano: str,
    perfil: str,
    email: str | None,
    tel: str | None,
) -> None:
    """Atualiza os dados de um utilizador."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET Nome_completo=?,NI=?,ano=?,perfil=?,email=?,telemovel=? WHERE NII=?",
            (nome, ni, ano, perfil, email, tel, nii),
        )
        conn.commit()


def update_user_password(nii: str, pw_hash: str) -> None:
    """Atualiza a password de um utilizador e força mudança no próximo login."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET Palavra_chave=?,must_change_password=1 WHERE NII=?",
            (pw_hash, nii),
        )
        conn.commit()


def update_contacts(nii: str, email: str | None, tel: str | None) -> None:
    """Atualiza apenas os contactos de um utilizador."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET email=?, telemovel=? WHERE NII=?",
            (email, tel, nii),
        )
        conn.commit()


def csv_check_duplicates() -> set[str]:
    """Retorna o conjunto de NIIs existentes na BD."""
    with db() as conn:
        return {
            r["NII"] for r in conn.execute("SELECT NII FROM utilizadores").fetchall()
        }
