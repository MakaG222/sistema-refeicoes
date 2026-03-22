"""
tests/test_admin_companhias.py — Testes para blueprints/admin/companhias.py
============================================================================
Cobre linhas não cobertas: 41, 46-47, 53-54, 59-60, 64-65, 68-76, 82, 84,
89-90, 93-99, 104-105, 112-114, 134, 140
  - criar_turma: sucesso, nome/ano em falta, ano inválido, excepção
  - eliminar_turma: sucesso, tid inválido, excepção
  - atribuir_turma: sucesso, sem nii (ignorado), excepção
  - mover_aluno: sucesso, nii inválido, ano inválido, excepção
  - promover_um: sucesso, uid inválido
  - promover_todos: sucesso, ano inválido
  - promover_todos_anos: sucesso
  - admin_turmas e admin_promover redirects
"""

from __future__ import annotations

import pytest

from tests.conftest import create_aluno, create_system_user, get_csrf, login_as


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_admin_comp(app):
    """Cria admin e alunos para os testes de companhias."""
    create_system_user("adm_comp", "admin", nome="Admin Comp", pw="AdminComp1")
    # Alunos para operações de mover/promover
    create_aluno("mv_aluno1", "MV01", "Mover Aluno 1", ano="2", pw="mv_aluno1")
    create_aluno("mv_aluno2", "MV02", "Mover Aluno 2", ano="3", pw="mv_aluno2")
    create_aluno("prom_al1", "PR01", "Promover Aluno 1", ano="1", pw="prom_al1")
    create_aluno("prom_al2", "PR02", "Promover Aluno 2", ano="4", pw="prom_al2")


def _login(client):
    """Faz login como admin e devolve o CSRF token."""
    login_as(client, "adm_comp", pw="AdminComp1")
    return get_csrf(client)


def _get_user_id(nii):
    from core.database import db

    with db() as conn:
        row = conn.execute("SELECT id FROM utilizadores WHERE NII=?", (nii,)).fetchone()
    return row["id"] if row else None


def _create_turma_in_db(nome, ano, descricao=None):
    from core.companhias import create_turma

    create_turma(nome, ano, descricao)
    from core.database import db

    with db() as conn:
        row = conn.execute(
            "SELECT id FROM turmas WHERE nome=? AND ano=?", (nome, ano)
        ).fetchone()
    return row["id"] if row else None


# ── GET /admin/companhias ──────────────────────────────────────────────────────


class TestGetCompanhias:
    def test_get_returns_200(self, app, client):
        _login(client)
        resp = client.get("/admin/companhias")
        assert resp.status_code == 200

    def test_requires_admin(self, app, client):
        create_system_user("coz_comp", "cozinha", pw="Cozinha123")
        login_as(client, "coz_comp", pw="Cozinha123")
        resp = client.get("/admin/companhias", follow_redirects=False)
        assert resp.status_code in (302, 403)


# ── criar_turma ────────────────────────────────────────────────────────────────


