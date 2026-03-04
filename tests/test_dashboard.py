"""
tests/test_dashboard.py — Testes do dashboard semanal e previsão amanhã
=======================================================================
"""

from tests.conftest import create_system_user, login_as


class TestPrevisaoAmanha:
    def test_painel_mostra_previsao_para_cozinha(self, app, client):
        """Cozinha deve ver o card de previsão para amanhã."""
        with app.app_context():
            create_system_user("T_DSH_COZ", "cozinha", pw="test123")
        login_as(client, "T_DSH_COZ", pw="test123")
        resp = client.get("/painel")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Previsão Amanhã" in html or "Previs" in html

    def test_painel_mostra_previsao_para_admin(self, app, client):
        """Admin deve ver o card de previsão para amanhã."""
        login_as(client, "admin", "admin123")
        resp = client.get("/painel", follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Previsão Amanhã" in html or "Previs" in html


class TestDashboardComparacaoSemanal:
    def test_dashboard_mostra_semana_anterior(self, app, client):
        """Dashboard semanal deve mostrar comparação com semana anterior."""
        login_as(client, "admin", "admin123")
        resp = client.get("/dashboard-semanal")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Semana anterior" in html
        assert "Total semana" in html

    def test_dashboard_mostra_variacao(self, app, client):
        """Dashboard semanal deve mostrar linha de variação."""
        login_as(client, "admin", "admin123")
        resp = client.get("/dashboard-semanal")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Varia" in html
