"""
tests/test_reporting.py — Testes de cobertura para blueprints/reporting/routes.py
=================================================================================
"""

from datetime import date

import pytest

from tests.conftest import create_aluno, create_system_user, login_as


@pytest.fixture(autouse=True)
def setup_users(app):
    """Cria utilizadores necessarios para os testes de reporting."""
    create_system_user("rpt_adm", "admin", nome="Admin RPT", pw="RptAdm1234")
    create_system_user("rpt_coz", "cozinha", nome="Cozinha RPT", pw="RptCoz1234")
    create_aluno("rpt_al", "RA01", "Aluno RPT", ano="1")


def _admin(client):
    login_as(client, "rpt_adm", pw="RptAdm1234")


# ── Exportar Mensal ──────────────────────────────────────────────────────


class TestExportarMensal:
    def test_csv_default(self, client):
        _admin(client)
        resp = client.get("/exportar/mensal")
        assert resp.status_code == 200
        assert "csv" in resp.content_type
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert "relatorio_mensal_" in resp.headers["Content-Disposition"]

    def test_csv_with_mes_param(self, client):
        _admin(client)
        resp = client.get("/exportar/mensal?mes=2026-01")
        assert resp.status_code == 200
        assert "csv" in resp.content_type
        assert "2026-01" in resp.headers["Content-Disposition"]

    def test_xlsx_format(self, client):
        _admin(client)
        resp = client.get("/exportar/mensal?fmt=xlsx")
        # Either 200 with xlsx content-type, or 200 with csv fallback
        assert resp.status_code == 200
        ct = resp.content_type
        assert "spreadsheet" in ct or "csv" in ct

    def test_invalid_format_returns_400(self, client):
        _admin(client)
        resp = client.get("/exportar/mensal?fmt=pdf")
        assert resp.status_code == 400

    def test_invalid_month_returns_400(self, client):
        _admin(client)
        resp = client.get("/exportar/mensal?mes=abc")
        assert resp.status_code == 400

    def test_december_boundary(self, client):
        """Meses de dezembro calculam d1 no ano seguinte."""
        _admin(client)
        resp = client.get("/exportar/mensal?mes=2025-12")
        assert resp.status_code == 200
        assert "2025-12" in resp.headers["Content-Disposition"]


# ── Exportar Dia ─────────────────────────────────────────────────────────


class TestExportarDia:
    def test_csv_default(self, client):
        _admin(client)
        resp = client.get("/exportar/dia")
        assert resp.status_code == 200
        assert "csv" in resp.content_type
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_xlsx_format(self, client):
        _admin(client)
        today = date.today().isoformat()
        resp = client.get(f"/exportar/dia?d={today}&fmt=xlsx")
        assert resp.status_code == 200
        ct = resp.content_type
        assert "spreadsheet" in ct or "csv" in ct

    def test_invalid_format_returns_400(self, client):
        _admin(client)
        resp = client.get(f"/exportar/dia?d={date.today().isoformat()}&fmt=pdf")
        assert resp.status_code == 400

    def test_invalid_date_returns_400(self, client):
        _admin(client)
        resp = client.get("/exportar/dia?d=not-a-date&fmt=csv")
        assert resp.status_code == 400


# ── Exportar Relatorio ───────────────────────────────────────────────────


class TestExportarRelatorio:
    def test_csv_default(self, client):
        _admin(client)
        resp = client.get("/exportar/relatorio")
        assert resp.status_code == 200
        assert "csv" in resp.content_type

    def test_xlsx_format(self, client):
        _admin(client)
        d0 = date.today().isoformat()
        resp = client.get(f"/exportar/relatorio?d0={d0}&fmt=xlsx")
        assert resp.status_code == 200
        ct = resp.content_type
        assert "spreadsheet" in ct or "csv" in ct

    def test_invalid_format_returns_400(self, client):
        _admin(client)
        resp = client.get("/exportar/relatorio?fmt=pdf")
        assert resp.status_code == 400


# ── Dashboard Semanal ────────────────────────────────────────────────────


class TestDashboardSemanal:
    def test_default_current_week(self, client):
        _admin(client)
        resp = client.get("/dashboard-semanal")
        assert resp.status_code == 200

    def test_with_d0_param(self, client):
        _admin(client)
        resp = client.get("/dashboard-semanal?d0=2026-01-05")
        assert resp.status_code == 200


# ── Calendario Publico ───────────────────────────────────────────────────


class TestCalendarioPublico:
    def test_default(self, client):
        _admin(client)
        resp = client.get("/calendario")
        assert resp.status_code == 200

    def test_with_mes_param(self, client):
        _admin(client)
        resp = client.get("/calendario?mes=2026-03")
        assert resp.status_code == 200
        assert "Mar" in resp.data.decode()

    def test_invalid_mes_defaults(self, client):
        """Mes invalido nao causa erro — faz fallback ao mes actual."""
        _admin(client)
        resp = client.get("/calendario?mes=xyz")
        assert resp.status_code == 200

    def test_january_boundary(self, client):
        """Janeiro: prev_mes deve ser dezembro do ano anterior."""
        _admin(client)
        resp = client.get("/calendario?mes=2026-01")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "2025-12" in html
        assert "Janeiro" in html
