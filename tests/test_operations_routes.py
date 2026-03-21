"""Testes para as rotas do blueprint operations."""

from datetime import date, timedelta

import pytest

from tests.conftest import create_system_user, create_aluno, login_as, get_csrf


@pytest.fixture(autouse=True)
def setup_ops(app):
    """Cria utilizadores para testes de operações."""
    create_system_user("ofd_ops", "oficialdia", nome="OFD Ops", pw="OfdOps1234")
    create_system_user("coz_ops", "cozinha", nome="Cozinha Ops", pw="CozOps1234")
    create_system_user("adm_ops", "admin", nome="Admin Ops", pw="AdmOps1234")
    create_system_user("cmd_ops", "cmd", nome="CMD Ops", ano="1", pw="CmdOps1234")
    create_aluno("al_ops1", "AO01", "Aluno Ops1", ano="1")
    create_aluno("al_ops2", "AO02", "Aluno Ops2", ano="2")


def _login_ofd(client):
    login_as(client, "ofd_ops", pw="OfdOps1234")
    return get_csrf(client)


def _login_admin_ops(client):
    login_as(client, "adm_ops", pw="AdmOps1234")
    return get_csrf(client)


def _login_cozinha(client):
    login_as(client, "coz_ops", pw="CozOps1234")
    return get_csrf(client)


class TestPainelDia:
    def test_painel_get_ofd(self, app, client):
        _login_ofd(client)
        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_painel_get_admin(self, app, client):
        _login_admin_ops(client)
        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_painel_get_cozinha(self, app, client):
        _login_cozinha(client)
        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_painel_with_date(self, app, client):
        _login_ofd(client)
        resp = client.get(f"/painel?d={date.today().isoformat()}")
        assert resp.status_code == 200

    def test_painel_cmd_sees_own_year(self, app, client):
        login_as(client, "cmd_ops", pw="CmdOps1234")
        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_painel_backup(self, app, client):
        csrf = _login_admin_ops(client)
        resp = client.post(
            "/painel",
            data={"csrf_token": csrf, "acao": "backup"},
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestListaAlunos:
    def test_lista_alunos_ano1(self, app, client):
        _login_ofd(client)
        resp = client.get("/alunos/1")
        assert resp.status_code == 200

    def test_lista_alunos_admin(self, app, client):
        _login_admin_ops(client)
        resp = client.get("/alunos/1")
        assert resp.status_code == 200

    def test_lista_alunos_with_date(self, app, client):
        _login_ofd(client)
        resp = client.get(f"/alunos/1?d={date.today().isoformat()}")
        assert resp.status_code == 200

    def test_lista_alunos_post_marcar(self, app, client):
        """Testa marcação de refeições."""
        csrf = _login_ofd(client)
        from core.database import db

        with db() as conn:
            uid = conn.execute(
                "SELECT id FROM utilizadores WHERE NII='al_ops1'"
            ).fetchone()["id"]
        resp = client.post(
            "/alunos/1",
            data={
                "csrf_token": csrf,
                "uid": str(uid),
                "data": date.today().isoformat(),
                "almoco": "normal",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestRelatorioSemanal:
    def test_relatorio_get(self, app, client):
        _login_ofd(client)
        resp = client.get("/relatorio")
        assert resp.status_code == 200

    def test_relatorio_with_date(self, app, client):
        _login_admin_ops(client)
        segunda = date.today() - timedelta(days=date.today().weekday())
        resp = client.get(f"/relatorio?d0={segunda.isoformat()}")
        assert resp.status_code == 200


class TestExcecoes:
    def test_excecoes_get(self, app, client):
        _login_ofd(client)
        resp = client.get(f"/excecoes/{date.today().isoformat()}")
        assert resp.status_code == 200

    def test_excecoes_post(self, app, client):
        from core.database import db

        csrf = _login_ofd(client)
        with db() as conn:
            uid = conn.execute(
                "SELECT id FROM utilizadores WHERE NII='al_ops1'"
            ).fetchone()["id"]
        resp = client.post(
            f"/excecoes/{date.today().isoformat()}",
            data={
                "csrf_token": csrf,
                "uid": str(uid),
                "almoco": "veg",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAusencias:
    def test_ausencias_get(self, app, client):
        _login_admin_ops(client)
        resp = client.get("/ausencias")
        assert resp.status_code == 200

    def test_ausencias_post_registar(self, app, client):
        csrf = _login_admin_ops(client)
        hoje = date.today().isoformat()
        resp = client.post(
            "/ausencias",
            data={
                "csrf_token": csrf,
                "nii": "al_ops1",
                "de": hoje,
                "ate": hoje,
                "motivo": "Teste ops",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestLicencasES:
    def test_licencas_get(self, app, client):
        _login_ofd(client)
        resp = client.get("/oficialdia/licencas-es")
        assert resp.status_code == 200

    def test_licencas_post_saida(self, app, client):
        csrf = _login_ofd(client)
        resp = client.post(
            "/oficialdia/licencas-es",
            data={
                "csrf_token": csrf,
                "acao": "saida",
                "nii": "al_ops1",
                "hora_saida": "14:00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestControloPresencas:
    def test_presencas_get(self, app, client):
        _login_ofd(client)
        resp = client.get("/presencas")
        assert resp.status_code == 200

    def test_presencas_post_search(self, app, client):
        csrf = _login_ofd(client)
        resp = client.post(
            "/presencas",
            data={
                "csrf_token": csrf,
                "ni": "AO01",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_presencas_post_no_result(self, app, client):
        csrf = _login_ofd(client)
        resp = client.post(
            "/presencas",
            data={
                "csrf_token": csrf,
                "ni": "ZZZZZZ",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestImprimir:
    def test_imprimir_ano(self, app, client):
        _login_ofd(client)
        resp = client.get("/imprimir/1")
        assert resp.status_code == 200
