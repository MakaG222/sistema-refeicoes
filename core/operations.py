"""core/operations — Queries operacionais (painel, presenças, licenças, ausências)."""

from __future__ import annotations

from datetime import date, datetime

from core.database import db
from utils.business import _registar_ausencia, _tem_ausencia_ativa


def get_detidos_dia(d_str: str) -> list[dict]:
    """Retorna detidos activos numa data."""
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT uu.NI, uu.Nome_completo, uu.ano, d.detido_de, d.detido_ate, d.motivo
                FROM detencoes d JOIN utilizadores uu ON uu.id=d.utilizador_id
                WHERE uu.perfil='aluno' AND d.detido_de<=? AND d.detido_ate>=?
                ORDER BY uu.ano, uu.NI""",
                (d_str, d_str),
            ).fetchall()
        ]


def get_licencas_dia(d_str: str) -> list[dict]:
    """Retorna licenças de uma data."""
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT uu.NI, uu.Nome_completo, uu.ano, l.tipo, l.hora_saida, l.hora_entrada
                FROM licencas l JOIN utilizadores uu ON uu.id=l.utilizador_id
                WHERE l.data=? ORDER BY uu.ano, uu.NI""",
                (d_str,),
            ).fetchall()
        ]


def get_alunos_ano_com_estado(ano: int, dt: date) -> list[dict]:
    """Retorna alunos de um ano com estado de refeição, ausência e licença."""
    d_str = dt.isoformat()
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT u.id, u.NII, u.NI, u.Nome_completo,
                       r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade,
                       EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                              AND a.ausente_de <= ? AND a.ausente_ate >= ?) AS ausente,
                       (SELECT l.tipo FROM licencas l WHERE l.utilizador_id=u.id AND l.data=?) AS licenca_tipo
                FROM utilizadores u
                LEFT JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
                WHERE u.ano=?
                ORDER BY u.NI""",
                (d_str, d_str, d_str, d_str, ano),
            ).fetchall()
        ]


def marcar_presente(uid: int, d_str: str) -> None:
    """Remove ausência de dia único para um aluno."""
    with db() as conn:
        conn.execute(
            "DELETE FROM ausencias WHERE utilizador_id=? AND ausente_de=? AND ausente_ate=?",
            (uid, d_str, d_str),
        )
        conn.commit()


def get_alunos_para_impressao(ano: int, dt: date) -> list[dict]:
    """Retorna alunos para mapa de impressão."""
    d_str = dt.isoformat()
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT u.NI, u.Nome_completo,
                       r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade,
                       EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                              AND a.ausente_de<=? AND a.ausente_ate>=?) AS ausente
                FROM utilizadores u
                LEFT JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
                WHERE u.ano=? ORDER BY u.NI""",
                (d_str, d_str, d_str, ano),
            ).fetchall()
        ]


def get_licencas_contadores(d_str: str) -> dict:
    """Retorna contadores e listas de licenças para a página de entradas/saídas."""
    with db() as conn:
        total = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE l.data=? AND uu.perfil='aluno'""",
            (d_str,),
        ).fetchone()["c"]

        saidas = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE l.data=? AND uu.perfil='aluno' AND l.hora_saida IS NOT NULL""",
            (d_str,),
        ).fetchone()["c"]

        entradas = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE l.data=? AND uu.perfil='aluno' AND l.hora_entrada IS NOT NULL""",
            (d_str,),
        ).fetchone()["c"]

        fora = conn.execute(
            """SELECT COUNT(*) c FROM licencas l
               JOIN utilizadores uu ON uu.id=l.utilizador_id
               WHERE uu.perfil='aluno'
                 AND l.hora_saida IS NOT NULL
                 AND l.hora_entrada IS NULL""",
        ).fetchone()["c"]

        rows_hoje = [
            dict(r)
            for r in conn.execute(
                """SELECT l.id, uu.NI, uu.Nome_completo, uu.ano,
                          l.data, l.tipo, l.hora_saida, l.hora_entrada
                   FROM licencas l
                   JOIN utilizadores uu ON uu.id=l.utilizador_id
                   WHERE l.data=? AND uu.perfil='aluno'
                   ORDER BY uu.ano, uu.NI""",
                (d_str,),
            ).fetchall()
        ]

        rows_fora = [
            dict(r)
            for r in conn.execute(
                """SELECT l.id, uu.NI, uu.Nome_completo, uu.ano,
                          l.data, l.tipo, l.hora_saida, l.hora_entrada
                   FROM licencas l
                   JOIN utilizadores uu ON uu.id=l.utilizador_id
                   WHERE uu.perfil='aluno'
                     AND l.hora_saida IS NOT NULL
                     AND l.hora_entrada IS NULL
                     AND l.data != ?
                   ORDER BY l.data ASC, uu.ano, uu.NI""",
                (d_str,),
            ).fetchall()
        ]

    return {
        "total": total,
        "saidas": saidas,
        "entradas": entradas,
        "fora": fora,
        "rows_hoje": rows_hoje,
        "rows_fora": rows_fora,
    }


def registar_hora_licenca(lic_id: str | int, acao: str) -> None:
    """Regista hora de saída/entrada numa licença."""
    agora = datetime.now().strftime("%H:%M")
    with db() as conn:
        if acao == "saida":
            conn.execute(
                "UPDATE licencas SET hora_saida=? WHERE id=? AND hora_saida IS NULL",
                (agora, lic_id),
            )
        elif acao == "entrada":
            conn.execute(
                "UPDATE licencas SET hora_entrada=? WHERE id=? AND hora_entrada IS NULL",
                (agora, lic_id),
            )
        elif acao == "limpar_saida":
            conn.execute("UPDATE licencas SET hora_saida=NULL WHERE id=?", (lic_id,))
        elif acao == "limpar_entrada":
            conn.execute("UPDATE licencas SET hora_entrada=NULL WHERE id=?", (lic_id,))
        conn.commit()


def get_ausencias_lista() -> list[dict]:
    """Lista todas as ausências com dados do utilizador."""
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                       a.ausente_de, a.ausente_ate, a.motivo
                FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
                ORDER BY a.ausente_de DESC"""
            ).fetchall()
        ]


