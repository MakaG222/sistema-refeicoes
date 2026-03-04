"""
tests/test_detencoes.py — Testes de detenções
==============================================
"""

from datetime import date, timedelta

import sistema_refeicoes_v8_4 as sr

from tests.conftest import create_aluno, create_system_user, login_as


def _future_date(days=10):
    return date.today() + timedelta(days=days)


# ── Testes directos (funções internas) ────────────────────────────────────────


class TestDetencaoFunctions:
    def test_tem_detencao_ativa_true(self, app):
        """_tem_detencao_ativa retorna True quando detido."""
        import app as app_module

        uid = create_aluno("T_DET_01", "701", "Detencao A", "1")
        d = _future_date(30)

        with sr.db() as conn:
            conn.execute(
                "INSERT INTO detencoes (utilizador_id, detido_de, detido_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "Teste", "cmd1"),
            )
            conn.commit()

        assert app_module._tem_detencao_ativa(uid, d) is True

    def test_tem_detencao_ativa_false(self, app):
        """_tem_detencao_ativa retorna False quando não detido."""
        import app as app_module

        uid = create_aluno("T_DET_02", "702", "Detencao B", "1")
        d = _future_date(31)
        assert app_module._tem_detencao_ativa(uid, d) is False

    def test_auto_marcar_refeicoes_detido(self, app):
        """Auto-marcação de refeições quando aluno é detido."""
        import app as app_module

        uid = create_aluno("T_DET_03", "703", "Detencao C", "1")
        d = _future_date(32)

        # Confirmar que não tem refeições
        got = sr.refeicao_get(uid, d)
        assert got["almoco"] is None

        # Auto-marcar
        app_module._auto_marcar_refeicoes_detido(uid, d, d)

        got = sr.refeicao_get(uid, d)
        assert got["pequeno_almoco"] == 1
        assert got["lanche"] == 1
        assert got["almoco"] == "Normal"
        assert got["jantar_tipo"] == "Normal"
        assert got["jantar_sai_unidade"] == 0

    def test_auto_marcar_multi_day(self, app):
        """Auto-marcação funciona para múltiplos dias de detenção."""
        import app as app_module

        uid = create_aluno("T_DET_04", "704", "Detencao D", "1")
        d1 = _future_date(33)
        d2 = d1 + timedelta(days=2)

        app_module._auto_marcar_refeicoes_detido(uid, d1, d2)

        # Verificar 3 dias (d1, d1+1, d2)
        for i in range(3):
            d = d1 + timedelta(days=i)
            got = sr.refeicao_get(uid, d)
            assert got["almoco"] == "Normal", f"Dia {d}: almoco deveria ser Normal"

    def test_auto_marcar_skips_existing(self, app):
        """Auto-marcação não sobrescreve refeição existente."""
        import app as app_module

        uid = create_aluno("T_DET_05", "705", "Detencao E", "1")
        d = _future_date(36)

        # Marcar refeição personalizada
        sr.refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 0,
                "almoco": "Vegetariano",
                "jantar_tipo": "Dieta",
                "jantar_sai_unidade": 0,
            },
        )

        # Auto-marcar — não deve sobrescrever porque já existe almoco
        app_module._auto_marcar_refeicoes_detido(uid, d, d)

        got = sr.refeicao_get(uid, d)
        assert got["almoco"] == "Vegetariano"  # Mantém o existente


# ── Testes de acesso (roles) ─────────────────────────────────────────────────


class TestDetencaoAccess:
    def test_cmd_can_access_detencoes(self, app, client):
        """CMD pode aceder ao módulo de detenções."""
        create_system_user("cmd_det_test", "cmd", ano="1")
        login_as(client, "cmd_det_test", "cmd_det_test123")

        resp = client.get("/cmd/detencoes")
        assert resp.status_code == 200

    def test_admin_can_access_detencoes(self, app, client):
        """Admin pode aceder ao módulo de detenções."""
        login_as(client, "admin", "admin123")
        resp = client.get("/cmd/detencoes")
        assert resp.status_code == 200

    def test_oficialdia_cannot_access_detencoes(self, app, client):
        """Oficial de dia NÃO pode aceder ao módulo de detenções."""
        create_system_user("od_det_test", "oficialdia")
        login_as(client, "od_det_test", "od_det_test123")

        resp = client.get("/cmd/detencoes")
        # Deve ser rejeitado (redirect ou 403)
        assert resp.status_code in (302, 403)

    def test_aluno_cannot_access_detencoes(self, app, client):
        """Aluno NÃO pode aceder ao módulo de detenções."""
        create_aluno("T_DET_ALUNO", "799", "Aluno Det", "1")
        login_as(client, "T_DET_ALUNO")

        resp = client.get("/cmd/detencoes")
        assert resp.status_code in (302, 403)
