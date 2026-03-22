"""core/companhias — Gestão de turmas, atribuição e promoção de alunos."""

from __future__ import annotations

from core.database import db
from utils.helpers import _ano_label


def create_turma(nome: str, ano: int, descricao: str | None = None) -> None:
    """Cria uma nova turma."""
    with db() as conn:
        conn.execute(
            "INSERT INTO turmas (nome, ano, descricao) VALUES (?,?,?)",
            (nome, ano, descricao),
        )
        conn.commit()


def delete_turma(tid: int) -> None:
    """Elimina uma turma, desassociando os alunos primeiro."""
    with db() as conn:
        conn.execute("UPDATE utilizadores SET turma_id=NULL WHERE turma_id=?", (tid,))
        conn.execute("DELETE FROM turmas WHERE id=?", (tid,))
        conn.commit()


def assign_turma(nii: str, turma_id: int | None) -> None:
    """Atribui (ou remove) a turma de um aluno."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET turma_id=? WHERE NII=? AND perfil='aluno'",
            (turma_id, nii),
        )
        conn.commit()


def move_aluno_ano(nii: str, novo_ano: int) -> None:
    """Move um aluno para outro ano."""
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET ano=? WHERE NII=? AND perfil='aluno'",
            (novo_ano, nii),
        )
        conn.commit()


def promote_one(uid: int, novo_ni: str | None = None) -> str:
    """Promove um aluno individual. Retorna a label do destino."""
    with db() as conn:
        al = conn.execute(
            "SELECT ano,NI FROM utilizadores WHERE id=?", (uid,)
        ).fetchone()
    if not al:
        return "Não encontrado"

    ano_a = al["ano"]
    novo_ano = 0 if ano_a >= 6 else ano_a + 1

    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET ano=?,NI=? WHERE id=?",
            (novo_ano, novo_ni or al["NI"], uid),
        )
        conn.commit()

    return _ano_label(novo_ano) if novo_ano else "Concluído"


def promote_all_in_year(ano: int) -> str:
    """Promove todos os alunos de um ano. Retorna a label do destino."""
    novo_ano = 0 if ano >= 6 else ano + 1
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET ano=? WHERE perfil='aluno' AND ano=?",
            (novo_ano, ano),
        )
        conn.commit()
    return _ano_label(novo_ano) if novo_ano else "Concluído"


def promote_all_years() -> dict[int, int]:
    """Promove todos os alunos de todos os anos (do maior para o menor).

    Retorna dict {ano_origem: contagem} com o número de alunos promovidos por ano.
    """
    counts: dict[int, int] = {}
    with db() as conn:
        for ano_a in range(6, 0, -1):
            novo_ano = 0 if ano_a >= 6 else ano_a + 1
            cursor = conn.execute(
                "UPDATE utilizadores SET ano=? WHERE perfil='aluno' AND ano=?",
                (novo_ano, ano_a),
            )
            counts[ano_a] = cursor.rowcount
        conn.commit()
    return counts


def get_companhias_data() -> dict:
    """Carrega todos os dados para a página de companhias.

    Retorna dict com: turmas, anos_data, promocao_data, alunos_all, all_anos.
    """
    try:
        with db() as conn:
            turmas = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM turmas ORDER BY ano, nome"
                ).fetchall()
            ]
    except Exception:
        turmas = []

    all_anos = list(range(1, 7)) + [7, 8]
    anos_data = {}
    for a in all_anos:
        with db() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE ano=? AND perfil='aluno'",
                (a,),
            ).fetchone()["c"]
        anos_data[a] = cnt

    promocao_data = []
    for a in all_anos:
        with db() as conn:
            alunos_a = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,NI,Nome_completo,ano FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI",
                    (a,),
                ).fetchall()
            ]
        if a >= 6:
            destino = "Concluído"
            cor_cls = "promo-final"
        else:
            destino = _ano_label(a + 1)
            cor_cls = "promo-next"
        promocao_data.append(
            {"ano": a, "alunos": alunos_a, "destino": destino, "cor_cls": cor_cls}
        )

    with db() as conn:
        alunos_all = [
            dict(r)
            for r in conn.execute(
                "SELECT NII, NI, Nome_completo, ano, turma_id FROM utilizadores WHERE perfil='aluno' ORDER BY ano, NI"
            ).fetchall()
        ]

    return {
        "turmas": turmas,
        "anos_data": anos_data,
        "all_anos": all_anos,
        "promocao_data": promocao_data,
        "alunos_all": alunos_all,
    }