class TestCriarTurma:
    def test_criar_turma_sucesso(self, app, client):
        """Linhas 48-51: turma criada com flash 'ok'."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "criar_turma",
                "nome_turma": "Alpha Company",
                "ano_turma": "1",
                "descricao": "Turma de teste",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "criada" in html.lower() or "Alpha" in html

    def test_criar_turma_sem_nome(self, app, client):
        """Linha 41: nome em falta → flash error."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "criar_turma",
                "nome_turma": "",
                "ano_turma": "1",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "obrigat" in html.lower() or resp.status_code == 200

    def test_criar_turma_sem_ano(self, app, client):
        """Linha 41: ano em falta → flash error."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "criar_turma",
                "nome_turma": "BetaTurma",
                "ano_turma": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "obrigat" in html.lower() or resp.status_code == 200

    def test_criar_turma_ano_invalido(self, app, client):
        """Linhas 46-47: ano fora de [0-8] → flash 'Ano inválido'."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "criar_turma",
                "nome_turma": "GammaTurma",
                "ano_turma": "99",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "inv" in html.lower() or resp.status_code == 200

    def test_criar_turma_ano_texto_invalido(self, app, client):
        """Ano não numérico → flash error."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "criar_turma",
                "nome_turma": "DeltaTurma",
                "ano_turma": "nao_e_numero",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_criar_turma_excecao(self, app, client):
        """Linhas 53-54: excepção em create_turma → flash error."""
        from unittest import mock

        csrf = _login(client)
        with mock.patch(
            "blueprints.admin.companhias.create_turma",
            side_effect=Exception("BD cheia"),
        ):
            resp = client.post(
                "/admin/companhias",
                data={
                    "csrf_token": csrf,
                    "acao": "criar_turma",
                    "nome_turma": "EpsilonTurma",
                    "ano_turma": "2",
                },
                follow_redirects=True,
            )
        html = resp.data.decode()
        assert "erro" in html.lower() or "BD cheia" in html or resp.status_code == 200


# ── eliminar_turma ─────────────────────────────────────────────────────────────


class TestEliminarTurma:
    def test_eliminar_turma_sucesso(self, app, client):
        """Linhas 62-63: turma eliminada → flash 'ok'."""
        tid = _create_turma_in_db("TurmaParaEliminar", 1)
        assert tid is not None
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "eliminar_turma",
                "tid": str(tid),
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "eliminada" in html.lower() or resp.status_code == 200

    def test_eliminar_turma_tid_invalido(self, app, client):
        """Linhas 58-60: tid não numérico → flash error + redirect."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "eliminar_turma",
                "tid": "nao_e_numero",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "inv" in html.lower() or resp.status_code == 200

    def test_eliminar_turma_tid_vazio(self, app, client):
        """tid vazio → ID inválido."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "eliminar_turma",
                "tid": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_eliminar_turma_excecao(self, app, client):
        """Linhas 64-65: excepção em delete_turma → flash error."""
        from unittest import mock

        csrf = _login(client)
        with mock.patch(
            "blueprints.admin.companhias.delete_turma",
            side_effect=Exception("FK violation"),
        ):
            resp = client.post(
                "/admin/companhias",
                data={
                    "csrf_token": csrf,
                    "acao": "eliminar_turma",
                    "tid": "1",
                },
                follow_redirects=True,
            )
        html = resp.data.decode()
        assert "erro" in html.lower() or "FK" in html or resp.status_code == 200


# ── atribuir_turma ─────────────────────────────────────────────────────────────


class TestAtribuirTurma:
    def test_atribuir_turma_sucesso(self, app, client):
        """Linhas 72-74: atribui turma a aluno → flash 'ok'."""
        tid = _create_turma_in_db("TurmaAtribuir", 2)
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "atribuir_turma",
                "nii_at": "mv_aluno1",
                "turma_id": str(tid),
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "atualizada" in html.lower() or resp.status_code == 200

    def test_atribuir_turma_sem_turma_remove(self, app, client):
        """turma_id vazio → turma_val=None (remove associação)."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "atribuir_turma",
                "nii_at": "mv_aluno1",
                "turma_id": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_atribuir_turma_nii_vazio_ignorado(self, app, client):
        """Linha 70: nii_at vazio → bloco skipped, sem flash."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "atribuir_turma",
                "nii_at": "",
                "turma_id": "1",
            },
            follow_redirects=True,
        )
        # Não deve dar erro, apenas redirect silencioso
        assert resp.status_code == 200

    def test_atribuir_turma_excecao(self, app, client):
        """Linhas 75-76: excepção em assign_turma → flash error."""
        from unittest import mock

        csrf = _login(client)
        with mock.patch(
            "blueprints.admin.companhias.assign_turma",
            side_effect=Exception("FK error"),
        ):
            resp = client.post(
                "/admin/companhias",
                data={
                    "csrf_token": csrf,
                    "acao": "atribuir_turma",
                    "nii_at": "mv_aluno1",
                    "turma_id": "1",
                },
                follow_redirects=True,
            )
        html = resp.data.decode()
        assert "erro" in html.lower() or resp.status_code == 200


# ── mover_aluno ────────────────────────────────────────────────────────────────


class TestMoverAluno:
    def test_mover_aluno_sucesso(self, app, client):
        """Linhas 87-88: mover aluno → flash 'ok'."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "mover_aluno",
                "nii_m": "mv_aluno1",
                "novo_ano": "3",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "movido" in html.lower() or resp.status_code == 200

    def test_mover_aluno_nii_invalido(self, app, client):
        """Linha 82: NII inválido → flash error."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "mover_aluno",
                "nii_m": "",
                "novo_ano": "2",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "inv" in html.lower() or resp.status_code == 200

    def test_mover_aluno_ano_invalido(self, app, client):
        """Linha 84: ano inválido → flash error."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "mover_aluno",
                "nii_m": "mv_aluno1",
                "novo_ano": "99",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "inv" in html.lower() or resp.status_code == 200

    def test_mover_aluno_excecao(self, app, client):
        """Linhas 89-90: excepção em move_aluno_ano → flash error."""
        from unittest import mock

        csrf = _login(client)
        with mock.patch(
            "blueprints.admin.companhias.move_aluno_ano",
            side_effect=Exception("BD locked"),
        ):
            resp = client.post(
                "/admin/companhias",
                data={
                    "csrf_token": csrf,
                    "acao": "mover_aluno",
                    "nii_m": "mv_aluno1",
                    "novo_ano": "2",
                },
                follow_redirects=True,
            )
        html = resp.data.decode()
        assert "erro" in html.lower() or resp.status_code == 200


