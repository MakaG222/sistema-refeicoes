"""core/users — CRUD e consultas de utilizadores."""

from __future__ import annotations

from core.database import db


def count_users() -> int:
    """Conta o número total de utilizadores."""
    with db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM utilizadores").fetchone()["c"]


def list_users(
    q: str | None = None,
    ano: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int]:
    """Lista utilizadores com filtros opcionais, pesquisa FTS e paginação.

    Returns:
        (rows, total) — lista paginada e contagem total.
    """
    where = "WHERE 1=1"
    args: list = []

    # Pesquisa FTS se disponível, senão fallback para LIKE
    if q:
        try:
            with db() as conn:
                fts_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT rowid FROM utilizadores_fts WHERE utilizadores_fts MATCH ?",
                        (q + "*",),
                    ).fetchall()
                ]
            if fts_ids:
                placeholders = ",".join("?" for _ in fts_ids)
                where += f" AND id IN ({placeholders})"  # nosec B608
                args.extend(fts_ids)
            else:
                # FTS não encontrou — fallback LIKE
                where += " AND Nome_completo LIKE ?"
                args.append(f"%{q}%")
        except Exception:
            where += " AND Nome_completo LIKE ?"
            args.append(f"%{q}%")

    if ano and ano != "all":
        where += " AND ano=?"
        args.append(ano)

    with db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) c FROM utilizadores {where}",  # nosec B608
            args,
        ).fetchone()["c"]

        offset = (page - 1) * per_page
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT id,NII,NI,Nome_completo,ano,perfil,locked_until,email,telemovel"  # nosec B608
                f" FROM utilizadores {where} ORDER BY ano, NI LIMIT ? OFFSET ?",
                [*args, per_page, offset],
            ).fetchall()
        ]
    return rows, total


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


_ALLOWED_USER_COLS = frozenset(
    {
        "id",
        "NII",
        "NI",
        "Nome_completo",
        "Palavra_chave",
        "ano",
        "perfil",
        "locked_until",
        "must_change_password",
        "password_updated_at",
        "is_active",
        "email",
        "telemovel",
        "data_criacao",
        "turma_id",
    }
)

_DEFAULT_USER_COLS = ("id", "NII", "NI", "Nome_completo", "ano", "email", "telemovel")


def get_user_by_nii_fields(
    nii: str, fields: str | tuple[str, ...] | None = None
) -> dict | None:
    """Busca um utilizador por NII com campos específicos (validados via allowlist)."""
    if fields is None:
        cols = _DEFAULT_USER_COLS
    elif isinstance(fields, str):
        cols = tuple(c.strip() for c in fields.split(","))
    else:
        cols = fields
    bad = set(cols) - _ALLOWED_USER_COLS
    if bad:
        raise ValueError(f"Colunas não permitidas: {bad}")
    col_sql = ",".join(cols)
    with db() as conn:
        row = conn.execute(
            f"SELECT {col_sql} FROM utilizadores WHERE NII=?",  # nosec B608
            (nii,),
        ).fetchone()
    return dict(row) if row else None


def get_aluno_by_ni(ni: str) -> dict | None:
    """Busca um aluno por NI."""
    with db() as conn:
        row = conn.execute(
            "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NI=? AND perfil='aluno'",
            (ni,),
        ).fetchone()
    return dict(row) if row else None


