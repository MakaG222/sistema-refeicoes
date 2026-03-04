"""
tests/test_alertas.py — Testes de alertas operacionais no painel
================================================================
"""

from datetime import date

import sistema_refeicoes_v8_4 as sr

from tests.conftest import create_aluno, login_as


class TestAlertasPainel:
    def test_detencao_expira_hoje_gera_alerta(self, app):
        """Detenção que expira hoje deve gerar alerta 'warn'."""
        from app import _alertas_painel

        with app.app_context():
            uid = create_aluno("T_AL_01", "981", "Alerta Teste 1", "1")
            hoje = date.today().isoformat()
            with sr.db() as conn:
                conn.execute(
                    "INSERT INTO detencoes(utilizador_id, detido_de, detido_ate, motivo, criado_por) VALUES(?,?,?,?,?)",
                    (uid, hoje, hoje, "Teste", "test"),
                )
                conn.commit()

            alertas = _alertas_painel(hoje, "oficialdia")
            msgs = [a["msg"] for a in alertas]
            assert any("expi" in m.lower() for m in msgs)
            assert any(
                a["cat"] == "warn" for a in alertas if "expi" in a["msg"].lower()
            )

    def test_licenca_pendente_gera_alerta(self, app):
        """Licença sem hora_saida deve gerar alerta."""
        from app import _alertas_painel

        with app.app_context():
            uid = create_aluno("T_AL_02", "982", "Alerta Teste 2", "3")
            hoje = date.today().isoformat()
            with sr.db() as conn:
                conn.execute(
                    "INSERT INTO licencas(utilizador_id, data, tipo) VALUES(?,?,?)",
                    (uid, hoje, "apos_jantar"),
                )
                conn.commit()

            alertas = _alertas_painel(hoje, "oficialdia")
            msgs = [a["msg"] for a in alertas]
            assert any("saída" in m.lower() or "saida" in m.lower() for m in msgs)

    def test_cozinha_nao_recebe_alertas(self, app):
        """Perfil cozinha não deve receber alertas operacionais."""
        from app import _alertas_painel

        with app.app_context():
            alertas = _alertas_painel(date.today().isoformat(), "cozinha")
            assert alertas == []

    def test_alertas_visiveis_no_painel_html(self, app, client):
        """Alertas devem aparecer no HTML do painel_dia."""
        with app.app_context():
            uid = create_aluno("T_AL_03", "983", "Alerta Teste 3", "2")
            hoje = date.today().isoformat()
            with sr.db() as conn:
                conn.execute(
                    "INSERT INTO detencoes(utilizador_id, detido_de, detido_ate, motivo, criado_por) VALUES(?,?,?,?,?)",
                    (uid, hoje, hoje, "Teste HTML", "test"),
                )
                conn.commit()

        login_as(client, "oficialdia", "oficial123")
        resp = client.get("/painel", follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alert-warn" in html
        assert "expira" in html.lower()
