"""
tests/test_companhias_core.py — Testes para core/companhias.py
"""

import pytest
from tests.conftest import create_aluno
from core.database import db


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_test_data(app):
    """Corre cada teste dentro do contexto da app e limpa dados de teste."""
    with app.app_context():
        yield
    # Cleanup turmas e alunos criados pelos testes
    with app.app_context():
        with db() as conn:
            conn.execute("DELETE FROM turmas WHERE nome LIKE 'TestTurma%'")
            conn.execute("DELETE FROM utilizadores WHERE NII LIKE 'TCOMP%'")
            conn.commit()


def _create_turma(nome, ano, descricao=None):
    """Cria uma turma de teste e retorna o seu id."""
    with db() as conn:
        conn.execute(
            "INSERT INTO turmas (nome, ano, descricao) VALUES (?,?,?)",
            (nome, ano, descricao),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM turmas WHERE nome=?", (nome,)).fetchone()
        return row["id"]


# ── delete_turma ──────────────────────────────────────────────────────────────


def test_delete_turma_removes_turma(app):
    """delete_turma elimina a turma da BD."""
    from core.companhias import delete_turma

    with app.app_context():
        tid = _create_turma("TestTurma_del", 1)
        delete_turma(tid)
        with db() as conn:
            row = conn.execute("SELECT id FROM turmas WHERE id=?", (tid,)).fetchone()
        assert row is None


def test_delete_turma_disassociates_alunos(app):
    """delete_turma desassocia alunos antes de apagar a turma."""
    from core.companhias import delete_turma

    with app.app_context():
        tid = _create_turma("TestTurma_disassoc", 1)
        uid = create_aluno("TCOMP_DA1", "DA1", "Aluno Disassoc", ano="1")
        # Associar aluno à turma
        with db() as conn:
            conn.execute("UPDATE utilizadores SET turma_id=? WHERE id=?", (tid, uid))
            conn.commit()

        delete_turma(tid)

        with db() as conn:
            row = conn.execute(
                "SELECT turma_id FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
        assert row["turma_id"] is None


# ── promote_one ───────────────────────────────────────────────────────────────


def test_promote_one_not_found(app):
    """promote_one retorna 'Não encontrado' para uid inexistente."""
    from core.companhias import promote_one

    with app.app_context():
        result = promote_one(uid=999999999)
    assert result == "Não encontrado"


def test_promote_one_increments_year(app):
    """promote_one incrementa o ano do aluno em 1."""
    from core.companhias import promote_one

    with app.app_context():
        uid = create_aluno("TCOMP_P1", "P001", "Aluno Promote One", ano="2")
        result = promote_one(uid)
        with db() as conn:
            row = conn.execute(
                "SELECT ano FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
    assert row["ano"] == 3
    assert "3" in result or "3º" in result or result != "Não encontrado"


def test_promote_one_year_6_becomes_concluido(app):
    """promote_one com ano >= 6 retorna 'Concluído' e define ano=0."""
    from core.companhias import promote_one

    with app.app_context():
        uid = create_aluno("TCOMP_P6", "P006", "Aluno Ano 6", ano="6")
        result = promote_one(uid)
        with db() as conn:
            row = conn.execute(
                "SELECT ano FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
    assert result == "Concluído"
    assert row["ano"] == 0


def test_promote_one_updates_ni(app):
    """promote_one atualiza o NI quando novo_ni é fornecido."""
    from core.companhias import promote_one

    with app.app_context():
        uid = create_aluno("TCOMP_NI", "NI_OLD", "Aluno NI Update", ano="3")
        promote_one(uid, novo_ni="NI_NEW")
        with db() as conn:
            row = conn.execute(
                "SELECT NI FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
    assert row["NI"] == "NI_NEW"


def test_promote_one_keeps_ni_when_not_provided(app):
    """promote_one mantém o NI original quando novo_ni não é fornecido."""
    from core.companhias import promote_one

    with app.app_context():
        uid = create_aluno("TCOMP_NIKO", "NI_KEEP", "Aluno NI Keep", ano="1")
        promote_one(uid)
        with db() as conn:
            row = conn.execute(
                "SELECT NI FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
    assert row["NI"] == "NI_KEEP"


# ── promote_all_in_year ───────────────────────────────────────────────────────


def test_promote_all_in_year_increments(app):
    """promote_all_in_year incrementa o ano de todos os alunos do ano dado."""
    from core.companhias import promote_all_in_year

    with app.app_context():
        uid1 = create_aluno("TCOMP_AY1", "AY01", "Aluno Year1 A", ano="4")
        uid2 = create_aluno("TCOMP_AY2", "AY02", "Aluno Year1 B", ano="4")
        result = promote_all_in_year(4)
        with db() as conn:
            row1 = conn.execute(
                "SELECT ano FROM utilizadores WHERE id=?", (uid1,)
            ).fetchone()
            row2 = conn.execute(
                "SELECT ano FROM utilizadores WHERE id=?", (uid2,)
            ).fetchone()
    assert row1["ano"] == 5
    assert row2["ano"] == 5
    assert result != "Não encontrado"


def test_promote_all_in_year_6_returns_concluido(app):
    """promote_all_in_year com ano=6 retorna 'Concluído'."""
    from core.companhias import promote_all_in_year

    with app.app_context():
        uid = create_aluno("TCOMP_AY6", "AY06", "Aluno Year6", ano="6")
        result = promote_all_in_year(6)
        with db() as conn:
            row = conn.execute(
                "SELECT ano FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
    assert result == "Concluído"
    assert row["ano"] == 0


# ── promote_all_years ─────────────────────────────────────────────────────────


def test_promote_all_years_promotes_every_year(app):
    """promote_all_years promove alunos de todos os anos."""
    from core.companhias import promote_all_years

    with app.app_context():
        uids = {}
        for ano in range(1, 7):
            nii = f"TCOMP_ALL{ano}"
            ni = f"ALL0{ano}"
            uid = create_aluno(nii, ni, f"Aluno All Year{ano}", ano=str(ano))
            uids[ano] = uid

        promote_all_years()

        with db() as conn:
            for ano, uid in uids.items():
                row = conn.execute(
                    "SELECT ano FROM utilizadores WHERE id=?", (uid,)
                ).fetchone()
                expected = 0 if ano >= 6 else ano + 1
                assert row["ano"] == expected, (
                    f"Aluno ano={ano}: esperado {expected}, obtido {row['ano']}"
                )


# ── get_companhias_data ───────────────────────────────────────────────────────


def test_get_companhias_data_returns_expected_keys(app):
    """get_companhias_data retorna dict com todas as chaves esperadas."""
    from core.companhias import get_companhias_data

    with app.app_context():
        data = get_companhias_data()

    assert "turmas" in data
    assert "anos_data" in data
    assert "all_anos" in data
    assert "promocao_data" in data
    assert "alunos_all" in data


def test_get_companhias_data_all_anos_content(app):
    """get_companhias_data retorna all_anos com anos 1-6 e 7,8."""
    from core.companhias import get_companhias_data

    with app.app_context():
        data = get_companhias_data()

    assert data["all_anos"] == [1, 2, 3, 4, 5, 6, 7, 8]


def test_get_companhias_data_anos_data_counts(app):
    """get_companhias_data retorna contagem de alunos por ano."""
    from core.companhias import get_companhias_data

    with app.app_context():
        create_aluno("TCOMP_GC1", "GC01", "Aluno GC", ano="3")
        data = get_companhias_data()

    assert isinstance(data["anos_data"], dict)
    assert data["anos_data"][3] >= 1


def test_get_companhias_data_promocao_data_structure(app):
    """get_companhias_data.promocao_data tem a estrutura correcta."""
    from core.companhias import get_companhias_data

    with app.app_context():
        data = get_companhias_data()

    assert len(data["promocao_data"]) == 8
    for item in data["promocao_data"]:
        assert "ano" in item
        assert "alunos" in item
        assert "destino" in item
        assert "cor" in item


def test_get_companhias_data_promocao_destino_concluido(app):
    """Anos >= 6 têm destino='Concluído' em promocao_data."""
    from core.companhias import get_companhias_data

    with app.app_context():
        data = get_companhias_data()

    for item in data["promocao_data"]:
        if item["ano"] >= 6:
            assert item["destino"] == "Concluído"


def test_get_companhias_data_turmas_list(app):
    """get_companhias_data retorna lista de turmas."""
    from core.companhias import get_companhias_data

    with app.app_context():
        _create_turma("TestTurma_gc", 2, "Turma de teste GC")
        data = get_companhias_data()

    assert isinstance(data["turmas"], list)
    names = [t["nome"] for t in data["turmas"]]
    assert "TestTurma_gc" in names


def test_get_companhias_data_turmas_exception_returns_empty(app, monkeypatch):
    """get_companhias_data retorna lista vazia de turmas quando a query falha."""
    import sqlite3
    from core import companhias, database

    call_count = {"n": 0}

    original_db = database.db

    def failing_db():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Primeira chamada (turmas) — lança excepção
            class BadConn:
                def execute(self, *a, **kw):
                    raise sqlite3.OperationalError("no such table: turmas")

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            return BadConn()
        return original_db()

    monkeypatch.setattr(database, "db", failing_db)
    monkeypatch.setattr(companhias, "db", failing_db)

    with app.app_context():
        data = companhias.get_companhias_data()

    assert data["turmas"] == []
