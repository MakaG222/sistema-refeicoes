"""Testes para as rotas do blueprint reporting."""

from datetime import date, timedelta

import pytest

from tests.conftest import create_system_user, create_aluno, login_as, get_csrf


@pytest.fixture(autouse=True)
def setup_reporting(app):
    """Cria utilizadores para testes de reporting."""
    create_system_user("adm_rep", "admin", nome="Admin Report", pw="AdmRep1234")
    create_system_user("coz_rep", "cozinha", nome="Cozinha Report", pw="CozRep1234")
    create_system_user("ofd_rep", "oficialdia", nome="OFD Report", pw="OfdRep1234")
    create_aluno("al_rep1", "RP01", "Aluno Report", ano="1")


def _login_admin(client):
    login_as(client, "adm_rep", pw="AdmRep1234")
    return get_csrf(client)


def _login_cozinha(client):
    login_as(client, "coz_rep", pw="CozRep1234")
    return get_csrf(client)


class TestExportarMensal:
    def test_exportar_mensal_csv(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/mensal?fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_exportar_mensal_csv_with_mes(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/mensal?mes=2026-01&fmt=csv")
        assert resp.status_code == 200

    def test_exportar_mensal_invalid_format(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/mensal?fmt=pdf")
        assert resp.status_code == 400

    def test_exportar_mensal_invalid_mes(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/mensal?mes=invalido&fmt=csv")
        assert resp.status_code == 400

    def test_exportar_mensal_cozinha(self, app, client):
        _login_cozinha(client)
        resp = client.get("/exportar/mensal?fmt=csv")
        assert resp.status_code == 200


class TestCalendarioPublico:
    def test_calendario_get(self, app, client):
        _login_admin(client)
        resp = client.get("/calendario")
        assert resp.status_code == 200

    def test_calendario_with_mes(self, app, client):
        _login_admin(client)
        resp = client.get("/calendario?mes=2026-03")
        assert resp.status_code == 200

    def test_calendario_invalid_mes(self, app, client):
        _login_admin(client)
        resp = client.get("/calendario?mes=invalido")
        assert resp.status_code == 200  # falls back to current month

    def test_calendario_aluno(self, app, client):
        login_as(client, "al_rep1")
        resp = client.get("/calendario")
        assert resp.status_code == 200

    def test_calendario_prev_next(self, app, client):
        _login_admin(client)
        resp = client.get("/calendario?mes=2026-01")
        html = resp.data.decode()
        assert "Janeiro" in html
        resp2 = client.get("/calendario?mes=2026-12")
        html2 = resp2.data.decode()
        assert "Dezembro" in html2


class TestDashboardSemanal:
    def test_dashboard_get(self, app, client):
        _login_admin(client)
        resp = client.get("/dashboard-semanal")
        assert resp.status_code == 200

    def test_dashboard_with_d0(self, app, client):
        _login_admin(client)
        segunda = date.today() - timedelta(days=date.today().weekday())
        resp = client.get(f"/dashboard-semanal?d0={segunda.isoformat()}")
        assert resp.status_code == 200

    def test_dashboard_cozinha(self, app, client):
        _login_cozinha(client)
        resp = client.get("/dashboard-semanal")
        assert resp.status_code == 200


class TestExportarDia:
    def test_exportar_dia_csv(self, app, client):
        _login_admin(client)
        resp = client.get(f"/exportar/dia?d={date.today().isoformat()}&fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_exportar_dia_invalid_format(self, app, client):
        _login_admin(client)
        resp = client.get(f"/exportar/dia?d={date.today().isoformat()}&fmt=pdf")
        assert resp.status_code == 400

    def test_exportar_dia_invalid_date(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/dia?d=invalido&fmt=csv")
        assert resp.status_code == 400

    def test_exportar_dia_default(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/dia")
        assert resp.status_code == 200


class TestExportarRelatorio:
    def test_exportar_relatorio_csv(self, app, client):
        _login_admin(client)
        segunda = date.today() - timedelta(days=date.today().weekday())
        resp = client.get(f"/exportar/relatorio?d0={segunda.isoformat()}&fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_exportar_relatorio_invalid_format(self, app, client):
        _login_admin(client)
        resp = client.get(f"/exportar/relatorio?d0={date.today().isoformat()}&fmt=pdf")
        assert resp.status_code == 400

    def test_exportar_relatorio_invalid_date(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/relatorio?d0=invalido&fmt=csv")
        assert resp.status_code == 400

    def test_exportar_relatorio_default(self, app, client):
        _login_admin(client)
        resp = client.get("/exportar/relatorio")
        assert resp.status_code == 200