def update_aluno_data(
    nii: str, nome: str, ni: str | None, email: str | None, tel: str | None
) -> None:
    """Atualiza dados de um aluno (cmd edit)."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET Nome_completo=?,NI=?,email=?,telemovel=? WHERE NII=?",
            (nome, ni or None, email, tel, nii),
        )
        conn.commit()


def get_aluno_profile_data(uid: int, dt_iso: str) -> dict:
    """Busca dados de perfil de um aluno: total refeições, ausências ativas, histórico."""
    with db() as conn:
        total_ref = conn.execute(
            "SELECT COUNT(*) c FROM refeicoes WHERE utilizador_id=?", (uid,)
        ).fetchone()["c"]
        ausencias_ativas = conn.execute(
            """SELECT COUNT(*) c FROM ausencias WHERE utilizador_id=?
               AND ausente_de<=? AND ausente_ate>=?""",
            (uid, dt_iso, dt_iso),
        ).fetchone()["c"]
        aus_recentes = [
            dict(r)
            for r in conn.execute(
                """SELECT ausente_de, ausente_ate, motivo FROM ausencias
                WHERE utilizador_id=? ORDER BY ausente_de DESC LIMIT 5""",
                (uid,),
            ).fetchall()
        ]
        ref_hoje = conn.execute(
            "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
            (uid, dt_iso),
        ).fetchone()
    return {
        "total_ref": total_ref,
        "ausencias_ativas": ausencias_ativas,
        "aus_recentes": aus_recentes,
        "ref_hoje": dict(ref_hoje) if ref_hoje else {},
    }


def update_aluno_contacts(uid: int, email: str | None, tel: str | None) -> None:
    """Atualiza contactos de um aluno por uid."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET email=?, telemovel=? WHERE id=?",
            (email, tel, uid),
        )
        conn.commit()


def get_aluno_stats(uid: int, d0_iso: str) -> dict | None:
    """Retorna estatísticas de refeições dos últimos 30 dias."""
    with db() as conn:
        rows = conn.execute(
            "SELECT pequeno_almoco,lanche,almoco,jantar_tipo FROM refeicoes WHERE utilizador_id=? AND data>=?",
            (uid, d0_iso),
        ).fetchall()
    if not rows:
        return None
    return {
        "pa": sum(1 for r in rows if r["pequeno_almoco"]),
        "lanche": sum(1 for r in rows if r["lanche"]),
        "almoco": sum(1 for r in rows if r["almoco"]),
        "jantar": sum(1 for r in rows if r["jantar_tipo"]),
    }


def get_aluno_historico(uid: int, d0_iso: str) -> list:
    """Retorna histórico de refeições desde d0_iso."""
    with db() as conn:
        return conn.execute(
            """SELECT data,pequeno_almoco,lanche,almoco,almoco_estufa,
            jantar_tipo,jantar_sai_unidade,jantar_estufa
            FROM refeicoes WHERE utilizador_id=? AND data>=? ORDER BY data DESC""",
            (uid, d0_iso),
        ).fetchall()


def get_aluno_ano_ni(uid: int) -> tuple[int, str]:
    """Retorna (ano, NI) de um aluno."""
    with db() as conn:
        row = conn.execute(
            "SELECT ano, NI FROM utilizadores WHERE id=?", (uid,)
        ).fetchone()
    return (int(row["ano"]), row["NI"]) if row else (1, "")


def get_aluno_licenca(uid: int, dt_iso: str) -> str:
    """Retorna tipo de licença para um aluno numa data, ou '' se não existir."""
    with db() as conn:
        row = conn.execute(
            "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
            (uid, dt_iso),
        ).fetchone()
    return row["tipo"] if row else ""


def upsert_licenca(uid: int, dt_iso: str, licenca_tipo: str) -> None:
    """Insere ou atualiza licença de um aluno."""
    with db() as conn:
        conn.execute(
            """INSERT INTO licencas(utilizador_id, data, tipo)
            VALUES(?,?,?)
            ON CONFLICT(utilizador_id, data) DO UPDATE SET tipo=excluded.tipo""",
            (uid, dt_iso, licenca_tipo),
        )
        conn.commit()


def delete_licenca(uid: int, dt_iso: str) -> None:
    """Remove licença de um aluno para uma data."""
    with db() as conn:
        conn.execute(
            "DELETE FROM licencas WHERE utilizador_id=? AND data=?",
            (uid, dt_iso),
        )
        conn.commit()


def delete_ausencia_propria(aid: int, uid: int) -> None:
    """Remove ausência do próprio aluno."""
    with db() as conn:
        conn.execute(
            "DELETE FROM ausencias WHERE id=? AND utilizador_id=?",
            (aid, uid),
        )
        conn.commit()


def get_ausencias_aluno(uid: int) -> list[dict]:
    """Lista ausências de um aluno."""
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id,ausente_de,ausente_ate,motivo FROM ausencias WHERE utilizador_id=? ORDER BY ausente_de DESC",
                (uid,),
            ).fetchall()
        ]