def get_ausencias_recentes(uid: int, limit: int = 5) -> list[dict]:
    """Retorna as ausências mais recentes de um utilizador."""
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT ausente_de, ausente_ate, motivo FROM ausencias
                WHERE utilizador_id=? ORDER BY ausente_de DESC LIMIT ?""",
                (uid, limit),
            ).fetchall()
        ]


def get_presenca_consulta(ni: str, dt: date) -> dict | None:
    """Consulta presença de um aluno por NI. Retorna dict ou None."""
    d_str = dt.isoformat()
    with db() as conn:
        aluno = conn.execute(
            "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NI=? AND perfil='aluno'",
            (ni,),
        ).fetchone()
    if not aluno:
        return None
    aluno = dict(aluno)
    uid = aluno["id"]
    ausente = _tem_ausencia_ativa(uid, dt)
    with db() as conn:
        ref = conn.execute(
            "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
            (uid, d_str),
        ).fetchone()
        ref = dict(ref) if ref else {}
        lic = conn.execute(
            "SELECT tipo, hora_saida, hora_entrada FROM licencas WHERE utilizador_id=? AND data=?",
            (uid, d_str),
        ).fetchone()
        lic = dict(lic) if lic else {}
    return {"aluno": aluno, "ausente": ausente, "ref": ref, "ni": ni, "licenca": lic}


def get_anos_resumo(dt: date, anos: list[int]) -> list[dict]:
    """Resumo por ano: total, ausentes, presentes, com refeição."""
    d_str = dt.isoformat()
    result = []
    for ano in anos:
        with db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE ano=? AND perfil='aluno'",
                (ano,),
            ).fetchone()["c"]
            ausentes_a = conn.execute(
                """SELECT COUNT(*) c FROM utilizadores u
                WHERE u.ano=? AND u.perfil='aluno'
                AND EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                           AND a.ausente_de<=? AND a.ausente_ate>=?)""",
                (ano, d_str, d_str),
            ).fetchone()["c"]
            com_ref = conn.execute(
                """SELECT COUNT(*) c FROM utilizadores u
                WHERE u.ano=? AND u.perfil='aluno'
                AND EXISTS(SELECT 1 FROM refeicoes r WHERE r.utilizador_id=u.id
                           AND r.data=? AND (r.almoco IS NOT NULL OR r.jantar_tipo IS NOT NULL))""",
                (ano, d_str),
            ).fetchone()["c"]
        result.append(
            {
                "ano": ano,
                "total": total,
                "ausentes": ausentes_a,
                "presentes": total - ausentes_a,
                "com_ref": com_ref,
            }
        )
    return result


def registar_saida_presenca(
    uid: int, dt: date, nii_actor: str, nome_actor: str, perfil_actor: str
) -> None:
    """Regista saída: cria ausência + marca hora_saida na licença."""
    d_str = dt.isoformat()
    _registar_ausencia(
        uid,
        d_str,
        d_str,
        f"Saída registada por {nome_actor} ({perfil_actor})",
        nii_actor,
    )
    agora = datetime.now().strftime("%H:%M")
    with db() as conn:
        conn.execute(
            "UPDATE licencas SET hora_saida=? WHERE utilizador_id=? AND data=? AND hora_saida IS NULL",
            (agora, uid, d_str),
        )
        conn.commit()


def registar_entrada_presenca(uid: int, dt: date) -> None:
    """Regista entrada: remove ausência do dia + marca hora_entrada na licença."""
    d_str = dt.isoformat()
    with db() as conn:
        conn.execute(
            "DELETE FROM ausencias WHERE utilizador_id=? AND ausente_de=? AND ausente_ate=?",
            (uid, d_str, d_str),
        )
        conn.commit()
    agora = datetime.now().strftime("%H:%M")
    with db() as conn:
        conn.execute(
            "UPDATE licencas SET hora_entrada=? WHERE utilizador_id=? AND data=? AND hora_entrada IS NULL",
            (agora, uid, d_str),
        )
        conn.commit()