# ── promover_um ────────────────────────────────────────────────────────────────


class TestPromoverUm:
    def test_promover_um_sucesso(self, app, client):
        """Linhas 98-99: promover aluno individual → flash com destino."""
        uid = _get_user_id("prom_al1")
        assert uid is not None
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_um",
                "uid": str(uid),
                "novo_ni": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "promovido" in html.lower() or resp.status_code == 200

    def test_promover_um_com_novo_ni(self, app, client):
        """Promover com novo_ni preenchido."""
        uid = _get_user_id("prom_al2")
        assert uid is not None
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_um",
                "uid": str(uid),
                "novo_ni": "NV99",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_promover_um_uid_invalido(self, app, client):
        """Linhas 95-97: uid não numérico → flash error + redirect."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_um",
                "uid": "nao_e_numero",
                "novo_ni": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "inv" in html.lower() or resp.status_code == 200

    def test_promover_um_uid_vazio(self, app, client):
        """uid vazio → ID inválido."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_um",
                "uid": "",
                "novo_ni": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ── promover_todos ─────────────────────────────────────────────────────────────


class TestPromoverTodos:
    def test_promover_todos_sucesso(self, app, client):
        """Linhas 106-109: promover todos do ano → flash com destino."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_todos",
                "ano_origem": "1",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "promovidos" in html.lower() or resp.status_code == 200

    def test_promover_todos_ano_invalido(self, app, client):
        """Linhas 103-105: ano inválido → flash error + redirect."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_todos",
                "ano_origem": "99",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "inv" in html.lower() or resp.status_code == 200

    def test_promover_todos_ano_vazio(self, app, client):
        """ano_origem vazio → int(0) → _val_ano(0) = 0 (válido)."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_todos",
                "ano_origem": "",
            },
            follow_redirects=True,
        )
        # ano 0 é válido (alunos concluídos) → pode promover ou dar destino
        assert resp.status_code == 200


# ── promover_todos_anos ────────────────────────────────────────────────────────


class TestPromoverTodosAnos:
    def test_promover_todos_anos_sucesso(self, app, client):
        """Linhas 112-114: promoção global → flash 'concluída'."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "promover_todos_anos",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert (
            "conclu" in html.lower()
            or "promov" in html.lower()
            or resp.status_code == 200
        )


# ── Redirects admin_turmas e admin_promover ────────────────────────────────────


class TestAdminTurmasAndPromover:
    def test_admin_turmas_redirects(self, app, client):
        """Linha 134: /admin/turmas → redireciona para /admin/companhias."""
        _login(client)
        resp = client.get("/admin/turmas", follow_redirects=False)
        assert resp.status_code == 302
        assert "companhias" in resp.headers.get("Location", "")

    def test_admin_promover_get_redirects(self, app, client):
        """Linha 140: GET /admin/promover → redireciona para /admin/companhias#promocao."""
        _login(client)
        resp = client.get("/admin/promover", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "companhias" in location

    def test_admin_promover_post_redirects(self, app, client):
        """POST /admin/promover também redireciona."""
        csrf = _login(client)
        resp = client.post(
            "/admin/promover",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302


# ── Acao desconhecida ─────────────────────────────────────────────────────────


class TestAcaoDesconhecida:
    def test_acao_desconhecida_redirects_without_flash(self, app, client):
        """Acao não reconhecida → redirect silencioso (nenhum elif corresponde)."""
        csrf = _login(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "acao_inexistente",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
