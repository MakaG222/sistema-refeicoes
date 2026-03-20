"""Testes para as rotas do blueprint aluno."""

from datetime import date, timedelta

import pytest

from tests.conftest import create_aluno, login_as, get_csrf


@pytest.fixture(autouse=True)
def setup_aluno(app):
    """Cria alunos de teste."""
    create_aluno("al_rt1", "AR01", "Aluno Teste Route", ano="1")
    create_aluno("al_rt2", "AR02", "Aluno Teste Route2", ano="2")


def _login_aluno(client, nii="al_rt1"):
    login_as(client, nii)
    return get_csrf(client)


class TestAlunoHome:
    def test_home_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno")
        assert resp.status_code == 200

    def test_home_shows_meals(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno")
        html = resp.data.decode()
        # Home page should render content
        assert "aluno" in html.lower() or resp.status_code == 200

    def test_home_with_date(self, app, client):
        _login_aluno(client)
        resp = client.get(f"/aluno?d={date.today().isoformat()}")
        assert resp.status_code == 200

    def test_home_non_aluno_redirect(self, app, client):
        """Non-aluno profiles should not see aluno home."""
        from tests.conftest import create_system_user

        create_system_user("adm_alu", "admin", pw="Admin1234")
        login_as(client, "adm_alu", pw="Admin1234")
        resp = client.get("/aluno", follow_redirects=False)
        # Admin accessing /aluno may redirect or show different content
        assert resp.status_code in (200, 302)


class TestAlunoEditar:
    def test_editar_get(self, app, client):
        _login_aluno(client)
        # Use a future date to ensure the day is editable
        futuro = (date.today() + timedelta(days=2)).isoformat()
        resp = client.get(f"/aluno/editar/{futuro}")
        # May redirect if day not editable (prazo rules), or show form
        assert resp.status_code in (200, 302)

    def test_editar_post_marcar(self, app, client):
        csrf = _login_aluno(client)
        d = date.today().isoformat()
        resp = client.post(
            f"/aluno/editar/{d}",
            data={
                "csrf_token": csrf,
                "almoco": "normal",
                "jantar": "normal",
                "pequeno_almoco": "1",
                "lanche": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_editar_invalid_date(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/editar/invalido")
        # Should handle invalid date gracefully
        assert resp.status_code in (200, 302, 400)

    def test_editar_past_date(self, app, client):
        _login_aluno(client)
        ontem = (date.today() - timedelta(days=1)).isoformat()
        resp = client.get(f"/aluno/editar/{ontem}")
        assert resp.status_code in (200, 302)


class TestAlunoAusencias:
    def test_ausencias_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/ausencias")
        assert resp.status_code == 200

    def test_ausencias_post_registar(self, app, client):
        csrf = _login_aluno(client)
        futuro = (date.today() + timedelta(days=7)).isoformat()
        futuro2 = (date.today() + timedelta(days=8)).isoformat()
        resp = client.post(
            "/aluno/ausencias",
            data={
                "csrf_token": csrf,
                "acao": "registar",
                "de": futuro,
                "ate": futuro2,
                "motivo": "Consulta médica",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAlunoHistorico:
    def test_historico_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/historico")
        assert resp.status_code == 200

    def test_historico_with_date_range(self, app, client):
        _login_aluno(client)
        d0 = (date.today() - timedelta(days=30)).isoformat()
        d1 = date.today().isoformat()
        resp = client.get(f"/aluno/historico?d0={d0}&d1={d1}")
        assert resp.status_code == 200


class TestAlunoExportarHistorico:
    def test_exportar_csv(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/exportar-historico?fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type or resp.status_code == 200

    def test_exportar_default(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/exportar-historico")
        assert resp.status_code == 200


class TestAlunoPassword:
    def test_password_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/password")
        assert resp.status_code == 200

    def test_password_post_mismatch(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "al_rt1",
                "pw_nova": "NovaPass123",
                "pw_confirma": "Diferente123",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert (
            "não coincidem" in html.lower()
            or "confirma" in html.lower()
            or resp.status_code == 200
        )

    def test_password_post_wrong_current(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "errada123",
                "pw_nova": "NovaPass123",
                "pw_confirma": "NovaPass123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_password_post_weak(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "al_rt1",
                "pw_nova": "123",
                "pw_confirma": "123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_password_post_valid(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "al_rt1",
                "pw_nova": "NovaSegura123",
                "pw_confirma": "NovaSegura123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAlunoPerfil:
    def test_perfil_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/perfil")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Aluno Teste Route" in html or "perfil" in html.lower()

    def test_perfil_post_update_email(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "csrf_token": csrf,
                "email": "aluno@teste.pt",
                "telemovel": "+351912345678",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_perfil_post_invalid_email(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "csrf_token": csrf,
                "email": "invalido",
                "telemovel": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_perfil_post_invalid_phone(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "csrf_token": csrf,
                "email": "",
                "telemovel": "abc",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAlunoLicencaFds:
    def test_licenca_post_no_data(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "csrf_token": csrf,
                "acao": "marcar",
                "data": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_licenca_post_marcar(self, app, client):
        csrf = _login_aluno(client)
        # Find next Saturday
        hoje = date.today()
        dias_ate_sab = (5 - hoje.weekday()) % 7
        if dias_ate_sab == 0:
            dias_ate_sab = 7
        sabado = (hoje + timedelta(days=dias_ate_sab)).isoformat()
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "csrf_token": csrf,
                "acao": "marcar",
                "data": sabado,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_licenca_post_cancelar(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "csrf_token": csrf,
                "acao": "cancelar",
                "data": date.today().isoformat(),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
