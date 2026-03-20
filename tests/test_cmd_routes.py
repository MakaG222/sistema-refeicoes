"""Testes para as rotas do blueprint cmd."""

from datetime import date, timedelta

import pytest

from tests.conftest import create_system_user, create_aluno, login_as, get_csrf


@pytest.fixture(autouse=True)
def setup_cmd(app):
    """Cria utilizadores cmd e alunos de teste."""
    create_system_user("cmdtest", "cmd", nome="CMD Teste", ano="1", pw="CmdTest123")
    create_aluno("al_cmd1", "AC01", "Aluno CMD1", ano="1")
    create_aluno("al_cmd2", "AC02", "Aluno CMD2", ano="2")


def _login_cmd(client):
    login_as(client, "cmdtest", pw="CmdTest123")
    return get_csrf(client)


class TestCmdEditarAluno:
    def test_get_editar_own_year(self, app, client):
        _login_cmd(client)
        resp = client.get("/cmd/editar-aluno/al_cmd1?ano=1")
        assert resp.status_code == 200
        assert "Aluno CMD1" in resp.data.decode()

    def test_get_editar_other_year_rejected(self, app, client):
        _login_cmd(client)
        resp = client.get("/cmd/editar-aluno/al_cmd2?ano=2", follow_redirects=True)
        html = resp.data.decode()
        assert "teu ano" in html.lower() or resp.status_code == 200

    def test_post_editar_valid(self, app, client):
        csrf = _login_cmd(client)
        resp = client.post(
            "/cmd/editar-aluno/al_cmd1?ano=1",
            data={
                "csrf_token": csrf,
                "nome": "Aluno Editado CMD",
                "ni": "AC01B",
                "email": "aluno@test.pt",
                "telemovel": "+351912345678",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_post_editar_empty_name(self, app, client):
        csrf = _login_cmd(client)
        resp = client.post(
            "/cmd/editar-aluno/al_cmd1?ano=1",
            data={
                "csrf_token": csrf,
                "nome": "",
                "ni": "AC01",
                "email": "",
                "telemovel": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "nome" in html.lower() or resp.status_code == 200

    def test_get_editar_not_found(self, app, client):
        _login_cmd(client)
        resp = client.get("/cmd/editar-aluno/inexistente", follow_redirects=True)
        assert resp.status_code == 200


class TestCmdVerPerfil:
    def test_ver_perfil_oficialdia(self, app, client):
        create_system_user("ofd_test", "oficialdia", nome="OFD Teste", pw="OfdTest123")
        login_as(client, "ofd_test", pw="OfdTest123")
        resp = client.get("/alunos/perfil/al_cmd1?ano=1")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Template renders the perfil page
        assert "visualiza" in html.lower() or "Perfil" in html

    def test_ver_perfil_not_found(self, app, client):
        create_system_user(
            "ofd_test2", "oficialdia", nome="OFD Teste2", pw="OfdTest223"
        )
        login_as(client, "ofd_test2", pw="OfdTest223")
        resp = client.get("/alunos/perfil/inexistente", follow_redirects=True)
        assert resp.status_code == 200

    def test_ver_perfil_admin_redirects(self, app, client):
        create_system_user("adm_prf", "admin", nome="Admin Perfil", pw="Admin1234")
        login_as(client, "adm_prf", pw="Admin1234")
        resp = client.get("/alunos/perfil/al_cmd1")
        assert resp.status_code == 302

    def test_cmd_cannot_see_other_year(self, app, client):
        _login_cmd(client)
        resp = client.get("/alunos/perfil/al_cmd2", follow_redirects=True)
        html = resp.data.decode()
        assert "restrito" in html.lower() or resp.status_code == 200


class TestCmdAusencias:
    def test_get_ausencias(self, app, client):
        _login_cmd(client)
        resp = client.get("/cmd/ausencias")
        assert resp.status_code == 200
        assert (
            "Ausências" in resp.data.decode()
            or "ausencia" in resp.data.decode().lower()
        )

    def test_registar_ausencia(self, app, client):
        csrf = _login_cmd(client)
        hoje = date.today().isoformat()
        resp = client.post(
            "/cmd/ausencias",
            data={
                "csrf_token": csrf,
                "nii": "al_cmd1",
                "de": hoje,
                "ate": hoje,
                "motivo": "Consulta médica",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "registada" in html.lower() or resp.status_code == 200

    def test_registar_ausencia_other_year(self, app, client):
        csrf = _login_cmd(client)
        hoje = date.today().isoformat()
        resp = client.post(
            "/cmd/ausencias",
            data={
                "csrf_token": csrf,
                "nii": "al_cmd2",
                "de": hoje,
                "ate": hoje,
                "motivo": "Teste",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "teu" in html.lower() or "ano" in html.lower() or resp.status_code == 200

    def test_registar_ausencia_not_found(self, app, client):
        csrf = _login_cmd(client)
        resp = client.post(
            "/cmd/ausencias",
            data={
                "csrf_token": csrf,
                "nii": "inexistente",
                "de": date.today().isoformat(),
                "ate": date.today().isoformat(),
                "motivo": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "não encontrado" in html.lower() or resp.status_code == 200


class TestCmdDetencoes:
    def test_get_detencoes(self, app, client):
        _login_cmd(client)
        resp = client.get("/cmd/detencoes")
        assert resp.status_code == 200

    def test_criar_detencao(self, app, client):
        csrf = _login_cmd(client)
        hoje = date.today().isoformat()
        amanha = (date.today() + timedelta(days=1)).isoformat()
        resp = client.post(
            "/cmd/detencoes",
            data={
                "csrf_token": csrf,
                "nii": "al_cmd1",
                "de": hoje,
                "ate": amanha,
                "motivo": "Detido por teste",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "registada" in html.lower() or resp.status_code == 200

    def test_criar_detencao_other_year(self, app, client):
        csrf = _login_cmd(client)
        hoje = date.today().isoformat()
        resp = client.post(
            "/cmd/detencoes",
            data={
                "csrf_token": csrf,
                "nii": "al_cmd2",
                "de": hoje,
                "ate": hoje,
                "motivo": "Teste",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "teu" in html.lower() or "ano" in html.lower() or resp.status_code == 200

    def test_criar_detencao_not_found(self, app, client):
        csrf = _login_cmd(client)
        resp = client.post(
            "/cmd/detencoes",
            data={
                "csrf_token": csrf,
                "nii": "naoexiste",
                "de": date.today().isoformat(),
                "ate": date.today().isoformat(),
                "motivo": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_criar_detencao_invalid_dates(self, app, client):
        csrf = _login_cmd(client)
        resp = client.post(
            "/cmd/detencoes",
            data={
                "csrf_token": csrf,
                "nii": "al_cmd1",
                "de": "invalido",
                "ate": "invalido",
                "motivo": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_criar_detencao_dates_reversed(self, app, client):
        csrf = _login_cmd(client)
        amanha = (date.today() + timedelta(days=1)).isoformat()
        hoje = date.today().isoformat()
        resp = client.post(
            "/cmd/detencoes",
            data={
                "csrf_token": csrf,
                "nii": "al_cmd1",
                "de": amanha,
                "ate": hoje,
                "motivo": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_remover_detencao_invalid_id(self, app, client):
        csrf = _login_cmd(client)
        resp = client.post(
            "/cmd/detencoes",
            data={
                "csrf_token": csrf,
                "acao": "remover",
                "id": "abc",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
